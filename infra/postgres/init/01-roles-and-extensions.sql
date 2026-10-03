-- =============================================================================
-- Tempus - Roles, privilegios y extensiones
-- =============================================================================
-- Se ejecuta como superusuario. Es idempotente: se puede correr más de una vez.
--
-- Hay DOS roles y la separación es deliberada:
--
--   tempus_owner  dueño del esquema. Es el único con DDL, y es con el que corren
--                 las migraciones de Alembic. NO lo usa la aplicación.
--   tempus_app    el único que usa la aplicación. NO tiene DDL, NO es superuser, y
--                 NO es dueño de ninguna tabla. Por eso las políticas de RLS se le
--                 aplican: sin esta separación, el dueño de las tablas se saltaría
--                 las políticas y las pruebas de aislamiento no probarían nada.
--
-- Las tablas de tenant se crean con `ALTER TABLE ... FORCE ROW LEVEL SECURITY`,
-- de modo que la garantía no depende de tener el ownership correcto: depende de
-- que la tabla lo tenga forzado.
--
-- Variables de entorno requeridas (sin default: si faltan, el script falla):
--   TEMPUS_OWNER_PASSWORD
--   TEMPUS_APP_PASSWORD
-- =============================================================================

\set ON_ERROR_STOP on

\getenv owner_pw TEMPUS_OWNER_PASSWORD
\getenv app_pw TEMPUS_APP_PASSWORD

\if :{?owner_pw}
\else
  \echo 'ERROR: falta la variable de entorno TEMPUS_OWNER_PASSWORD'
  \quit
\endif

\if :{?app_pw}
\else
  \echo 'ERROR: falta la variable de entorno TEMPUS_APP_PASSWORD'
  \quit
\endif


-- -----------------------------------------------------------------------------
-- Roles
-- -----------------------------------------------------------------------------
-- NOINHERIT: la app no hereda permisos de nadie. Los permisos que tiene son
-- exactamente los que se le otorgan abajo, y ninguno más.
--
-- Se usa `\gexec` y no un bloque DO por dos razones concretas: psql no interpola
-- variables dentro de cadenas delimitadas por dóblar, y CREATE DATABASE no puede
-- ejecutarse dentro de un bloque de código.
SELECT 'CREATE ROLE tempus_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '
       || quote_literal(:'owner_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner')
\gexec

SELECT 'CREATE ROLE tempus_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '
       || quote_literal(:'app_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app')
\gexec


-- -----------------------------------------------------------------------------
-- Bases
-- -----------------------------------------------------------------------------
-- tempus       desarrollo
-- tempus_test  tests de integración. Base separada a propósito: las pruebas
--              hacen TRUNCATE y crean datos, y no deben poder tocar desarrollo.
SELECT 'CREATE DATABASE tempus OWNER tempus_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tempus')
\gexec

SELECT 'CREATE DATABASE tempus_test OWNER tempus_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tempus_test')
\gexec


-- -----------------------------------------------------------------------------
-- Extensiones (por base)
-- -----------------------------------------------------------------------------
-- citext     case-insensitive para `businesses.slug` y `business_users.email`.
--            Una alternativa sería un CHECK con lower(); citext además indexa.
-- btree_gist EXIGIDO por la restricción anti-doble-reserva: la columna del
--            EXCLUDE es uuid y GiST no lo soporta solo. Ver ADR-0008.
--
-- pgcrypto NO se usa: `uuidv7()` es nativo desde PostgreSQL 18 (ADR-0003).
-- -----------------------------------------------------------------------------
\connect tempus
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS btree_gist;
GRANT USAGE ON SCHEMA public TO tempus_app;
GRANT ALL ON SCHEMA public TO tempus_owner;

\connect tempus_test
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS btree_gist;
GRANT USAGE ON SCHEMA public TO tempus_app;
GRANT ALL ON SCHEMA public TO tempus_owner;
