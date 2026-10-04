\set ON_ERROR_STOP on

-- =============================================================================
-- El rol BYPASSRLS del login pre-tenant y el rol puente de las migraciones
-- =============================================================================
-- Se ejecuta como superusuario en el init; idempotente.
-- Debe correr DESPUÉS de 01-roles-and-extensions.sql (orden alfabético).
--
-- Este rol existe SOLO para que auth_business_user_for_login pueda leer
-- business_users sin GUC de tenant. Tiene BYPASSRLS pero NO login,
-- NO herencia, y SOLO SELECT en las columnas que la función consulta.
-- =============================================================================

SELECT 'CREATE ROLE tempus_login_definer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer')
\gexec

-- -----------------------------------------------------------------------------
-- El rol puente: por qué existe y por qué NO se le deja ser dueño de nada
-- -----------------------------------------------------------------------------
-- Cuatro migraciones (0005, 0008, 0009 y 0010) necesitan que una
-- función termine siendo de tempus_login_definer, porque son funciones que corren
-- antes de que exista un tenant y por eso no pueden ver filas con la RLS puesta.
--
-- PostgreSQL pone dos condiciones a `ALTER FUNCTION ... OWNER TO <definer>`:
--
--   1. Poder hacer `SET ROLE <definer>`, o sea SER MIEMBRO del rol destino.
--   2. Que el dueño nuevo tenga CREATE sobre el esquema (la función se borra y se
--      vuelve a crear bajo el dueño nuevo).
--
-- tempus_owner es NOSUPERUSER, así que por sí solo no puede cumplir ninguna de las
-- dos. La salida NO es darle la membresia permanente --eso era lo que había, y por
-- eso `test_tempus_owner_no_es_miembro_del_definer` fallaba-- sino este tercer rol:
--
--     tempus_owner ──miembro──▶ tempus_migrator ──miembro+ADMIN──▶ tempus_login_definer
--
-- Las migraciones emiten SET ROLE tempus_migrator; GRANT tempus_login_definer TO
-- tempus_owner; ... ALTER FUNCTION ... OWNER TO ...; y después la revocan, todo
-- dentro de la misma transacción (Alembic usa DDL transaccional). Fuera de la
-- transacción no queda membresía de tempus_owner en el definer: por eso el test
-- pasa sin relajarse.
--
-- Por qué esto no abre un agujero:
--
--   - tempus_app no participa de ninguna de las dos relaciones. El rol que atiende
--     peticiones HTTP no puede hacer SET ROLE a ninguno de los dos.
--   - tempus_migrator es NOLOGIN. Nadie se conecta con él; solo existe como eslabón
--     de un SET ROLE.
--   - NOBYPASSRLS: el puente no lee datos. Solo administra la membresía.
--   - La relación tempus_owner → tempus_migrator NO lleva ADMIN OPTION, así que
--     desde el rol de DDL no se puede ampliar la membresía del definer a terceros.
--   - NOINHERIT en tempus_owner es la garantía de fondo: sin INHERIT, el rol NO
--     recibe los privilegios del definer a lo largo de la sesión. Solo los obtiene
--     si escribe SET ROLE explícitamente. La diferencia es entre "el rol de DDL
--     puede hacer esto si lo pide" y "el rol de DDL tiene esto".
--
-- Y una nota sobre CREATEROLE, que es lo primero que uno piensa como solución y no
-- sirve: en PostgreSQL 16+ CREATEROLE permite gestionar roles, pero otorgar una
-- membresía sigue exigiendo ADMIN OPTION sobre el rol destino, y ese privilegio no
-- se puede autoconceder. Comprobado en PostgreSQL 18:
--
--     GRANT tempus_login_definer TO <rol con CREATEROLE>;
--     ERROR:  se ha denegado el permiso para otorgar el rol "tempus_login_definer"
--     DETAIL: Solo los roles con la opcion ADMIN en el rol "tempus_login_definer"
--             pueden otorgar membresía en ese rol.
--
-- ADMIN OPTION sí es indispensable, y hay que concederlo explícitamente: si el rol
-- ya es miembro, un GRANT repetido NO pone admin_option en true, y el puente se
-- queda sin poder otorgar la membresía temporal -- que es exactamente lo que
-- rompería las migraciones siguientes. Por eso el par REVOKE/GRANT.
--
-- Estas relaciones son de nivel de CLUSTER (no de base), así que viven acá y no en
-- una migración: además de que es donde ya viven los otros roles, una migración no
-- podría crearlas, porque tempus_owner no tiene permiso para crear roles ni para
-- conceder ADMIN OPTION.
-- -----------------------------------------------------------------------------

SELECT 'CREATE ROLE tempus_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_migrator')
\gexec

REVOKE tempus_login_definer FROM tempus_migrator;
GRANT tempus_login_definer TO tempus_migrator WITH ADMIN OPTION;

GRANT tempus_migrator TO tempus_owner;

-- El definer necesita USAGE sobre el esquema para existir dentro de él, pero NO
-- CREATE de forma permanente: CREATE en public para un rol BYPASSRLS permitiría
-- crear cualquier objeto, y una tabla sin RLS con el nombre de otra es un agujero
-- que no aparece en ningún test. Cada migración que lo necesita se lo concede y se
-- lo devuelve dentro de su propia transacción.
--
-- El USAGE permanente y el CREATE transitorio son las dos mitades de la misma
-- garantía: con USAGE el definer existe dentro del esquema y puede ejecutar lo que
-- otros le EJECUTEN; con CREATE podría declarar objetos propios, que es lo que no
-- se le da.
\connect tempus
GRANT USAGE ON SCHEMA public TO tempus_login_definer;

\connect tempus_test
GRANT USAGE ON SCHEMA public TO tempus_login_definer;