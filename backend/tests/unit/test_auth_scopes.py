"""El mapa rol -> scopes tiene que ser la tabla del §10.4, celda por celda.

La tabla del §10.4 es texto. Este archivo es la traduccion, y una traduccion puede
perder informacion sin que nadie lo note: un scope de mas en una fila y el panel
funciona igual hasta que un `staff` configura algo que no deberia, y un scope de menos
y un admin no puede hacer su trabajo.

Por eso las aserciones son contra la **tabla**, escritas de nuevo aca, y no contra el
mapa. Comparar el mapa contra si mismo probaria que el mapa es igual al mapa.
"""

from __future__ import annotations

import pytest
from app.models.enums import BusinessUserRole, PlatformRole
from app.modules.auth.scopes import (
    BUSINESS_ROLE_SCOPES,
    PLATFORM_ROLE_SCOPES,
    PlatformScope,
    Scope,
    as_strings,
    scopes_for_business_role,
    scopes_for_platform_role,
)

pytestmark = pytest.mark.unit

#: La tabla del §10.4, escrita de nuevo. Cada celda es lo que el §10.4 afirma.
#: Se comparan las **capacidades**, no los strings de scope, porque el nombre del scope
#: es una decision de implementacion y la capacidad es la decision de producto.
TABLA_10_4: dict[BusinessUserRole, set[str]] = {
    # Configurar negocio, horarios, feriados: solo admin.
    BusinessUserRole.ADMIN: {
        "configurar",
        "equipo",
        "agenda_completa",
        "reservas_cualquiera",
        "walkin",
        "clientes",
    },
    # Gestionar profesionales y servicios: admin y staff. Configurar: no.
    BusinessUserRole.STAFF: {
        "equipo",
        "agenda_completa",
        "reservas_cualquiera",
        "walkin",
        "clientes",
    },
    # Ver agenda completa y cualquier reserva: no. Walk-in: si.
    BusinessUserRole.PROFESSIONAL: {"walkin", "clientes", "agenda_propia"},
}

#: Que capacidades de la tabla cubre cada scope.
CAPACIDAD_DE_SCOPE: dict[Scope, str] = {
    Scope.BUSINESS_CONFIG_READ: "configurar",
    Scope.BUSINESS_CONFIG_WRITE: "configurar",
    Scope.TEAM_READ: "equipo",
    Scope.TEAM_WRITE: "equipo",
    Scope.BOOKINGS_READ_ANY: "agenda_completa",
    Scope.BOOKINGS_READ_OWN: "agenda_propia",
    Scope.BOOKINGS_WRITE_ANY: "reservas_cualquiera",
    Scope.BOOKINGS_WRITE_OWN: "agenda_propia",
    Scope.BOOKINGS_WALKIN: "walkin",
    Scope.CLIENTS_READ: "clientes",
    Scope.CLIENTS_WRITE: "clientes",
    Scope.AVAILABILITY_READ: "agenda_propia",
}


def _capacidades(scopes: frozenset[Scope]) -> set[str]:
    """Las capacidades que un conjunto de scopes efectivamente concede.

    Con una correccion: `agenda_propia` desaparece si hay `agenda_completa`. Ver la
    agenda del negocio entero incluye ver la propia, asi que para un admin o un staff
    el scope `own` no agrega una capacidad nueva y contarlo por separado haria creer
    que el §10.4 lista una fila mas de la que lista.

    Esto es una asercion sobre el **significado** de la tabla, no una comodidad: si
    `any` no implicara `own`, un `staff` podria ver la agenda del negocio y no la
    propia, lo cual es absurdo y seria un bug de verdad.
    """
    capacidades = {CAPACIDAD_DE_SCOPE[s] for s in scopes}
    if "agenda_completa" in capacidades:
        capacidades.discard("agenda_propia")
    return capacidades


