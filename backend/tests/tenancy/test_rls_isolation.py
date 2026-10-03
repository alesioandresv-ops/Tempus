"""Aislamiento entre tenants contra PostgreSQL real.

Esta es la suite que justifica el modelo de datos. Si algo de aca falla, el
sistema tiene un IDOR y no hay test unitario que lo detecte: el aislamiento no
esta en Python, esta en la base.

Todos los tests corren con el rol de la **aplicacion** (`tempus_app`), no con el
de DDL. Un test que verifica RLS usando el dueno de las tablas no verifica RLS.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from app.db.session import TENANT_GUC
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.conftest import BUSINESS_A, BUSINESS_B

pytestmark = [pytest.mark.tenancy, pytest.mark.integration]


async def _tenant(conn: AsyncConnection, business_id: uuid.UUID) -> None:
    """Fija el GUC de tenant para el resto de la transaccion.

    `set_config(..., true)` es el equivalente Python de `SET LOCAL`: el `true` es
    el `is_local`. Se usa la forma parametrizada y no un f-string porque el valor
    viene de la sesion.
    """
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(business_id)},
    )


async def _clear_tenant(conn: AsyncConnection) -> None:
    await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '', true)"))


class TestGucRequired:
    """Sin GUC, la politica no tiene contra que filtrar."""

    async def test_sin_tenant_no_se_ve_nada(self, connection: AsyncConnection) -> None:
        await _clear_tenant(connection)
        filas: int = (await connection.execute(text("SELECT count(*) FROM customers"))).scalar_one()
        assert filas == 0, "sin tenant no se deberia ver ninguna fila"

    async def test_un_tenant_inexistente_tampoco(self, connection: AsyncConnection) -> None:
        """Un UUID al azar no abre la puerta.

        Es el test que distingue "filtra por igualdad" de "filtra porque no
        entiende el valor". Con un tenant inexistente, la segunda implementacion
        devolveria todo.
        """
        await _tenant(connection, uuid.UUID("99999999-9999-7999-8999-999999999999"))
        filas: int = (await connection.execute(text("SELECT count(*) FROM customers"))).scalar_one()
        assert filas == 0

    async def test_tenant_valido_si_muestra(self, connection: AsyncConnection) -> None:
        await _tenant(connection, BUSINESS_A)
        filas: int = (await connection.execute(text("SELECT count(*) FROM customers"))).scalar_one()
        assert filas == 1


class TestReadIsolation:
    async def test_cada_tenant_ve_solo_lo_suyo(self, connection: AsyncConnection) -> None:
        await _tenant(connection, BUSINESS_A)
        nombres_a: Sequence[str] = (
            (await connection.execute(text("SELECT first_name FROM customers"))).scalars().all()
        )
        await _tenant(connection, BUSINESS_B)
        nombres_b: Sequence[str] = (
            (await connection.execute(text("SELECT first_name FROM customers"))).scalars().all()
        )

        assert nombres_a == ["Ana"]
        assert nombres_b == ["Beto"]
        assert set(nombres_a) & set(nombres_b) == set()

    async def test_no_se_puede_leer_por_id_de_otro_tenant(
        self, connection: AsyncConnection, customer_b: uuid.UUID
    ) -> None:
        """Un SELECT directo por primary key tampoco cruza.

        Es el ataque mas basico y el que un filtro de la aplicacion dejaria pasar:
        `get(Customer, id)`. Si la RLS no esta, el filtro de la app es la unica
        barrera, y un endpoint con un `where` equivocado filtra datos.
        """
        from tests.conftest import CUSTOMER_B

        await _tenant(connection, BUSINESS_A)
        fila = (
            await connection.execute(
                text("SELECT first_name FROM customers WHERE id = :id"), {"id": CUSTOMER_B}
            )
        ).scalar_one_or_none()
        assert fila is None


class TestWriteIsolation:
    async def test_insert_en_otro_tenant_falla(
        self, connection: AsyncConnection, business_b: uuid.UUID
    ) -> None:
        """`INSERT` con `business_id` ajeno: `insufficient_privilege`.

        La politica de INSERT compara el `business_id` de la fila nueva con el GUC.
        El test no pone ningun tenant: el GUC queda vacio, `NULLIF` lo vuelve
        NULL, y la comparacion falla. Es el escenario de un endpoint de alta que
        olvidara resolver el negocio.

        **El mensaje se reconoce por el codigo de SQLSTATE, no por su texto.**
        PostgreSQL traduce el mensaje de error segun `lc_messages`, y el de esta
        maquina--y el de cualquier despliegue en Castellano-- dice `el nuevo
        registro viola la política de seguridad de registros`, no `row-level
        security`. Buscar la cadena en ingles hacia fallar el test en un entorno
        que funciona bien, que es la forma mas cara de tener un test fragil: no
        senala un problema del codigo, senala un problema del servidor.

        `42501`--`insufficient_privilege`-- es el codigo estable. Es el mismo para
        una violacion de politica que para un permiso denegado, asi que se completan
        los dos chequeos: el codigo dice que la base rechazo la escritura, y el texto
        dice que fue por la politica y no por un permiso.
        """
        with pytest.raises(DBAPIError) as excinfo:
            await connection.execute(
                text(
                    """
                    INSERT INTO customers (id, business_id, first_name, last_name, phone_e164)
                    VALUES (:id, :business_id, 'Intruso', 'Ajeno', '+5491100000099')
                    """
                ),
                {"id": uuid.uuid4(), "business_id": business_b},
            )

        sqlstate = getattr(excinfo.value.orig, "sqlstate", None)
        assert sqlstate == "42501", (
            f"la base no devolvio insufficient_privilege sino {sqlstate!r}: "
            f"si fuera '23514' o '23505' el rechazo seria una constraint, no la "
            f"politica, y este test estaria probando otra cosa."
        )
        # Solo informativo: el texto varia con el idioma del servidor.
        assert "pol" in str(excinfo.value).lower() or "row-level" in str(excinfo.value).lower()

    async def test_insert_dentro_del_tenant_propio_funca(
        self, connection: AsyncConnection, business_a: uuid.UUID
    ) -> None:
        await _tenant(connection, business_a)
        # El telefono lleva un sufijo aleatorio porque `uq_customers_business_id_phone`
        # es unique por negocio. Con un valor fijo, la segunda corrida del suite
        # falla con `duplicate key` y parece un problema de RLS.
        await connection.execute(
            text(
                """
                INSERT INTO customers (id, business_id, first_name, last_name, phone_e164)
                VALUES (:id, :business_id, 'Nuevo', 'Cliente', :phone)
                """
            ),
            {
                "id": uuid.uuid4(),
                "business_id": business_a,
                "phone": f"+54911{uuid.uuid4().int % 10**8:08d}",
            },
        )
        total: int = (await connection.execute(text("SELECT count(*) FROM customers"))).scalar_one()
        assert total == 2

    async def test_update_de_otro_tenant_no_afecta_filas(self, connection: AsyncConnection) -> None:
        """El UPDATE cruzado no da error: filtra en silencio.

        Este es el detalle que mas sorprende y mas hay que documentar. La politica
        de UPDATE filtra con `USING`, asi que la fila ajena es invisible y el
        `UPDATE ... WHERE id = <ajeno>` matchea cero filas. No hay excepcion, no hay
        aviso: hay un `rowcount` de 0. Por eso la aplicacion no puede confiar en
        "no hubo error" para concluir que algo se modifico.
        """
        from tests.conftest import CUSTOMER_B

        await _tenant(connection, BUSINESS_A)
        resultado = await connection.execute(
            text("UPDATE customers SET first_name = 'Secuestrado' WHERE id = :id"),
            {"id": CUSTOMER_B},
        )
        assert resultado.rowcount == 0

    async def test_delete_de_otro_tenant_no_afecta_filas(self, connection: AsyncConnection) -> None:
        from tests.conftest import CUSTOMER_B

        await _tenant(connection, BUSINESS_A)
        resultado = await connection.execute(
            text("DELETE FROM customers WHERE id = :id"),
            {"id": CUSTOMER_B},
        )
        assert resultado.rowcount == 0

    async def test_update_dentro_del_tenant_si_funca(self, connection: AsyncConnection) -> None:
        from tests.conftest import CUSTOMER_A

        await _tenant(connection, BUSINESS_A)
        resultado = await connection.execute(
            text("UPDATE customers SET notes = 'nota' WHERE id = :id"),
            {"id": CUSTOMER_A},
        )
        assert resultado.rowcount == 1


class TestRlsEnCadaTabla:
    """Cada tabla de `TENANT_TABLES` esta realmente protegida en la base.

    Compara el modelo contra el catalogo, no contra una lista escrita a mano. Si
    alguien agrega una tabla al modelo y olvida la migracion, el desync aparece
    aca nombrando la tabla.
    """

    async def test_todas_las_tablas_tenant_tienen_rls_y_force(
        self, connection: AsyncConnection
    ) -> None:
        filas = (
            await connection.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                    """
                )
            )
        ).all()
        estado = {nombre: (rls, force) for nombre, rls, force in filas}

        from app.models import TENANT_TABLES

        # Una tabla tiene RLS si tiene ENABLE ROW LEVEL SECURITY (relrowsecurity)
        # O FORCE ROW LEVEL SECURITY (relforcerowsecurity).
        # FORCE RLS implica RLS activado aunque relrowsecurity sea false.
        faltantes = [
            tabla
            for tabla in sorted(TENANT_TABLES)
            if not (estado.get(tabla, (False, False))[0] or estado.get(tabla, (False, False))[1])
        ]
        sin_force = [
            tabla
            for tabla in sorted(TENANT_TABLES)
            if estado.get(tabla, (False, False))[1] is not True
        ]
        assert not faltantes, f"tablas de tenant sin RLS ni FORCE RLS: {faltantes}"
        assert not sin_force, f"tablas de tenant sin FORCE RLS: {sin_force}"

    async def test_jobs_no_tiene_rls(self, connection: AsyncConnection) -> None:
        """La excepcion documentada, verificada contra el catalogo.

        Si alguien le pone RLS a `jobs` sin darse cuenta, el dispatcher deja de
        procesar los trabajos de plataforma: fallan con "new row violates row-level
        security" y el sintoma aparece como un job que nunca se ejecuta.
        """
        fila: bool = (
            await connection.execute(
                text(
                    """
                    SELECT c.relrowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'jobs'
                    """
                )
            )
        ).scalar_one()
        assert fila is False

    async def test_cada_tabla_tenant_tiene_exactamente_una_politica(
        self, connection: AsyncConnection
    ) -> None:
        """Ninguna tabla de tenant queda sin RLS, y solo las globales declaradas la llevan.

        La segunda mitad--que las tablas globales *no* lleven politica-- tiene una
        excepcion: `businesses`. Es global porque se identifica por su propio `id`, y
        aun asi necesita RLS, porque el flujo publico tiene que resolver el negocio
        por `slug` antes de conocer el tenant. La excepcion esta declarada en
        `app.models.GLOBAL_TABLES_WITH_RLS`, con la razon, para que la lista no viva
        dentro de esta asercion.
        """
        from app.models import GLOBAL_TABLES, GLOBAL_TABLES_WITH_RLS, TENANT_TABLES

        filas = (
            await connection.execute(
                text(
                    "SELECT tablename, count(*) FROM pg_policies "
                    "WHERE schemaname = 'public' GROUP BY tablename"
                )
            )
        ).all()
        conteo: dict[str, int] = dict(filas)

        sin_politica = [t for t in sorted(TENANT_TABLES) if conteo.get(t, 0) == 0]
        con_politica = [
            t
            for t in sorted(GLOBAL_TABLES)
            if conteo.get(t, 0) > 0 and t not in GLOBAL_TABLES_WITH_RLS
        ]
        assert not sin_politica, f"tablas de tenant sin politica: {sin_politica}"
        assert not con_politica, (
            f"tablas globales con politica sin declararlas en "
            f"GLOBAL_TABLES_WITH_RLS: {con_politica}"
        )

    async def test_las_politicas_usan_current_business_id(
        self, connection: AsyncConnection
    ) -> None:
        """La politica debe colgar del GUC, o ser inalcanzable para el rol de la app.

        Una politica escrita con un id fijo "funciona" en desarrollo y es un
        catastrophico en produccion: todos los clientes ven los datos del
        negocio del id hardcodeado.

        **El invariante no es "todo `qual` menciona el GUC" sino "nada que pueda
        alcanzar el rol de la aplicacion llega sin el GUC".** Son dos cosas
        distintas, y la segunda es la que importa. Hay dos excepciones legitimas y
        las dos se separan por a quien alcanzan, no por su nombre:

        - `businesses_read` (`USING (true)`, `PUBLIC`): resolver el negocio por `slug`
          tiene que funcionar *antes* de que exista un GUC--es el paso uno de una
          sesion sin tenant-- asi que el `true` es el diseno, no un descuido.
        - `businesses_insert` (`WITH CHECK (true)`, `TO tempus_owner`): la fila que se
          crea todavia no es de nadie, no hay nada que comparar. La separacion real
          la hace el `TO`, que deja fuera al rol de la app.

        Escribirlo como "toda politica menciona el GUC" obliga a elegir entre romper
        el alta de tenants y borrar el test. Como invariante es inutil: la excepcion
        real es "esta politica no aplica a `tempus_app`", y eso es lo que se comprueba.
        """
        filas = (
            await connection.execute(
                text(
                    "SELECT policyname, tablename, cmd, qual, with_check, roles "
                    "FROM pg_policies WHERE schemaname = 'public'"
                )
            )
        ).all()
        assert filas, "no hay politicas en la base"

        rol_app: str = (await connection.execute(text("SELECT current_user"))).scalar_one()

        sueltas = [
            f"{tabla}.{nombre} ({cmd}) aplica a {sorted(roles)}"
            for nombre, tabla, cmd, qual, _check, roles in filas
            if (qual is None or "current_business_id" not in qual)
            and rol_app in set(roles)
            and not (tabla == "businesses" and cmd == "SELECT")
        ]
        assert not sueltas, (
            f"politicas sin el GUC de tenant que el rol de la app puede invocar: "
            f"{sueltas}. Con una constante, todos los clientes ven los datos del "
            f"negocio del id fijo."
        )

    async def test_las_escrituras_de_businesses_si_miran_el_guc(
        self, connection: AsyncConnection
    ) -> None:
        """Leer `businesses` sin GUC es el diseño; escribirlo sin GUC sería un agujero.

        `businesses_read` con `USING (true)` es lo que permite el paso uno del flujo
        publico. Lo que este test protege es el otro lado, y son **dos** requisitos
        distintos:

        - `UPDATE` tiene que comparar el GUC. Si `businesses_write` perdiera su
          `with_check`, cualquiera con permiso de escritura podria cambiar el `slug`
          o el `status` de un negocio ajeno, y la politica--que es la ultima linea--
          ya no estaria protegiendo nada.
        - `INSERT` no puede comparar nada, porque la fila es nueva. Lo que tiene que
          estar acotado es **a quien aplica**: el rol de onboarding y nadie mas. Sin
          ese `TO`, un `true` en el `WITH CHECK` seria "cualquiera con permiso de
          tabla crea tenants".
        """
        filas = (
            await connection.execute(
                text(
                    "SELECT policyname, cmd, with_check, roles FROM pg_policies "
                    "WHERE schemaname = 'public' AND tablename = 'businesses' "
                    "AND cmd IN ('INSERT', 'UPDATE')"
                )
            )
        ).all()

        assert filas, "las escrituras de `businesses` no tienen politica"
        sin_guc = [
            nombre
            for nombre, cmd, with_check, _roles in filas
            if cmd == "UPDATE" and (with_check is None or "current_business_id" not in with_check)
        ]
        assert not sin_guc, (
            f"politicas de UPDATE sobre `businesses` que no comparan el GUC: {sin_guc}. "
            f"Con `true` a secas, editar la configuracion de un negocio de otro tenant "
            f"dejaria de estar protegida."
        )

        rol_app: str = (await connection.execute(text("SELECT current_user"))).scalar_one()
        abiertas = [
            nombre
            for nombre, cmd, _check, roles in filas
            if cmd == "INSERT" and rol_app in set(roles)
        ]
        assert not abiertas, (
            f"politicas de INSERT sobre `businesses` que alcanzan a {rol_app}: "
            f"{abiertas}. El alta de tenants no puede depender de una politica de la "
            f"tabla que la propia app pueda invocar."
        )

    async def test_el_alta_de_negocios_es_privilegio_del_rol_de_onboarding(
        self, connection: AsyncConnection
    ) -> None:
        """Insertar un negocio es la operacion de onboarding: solo el rol de DDL.

        Se comprueba por las dos vias, porque cada una sola no alcanza. La politica
        `TO tempus_owner` dice quien esta autorizado; el permiso de tabla dice quien
        esta en condiciones de proponer algo. Si faltara el permiso, la politica
        estaria incompleta; si sobrara el permiso, la politica seria irrelevante.
        """
        rol_app = (await connection.execute(text("SELECT current_user"))).scalar_one()
        puede = (
            await connection.execute(
                text("SELECT has_table_privilege(:rol, 'businesses', 'INSERT')"),
                {"rol": rol_app},
            )
        ).scalar_one()
        assert puede is False, (
            f"{rol_app} puede insertar en `businesses`: el alta de tenants dejaria de "
            f"ser una operacion de onboarding."
        )
