"""RLS, trigger de updated_at y permisos del rol de aplicacion

Revision ID: 0002_rls_triggers_grants
Revises: 0001_initial_schema
Create Date: 2026-09-28

Va separada de la migracion del schema a proposito. La 0001 es el DDL que salio
del autogenere y no se toca; esta es la capa de **seguridad** del modelo, que se
revisa con otros ojos. Juntarlas hacia que una revision de schema tocara tambien
politicas de RLS, que es el diff que nadie quiere ver de noche.

Las listas de tablas de esta migracion son **copias congeladas**, no imports de
`app.models`. Una migracion tiene que seguir aplicando igual dentro de dos anos,
cuando el modelo haya cambiado. El acoplamiento se paga al reves: el test
`tests/tenancy/test_rls_coverage.py` compara estas listas contra
`app.models.TENANT_TABLES` y falla si se desincronizan, de modo que el drift se
detecta sin que la migracion dependa del codigo.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_rls_triggers_grants"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Rol con DDL. Dueño de las tablas.
MIGRATION_ROLE = "tempus_owner"

#: Rol que usa la aplicacion. NO es dueño y esta sujeto a RLS.
APP_ROLE = "tempus_app"

#: Tablas con RLS. Copia congelada de `app.models.TENANT_TABLES`.
#:
#: `whatsapp_templates` entra aunque su `business_id` sea nullable: las plantillas
#: de plataforma tienen NULL y las del negocio su id, y ambas tienen que quedar
#: detrás de la politica.
TENANT_TABLES: tuple[str, ...] = (
    "audit_log",
    "blocks",
    "booking_events",
    "bookings",
    "business_exception_windows",
    "business_exceptions",
    "business_hours",
    "business_users",
    "customers",
    "holidays",
    "idempotency_keys",
    "media",
    "notification_requests",
    "professional_schedules",
    "professional_services",
    "professionals",
    "services",
    "time_off",
    "whatsapp_connections",
    "whatsapp_templates",
)

#: Tablas que la aplicacion no debe poder escribir nunca.
#:
#: `platform_users` separa la administracion de la plataforma del mundo del cliente
#: (§5.1). `businesses` y `slug_reservations` las crea el alta desde la consola de
#: plataforma, no desde la API: si la API pudiera crear negocios, cualquier
#: endpoint de registro publico seria una via para dar de alta un negocio sin pasar
#: por el alta.
FORBIDDEN_FOR_APP_ROLE: tuple[str, ...] = (
    "platform_users",
    "businesses",
    "slug_reservations",
)

#: Tablas con `created_at` / `updated_at`. Se derivan de los mixins.
_TABLES_WITH_TIMESTAMPS: tuple[str, ...] = (
    "audit_log",
    "blocks",
    "booking_events",
    "bookings",
    "business_exception_windows",
    "business_exceptions",
    "business_hours",
    "business_users",
    "businesses",
    "customers",
    "holidays",
    "idempotency_keys",
    "jobs",
    "media",
    "notification_requests",
    "platform_users",
    "professional_schedules",
    "professional_services",
    "professionals",
    "rate_limit_buckets",
    "refresh_tokens",
    "services",
    "slug_reservations",
    "time_off",
    "webhook_events",
    "whatsapp_connections",
    "whatsapp_templates",
)

_TENANT_GUC = "app.current_business_id"


def _enable_rls() -> None:
    """Activa y fuerza la RLS en las tablas de tenant, con su politica."""
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        # `FORCE` no es opcional. Sin el, el dueño de la tabla la bypasea, y el rol
        # de migraciones ES el dueño: un `SET app.current_business_id` olvidado
        # dejaria ver todo. Con `FORCE` el aislamiento deja de depender de que el
        # rol este bien configurado y pasa a depender solo de las politicas.
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        # El `true` del segundo argumento de `current_setting` es lo que hace que
        # devuelva NULL en vez de fallar cuando la variable no esta puesta. Con
        # `false` fallaria, y una consulta sin contexto de tenant daria error en
        # lugar de devolver cero filas. Cero filas es la respuesta correcta para
        # "no se de que tenant es esta peticion".
        #
        # El `NULLIF(..., '')` cubre el otro caso, que es el que realmente aparece
        # en produccion: el GUC existe pero **vacio**. Pasa cuando el tenant sale
        # de una variable de entorno sin valor, o de un header ausente. Sin el
        # `NULLIF`, `''::uuid` es `invalid input syntax for type uuid: ""` y cada
        # consulta de la peticion revienta con 500 en vez de devolver cero filas.
        # Con `NULLIF`, vacio y ausente se comportan igual: deny all.
        #
        # La comparacion queda en NULL cuando no hay tenant, y NULL en `USING` se
        # trata como falso (la fila no se ve) y en `WITH CHECK` como rechazo.
        op.execute(
            f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
            f"USING (business_id = NULLIF(current_setting('{_TENANT_GUC}', true), '')::uuid) "
            f"WITH CHECK (business_id = NULLIF(current_setting('{_TENANT_GUC}', true), '')::uuid)"
        )


def _disable_rls() -> None:
    """Saca las politicas y apaga la RLS."""
    for table in TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def _create_updated_at_trigger() -> None:
    """Trigger que mantiene `updated_at`.

    Lo pone la base y no la aplicacion. Un UPDATE ejecutado por un worker con SQL
    crudo pasaria por alto el `onupdate` de SQLAlchemy y dejaria la fila con una
    fecha vieja, que es justo el dato con el que se investiga un incidente.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in _TABLES_WITH_TIMESTAMPS:
        op.execute(
            f'CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def _drop_updated_at_trigger() -> None:
    for table in _TABLES_WITH_TIMESTAMPS:
        op.execute(f'DROP TRIGGER IF EXISTS trg_{table}_set_updated_at ON "{table}"')
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")


def _restrict_update_to_columns(table: str, writable: tuple[str, ...]) -> None:
    """Deja al rol de la app actualizar **solo** las columnas de `writable`.

    Revoca el `UPDATE` de tabla y lo vuelve a conceder columna por columna. El
    revoke de tabla es la parte que no se puede omitir: los privilegios de columna
    se acumulan con los de tabla, no los reemplazan, asi que un
    `REVOKE UPDATE (col)` sobre una tabla con `UPDATE` concedido no restringe nada.
    """
    op.execute(f'REVOKE UPDATE ON TABLE "{table}" FROM "{APP_ROLE}"')
    columns = ", ".join(f'"{column}"' for column in writable)
    op.execute(f'GRANT UPDATE ({columns}) ON TABLE "{table}" TO "{APP_ROLE}"')


def _grant_app_role() -> None:
    """Permisos del rol de la aplicacion.

    El principio: la aplicacion puede leer y escribir **datos de tenant**, y nada
    mas. No hace DDL, no bypasea RLS, y no toca las tres tablas de administracion.

    Los `DEFAULT PRIVILEGES` no son un extra: sin ellos, la proxima migracion crea
    una tabla que el rol de la app no puede ni leer, y el error aparece en
    produccion como un 403 en un endpoint recien agregado. Es la falla que hace
    que un deploy se revierta.
    """
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE}"')
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"'
    )
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"')

    op.execute(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{MIGRATION_ROLE}" IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{APP_ROLE}"'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{MIGRATION_ROLE}" IN SCHEMA public '
        f'GRANT USAGE, SELECT ON SEQUENCES TO "{APP_ROLE}"'
    )

    # `REVOKE CREATE` explicito, noporque sobre en una base de PG15+ el schema
    # `public` ya no le da CREATE a PUBLIC, sino por lo contrario: justamente por
    # depender de ese default el invariante "la app no hace DDL" no lo garantizaba
    # nadie. Se opto por esta base durante las pruebas manuales de RLS, quedo con
    # `UC` en el ACL del schema, y el unico sintoma era que el test de "la app no
    # tiene DDL" pasaba en `tempus_test` y deberia haber fallado en `tempus`.
    #
    # Un invariante de seguridad que depende de como se creo la base no es un
    # invariante: es una coincidencia que se rompe en el primer `CREATE DATABASE`
    # con otra plantilla. Por eso se revoca de forma explicita.
    #
    # Se revoca de PUBLIC tambien porque `has_schema_privilege` tiene en cuenta la
    # pertenencia a PUBLIC: revocar solo del rol no alcanzaria si PUBLIC tuviera
    # CREATE, que es exactamente el caso de un PostgreSQL anterior a 15.
    op.execute(f'REVOKE CREATE ON SCHEMA public FROM "{APP_ROLE}"')
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    # Se revoca despues de conceder, en este orden, y no al reves: revocar sobre
    # un privilege que no existe da error y aborta la migracion.
    for table in FORBIDDEN_FOR_APP_ROLE:
        op.execute(f'REVOKE ALL ON TABLE "{table}" FROM "{APP_ROLE}"')

    # `businesses` necesita SELECT porque el sitio publico resuelve el negocio por
    # `slug` **antes** de tener tenant. Es el unico momento legitimo en que se lee
    # sin `SET LOCAL`, y por eso queda en lectura y nada mas. Se concede al rol de
    # la app y no a `PUBLIC`: en este monolitipo el sitio publico lo sirve la misma
    # app, asi que abrirlo a `PUBLIC` solo le daria lectura a cualquier rol futuro
    # (incluidos los de migraciones o de tests) sin que nadie lo pidiera.
    op.execute(f'GRANT SELECT ON TABLE "businesses" TO "{APP_ROLE}"')

    # El rol de la app no puede cambiar su propia membresia: si pudiera, un
    # endpoint de perfil con un bug de mass assignment le da rol admin a cualquiera.
    #
    # Ojo con como se hace. `REVOKE UPDATE (role, status, password_hash) ...` NO
    # funciona: los privilegios de columna son acumulativos, asi que mientras exista
    # el `UPDATE` a nivel de tabla del `GRANT` de arriba, revocar tres columnas no
    # cambia nada y `has_column_privilege` sigue respondiendo `true`. Hay que
    # **sacarle** el UPDATE de tabla y concederlo columna por columna. El orden es
    # obligatorio: primero el revoke de tabla, despues los grants de columna.
    _restrict_update_to_columns(
        table="business_users",
        writable=(
            "email",
            "full_name",
            "last_login_at",
            "invited_at",
        ),
    )

    # La funcion de timestamps queda en manos del rol de migraciones para que nadie
    # pueda reemplazarla desde la API. No es `SECURITY DEFINER` y no lo necesita:
    # la funcion solo reescribe `NEW.updated_at` de la fila que se esta
    # modificando, no lee ni escribe nada mas, asi que no cambia el nivel de
    # privilegio de quien la dispara. Marcarla como definer seria ruido.
    op.execute(f'ALTER FUNCTION set_updated_at() OWNER TO "{MIGRATION_ROLE}"')


