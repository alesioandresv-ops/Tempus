-- =============================================================================
-- Tempus - Roles, privilegios y extensiones
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
SELECT 'CREATE ROLE tempus_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '
       || quote_literal(:'owner_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_owner')
\gexec

SELECT 'CREATE ROLE tempus_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '
       || quote_literal(:'app_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_app')
\gexec

-- El POSTGRES_USER nace superuser. No puede degradarse a si mismo.
-- Creamos un superuser temporal para degradar.
SELECT 'CREATE ROLE temp_bootstrap SUPERUSER LOGIN PASSWORD ''temp_bootstrap_pw'''
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'temp_bootstrap')
\gexec

SET ROLE temp_bootstrap;
ALTER ROLE tempus_owner NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE tempus_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
RESET ROLE;
DROP ROLE IF EXISTS temp_bootstrap;

SELECT format('ALTER ROLE tempus_owner PASSWORD %L', :'owner_pw') \gexec
SELECT format('ALTER ROLE tempus_app PASSWORD %L', :'app_pw') \gexec

-- -----------------------------------------------------------------------------
-- Bases
-- -----------------------------------------------------------------------------
SELECT 'CREATE DATABASE tempus OWNER tempus_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tempus')
\gexec

SELECT 'CREATE DATABASE tempus_test OWNER tempus_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tempus_test')
\gexec

-- -----------------------------------------------------------------------------
-- Extensiones
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