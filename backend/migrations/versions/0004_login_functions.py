"""Las dos funciones que hacen posible el login, y el rate limit atomico

Revision ID: 0004_login_functions
Revises: 0003_auth_roles
Create Date: 2026-09-29

**El problema que resuelve.** `business_users` tiene RLS con la politica habitual,
`business_id = current_setting('app.current_business_id')`. El login necesita, en este
orden: (1) encontrar la fila del usuario **por email**, (2) leer su `business_id`, (3)
poner ese `business_id` en el GUC para que la RLS empiece a filtrar. Los pasos (1) y
(2) son el mismo `SELECT`, y no se puede hacer con la RLS prendida: sin GUC,
`NULLIF(current_setting(...), '')` es NULL, la comparacion da NULL, y la consulta
devuelve **cero filas**. Es circular por construccion.

Las tres salidas que hay, y por que se descartan dos:

- `BYPASSRLS` para el rol de la app: apaga el aislamiento entero para poder encender
  una parte. Es la unica que resuelve el problema y la unica que no se puede.
- `FORCE`/`ALTER TABLE ... DISABLE ROW LEVEL SECURITY` alrededor del SELECT: la ventana
  entre el SELECT y el `SET LOCAL` es una condicion de carrera en la que cualquier
  consulta concurrente ve todo. Y si el proceso muere ahi, la sesion del pool queda
  con la RLS apagada.
- Una funcion `SECURITY DEFINER`: corre como dueno, saltea la RLS de esa tabla, y el
  rol de la app solo puede **llamarla**, no leer la tabla. Es la que se usa.

**Por que `platform_users` necesita lo mismo y `business_users` no lo va a notar.**
`tempus_app` no tiene ningun privilegio sobre `platform_users` -- es la separacion
del §7, y el test `test_el_rol_de_app_no_tiene_privilegios_sobre_platform_users` la
verifica. Sin la funcion, el login de plataforma seria imposible. Esto no es un
agujero que se abre: es un agujero que ya existia y que la funcion mantiene cerrado
en una sola direccion.

**Lo que hace que la funcion no sea un agujero.** Cuatro cosas, y las cuatro importan:

1. `SET search_path = pg_catalog, public`. Sin esto, cualquiera con `CREATE` en el
   esquema podria crear una funcion o una vista con el mismo nombre y ser ejecutada
   en vez de esta. El rol de la app no tiene `CREATE` en `public` (verificado por
   `test_el_rol_de_app_no_puede_crear_tablas`), asi que hoy no se puede -- pero
   `search_path` explicito no cuesta nada y no depende de que ese permiso siga
   asi manana.
2. `REVOKE ALL ... FROM PUBLIC`. Por defecto PostgreSQL le da `EXECUTE` sobre toda
   funcion nueva a `PUBLIC`. Sin el revoke, **cualquier** rol conectado a la base --
   includedo uno que se cree para otra cosa -- puede llamar la funcion y enumerar
   usuarios con sus hashes de contrasena.
3. Devuelve columnas explicitas, no `SELECT *`. La lista de columnas de la funcion es
   su superficie: agregar una columna a la tabla no la agrega a la funcion.
4. `GRANT EXECUTE` solo a `tempus_app`.

**La ventana que queda, y es aceptable a proposito.** Un login de plataforma y un
login de negocio son superficies distintas, asi que hay dos funciones y no una con un
parametro de tipo: con una sola, un `type` equivocado en el codigo buscaria en la tabla
equivocada, y el error seria "no existe" en vez de "estas buscando en el sitio
equivocado". Ademas `platform_users` no tiene RLS justamente porque su aislamiento es
de permisos, y para el login eso no cambia nada: la funcion devuelve una fila y nada
mas.

**El rate limit.** `rate_limit_buckets` no lleva RLS justamente porque se consulta por
IP y por email, que existen antes del tenant. La ventana deslizante necesita leer la
fila, podar los timestamps viejos, agregar el nuevo y contar, **todo sin que otra
peticion se cuele en el medio**. Hacerlo en Python son cuatro round-trips y una
condicion de carrera; hacerlo en una funcion de SQL es una sentencia. Se devuelve si
se permitio, cuantos intentos hay en la ventana, y cuanto falta para que se libere.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from alembic.util import CommandError

revision: str = "0004_login_functions"
down_revision: str | None = "0003_auth_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tempus_app"

BUSINESS_LOGIN = "auth_business_user_for_login"
PLATFORM_LOGIN = "auth_platform_user_for_login"
RATE_LIMIT_HIT = "rate_limit_hit"

#: `search_path` de las funciones. `pg_catalog` primero para que un objeto del
#: catalogo no pueda ser sombreado, `public` porque las tablas y el tipo `citext`
#: viven ahi. `pg_temp` NO va: un `CREATE TEMP TABLE` del rol de la app seria
#: justo lo que esta linea existe para impedir.
SEARCH_PATH = "pg_catalog, public"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {BUSINESS_LOGIN}(p_email citext)
        RETURNS TABLE (
            id uuid,
            business_id uuid,
            password_hash text,
            role business_user_role,
            status membership_status,
            full_name text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT u.id, u.business_id, u.password_hash, u.role, u.status, u.full_name
            FROM business_users u
            WHERE u.email = p_email
        $$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {PLATFORM_LOGIN}(p_email citext)
        RETURNS TABLE (
            id uuid,
            password_hash text,
            role platform_role,
            is_active boolean,
            full_name text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = {SEARCH_PATH}
        AS $$
            SELECT u.id, u.password_hash, u.role, u.is_active, u.full_name
            FROM platform_users u
            WHERE u.email = p_email
        $$
        """
    )

    # La ventana deslizante, atomica. Lo que hace el rate limit bien es que contar un
    # intento y decidir si se permite sea **indivisible**: si se pudiera intercalar una
    # lectura entre el "sumar" y el "comparar", N peticiones simultaneas pasarian todas
    # cuando el limite es 1.
    #
    # Por eso todo vive en una funcion: una llamada = una transaccion. Ademas el
    # `INSERT ... ON CONFLICT` asegura que la fila exista, y desde ese momento la
    # fila la tenemos bloqueada por nuestra propia escritura, asi que las tres
    # sentencias de abajo se ejecutan sin que nadie mas las vea a medio hacer.
    #
    # `window_start` queda fijo en el momento en que se creo la fila y no se mueve:
    # es parte de la clave unica `(key, window_start)`. La ventana deslizante la dan
    # los `hits`, que son un timestamp por intento. Asi hay una fila por (clave,
    # primer intento) y no una fila por intento, que es lo que evita que la tabla
    # crezca con cada request.
    #
    # El `#>> '{}'` de abajo es el operador de JSONB que devuelve un elemento de la
    # raiz. Va en un f-string, asi que sus llaves van dobladas: sin eso Python come el
    # `'{}'` y el error que sale es "f-string: valid expression required before '}'",
    # que no dice nada de SQL.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {RATE_LIMIT_HIT}(
            p_key text,
            p_limit integer,
            p_window_seconds integer,
            p_scope text DEFAULT NULL
        )
        RETURNS TABLE (allowed boolean, hits integer, retry_after integer)
        LANGUAGE plpgsql
        VOLATILE
        -- `INVOKER` y no `SECURITY DEFINER`, a diferencia de las dos de arriba. Es
        -- deliberado y es la diferencia de menor privilegio correcta: esta funcion
        -- escribe en `rate_limit_buckets`, que no lleva RLS y sobre la que el rol de
        -- la app ya tiene INSERT/UPDATE/SELECT. No hay nada que saltar, asi que
        -- correr como dueno no compra nada y amplificaria el radio de un bug futuro.
        -- El `SET search_path` si va igual.
        SET search_path = {SEARCH_PATH}
        AS $$
        DECLARE
            -- `clock_timestamp()` y no `now()`, y no es un detalle. En PostgreSQL
            -- `now()` es el **inicio de la transaccion**: es constante durante toda
            -- la transaccion. Para un rate limit eso esta mal, porque el instante
            -- que importa es cuando llego el request, no cuando empezo a procesarse.
            -- Con `now()`, un login que tarda 5 segundos en llegar a la base se
            -- cuenta como si hubiera llegado 5 segundos antes, y un atacante que
            -- abre 5 conexiones lentas puede meter los 5 intentos en el mismo
            -- instante logico.
            --
            -- `clock_timestamp()` es el reloj real y avanza dentro de la
            -- transaccion. Es lo unico de esta funcion que se swims contra el
            -- patron de "una transaccion, un instante" -- y tambien es lo que hace
            -- que este test pueda comprobar la ventana deslizante de verdad.
            v_hoy      text := to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
            v_cutoff   timestamptz := clock_timestamp() - make_interval(secs => p_window_seconds);
            v_id       uuid;
            v_hits     jsonb;
            v_count    integer;
            v_retry    integer;
        BEGIN
            -- 1. Asegurar la fila y registrar este intento.
            INSERT INTO rate_limit_buckets (key, window_start, count, hits, scope)
            VALUES (p_key, date_trunc('second', clock_timestamp()), 0, jsonb_build_array(v_hoy), p_scope)
            ON CONFLICT (key, window_start) DO UPDATE
                SET hits = rate_limit_buckets.hits || jsonb_build_array(v_hoy)
            RETURNING id INTO v_id;

            -- 2. Podar lo que ya salio de la ventana. El `ORDER BY` por timestamp es
            --    lo que hace que `hits` quede ordenado, y el orden importa para que
            --    `jsonb_agg` sea determinista: el array es la auditoria de la
            --    ventana, y dos corridas con los mismos intentos tienen que dar el
            --    mismo array.
            UPDATE rate_limit_buckets b
               SET hits = COALESCE(
                       (SELECT jsonb_agg(e.value ORDER BY (e.value #>> '{{}}')::timestamptz)
                          FROM jsonb_array_elements(b.hits) AS e(value)
                         WHERE (e.value #>> '{{}}')::timestamptz > v_cutoff),
                       '{{}}'::jsonb
                   )
             WHERE b.id = v_id
            RETURNING b.hits INTO v_hits;

            v_count := COALESCE(jsonb_array_length(v_hits), 0);

            -- 3. `count` es derivado de `hits`, no al reves. Se escribe aparte para
            --    no repetir la misma agregacion dos veces en un `SET`, que es donde
            --    la version de una sola sentencia seonia ilegible.
            UPDATE rate_limit_buckets
               SET count = v_count
             WHERE id = v_id;

            -- Segundos hasta que salga el intento mas viejo de la ventana, que es el
            -- que va a liberar un lugar. `GREATEST(1, ...)` porque un `retry_after`
            -- de 0 en un header `Retry-After` significa "reintentá ya" y produce un
            -- bucle de reintentos desde el cliente.
            v_retry := GREATEST(
                1,
                CEIL(EXTRACT(EPOCH FROM (
                    (SELECT MIN((e.value #>> '{{}}')::timestamptz)
                       FROM jsonb_array_elements(v_hits) AS e(value))
                    + make_interval(secs => p_window_seconds)
                    - clock_timestamp()
                )))::integer
            );

            RETURN QUERY SELECT (v_count <= p_limit), v_count, v_retry;
        END
        $$
        """
    )

    # El revoke va **despues** del create en todos los casos, y el grant solo al rol
    # de la app. El orden importa: revocar antes de que existan las funciones no
    # revoca nada.
    for nombre, firma in (
        (BUSINESS_LOGIN, "(citext)"),
        (PLATFORM_LOGIN, "(citext)"),
        (RATE_LIMIT_HIT, "(text, integer, integer, text)"),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {nombre}{firma} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {BUSINESS_LOGIN}(citext) TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {PLATFORM_LOGIN}(citext) TO {APP_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {RATE_LIMIT_HIT}(text, integer, integer, text) TO {APP_ROLE}"
    )


def downgrade() -> None:
    """Dropea las funciones. Reversible, y el orden es el inverso.

    Como las funciones de `0004` no son de las que `asyncpg` no puede deshacer --
    `DROP FUNCTION` es un DDL normal -- esta migracion **si** tiene vuelta, y por eso
    el piso de reversibilidad sigue siendo 0003 y no sube a 0004.
    """
    op.execute(f"DROP FUNCTION IF EXISTS {RATE_LIMIT_HIT}(text, integer, integer, text)")
    op.execute(f"DROP FUNCTION IF EXISTS {PLATFORM_LOGIN}(citext)")
    op.execute(f"DROP FUNCTION IF EXISTS {BUSINESS_LOGIN}(citext)")


def _verificar_privilegios() -> None:  # pragma: no cover - util para depurar a mano
    """Imprime los privilegios effective de las tres funciones. Diagnostico manual."""
    for nombre, firma in (
        (BUSINESS_LOGIN, "(citext)"),
        (PLATFORM_LOGIN, "(citext)"),
        (RATE_LIMIT_HIT, "(text, integer, integer, text)"),
    ):
        rows = (
            op.get_bind()
            .execute(
                __import__("sqlalchemy").text(
                    """
                SELECT has_function_privilege('PUBLIC', :n || :f, 'EXECUTE') AS publico,
                       has_function_privilege(:rol, :n || :f, 'EXECUTE') AS app
                """
                ),
                {"n": nombre, "f": firma, "rol": APP_ROLE},
            )
            .one()
        )
        if rows.publico:
            raise CommandError(f"{nombre} sigue siendo ejecutable por PUBLIC")