def _revoke_app_role() -> None:
    # Primero se deshace la restriccion por columnas. Si se invirtiera el orden,
    # el `GRANT UPDATE` de tabla volveria a abrir `role` y `password_hash` en el
    # camino de salida, que es justo lo que esta migracion vino a cerrar.
    op.execute(f'REVOKE UPDATE ON TABLE "business_users" FROM "{APP_ROLE}"')
    for table in FORBIDDEN_FOR_APP_ROLE:
        op.execute(f'REVOKE ALL ON TABLE "{table}" FROM "{APP_ROLE}"')
    op.execute(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{MIGRATION_ROLE}" IN SCHEMA public '
        f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{APP_ROLE}"'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{MIGRATION_ROLE}" IN SCHEMA public '
        f'REVOKE USAGE, SELECT ON SEQUENCES FROM "{APP_ROLE}"'
    )
    # El `REVOKE CREATE ON SCHEMA public` del upgrade **no** se revierte. Un
    # downgrade devuelve el esquema a un estado anterior, no reintroduce un
    # privilegio: si se devolviera, un rollback de emergencia abriria DDL a la
    # aplicacion en la base de destino, que es justo cuando nadie esta mirando.


def upgrade() -> None:
    _enable_rls()
    _create_updated_at_trigger()
    _grant_app_role()


def downgrade() -> None:
    _revoke_app_role()
    _drop_updated_at_trigger()
    _disable_rls()
