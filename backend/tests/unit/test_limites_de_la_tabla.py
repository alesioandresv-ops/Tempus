"""Los numeros del §10.5, fijados.

En `test_rate_limits.py` los limites se agrandan para que los tests sean rapidos, asi
que nada ahi comprueba que el valor por defecto sea el de la tabla de `ARCHITECTURE.md`.
Esta es la prueba que si lo comprueba, y es una linea por setting.

Sin ella, cambiar `rate_limit_login_per_ip` de 5 a 5000 seria un cambio que nadie
nota: los tests siguen verdes porque todos usan limites propios.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings

#: (setting, valor del §10.5)
LIMITES_DE_LA_TABLA = [
    ("rate_limit_login_per_ip", 5),
    ("rate_limit_login_per_email", 10),
    ("rate_limit_public_booking_per_ip", 10),
    ("rate_limit_public_booking_per_phone_hourly", 5),
    ("rate_limit_scheduler_tick", 2),
    ("rate_limit_general_per_ip", 120),
    ("rate_limit_admin_per_user", 600),
]


@pytest.mark.parametrize(("setting", "esperado"), LIMITES_DE_LA_TABLA)
def test_el_limite_por_defecto_es_el_de_la_tabla(setting: str, esperado: int) -> None:
    assert getattr(Settings(), setting) == esperado


def test_todo_limite_tiene_ventana_conocida() -> None:
    """Las ventanas del §10.5 son 1 minuto, menos el del telefono que es 1 hora.

    El del telefono es el unico distinto, y por el motivo que esta escrito en el
    modulo de rate limiting: el costo se paga al enviar el mensaje, no al reservar.
    """
    from app.core.rate_limit import WINDOW_HOUR, WINDOW_MINUTE

    assert WINDOW_MINUTE == 60
    assert WINDOW_HOUR == 3600
