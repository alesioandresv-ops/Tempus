"""rate limit deslizante de verdad: el cubo no se reinicia cada segundo

Revision ID: 0013_rate_limit_deslizante
Revises: 0012_businesses_insert_policy
Create Date: 2026-10-02

Arregla el limitador de intentos. Antes de esta migracion **no limitaba**.

El `revision` tiene que caber en `alembic_version.version_num`, que es
`varchar(32)`. "0013_rate_limit_ventana_deslizante" son 35, y la migracion fallaba
con un `StringDataRightTruncationError` que no dice nada de esto. El nombre del
archivo puede ser largo; el del `revision` no.

## Que estaba roto

`0004` escribio `window_start` con `date_trunc('second', clock_timestamp())`. Ese
valor es parte de la clave unica `(key, window_start)`, asi que cada segundo--cada
segundo del reloj--nacia un cubo nuevo con `hits = []`. El conteo de la funcion
miraba **un solo cubo**, el del segundo en curso.

El resultado es que `rate_limit_login_per_ip = 5` no era "5 intentos por minuto":
era **5 intentos por segundo**. Seis logins con contrasena incorrecta desde la misma
IP, uno por segundo, nunca rebanan el limite: cada request abre su cubo vacio y se
responde 401. Un atacante que evade un rate limit de esta forma no tuvo que hacer
nada raro; simplemente no hizo nada.

El propio `0004` describe el comportamiento correcto en sus comentarios--hay una fila
por clave, no una por intento; la ventana deslizante la dan los `hits`-- y el
codigo no lo hacia. `tests/tenancy/test_login_functions.py::test_la_ventana_deslizante`
pase igual porque el test hace sus tres llamadas en el mismo segundo, que es
justamente el unico caso en que la implementacion rota se comporta bien.

Como la ventana era de 60s y el cubo de 1s, `retry_after` tambien mentia: decia
"espera hasta 60 segundos" y al segundo siguiente el limite ya estaba limpio.

## Que hace esta version

1. **El cubo esta alineado a la ventana, no al segundo.** `window_start` pasa a ser
   `floor(epoch / p_window_seconds) * p_window_seconds`, de modo que dentro de una
   ventana de 60s hay un solo cubo por clave. Ademas el alineamiento es
   deterministico, que es lo que hace que dos peticiones concurrentes caigan en la
   misma fila y compitan por el indice unico en vez de crear dos cubos y contar una
   vez cada una.

2. **La cuenta suma todos los cubos de la clave**, no solo el actual. Un cubo
   anterior--el del tramo previo--todavia puede tener intentos vigentes, y sin esto
   el corte de tramo volveria a dejar pasar la mitad de un limite.

3. **La poda es por timestamp de cada intento**, en todos los cubos de la clave, y
   es la que decide cuando un cubo se borra: se borra recien cuando **ninguno** de
   sus intentos sigue dentro de la ventana. Borrarlo por `window_start < cutoff`
   seria incorrecto con los cubos alineados--el tramo previo empieza antes del
   corte y todavia puede tener hits vigentes-- y perderia intentos que cuentan.

4. **Se sigue usando `clock_timestamp()`**, que es lo que hace que la ventana se
   pueda liberar dentro de una transaccion y que un login lento se cuente cuando
   llego y no cuando empezo a procesarse. Perder eso seria cambiar un bug por otro.

El resto de la superficie no se toca: sigue siendo `INVOKER`--no hay nada que
saltar, porque `rate_limit_buckets` no tiene RLS--, sigue fijando `search_path` sin
`pg_temp`, y `GREATEST(1, ...)` sigue impidiendo un `Retry-After: 0`.

## Un bug de paso, y por que esta migracion lo destapa

`0004` guardaba el `hits` vacio como `'{}'::jsonb`. En JSON `{}` es un **objeto**
vacio, no un array, y `jsonb_array_length` de un objeto lanza "no se puede obtener
el largo de array de un no-array". Era latente porque, con un cubo por segundo y
una ventana de 60s, la poda nunca llegaba a vaciar un cubo: los hits tenian 59
segundos de margen. Esta version poda de verdad, asi que el cubo se vacia apenas
expira el ultimo intento, y el error aparece. Aqui va `'[]'`.

El `downgrade` restaura el cuerpo de `0004` **con su `'{}'`**, y no corregido: un
`downgrade` devuelve el estado anterior, no una version mejorada. Si tambien se
arreglara el `downgrade`, el estado restaurado ya no seria el que existia y el
rollback dejaria de ser reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_rate_limit_deslizante"
down_revision: str | None = "0012_businesses_insert_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RATE_LIMIT_HIT = "rate_limit_hit"
SEARCH_PATH = "pg_catalog, public"

# La version de `0004`, que se restaura en el `downgrade`. Se copia literal y no se
# reimplementa: el `downgrade` tiene que devolver el estado anterior, no uno
# aproximado.
# Estas dos cadenas son literales normales, **no** f-strings: `_cuerpo()` las
# interpola una sola vez. El `'{}'` del operador JSONB va con una sola pareja de
# llaves, y aun asi la funcion fallaba en la primera corrida con
# "sintaxis de entrada no valida para tipo json": las llaves estaban dobladas
# porque el cuerpo venia de `0004`, donde si hace falta doblarlas para no que
# Python se coma el `'{}'`. Aqui no hay f-string, y doblarlas deja el
# `'{{}}'` literal en el SQL.
_VERSION_0004 = """
        DECLARE
            v_hoy      text := to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
            v_cutoff   timestamptz := clock_timestamp() - make_interval(secs => p_window_seconds);
            v_id       uuid;
            v_hits     jsonb;
            v_count    integer;
            v_retry    integer;
        BEGIN
            INSERT INTO rate_limit_buckets (key, window_start, count, hits, scope)
            VALUES (p_key, date_trunc('second', clock_timestamp()), 0, jsonb_build_array(v_hoy), p_scope)
            ON CONFLICT (key, window_start) DO UPDATE
                SET hits = rate_limit_buckets.hits || jsonb_build_array(v_hoy)
            RETURNING id INTO v_id;

            UPDATE rate_limit_buckets b
               SET hits = COALESCE(
                       (SELECT jsonb_agg(e.value ORDER BY (e.value #>> '{}')::timestamptz)
                          FROM jsonb_array_elements(b.hits) AS e(value)
                         WHERE (e.value #>> '{}')::timestamptz > v_cutoff),
                       '{}'::jsonb
                   )
             WHERE b.id = v_id
            RETURNING b.hits INTO v_hits;

            v_count := COALESCE(jsonb_array_length(v_hits), 0);

            UPDATE rate_limit_buckets
               SET count = v_count
             WHERE id = v_id;

            v_retry := GREATEST(
                1,
                CEIL(EXTRACT(EPOCH FROM (
                    (SELECT MIN((e.value #>> '{}')::timestamptz)
                       FROM jsonb_array_elements(v_hits) AS e(value))
                    + make_interval(secs => p_window_seconds)
                    - clock_timestamp()
                )))::integer
            );

            RETURN QUERY SELECT (v_count <= p_limit), v_count, v_retry;
        END
"""

# `_cuerpo` es el unico f-string de este archivo. Las llaves del operador JSONB
# van simples por lo mismo que arriba.
_VERSION_0013 = """
        DECLARE
            v_ahora   timestamptz := clock_timestamp();
            v_cutoff  timestamptz := v_ahora - make_interval(secs => p_window_seconds);
            -- El cubo va alineado a la ventana. Con `p_window_seconds = 60` hay un
            -- cubo por minuto y por clave, y dos peticiones simultaneas caen en la
            -- misma fila y compiten por el indice unico.
            v_bucket  timestamptz := to_timestamp(
                          floor(extract(epoch FROM v_ahora) / p_window_seconds)
                          * p_window_seconds
                      );
            v_hoy     text := to_char(v_ahora AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
            v_id      uuid;
            v_total   integer;
            v_retry   integer;
        BEGIN
            IF p_window_seconds IS NULL OR p_window_seconds < 1 THEN
                RAISE EXCEPTION 'p_window_seconds tiene que ser >= 1, vino %',
                    p_window_seconds;
            END IF;

            -- 1. El intento entra en el cubo del tramo actual.
            INSERT INTO rate_limit_buckets (key, window_start, count, hits, scope)
            VALUES (p_key, v_bucket, 0, jsonb_build_array(v_hoy), p_scope)
            ON CONFLICT (key, window_start) DO UPDATE
                SET hits = rate_limit_buckets.hits || jsonb_build_array(v_hoy)
            RETURNING id INTO v_id;

            -- 2. Podar por timestamp, en TODOS los cubos de la clave y no solo en
            --    el actual: el tramo previo puede tener intentos vigentes. El
            --    `ORDER BY` deja `hits` ordenado, que es lo que hace la funcion
            --    determinista para la misma secuencia de intentos.
            --
            --    El vacio es `'[]'` y **no** `'{}'`, que es lo que escribia `0004`:
            --    en JSON, `{}` es un objeto vacio y `[]` un array vacio, asi que
            --    `'{}'::jsonb` dejaba `hits` con un tipo del que
            --    `jsonb_array_length` no puede sacar el largo--"
            --    no se puede obtener el largo de array de un no-array"-- en el paso 3.
            --    Era un bug latente: con cubos de 1s y ventana de 60s la poda nunca
            --    dejaba un cubo vacio, y ahora, que la poda hace su trabajo, salta
            --    en cuanto expira el ultimo intento del cubo.
            UPDATE rate_limit_buckets b
               SET hits = COALESCE(
                       (SELECT jsonb_agg(e.value ORDER BY (e.value #>> '{}')::timestamptz)
                          FROM jsonb_array_elements(b.hits) AS e(value)
                         WHERE (e.value #>> '{}')::timestamptz > v_cutoff),
                       '[]'::jsonb
                   )
             WHERE b.key = p_key;

            -- 3. `count` es derivado de `hits` y va por cubo. En una sentencia
            --    aparte porque repetir la agregacion dentro del `SET` es la version
            --    de una sola sentencia que se vuelve ilegible.
            --
            --    `b.hits` y no `hits`: la funcion declara `hits` como parametro de
            --    salida, y en plpgsql eso es una variable del ambito. Sin el alias
            --    la referencia es ambigua y la sentencia no compila--que es un
            --    error honesto, a diferencia de los otros dos que dio esta funcion
            --    cuando el SQL llego mal formado.
            UPDATE rate_limit_buckets b
               SET count = jsonb_array_length(b.hits)
             WHERE b.key = p_key;

            -- 4. El total de la clave. Esta es la linea que arregla el bug: antes
            --    contaba solo el cubo del segundo en curso.
            SELECT COALESCE(SUM(jsonb_array_length(b.hits)), 0)
              INTO v_total
              FROM rate_limit_buckets b
             WHERE b.key = p_key;

            -- 5. `retry_after`: segundos hasta que salga de la ventana el intento
            --    mas viejo. `GREATEST(1, ...)` porque un 0 en el header significa
            --    "reintenta ya" y produce un bucle de reintentos desde el cliente.
            SELECT GREATEST(1, COALESCE(CEIL(EXTRACT(EPOCH FROM (
                       MIN((e.value #>> '{}')::timestamptz)
                       + make_interval(secs => p_window_seconds)
                       - v_ahora
                   )))::integer, 1))
              INTO v_retry
              FROM rate_limit_buckets b,
                   LATERAL jsonb_array_elements(b.hits) AS e(value)
             WHERE b.key = p_key;

            -- 6. Un cubo se borra recien cuando **ninguno** de sus intentos sigue
            --    dentro de la ventana. Borrarlo por `window_start < v_cutoff` seria
            --    incorrecto con los cubos alineados: el tramo previo arranca antes
            --    del corte y todavia puede tener hits vigentes, y perderlos seria
            --    perder intentos que cuentan.
            DELETE FROM rate_limit_buckets b
             WHERE b.key = p_key
               AND NOT EXISTS (
                       SELECT 1 FROM jsonb_array_elements(b.hits) AS e(value)
                        WHERE (e.value #>> '{}')::timestamptz > v_cutoff
                   );

            RETURN QUERY SELECT (v_total <= p_limit), v_total, v_retry;
        END
"""


def _cuerpo(version: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION {RATE_LIMIT_HIT}(
            p_key text,
            p_limit integer,
            p_window_seconds integer,
            p_scope text DEFAULT NULL
        )
        RETURNS TABLE (allowed boolean, hits integer, retry_after integer)
        LANGUAGE plpgsql
        VOLATILE
        SET search_path = {SEARCH_PATH}
        AS $$
        {version}
        $$;
    """


def upgrade() -> None:
    op.execute(_cuerpo(_VERSION_0013))


def downgrade() -> None:
    op.execute(_cuerpo(_VERSION_0004))


__all__ = ["downgrade", "upgrade"]
