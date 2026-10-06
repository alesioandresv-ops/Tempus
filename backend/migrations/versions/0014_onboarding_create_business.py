"""alta de tenants: la funcion que si puede crear un negocio

Revision ID: 0014_onboarding_create_business
Revises: 0013_rate_limit_deslizante
Create Date: 2026-10-03

## Que resuelve

El alta self-service necesita `INSERT` sobre `businesses`, y `tempus_app` no lo
tiene: `0011` le dio `SELECT` mas `UPDATE` por columna, `0012` dejo
`businesses_insert` **exclusiva de `tempus_owner`** y revoco `INSERT` a mano por
si alguien lo habia concedido. O sea que el endpoint de alta no puede escribir el
negocio con la sesion que usa el resto de la aplicacion, y tampoco con una sesion
de tenant: todavia no hay tenant al que ponerle el GUC.

Esta migracion abre **una sola puerta, del tamano exacto del besoin**: una funcion
`SECURITY DEFINER` que inserta en `businesses` y devuelve el id.

## Por que una funcion y no "correr el alta con el rol de DDL"

Hasta aca el comentario de `app.modules.businesses.service` decia que el alta
"corre con la sesion de migraciones". Esa frase describe bien lo que la base
permitia y **esta mal como diseno**, por dos motivos:

1. `DATABASE_MIGRATION_URL` es el rol de DDL. Si el proceso de la API lo necesita
   en runtime, el servidor web corre con una credencial que puede `CREATE TABLE`,
   `ALTER` y `DROP` cualquier cosa, y un `text()` mal armado deja de ser un bug de
   datos y pasa a ser uno de esquema. El §25 pide que el servidor web no pueda
   escribir el esquema; "la base me deja" no es un criterio de seguridad.
2. El rol de DDL es dueno de las tablas, asi que con `FORCE ROW LEVEL SECURITY` la
   separacion entre tenants pasa a depender de que ese rol este bien configurado
   en vez de depender de las politicas. Eso es exactamente lo que `0002` hizo al
   activar `FORCE` en todas las tablas de tenant.

Con la funcion, la garantia queda donde esta el resto: en la politica.

## Por que `SECURITY DEFINER` sin `BYPASSRLS`

La politica `businesses_insert` de `0012` es `FOR INSERT TO tempus_owner`, y
`FORCE ROW LEVEL SECURITY` alcanza tambien al dueno de la tabla. Como la funcion
corre *como* su dueno, `current_user` es `tempus_owner` y la politica aplica: el
INSERT se permite. No hace falta `BYPASSRLS`.

Eso importa por lo que `0012` ya senalo: `BYPASSRLS` es todo o nada, y el rol
dueno tambien siembra las tablas con `business_id` en los tests de tenancy. Con
`BYPASSRLS` esos tests pasarian **probando de menos**, porque ninguna politica se
evaluaria. Con esta funcion los tests siguen evaluando politicas.

Ademas el permiso queda con la forma correcta: "crear un tenant", no "ver todos los
tenants". Un `BYPASSRLS` sobre el rol de la app habria abierto tambien el `SELECT`
de las demas tablas; esta funcion solo inserta.

## Por que la funcion tambien consulta `slug_reservations`

`slug_reservations` esta en `FORBIDDEN_FOR_APP_ROLE` (`0002`): el rol de la app no
puede ni **leerla**, porque es la lista de palabras que el sistema necesita quitar
antes de que exista ningun negocio --marca, rutas del panel, dominios--.

Si el chequeo de "este slug esta reservado" viviera en Python, no se podria
escribir de forma correcta: el rol que tendria que consultarla no tiene permiso.
Por eso el chequeo va **dentro** de la funcion, que si lo tiene. El resultado es
que la palabra reservada no es una convencion del codigo de aplicacion sino una
garantia de la base, que se cumple igual si el alta entra por el API, por un
script o por `psql`.

La unicidad entre negocios la sigue arbitrando el indice `UNIQUE` de
`businesses.slug`; la funcion no lo reimplementa ni lo consulta antes, porque una
comprobacion previa tiene ventana y el indice no.

## Los dos errores, distinguibles

- **slug reservado**: la funcion levanta `23514` (`check_violation`), que ninguna
  otra restriccion de esta tabla produce. El servicio lo traduce a 409 con un
  mensaje que invite a elegir otro.
- **slug duplicado**: sube el `23505` (`unique_violation`) del indice, sin
  interceptar. El servicio lo traduce a 409 tambien, pero con otro mensaje.

Que sean distinguibles no es un detalle: el formulario del onboarding muestra el
error al lado del campo del slug, y "ya esta en uso" y "esta reservado" llevan a
acciones distintas.

## `search_path` fijo en la funcion

Va en el cuerpo de la funcion (`SET search_path = public`), no como atributo
suelto. Una `SECURITY DEFINER` sin `search_path` resuelve nombres con el del
llamante, y `pg_temp` va primero para cualquier rol: eso es el R-03. Es el mismo
criterio que aplica `verificar_produccion.py` sobre `rate_limit_hit`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

#: revision identifiers, used by Alembic.
revision: str = "0014_onboarding_create_business"
down_revision: str | None = "0013_rate_limit_deslizante"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "tempus_app"
_MIGRATION_ROLE = "tempus_owner"

#: Nombre de la funcion, sin firma.
_FUNCION = "tempus_create_business"

#: La firma completa. `GRANT` y `REVOKE` nombran la funcion **con** la firma, asi
#: que tiene que estar literal: si se agrega un parametro y no se actualizan las
#: dos sentencias, el `REVOKE` deja de borrar la version anterior y su permiso
#: sobrevive al deploy.
_FIRMA = f"{_FUNCION}(citext, text, text, text)"

#: La segunda funcion. Mismo motivo de permiso: `slug_reservations` no es legible
#: para `tempus_app`, asi que el chequeo en tiempo real de la URL del negocio
#: tambien tiene que ir del otro lado. Sin esto el formulario del onboarding
#: responderia "disponible" para `admin` y solo al enviar el formulario apareceria el
#: 409--justo el caso que el chequeo en vivo existe para evitar.
_FUNCION_DISPONIBLE = "tempus_slug_disponible"
_FIRMA_DISPONIBLE = f"{_FUNCION_DISPONIBLE}(citext)"

#: `23514` = `check_violation`. Ninguna otra restriccion de `businesses` lo
#: produce, asi que "el slug esta reservado" no se confunde con otra cosa.
_SQLSTATE_SLUG_RESERVADO = "23514"

_CREAR_FUNCION = f"""
CREATE FUNCTION {_FUNCION}(p_slug citext, p_name text, p_timezone text, p_phone_e164 text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $cuerpo$
DECLARE
    v_id uuid;
BEGIN
    IF EXISTS (SELECT 1 FROM slug_reservations WHERE slug = p_slug) THEN
        RAISE EXCEPTION 'El nombre de URL % esta reservado', p_slug
            USING ERRCODE = '{_SQLSTATE_SLUG_RESERVADO}';
    END IF;

    INSERT INTO businesses (slug, name, timezone, phone_e164)
    VALUES (p_slug, p_name, p_timezone, p_phone_e164)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$cuerpo$
"""

_CREAR_DISPONIBLE = f"""
CREATE FUNCTION {_FUNCION_DISPONIBLE}(p_slug citext)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $cuerpo$
    SELECT NOT EXISTS (SELECT 1 FROM slug_reservations WHERE slug = p_slug)
       AND NOT EXISTS (SELECT 1 FROM businesses WHERE slug = p_slug);
$cuerpo$
"""


def upgrade() -> None:
    op.execute(_CREAR_FUNCION)
    op.execute(_CREAR_DISPONIBLE)

    # El dueno explicito. La migracion corre con `DATABASE_MIGRATION_URL`, asi que
    # las funciones ya nacen de `tempus_owner`; dejarlo escrito convierte "quien es
    # el dueno" de una consecuencia del URL de la migracion en algo que se lee en el
    # archivo. Si alguien corre el alta con otro rol, el permiso de abajo no alcanza
    # y el endpoint falla con `permission denied`--que es el sintoma correcto-- en
    # vez de crear el negocio con el dueno equivocado.
    op.execute(f"ALTER FUNCTION {_FIRMA} OWNER TO {_MIGRATION_ROLE}")
    op.execute(f"ALTER FUNCTION {_FIRMA_DISPONIBLE} OWNER TO {_MIGRATION_ROLE}")

    # `PUBLIC` primero y sin excepciones: `EXECUTE` sobre funciones es publico por
    # defecto en PostgreSQL, asi que sin este `REVOKE` cualquier rol futuro de la
    # base--incluido uno de tests-- podria crear negocios.
    #
    # `tempus_slug_disponible` podria quedarse en `PUBLIC` sin riesgo--solo lee--pero
    # comparte el `REVOKE` porque deja de ser una decision: hoy es inofensiva y manana
    # podria no serlo, y el costo de cubrirla es una linea.
    op.execute(f"REVOKE ALL ON FUNCTION {_FIRMA} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {_FIRMA_DISPONIBLE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_FIRMA} TO {_APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_FIRMA_DISPONIBLE} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {_FIRMA} FROM {_APP_ROLE}")
    op.execute(f"REVOKE ALL ON FUNCTION {_FIRMA_DISPONIBLE} FROM {_APP_ROLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_FIRMA}")
    op.execute(f"DROP FUNCTION IF EXISTS {_FIRMA_DISPONIBLE}")


__all__ = ["downgrade", "upgrade"]
