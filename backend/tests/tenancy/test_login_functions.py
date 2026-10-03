"""Las funciones de 0004 no pueden ser un agujero, y eso se verifica en la base.

El docstring de la migracion hace cuatro promesas sobre `auth_business_user_for_login`
y `auth_platform_user_for_login`: que son `SECURITY DEFINER`, que `PUBLIC` no puede
llamarlas, que `search_path` esta fijo, y que devuelven columnas explicitas. Un
docstring de seguridad que no se verifica es un docstring que se vuelve falso sin que
nadie se entere, y estas son las funciones por las que pasa el hash de contrasena de
todo el sistema.

Por que se prueba contra la base y no leyendo el archivo: las cuatro propiedades son
del **objeto en el catalogo**, no del texto. Una migracion puede seguir diciendo
`SECURITY DEFINER` y tener la funcion creada sin el, si alguien la dropea y la
recrea mal. Lo que importa es `pg_proc`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

pytestmark = pytest.mark.tenancy

#: Las dos funciones que cruzan la RLS. `rate_limit_hit` va aparte porque no la
#: cruza: escribe en una tabla que no tiene RLS.
FUNCIONES_LOGIN = ("auth_business_user_for_login", "auth_platform_user_for_login")
RATE_LIMIT = "rate_limit_hit"


async def _proacl(connection: AsyncConnection, nombre: str) -> str | None:
    return (
        await connection.execute(
            text(
                "SELECT p.proacl::text FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = :n"
            ),
            {"n": nombre},
        )
    ).scalar_one()


class TestLasFuncionesDeLoginNoSonUnAgujero:
    @pytest.mark.parametrize("nombre", FUNCIONES_LOGIN)
    async def test_public_no_la_puede_ejecutar(
        self, connection: AsyncConnection, nombre: str
    ) -> None:
        """`REVOKE ... FROM PUBLIC`, verificado en `proacl`.

        Por defecto PostgreSQL le da `EXECUTE` a `PUBLIC` sobre **toda** funcion
        nueva. Sin el revoke, cualquier rol conectado a la base -- uno creado para
        cualquier otra cosa -- puede llamar
        la funcion y quedarse con el email, el hash y el rol de todos los usuarios.
        Eso es una lectura completa de credenciales a una linea de distancia.

        La comprobacion mira `proacl` y no `has_function_privilege('PUBLIC', ...)`
        porque `PUBLIC` es un pseudo-rol: la funcion de conveniencia responde
        "role PUBLIC does not exist", que es un error que no dice nada de permisos.
        En `proacl` un permiso para `PUBLIC` es un `=X/...` sin nombre de rol
        adelante.
        """
        acl = await _proacl(connection, nombre)
        entradas = acl.strip("{}").split(",") if acl else []
        para_publico = [e for e in entradas if e.startswith("=X/") or e == "=X"]
        assert not para_publico, (
            f"{nombre} sigue siendo ejecutable por PUBLIC: {para_publico}. "
            "Falta el REVOKE ALL ... FROM PUBLIC de la migracion."
        )

    @pytest.mark.parametrize("nombre", FUNCIONES_LOGIN)
    async def test_una_privilegiada_para_la_app_no_para_nadie_mas(
        self, connection: AsyncConnection, nombre: str
    ) -> None:
        """Solo `tempus_owner`, `tempus_app` y (para auth_business_user_for_login)
        `tempus_login_definer` tienen `EXECUTE`.

        El `GRANT` puntual es lo que hace que la superficie sea controlada.
        `tempus_login_definer` es owner de auth_business_user_for_login y por eso
        tiene EXECUTE implicitamente.
        """
        acl = await _proacl(connection, nombre)
        holders = {
            e.split("=")[0] for e in (acl.strip("{}").split(",") if acl else []) if e.split("=")[0]
        }
        permitidos = {"tempus_owner", "tempus_app"}
        if nombre == "auth_business_user_for_login":
            permitidos.add("tempus_login_definer")
        assert holders <= permitidos, (
            f"{nombre} tiene EXECUTE para roles que no deberían: {holders - permitidos}"
        )
        assert "tempus_app" in holders, f"{nombre} no la puede ejecutar el rol de la app: {acl}"

    @pytest.mark.parametrize("nombre", FUNCIONES_LOGIN)
    async def test_es_security_definer_con_search_path_fijo(
        self, connection: AsyncConnection, nombre: str
    ) -> None:
        """`SECURITY DEFINER` y `SET search_path`, y el `pg_temp` que no debe estar.

        Sin `SECURITY DEFINER` la funcion corre como `tempus_app` y devuelve cero
        filas, que es el bug original: sin tenant no hay login.

        Sin `search_path` fijo, cualquiera con `CREATE` en el esquema podria crear
        un objeto con el mismo nombre y ser lo que se ejecuta. Hoy `tempus_app` no
        tiene `CREATE` en `public` -- hay otro test que lo verifica -- asi que esto
        no es explotable, y por eso no es urgente. Sigue puesto porque la defensa no
        deberia depender de que otro permiso siga asi manana.

        Y `pg_temp` no puede estar en el `search_path`: un `CREATE TEMP TABLE` es
        exactamente el ataque que la linea previene.
        """
        secdef, config = (
            await connection.execute(
                text(
                    "SELECT p.prosecdef, COALESCE(array_to_string(p.proconfig, ','), '') "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.proname = :n"
                ),
                {"n": nombre},
            )
        ).one()
        assert secdef is True, f"{nombre} no es SECURITY DEFINER, asi que no cruza la RLS"
        assert "search_path" in config, f"{nombre} no fija search_path: {config or '(vacio)'}"
        assert "pg_temp" not in config, f"{nombre} tiene pg_temp en el search_path: {config}"
        # `pg_catalog` primero: un objeto del catalogo no debe ser sombreable.
        assert config.split("search_path=")[1].strip().startswith("pg_catalog"), (
            f"{nombre} no pone pg_catalog primero en el search_path: {config}"
        )

    async def test_la_funcion_de_negocio_devuelve_la_fila_sin_guc(
        self, connection: AsyncConnection, business_a: uuid.UUID
    ) -> None:
        """**El** test que faltaba: la funcion tiene que devolver algo.

        Todo lo demas de esta clase mira el catalogo -- que sea `SECURITY DEFINER`, que
        `PUBLIC` no pueda llamarla, que fije `search_path`, que devuelva columnas
        explicitas. Ninguno de esos tests dice si la funcion *funciona*. Una funcion
        perfectamente construida, correctamente revocada y con las columnas juste,
        puede devolver cero filas siempre y todos ellos pasan.

        **Este test ahora pasa** porque se resolvio el defecto: se creo un rol dedicado
        `tempus_login_definer` con `BYPASSRLS` y se le dio ownership de la funcion,
        junto con `SELECT` por columna en `business_users`. Ahora la funcion `SECURITY
        DEFINER` corre como ese rol y saltea la RLS correctamente.

        **El INSERT necesita el GUC y la llamada no: esa asimetria es el punto.** La
        sesion del test es `tempus_app`, que obeyece la RLS como todos, asi que
        sembrar la fila--que si es una escritura de un tenant-- exige el contexto de
        tenant. Lo que se prueba es que despues de vaciarlo, la funcion de login
        todavia devuelve la fila. Si el GUC no se limpiara, el test pasaria aunque la
        funcion no hiciera nada: la propia sesion filtraria la fila por el GUC que
        tiene puesto y el resultado seria indistinguible del correcto.
        """
        # Primero con tenant: sembrar una fila es escribir dentro de un tenant.
        await connection.execute(
            text("SELECT set_config('app.current_business_id', :tenant, true)"),
            {"tenant": str(business_a)},
        )
        await connection.execute(
            text(
                "INSERT INTO business_users "
                "(id, business_id, email, password_hash, full_name, role, status) "
                "VALUES (:id, :b, 'sonda@ejemplo.test', 'hash', 'S', 'admin', 'active')"
            ),
            {"id": uuid.uuid4(), "b": business_a},
        )
        # El GUC se vacia despues, a proposito: es la condicion real del login, que
        # todavia no sabe de que tenant viene.
        await connection.execute(text("SELECT set_config('app.current_business_id', '', true)"))

        filas = (
            await connection.execute(
                text("SELECT id FROM auth_business_user_for_login('sonda@ejemplo.test')")
            )
        ).all()
        assert len(filas) == 1, (
            f"la funcion devolvio {len(filas)} filas sin GUC (esperaba 1): "
            "el rol dedicado con BYPASSRLS permite que la funcion SECURITY DEFINER "
            "cruce la RLS correctamente"
        )

    async def test_devuelven_columnas_explicitas_y_no_todas(
        self, connection: AsyncConnection
    ) -> None:
        """La lista de columnas de la funcion es su superficie.

        Una funcion `SELECT *` no devuelve columnas, devuelve la fila: agregar una
        columna a la tabla la agrega a la funcion sin que nadie lo decida. Con la
        lista escrita, agregar `last_login_ip` a `business_users` no la filtra al rol
        de la app hasta que alguien la escriba en la funcion.
        """
        # `proargnames` de una funcion con `RETURNS TABLE` no existe: los nombres de
        # salida estan en `pg_get_function_result`, que es el contrato real.
        filas = (
            await connection.execute(
                text(
                    "SELECT p.proname, pg_get_function_result(p.oid) "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.proname = ANY(:nombres)"
                ),
                {"nombres": list(FUNCIONES_LOGIN)},
            )
        ).all()
        contrato: dict[str, str] = {str(nombre): str(firma) for nombre, firma in filas}

        esperado_negocio = {
            "id",
            "business_id",
            "password_hash",
            "role",
            "status",
            "full_name",
        }
        esperado_plataforma = {
            "id",
            "password_hash",
            "role",
            "is_active",
            "full_name",
        }
        for nombre, esperado in (
            ("auth_business_user_for_login", esperado_negocio),
            ("auth_platform_user_for_login", esperado_plataforma),
        ):
            columnas = {
                parte.split()[0]
                for parte in contrato[nombre]
                .removeprefix("TABLE(")
                .removesuffix(")")
                .replace("\n", " ")
                .split(",")
            }
            assert columnas == esperado, (
                f"{nombre} devuelve {columnas} y el docstring promete {esperado}. "
                "Una columna de mas filtra algo que el rol de la app no deberia ver; "
                "una de menos rompe el login de una forma que no dice cual."
            )

    async def test_el_hash_de_contrasena_es_lo_unico_sensible_que_sale(
        self, connection: AsyncConnection
    ) -> None:
        """`platform_users` no devuelve `business_id`, y no hay forma de que lo devuelva.

        Es la mitad de la separacion del §7: un operador de plataforma administra el
        servicio, no los negocios de los demas. Que la funcion de plataforma no
        tenga `business_id` es lo que hace que un `owner` de la plataforma no pueda
        derivar un tenant aunque quiera.
        """
        contrato: str = (
            await connection.execute(
                text(
                    "SELECT pg_get_function_result(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.proname = 'auth_platform_user_for_login'"
                )
            )
        ).scalar_one()
        assert "business_id" not in contrato, (
            f"auth_platform_user_for_login devuelve business_id: {contrato}"
        )


class TestLaFuncionDeLoginRealmenteCruzaLaRls:
    async def test_sin_la_funcion_no_se_ve_nada(self, connection: AsyncConnection) -> None:
        """La RLS efectivamente esta prendida sobre `business_users`.

        Si esto devuelve filas, la politica se rompio y el `SELECT` del login tiene
        un plan B que no deberia tener.
        """
        filas: int = (
            await connection.execute(text("SELECT count(*) FROM business_users"))
        ).scalar_one()
        assert filas == 0, (
            f"SELECT directo sobre business_users sin GUC devolvio {filas} filas. "
            "La RLS no esta filtrando y la politica se rompio."
        )

    async def test_para_un_email_inexistente_no_devuelve_filas(
        self, connection: AsyncConnection
    ) -> None:
        """Para un email que no existe, la funcion devuelve cero filas.

        Este test estaba llamado `test_la_funcion_devuelve_la_fila_sin_guc` y el nombre
        mentia: lo que verifica es lo contrario. Se renombro porque un nombre que
        promete "devuelve la fila" y afirma "0 filas" es exactamente lo que dejo pasar
        el defecto de `FORCE ROW LEVEL SECURITY` -- durante meses, el archivo tenia un
        test que *parecia* cubrir el caso y no lo cubria.

        El caso del email inexistente importa por si mismo: si la funcion devolviera
        una fila "vacia", el login tendria que distinguir entre "no existe" y "existe con
        esta contrasena", que es enumeracion de usuarios. Cero filas para un email que
        no existe es la mitad del contrato; la otra mitad -- que exista una fila de
        verdad cuando hay un usuario -- la cubre
        `test_la_funcion_de_negocio_devuelve_la_fila_sin_guc` de mas arriba.
        """
        filas: int = (
            await connection.execute(
                text("SELECT count(*) FROM auth_business_user_for_login('nadie@ejemplo.invalid')")
            )
        ).scalar_one()
        assert filas == 0, "la funcion devolvio filas para un email inexistente"

    async def test_rota_la_rls_despues_de_usarla(self, connection: AsyncConnection) -> None:
        """Cruzar la RLS no la deja apagada.

        Es la propiedad que hace que esto sea seguro de usar **dentro** de una
        transaccion con otras consultas. Una funcion que apagara la RLS y no la
        prendiera dejaria la sesion del pool con el aislamiento caido para la
        siguiente peticion, y ninguna consulta posterior lo detectaria.
        """
        await connection.execute(
            text("SELECT count(*) FROM auth_business_user_for_login('nadie@ejemplo.invalid')")
        )
        filas: int = (
            await connection.execute(text("SELECT count(*) FROM business_users"))
        ).scalar_one()
        assert filas == 0, "usar la funcion dejo la RLS sin efecto en la sesion"


class TestElRateLimit:
    async def test_corta_en_el_limite(self, connection: AsyncConnection) -> None:
        """`p_limit` intentos pasan y el siguiente no.

        Se prueba con limite 3 y ventana larga para que la unica variable sea el
        conteo: si la ventana se cumpliera durante el test, el numero de `allowed`
        seria impredecible y el test no distinguiria "funciona" de "a veces corta".
        """
        for intento in range(1, 4):
            permitido: bool = (
                await connection.execute(
                    text("SELECT allowed FROM rate_limit_hit('t_limite', 3, 600, 'auth')")
                )
            ).scalar_one()
            assert permitido is True, f"el intento {intento} de 3 fue rechazado"
        permitido = (
            await connection.execute(
                text("SELECT allowed FROM rate_limit_hit('t_limite', 3, 600, 'auth')")
            )
        ).scalar_one()
        assert permitido is False, "el intento 4 de 3 fue permitido"

    async def test_la_ventana_deslizante_deja_pasar_al_que_cumplio(
        self, connection: AsyncConnection
    ) -> None:
        """No es una ventana fija: el que se fue vuelve a poder.

        Con una ventana fija, 5 intentos al segundo 59 y 5 mas al segundo 61 son
        10 intentos en dos segundos y el limite de 5 nunca frena nada. Con una ventana
        deslizante, el segundo grupo solo cuenta si el primero sigue dentro de la
        ventana.

        El `sleep` es real y hace falta. `rate_limit_hit` usa `clock_timestamp()` y
        no `now()` justamente para esto: `now()` es el inicio de la transaccion y es
        constante, asi que con `now()` la ventana no avanzaria nunca dentro de este
        test -- y tampoco avanzaria entre el principio y el final de un request lento
        en produccion. Un test que pasaria solo con `now()` esta probando la
        semantica equivocada.
        """
        # Ventana de 1 segundo: el tiempo real del test es despreciable contra eso.
        for _ in range(2):
            await connection.execute(
                text("SELECT allowed FROM rate_limit_hit('t_ventana', 1, 1, 'auth')")
            )
        tercero: bool = (
            await connection.execute(
                text("SELECT allowed FROM rate_limit_hit('t_ventana', 1, 1, 'auth')")
            )
        ).scalar_one()
        assert tercero is False, "tres intentos en el mismo segundo pasaron con limite 1"

        # Los hits se guardan como timestamps, no como un contador, asi que esperando
        # a que el mas viejo salga de la ventana el limite vuelve a estar libre.
        await asyncio.sleep(1.2)
        primero: bool = (
            await connection.execute(
                text("SELECT allowed FROM rate_limit_hit('t_ventana', 1, 1, 'auth')")
            )
        ).scalar_one()
        assert primero is True, "el limite no se liberó al salir el intento más viejo de la ventana"

    async def test_la_ventana_usa_el_reloj_y_no_el_inicio_de_la_transaccion(
        self, connection: AsyncConnection
    ) -> None:
        """El reloj avanza dentro de la transaccion.

        Es la diferencia entre `clock_timestamp()` y `now()`, y importa por dos
        motivos que este test separa: sin la asercion de arriba, cambiar a `now()`
        haria que la ventana deslizante no se liberara nunca; y con `now()`, un
        login que tarda 5 segundos en llegar a la base se contaria como si hubiera
        llegado al empezar a procesarse.
        """
        ahora: dt.datetime = (
            await connection.execute(text("SELECT clock_timestamp()"))
        ).scalar_one()
        await asyncio.sleep(0.3)
        despues: dt.datetime = (
            await connection.execute(text("SELECT clock_timestamp()"))
        ).scalar_one()
        assert (despues - ahora).total_seconds() >= 0.25, (
            "clock_timestamp() no avanzo dentro de la transaccion: la ventana "
            "deslizante no se va a liberar nunca"
        )

    async def test_retry_after_nunca_cero(self, connection: AsyncConnection) -> None:
        """`retry_after >= 1` siempre.

        Un `Retry-After: 0` en la respuesta significa "reintentá ya", y un cliente
        bien escrito lo obedece en bucle: pega 5 requests por segundo contra un
        endpoint que acaba de decir "esperá".
        """
        valores: Sequence[int] = (
            (
                await connection.execute(
                    text("SELECT retry_after FROM rate_limit_hit('t_retry', 1, 1, 'auth')")
                )
            )
            .scalars()
            .all()
        )
        assert valores, "la funcion no devolvio nada"
        assert all(v >= 1 for v in valores), f"retry_after con 0 o negativo: {list(valores)}"
        assert all(v <= 60 for v in valores), (
            f"retry_after mayor que la ventana de 1s: {list(valores)}. El header "
            "Retry-After no puede pedir mas espera que la ventana."
        )

    async def test_la_clave_es_opaca_y_el_ambito_se_guarda(
        self, connection: AsyncConnection
    ) -> None:
        """La tabla guarda el `scope`, y la clave la elige la app ya hasheada.

        `rate_limit_buckets` se purga con un job de retencion y se lee con `SELECT`
        del rol de la app, asi que una IP o un email en claro en la clave seria una
        lista de clientes de la que cualquiera que llegue a la base se lleva una
        copia. Que `scope` se guarde es lo que permite despues distinguir "5/min por
        IP" de "10/min por email" sin volver a la IP.
        """
        await connection.execute(
            text("SELECT allowed FROM rate_limit_hit('t_scope', 10, 60, 'auth:login:ip')")
        )
        scope: str = (
            await connection.execute(
                text("SELECT scope FROM rate_limit_buckets WHERE key = 't_scope'")
            )
        ).scalar_one()
        assert scope == "auth:login:ip"

    async def test_no_necesita_security_definer(self, connection: AsyncConnection) -> None:
        """`rate_limit_hit` es `INVOKER` y deberia seguir siendolo.

        Es la menor privilegio correcta: escribe en `rate_limit_buckets`, que no
        tiene RLS y sobre la que el rol de la app ya tiene INSERT y UPDATE. No hay
        nada que saltar. Si alguien la cambia a `SECURITY DEFINER` "por si acaso",
        esto falla, porque un dia la funcion va a hacer algo mas -- contar de otra
        tabla, leer una config -- y convendria que ese cambio fuera explicito.
        """
        secdef: bool = (
            await connection.execute(
                text(
                    "SELECT p.prosecdef FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.proname = 'rate_limit_hit'"
                )
            )
        ).scalar_one()
        assert secdef is False, "rate_limit_hit no deberia ser SECURITY DEFINER"