class TestLaTablaDelSpec:
    @pytest.mark.parametrize("rol", list(BusinessUserRole))
    def test_las_capacidades_coinciden_con_la_tabla(self, rol: BusinessUserRole) -> None:
        assert _capacidades(scopes_for_business_role(rol)) == TABLA_10_4[rol]

    def test_admin_es_staff_mas_configurar(self) -> None:
        """La diferencia entre admin y staff es exactamente la configuracion.

        El §10.4 lo dice asi: `staff` existe para delegar sin dar el control de la
        configuracion. Si la diferencia fuera otra, `staff` seria otro rol con otro
        nombre.
        """
        admin = scopes_for_business_role(BusinessUserRole.ADMIN)
        staff = scopes_for_business_role(BusinessUserRole.STAFF)
        assert staff < admin, "staff tiene que ser un subconjunto estricto de admin"
        assert admin - staff == frozenset({Scope.BUSINESS_CONFIG_READ, Scope.BUSINESS_CONFIG_WRITE})

    def test_professional_no_tiene_nada_de_equipo(self) -> None:
        """La propiedad que define al rol: ve lo suyo y nada del equipo.

        Este es el limite del §10.4 que justifico agregar `professional` al enum: un
        `staff` ve la agenda completa, asi que sin este rol un profesional con login
        tendria que ver la agenda del negocio entero.
        """
        scopes = scopes_for_business_role(BusinessUserRole.PROFESSIONAL)
        assert Scope.BOOKINGS_READ_ANY not in scopes
        assert Scope.BOOKINGS_WRITE_ANY not in scopes
        assert Scope.TEAM_READ not in scopes
        assert Scope.TEAM_WRITE not in scopes
        assert Scope.BUSINESS_CONFIG_WRITE not in scopes

    def test_professional_si_puede_registrar_walkin(self) -> None:
        """Walk-in es lo unico de equipo que el §10.4 le concede.

        Y tiene sentido: el profesional es quien esta ahi, es el unico que puede
        decided que un cliente que llego sin cita entre ahora.
        """
        assert Scope.BOOKINGS_WALKIN in scopes_for_business_role(BusinessUserRole.PROFESSIONAL)

    def test_own_y_any_van_juntos(self) -> None:
        """`own` y `any` no se separan.

        No es una regla estetica: si un rol tuviera `read:own` sin `read:any`, seria
        un rol que puede leer su agenda pero no la de otros, que es exactamente `own`.
        Y si tuviera `any` sin `own`, un profesional con `any` podria leer su propia
        agenda solo por el hecho de tener `any`, que es un permiso implicito que depende
        de como este escrito el handler. Van juntos para que `own` signifique `own`.
        """
        for rol in BusinessUserRole:
            scopes = scopes_for_business_role(rol)
            assert (Scope.BOOKINGS_READ_OWN in scopes) == (Scope.BOOKINGS_WRITE_OWN in scopes), rol
            assert (Scope.BOOKINGS_READ_ANY in scopes) == (Scope.BOOKINGS_WRITE_ANY in scopes), rol
            # Y tener `any` implica tener `own`: `any` es un superconjunto.
            if Scope.BOOKINGS_READ_ANY in scopes:
                assert Scope.BOOKINGS_READ_OWN in scopes, rol


class TestMapaTotal:
    def test_todo_rol_tiene_scopes(self) -> None:
        """Un rol sin scopes es un rol sin permisos, no un rol vacio.

        El mapa es una funcion total sobre el enum: si mañana se agrega un rol al enum
        y no al mapa, esto falla al arrancar en vez de emitir tokens sin permisos.
        """
        assert set(BUSINESS_ROLE_SCOPES) == set(BusinessUserRole)
        assert set(PLATFORM_ROLE_SCOPES) == set(PlatformRole)

    def test_un_rol_desconocido_falla_en_vez_de_devolver_vacio(self) -> None:
        """`KeyError`, no `frozenset()`.

        Un rol nuevo sin scopes emite tokens sin permisos y el error aparece en
        produccion, en el endpoint que falla. Fallar en el mapa es fallar antes.
        """
        with pytest.raises(KeyError):
            scopes_for_business_role("admin-inventado")  # type: ignore[arg-type]

    def test_ningun_rol_se_queda_sin_scopes(self) -> None:
        for rol, scopes in BUSINESS_ROLE_SCOPES.items():
            assert scopes, f"{rol} no tiene ningun scope"
        vacios: list[PlatformRole] = [
            rol for rol, scopes in PLATFORM_ROLE_SCOPES.items() if not scopes
        ]
        assert not vacios, f"roles de plataforma sin scopes: {vacios}"


class TestPlataformaSeparada:
    def test_los_scopes_de_plataforma_no_son_de_negocio(self) -> None:
        """Un operador de plataforma no es un `admin` de otro negocio.

        El §10.4 lo prohibe explicitamente: mezclar ambos garantiza que alguna
        asignacion de rol conceda acceso cruzado. Que vivan en enums distintos hace
        que el error se vea en la firma del handler, y no en una comparacion de rol
        dentro de un `if`.
        """
        assert not set(PlatformScope) & set(Scope)
        assert not set(PLATFORM_ROLE_SCOPES[PlatformRole.OWNER]) & set(
            BUSINESS_ROLE_SCOPES[BusinessUserRole.ADMIN]
        )

    def test_support_no_escribe_tenants(self) -> None:
        """`support` existe para poder ayudar sin poder romper.

        Es el rol que ve una queja y necesita leer la cuenta, pero no va a cambiar el
        nombre del negocio ni la facturacion.
        """
        support = scopes_for_platform_role(PlatformRole.SUPPORT)
        assert PlatformScope.SUPPORT_IMPERSONATE_READ in support
        assert PlatformScope.TENANTS_WRITE not in support
        assert PlatformScope.TENANTS_READ in support


class TestAsStrings:
    def test_es_ordenado(self) -> None:
        """El claim `scopes` es un lista ordenada.

        Un `set` no tiene orden, asi que sin esto dos llamadas con los mismos scopes
        producen tokens distintos byte a byte y comparar tokens en un test se vuelve
        imposible.
        """
        scopes = scopes_for_business_role(BusinessUserRole.ADMIN)
        assert as_strings(scopes) == sorted(as_strings(scopes))
        assert as_strings(scopes) == as_strings(scopes)

    def test_es_determinista_entre_llamadas(self) -> None:
        primero = as_strings(scopes_for_business_role(BusinessUserRole.STAFF))
        segundo = as_strings(scopes_for_business_role(BusinessUserRole.STAFF))
        assert primero == segundo

    def test_no_hay_duplicados(self) -> None:
        for rol in BusinessUserRole:
            valores = as_strings(scopes_for_business_role(rol))
            assert len(valores) == len(set(valores)), rol
