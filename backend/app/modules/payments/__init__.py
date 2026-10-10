"""Modulo de pagos: Mercado Pago (credenciales, suscripciones y pagos de reserva).

Fase 9.1. Los modelos de las tres tablas de tenant viven en `models.py`; la
configuracion por negocio (credenciales y politica de seña) en `config.py`; el
cliente HTTP de MP en `mercadopago.py`; y el servicio de suscripciones en
`subscriptions.py`. El router del panel y el webhook importan desde aca.
"""

from __future__ import annotations

from app.modules.payments.config import (
    calcular_monto_servicio,
    guardar_cuenta,
    guardar_politica_pago,
    obtener_cuenta,
    obtener_politica_pago,
)
from app.modules.payments.mercadopago import (
    MercadoPagoError,
    PaymentInfo,
    PreapprovalInfo,
    PreferenceInfo,
)
from app.modules.payments.models import BookingPayment, MpPaymentAccount, MpSubscription
from app.modules.payments.pagos import aplicar_webhook_pago
from app.modules.payments.subscriptions import (
    activar_plan_pro,
    aplicar_webhook_suscripcion,
    cancelar_suscripcion,
    estado_suscripcion,
)

__all__ = [
    "BookingPayment",
    "MercadoPagoError",
    "MpPaymentAccount",
    "MpSubscription",
    "PaymentInfo",
    "PreapprovalInfo",
    "PreferenceInfo",
    "activar_plan_pro",
    "aplicar_webhook_pago",
    "aplicar_webhook_suscripcion",
    "calcular_monto_servicio",
    "cancelar_suscripcion",
    "estado_suscripcion",
    "guardar_cuenta",
    "guardar_politica_pago",
    "obtener_cuenta",
    "obtener_politica_pago",
]
