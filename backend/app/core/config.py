"""Configuracion de la aplicacion.

La regla que sigue este modulo: la configuracion se valida al arrancar. Si falta
una variable obligatoria, o una tiene un valor que no tiene sentido, la app NO
levanta y dice cual. Es preferible fallar en el deploy que descubrir a las 3am
que la firma de los webhooks no verifica porque falta un secreto (PROJECT_MASTER
seccion 25 y 42).

Los nombres de los campos corresponden uno a uno con los nombres de las variables
de entorno: `jwt_secret_key` <-> `JWT_SECRET_KEY`. El test
`test_config_cubre_todas_las_variables_del_env_example` verifica que eso siga
siendo cierto, para que el `.env.example` no se quede viejo en silencio.
"""

from __future__ import annotations

import base64
import binascii
import json
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Valores de ejemplo que viven en `.env.example`. No pueden usarse en produccion:
# un despliegue con el secreto de ejemplo no es un despliegue, es una amenaza.
PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {
        "cambiame_en_produccion_openssl_rand_hex_32",
        "cambiame_openssl_rand_hex_32",
        "cambiame_openssl_rand_base64_32",
    }
)


class Environment(StrEnum):
    """Entorno de ejecucion."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        return self in (Environment.STAGING, Environment.PRODUCTION)

    @property
    def is_local(self) -> bool:
        return self is Environment.LOCAL


class Settings(BaseSettings):
    """Configuracion completa, leida del entorno."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `ignore` y no `forbid`: hay variables en el entorno que no son nuestras
        # (las de la plataforma de hosting, por ejemplo) y no es asunto nuestro
        # rechazar el arranque por ellas. Los typos en NUESTRAS variables se
        # detectan en el test que compara contra `.env.example`.
        extra="ignore",
        case_sensitive=False,
    )

    # --- Entorno -----------------------------------------------------------
    environment: Environment = Field(default=Environment.LOCAL, description="Entorno")
    debug: bool = Field(default=False, description="Modo debug. Nunca true en produccion")
    public_domain: str = Field(
        default="localhost:5173", description="Dominio canonico, para URLs absolutas"
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="Origenes permitidos por CORS",
    )

    # --- Base de datos -----------------------------------------------------
    database_url: str = Field(description="URL de la app. Rol sin DDL, con RLS aplicado")
    database_migration_url: str = Field(
        default="", description="URL con permisos de DDL. Solo para Alembic"
    )
    database_ssl: bool = Field(default=False, description="TLS con la base")
    db_app_role: str = Field(
        default="tempus_app", description="Rol que usa la aplicacion. No debe ser el dueño"
    )
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_isolation_level: str = Field(
        default="READ COMMITTED",
        description=(
            "La garantia de no doble reserva la da la restriccion EXCLUDE, no el nivel "
            "de aislamiento. Ver ADR-0008."
        ),
    )

    # --- Seguridad ---------------------------------------------------------
    jwt_secret_key: SecretStr = Field(description="Firma de los access tokens")
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=15, ge=1, le=1440)
    jwt_issuer: str = Field(default="tempus")
    jwt_audience: str = Field(default="tempus-api")
    refresh_token_expire_days: int = Field(default=30, ge=1, le=365)
    encryption_key: SecretStr = Field(
        description="Clave Fernet para secretos de terceros en reposo"
    )
    cookie_secure: bool = Field(default=False)
    cookie_samesite: str = Field(default="strict")
    cookie_path: str = Field(default="/api/v1/auth")
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=1024)
    argon2_parallelism: int = Field(default=4, ge=1)

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_login_per_ip: int = Field(default=5, ge=1)
    rate_limit_login_per_email: int = Field(default=10, ge=1)
    rate_limit_public_booking_per_ip: int = Field(default=10, ge=1)
    rate_limit_public_booking_per_phone_hourly: int = Field(default=5, ge=1)
    rate_limit_general_per_ip: int = Field(default=120, ge=1)
    rate_limit_admin_per_user: int = Field(default=600, ge=1)
    rate_limit_scheduler_tick: int = Field(default=2, ge=1)

    # --- WhatsApp ----------------------------------------------------------
    meta_app_id: str = Field(default="")
    meta_app_secret: SecretStr = SecretStr("")
    meta_graph_api_version: str = Field(default="v21.0")
    meta_api_base_url: str = Field(default="https://graph.facebook.com")
    meta_webhook_verify_token: SecretStr = SecretStr("")
    whatsapp_platform_template_confirmation: str = Field(default="")
    whatsapp_platform_template_reminder_2h: str = Field(default="")
    whatsapp_platform_template_reminder_1h: str = Field(default="")
    whatsapp_platform_template_cancelled: str = Field(default="")
    whatsapp_platform_template_rescheduled: str = Field(default="")
    whatsapp_default_locale: str = Field(default="es_AR")
    whatsapp_pricing_currency: str = Field(default="USD", min_length=3, max_length=3)
    whatsapp_pricing_marketing: float = Field(default=0.012, ge=0)
    whatsapp_pricing_utility: float = Field(default=0.008, ge=0)
    whatsapp_pricing_service: float = Field(default=0.008, ge=0)
    whatsapp_monthly_budget_alert: float = Field(default=25.0, ge=0)
    whatsapp_tenant_hourly_cap: int = Field(default=500, ge=1)

    # --- Background jobs ---------------------------------------------------
    scheduler_tick_seconds: int = Field(default=30, ge=1)
    job_batch_size: int = Field(default=25, ge=1)
    job_lease_timeout_seconds: int = Field(default=300, ge=10)
    job_max_attempts: int = Field(default=5, ge=1)
    enable_inline_scheduler: bool = Field(default=True)
    scheduler_tick_secret: SecretStr = Field(description="Bearer del endpoint interno de tick")
    job_queues: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "reminders",
            "whatsapp_outbound",
            "webhooks",
            "media",
            "reports",
        ]
    )
    job_concurrency: int = Field(default=5, ge=1)
    job_retention_days: int = Field(default=30, ge=1)

    # --- Almacenamiento ----------------------------------------------------
    r2_endpoint: str = Field(default="")
    r2_access_key_id: str = Field(default="")
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = Field(default="tempus-media")
    r2_public_base_url: str = Field(default="")
    media_max_upload_bytes: int = Field(default=5242880, ge=1024)
    media_presign_expire_seconds: int = Field(default=300, ge=30)
    media_allowed_content_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )
    media_max_width: int = Field(default=2000, ge=1)
    media_max_height: int = Field(default=2000, ge=1)

    # --- Disponibilidad: valores por defecto -------------------------------
    default_timezone: str = Field(default="America/Argentina/Buenos_Aires")
    default_locale: str = Field(default="es-AR")
    default_currency: str = Field(default="ARS", min_length=3, max_length=3)
    default_slot_interval_minutes: int = Field(default=15, ge=5, le=120)
    default_min_lead_minutes: int = Field(default=60, ge=0)
    default_max_advance_days: int = Field(default=60, ge=1, le=365)
    default_cancellation_window_minutes: int = Field(default=120, ge=0)
    availability_max_range_days: int = Field(default=31, ge=1, le=366)
    availability_cache_ttl_seconds: int = Field(default=30, ge=0)
    booking_allow_cancel_after_start_minutes: int = Field(default=0, ge=0)

    # --- Observabilidad ----------------------------------------------------
    log_format: str = Field(default="json", description="json | console")
    log_level: str = Field(default="INFO")
    log_redact_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "password",
            "password_hash",
            "access_token",
            "refresh_token",
            "token",
            "secure_token",
            "access_token_encrypted",
            "app_secret_encrypted",
            "authorization",
        ]
    )
    log_client_data: bool = Field(
        default=False, description="Enviar datos de clientes a terceros. En produccion, false"
    )

    # --- Frontend ----------------------------------------------------------
    vite_api_base_url: str = Field(default="http://localhost:8000/api/v1")
    vite_turnstile_site_key: str = Field(default="")
    turnstile_secret: SecretStr = SecretStr("")
    turnstile_verify_url: str = Field(
        default="https://challenges.cloudflare.com/turnstile/v0/siteverify"
    )
    turnstile_enabled: bool = Field(default=False)

    # --- Plataforma admin --------------------------------------------------
    meta_embedded_signup_app_id: str = Field(default="")
    meta_embedded_signup_config_id: str = Field(default="")
    support_email: str = Field(default="ayuda@tempus.app")

    # =====================================================================
    # Validadores
    # =====================================================================
    @field_validator(
        "cors_origins",
        "job_queues",
        "media_allowed_content_types",
        "log_redact_keys",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Acepta listas en CSV y en JSON.

        Los cuatro campos usan `NoDecode`: sin eso, `pydantic-settings` les pasa el
        valor por `json.loads` **antes** de que llegue un validador, y
        `CORS_ORIGINS=a,b` -que es la forma natural de escribirlo- falla con
        "Expecting value: line 1 column 1".

        El JSON se parsea aqui y no se devuelve tal cual. Antes se hacia
        `return value` creyendo que pydantic loeria la lista, y no: lo que llegaba
        al validador de tipo era un `str`, y el error era
        "Input should be a valid list". O sea que el formato JSON no estaba
        soportado, solo documentado. Y es el formato que produce `docker compose`
        cuando serializa una lista, asi que el primer `docker compose up` con
        `CORS_ORIGINS` en el `.env` habria dejado la app sin arrancar.
        """
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON invalido en una lista de configuracion: {exc}") from exc
            if not isinstance(parsed, list):
                raise ValueError(
                    f"se esperaba una lista JSON y vino {type(parsed).__name__}: {stripped!r}"
                )
            return parsed
        return [part.strip() for part in stripped.split(",") if part.strip()]

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        """Rechaza un timezone inexistente.

        Un timezone invalido no falla al arrancar: falla meses despues, el dia que
        un negocio nuevo no muestra disponibilidad. Es exactamente el tipo de error
        que la §48 dice que la plataforma no puede tener.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"timezone IANA inexistente: {value!r}") from exc
        return value

    @field_validator("cookie_samesite")
    @classmethod
    def _valid_samesite(cls, value: str) -> str:
        allowed = {"strict", "lax", "none"}
        if value.lower() not in allowed:
            raise ValueError(f"COOKIE_SAMESITE debe ser uno de {sorted(allowed)}")
        return value.lower()

    @model_validator(mode="after")
    def _validate_secrets(self) -> Self:
        """Reglas que solo se pueden comprobar teniendo todos los campos."""
        if self.environment.is_production:
            if self.debug:
                raise ValueError("DEBUG no puede ser true en staging ni en produccion")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE debe ser true fuera de local")
            if self.log_client_data:
                raise ValueError("LOG_CLIENT_DATA no puede ser true en produccion")
            if self.log_format != "json":
                raise ValueError("LOG_FORMAT debe ser json en produccion")
            for field in (
                "jwt_secret_key",
                "encryption_key",
                "scheduler_tick_secret",
            ):
                raw = getattr(self, field).get_secret_value()
                if raw in PLACEHOLDER_SECRETS:
                    raise ValueError(
                        f"{field.upper()} tiene el valor de ejemplo de .env.example. "
                        "En produccion es un secreto de verdad: generar con "
                        "`openssl rand -hex 32`."
                    )
            if (
                self.scheduler_tick_secret.get_secret_value()
                == self.jwt_secret_key.get_secret_value()
            ):
                raise ValueError("SCHEDULER_TICK_SECRET no puede ser igual a JWT_SECRET_KEY")
        return self

    @field_validator("encryption_key")
    @classmethod
    def _valid_fernet_key(cls, value: SecretStr) -> SecretStr:
        """Valida que la clave sea una clave Fernet usable.

        Fernet necesita exactamente 32 bytes en base64 url-safe. Una clave de otro
        tamano no falla al arrancar: falla la primera vez que hay que cifrar un
        token de Meta, que es en produccion y de noche.
        """
        raw = value.get_secret_value()
        if not raw:
            return value
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("ENCRYPTION_KEY no es base64 url-safe valido") from exc
        if len(decoded) != 32:
            raise ValueError(
                f"ENCRYPTION_KEY debe decodificar a 32 bytes;-decodifica a {len(decoded)}. "
                "Generar con `openssl rand -base64 32`."
            )
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def _jwt_key_larga_suficiente(cls, value: SecretStr) -> SecretStr:
        """Rechaza una clave de firma demasiado corta.

        HS256 es HMAC-SHA256, y la longitud de la clave es la del hash: menos de 32
        bytes divide la fuerza de la firma y la deja vulnerable a buscar colisiones.
        PyJWT 2.15 avisa con `InsecureKeyLengthWarning` cuando la clave tiene menos de
        32 bytes, y como los warnings de la suite son errores, el aviso llega al
        desarrollador durante el desarrollo y no al log de produccion.

        El piso es el de la RFC 7518 §3.2, que es el mismo que el de las guias de
        HS256. 32 bytes es un piso, no una recomendacion: 64 es lo que corresponderia a
        una clave de sesion.

        Este validador **reemplaza** al chequeo de longitud que estaba en el
        `model_validator` de produccion, que solo corria con `environment=production` y
        contaba caracteres en vez de bytes. Tres diferencias, todas a favor de este:
        corre en todos los entornos, asi que una clave corta en desarrollo se detecta
        antes de llegar a produccion; cuenta bytes, que es lo que HS256 usa como
        longitud de clave; y el mensaje dice como arreglarlo.
        """
        raw = value.get_secret_value()
        if not raw:
            return value
        if len(raw.encode("utf-8")) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY tiene {len(raw.encode('utf-8'))} bytes y HS256 necesita al menos 32. "
                "Generar con `openssl rand -hex 32`."
            )
        return value

    # =====================================================================
    # Derivados
    # =====================================================================
    @property
    def business_timezone(self) -> ZoneInfo:
        """Timezone por defecto de los negocios nuevos."""
        return ZoneInfo(self.default_timezone)

    @property
    def is_sqlite_url(self) -> bool:
        """SQLite esta prohibido en tests (ARCHITECTURE.md seccion 16.1)."""
        return self.database_url.startswith("sqlite")

    @property
    def redaction_keys_lower(self) -> frozenset[str]:
        """Las claves a redactar, en minusculas, para comparar sin distinguir caso."""
        return frozenset(key.lower() for key in self.log_redact_keys)

    def sqlalchemy_url(self, *, for_migrations: bool = False) -> str:
        """URL para el engine, con el SSL aplicado.

        `for_migrations` elige entre el rol con DDL y el rol de la app. Nunca se
        conectan por el mismo: el rol de migraciones tiene DDL, y usarlo para
        servir requests anularia la separacion de permisos.
        """
        url = self.database_migration_url if for_migrations else self.database_url
        if not self.database_ssl or "sslmode" in url:
            return url
        # `?` y no siempre `?`: una URL que ya trae parametros usa `&`. Poner `?`
        # sobre una URL con query deja el resto de la query pegada al valor del
        # parametro, y el error aparece como "sslmode sin valor" lejos de la causa.
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}sslmode=require"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuracion del proceso, cacheada.

    `lru_cache` y no un modulo global: asi los tests pueden monkeypatchearla y cada
    proceso de la app lee el entorno una sola vez.
    """
    return Settings()
