"""Primitivas de rol para las migraciones que usan el definer BYPASSRLS.

Las migraciones que crean una funcion a nombre de `tempus_login_definer` (o le
transfieren la propiedad) necesitan las mismas cuatro piezas, y las necesitan **dentro
de la misma transaccion** para que no quede nada. Un helper con las cuatro juntas es
la forma de que la garantia se lea en un lugar en vez de estar implicita en cinco
archivos.

Se usa `op.execute` y no un contexto: las migraciones son copias congeladas y este
modulo es parte de la migracion, no codigo de aplicacion. El test
`test_las_migraciones_no_dependen_del_codigo_de_aplicacion` no lo marca justamente
porque vive en `migrations/`, no en `app/`.
"""

from __future__ import annotations

from alembic import op

#: Rol con DDL. Dueeno de las tablas. **No** es miembro del definer.
OWNER_ROLE = "tempus_owner"

#: Rol BYPASSRLS dueno de las funciones de pre-tenant y del escaneo de la outbox.
DEFINER_ROLE = "tempus_login_definer"

#: Rol puente: sabe hacer `SET ROLE` al definer y sabe otorgar esa membresia.
MIGRATOR_ROLE = "tempus_migrator"


def como_definer() -> None:
    """Convierte la sesion actual en `tempus_login_definer`.

    Va por el puente porque `tempus_owner` no es miembro del definer--por diseno, y
    `tests/tenancy/test_login_definer.py` lo verifica-- y lo que puede es hacer
    `SET ROLE tempus_migrator`, que si lo es.

    El `SET ROLE` es a sesion, no `SET LOCAL`: dentro de una transaccion de Alembic
    da igual, y fuera de ella seria un `SET ROLE` que sobrevive al `COMMIT`, que es
    exactamente la clase de estado residual que no se quiere. Quien lo use debe
    terminar con `reset_role()`.

    **Hay que devolver `CREATE` antes de esto**, y quitarlo despues: crear una funcion
    requiere `CREATE` en el esquema, y el definer--siendo BYPASSRLS y sin necesidad de
    crear objetos--no lo tiene de forma permanente. Ver `definer_con_create()`.
    """
    op.execute(f'SET ROLE "{MIGRATOR_ROLE}"')
    op.execute(f'SET ROLE "{DEFINER_ROLE}"')


def reset_role() -> None:
    """Vuelve a `tempus_owner`, deshaciendo los `SET ROLE` que se hubieran hecho.

    `RESET ROLE` devuelve la sesion al **usuario de sesion** en un solo paso, por
    muchos `SET ROLE` que se hayan encadenado: comprobado en PostgreSQL 18, tras
    `SET ROLE tempus_migrator; SET ROLE tempus_login_definer` basta un `RESET ROLE`
    para volver a ser `tempus_owner`, y el segundo no hace nada.

    Se emiten dos igual por lo unico que evita: que cada llamador tenga que contar
    cuantos `SET ROLE` encadeno. Un `RESET` de mas es un no-op inofensivo; el que
    falte, en cambio, deja la sesion corriendo como el rol BYPASSRLS--y todo lo que
    se ejecutase despues heredaria esa vista sin RLS.
    """
    op.execute("RESET ROLE")
    op.execute("RESET ROLE")


def definer_con_create() -> None:
    """Da `CREATE` en `public` al definer, de forma transitoria.

    El dueno de una funcion necesita `CREATE` en el esquema: PostgreSQL borra y
    recrea el objeto cuando cambia el dueno, y sin `CREATE` el `ALTER ... OWNER TO`
    falla con `permiso denegado al esquema public`--un mensaje que no dice nada de
    membresias y por eso cuesta mucho.

    **Es transitorio a proposito.** `CREATE` en `public` para un rol BYPASSRLS
    significa poder crear cualquier objeto--y una tabla sin RLS con el nombre de otra
    es un agujero silencioso-- asi que solo existe durante la migracion que lo pide.
    Quien lo usa debe llamar `definer_sin_create()` despues.
    """
    op.execute(f'GRANT CREATE ON SCHEMA public TO "{DEFINER_ROLE}"')


def definer_sin_create() -> None:
    """Le saca `CREATE` al definer, dejando `USAGE`.

    Va antes de `reset_role()` en la practica, porque `REVOKE` lo hace el rol con DDL,
    no el definer. Y es idempotente: `REVOKE` de algo que no esta concedido no falla.
    """
    op.execute(f'REVOKE CREATE ON SCHEMA public FROM "{DEFINER_ROLE}"')


def membresia_temporal_al_definer() -> None:
    """Le da a `tempus_owner` la membresia en el definer, hasta nuevo aviso.

    Existe para `ALTER FUNCTION ... OWNER TO`, que exige poder hacer `SET ROLE` al rol
    destino. `tempus_owner` no es miembro--y no debe serlo fuera de la transaccion--,
    asi que el puente, que tiene `ADMIN OPTION`, se la otorga por el momento.

    Hay que llamarla junto a `revoca_membresia_al_definer()` en la misma transaccion.
    """
    op.execute(f'SET ROLE "{MIGRATOR_ROLE}"')
    op.execute(f'GRANT "{DEFINER_ROLE}" TO "{OWNER_ROLE}"')
    op.execute("RESET ROLE")


def revoca_membresia_al_definer() -> None:
    """Deshace `membresia_temporal_al_definer()`.

    Va por el puente porque es el unico con `ADMIN OPTION` sobre el definer: un
    `REVOKE` de la propia membresia--`REVOKE x FROM tempus_owner`-- requiere el mismo
    privilegio que el `GRANT`, asi que no se puede hacer "desde adentro" de la
    membresia que se quiere quitar.
    """
    op.execute(f'SET ROLE "{MIGRATOR_ROLE}"')
    op.execute(f'REVOKE "{DEFINER_ROLE}" FROM "{OWNER_ROLE}"')
    op.execute("RESET ROLE")
