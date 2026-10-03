-- =============================================================================
-- Tempus - Rol dedicado para login pre-tenant
-- =============================================================================
-- Se ejecuta como superusuario en el init; idempotente.
-- Debe correr DESPUÉS de 01-roles-and-extensions.sql (orden alfabético).
--
-- Este rol existe SOLO para que auth_business_user_for_login pueda leer
-- business_users sin GUC de tenant. Tiene BYPASSRLS pero NO login,
-- NO herencia, y SOLO SELECT en las 6 columnas que la función consulta.
-- =============================================================================

\set ON_ERROR_STOP on

SELECT 'CREATE ROLE tempus_login_definer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tempus_login_definer')
\gexec