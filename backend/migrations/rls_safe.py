"""Primitivas idempotentes y single-role safe para RLS y ACL en migraciones.

En Render la base tiene **un solo rol** (dueño == migrador == app). Eso rompe dos
supuestos de las migraciones, que asumen el esquema multi-rol que arma
`infra/postgres/init/*.sql` (dueño `tempus_owner`, app `tempus_app`, etc.):

1. **`REVOKE ... FROM "<rol>"` cuando `<rol>` es el propio `CURRENT_USER` le quita
   privilegios al dueño.** PostgreSQL permite revocar los privilegios propios, y
   el dueño deja de tenerlos hasta que se los vuelvan a otorgar. Después,
   cualquier `INSERT` / `REFERENCES` del dueño falla con "permission denied for
   table X": el mensaje parece de RLS, pero es de ACL.

2. **Apagar RLS no alcanza para crear una FK.** Los chequeos de integridad
   referencial **bypassean RLS** siempre; lo que falta es el privilegio
   `REFERENCES` sobre la tabla referenciada. Por eso `claim_and_open` re-otorga
   `GRANT ALL ... TO CURRENT_USER` además de apagar RLS.

Las funciones encapsulan las dos cosas, siempre con `EXCEPTION WHEN others`, de
modo que la misma migración corre igual en multi-rol (dev / CI / tests) y en
single-rol (Render) sin ramas por entorno. Nunca tocan políticas ni columnas:
sólo ownership, RLS y ACL.
"""

from __future__ import annotations

from alembic import op


def _literal(valor: str) -> str:
    """Comilla un valor de texto para interpolarlo en SQL."""
    return "'" + valor.replace("'", "''") + "'"


def grant_if_exists(sql_grant: str, role_name: str) -> None:
    """Ejecuta `sql_grant` sólo si `role_name` existe.

    Es la guarda estándar para `GRANT` a un rol que en single-rol no existe
    (`tempus_app`, `tempus_owner`, `tempus_login_definer`). En multi-rol el rol
    existe y la sentencia se ejecuta como siempre.
    """
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_literal(role_name)}) THEN
                EXECUTE $g${sql_grant}$g$;
            END IF;
        END
        $do$
        """
    )


def revoke_if_exists(sql_revoke: str, role_name: str) -> None:
    """Ejecuta `sql_revoke` sólo si el rol existe y **no es el usuario actual**.

    La segunda condición es la que evita que, en single-rol, el dueño se revoque
    a sí mismo y después falle con "permission denied for table X" al escribir.
    En multi-rol (`role_name != CURRENT_USER`) se comporta como un `REVOKE`
    normal.
    """
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_literal(role_name)})
               AND {_literal(role_name)} <> CURRENT_USER THEN
                EXECUTE $g${sql_revoke}$g$;
            END IF;
        END
        $do$
        """
    )


def claim_and_open(tabla: str) -> None:
    """Deja `tabla` escribible por el usuario actual, pase lo que pase.

    Reclama el ownership para `CURRENT_USER`, apaga la RLS (incluido `FORCE`) y
    re-otorga todos los privilegios al usuario actual. Cualquiera de los pasos
    que no se pueda hacer (por ejemplo, en multi-rol el `ALTER OWNER` es no-op o
    falla si el rol nuevo no tiene `CREATE`) se ignora con `EXCEPTION`.
    """
    op.execute(
        f"""
        DO $do$
        BEGIN
            BEGIN
                EXECUTE format('ALTER TABLE %I OWNER TO %I', {_literal(tabla)}, CURRENT_USER);
            EXCEPTION WHEN others THEN NULL;
            END;
            BEGIN
                EXECUTE 'ALTER TABLE "{tabla}" DISABLE ROW LEVEL SECURITY';
            EXCEPTION WHEN others THEN NULL;
            END;
            BEGIN
                EXECUTE 'ALTER TABLE "{tabla}" NO FORCE ROW LEVEL SECURITY';
            EXCEPTION WHEN others THEN NULL;
            END;
            BEGIN
                EXECUTE 'GRANT ALL ON TABLE "{tabla}" TO CURRENT_USER';
            EXCEPTION WHEN others THEN NULL;
            END;
        END $do$
        """
    )


def restore(tabla: str) -> None:
    """Reenciende RLS. Solo lo hace si la tabla realmente tiene alguna política.

    Esto evita volver a encender RLS en tablas que nunca debieron tenerla (ej.
    `slug_reservations`), donde quedar con RLS activada sin políticas es letal
    incluso para el dueño bajo `FORCE ROW LEVEL SECURITY`.
    """
    op.execute(
        f"""
        DO $do$
        DECLARE v_tiene_politica boolean;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_policies p ON p.schemaname = n.nspname AND p.tablename = c.relname
                WHERE n.nspname = 'public' AND c.relname = {_literal(tabla)}
            ) INTO v_tiene_politica;

            IF v_tiene_politica THEN
                BEGIN
                    EXECUTE 'ALTER TABLE "{tabla}" ENABLE ROW LEVEL SECURITY';
                EXCEPTION WHEN others THEN NULL;
                END;
                BEGIN
                    EXECUTE 'ALTER TABLE "{tabla}" FORCE ROW LEVEL SECURITY';
                EXCEPTION WHEN others THEN NULL;
                END;
            END IF;
        END $do$
        """
    )


__all__ = ["claim_and_open", "grant_if_exists", "restore", "revoke_if_exists"]
