#!/bin/sh
# Entry point de produccion.
#
# Corre las migraciones antes de levantar el servidor y despues le cede el proceso.
#
# `alembic upgrade head` **ya es idempotente**: si el esquema esta al dia no aplica
# nada y sale con 0. Por eso esto se puede correr en cada arranque sin miedo--un pod
# que se reinicia no aplica la migracion dos veces-- y a la vez cierra el despliegue
# en el que la app arranca contra un esquema viejo, que es el modo de fallo mas caro
# y mas dificil de ver: la API responde 500 en vez de negarse a arrancar.
#
# Lo que **no** hace es dejar esto como opcion por default sin el rol de DDL. Ver
# abajo: sin `DATABASE_MIGRATION_URL` se saltea, y lo dice en voz alta.

set -eu

log() { printf '[entrypoint] %s\n' "$1"; }

# --------------------------------------------------------------------------
# 1. Migraciones
# --------------------------------------------------------------------------
# Las corre el rol de **migracion**, no el de la aplicacion. `DATABASE_URL` es el rol
# de la app y por diseno no puede DDL: meter migraciones ahi seria darle al proceso
# web el permiso de modificar el esquema.
if [ -n "${DATABASE_MIGRATION_URL:-}" ]; then
    log "aplicando migraciones (rol de DDL)"
    alembic upgrade head
    log "migraciones al dia"
else
    # Se salta en vez de fallar a proposito: hay despliegues--tests de integracion,
    # una imagen corriendo solo migrations-- donde el contenedor de la app no debe
    # llevar el rol de DDL. Callarse seria peor: el log tiene que decir que el
    # esquema **no** se verifico en este arranque.
    log "AVISO: sin DATABASE_MIGRATION_URL, no se corrieron migraciones."
    log "AVISO: el esquema se asume migrado por fuera. Si no es asi, la API va a fallar."
fi

# --------------------------------------------------------------------------
# 2. Proxy de confianza
# --------------------------------------------------------------------------
# Solo se agrega el flag si el despliegue declara que proxy es de confianza. El valor
# vacio significa "nadie", que es el default correcto: prefiero que el rate limiting
# use la IP del peer antes que confiar en una cabecera que cualquiera pueda mandar.
#
# Ver la seccion "Rate limiting y proxies" del README: sin esto, y detras de un
# proxy, todos los usuarios comparten un cubo y el login de 5/min se vuelve 5/min
# para el producto entero.
if [ -n "${FORWARDED_ALLOW_IPS:-}" ]; then
    set -- "$@" --proxy-headers --forwarded-allow-ips="$FORWARDED_ALLOW_IPS"
    log "confiando en el proxy declarado: $FORWARDED_ALLOW_IPS"
else
    log "sin proxy de confianza declarado: la IP sera la del peer"
fi

log "arrancando: $*"
exec "$@"