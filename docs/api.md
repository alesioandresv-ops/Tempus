# Documentación de API — Tempus

## Base URL

```
http://localhost:8000/api/v1
```

## Autenticación

La API usa JWT Bearer tokens para endpoints internos y públicos sin autenticación.

### Login

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123"
}
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 900
}
```

## Endpoints públicos

### Obtener negocio

```http
GET /api/v1/public/businesses/{slug}
```

### Listar servicios

```http
GET /api/v1/public/businesses/{slug}/services
```

### Listar profesionales

```http
GET /api/v1/public/businesses/{slug}/professionals?service_id={uuid}
```

### Calcular disponibilidad

```http
POST /api/v1/public/businesses/{slug}/availability
Content-Type: application/json

{
  "service_id": "uuid",
  "professional_id": "uuid" | null,
  "date": "2026-01-15"
}
```

### Crear reserva

```http
POST /api/v1/public/bookings
Content-Type: application/json

{
  "slug": "test-business",
  "service_id": "uuid",
  "professional_id": "uuid" | null,
  "customer_first_name": "Juan",
  "customer_last_name": "Pérez",
  "customer_phone_e164": "+5491112345678",
  "starts_at": "2026-01-15T10:00:00Z",
  "ends_at": "2026-01-15T11:00:00Z",
  "local_date": "2026-01-15",
  "idempotency_key": "unique-key-123"
}
```

### Ver reserva

```http
GET /api/v1/public/bookings/{token}
```

### Cancelar reserva

```http
POST /api/v1/public/bookings/{token}/cancel
```

### Reprogramar reserva

```http
POST /api/v1/public/bookings/{token}/reschedule
Content-Type: application/json

{
  "new_starts_at": "2026-01-15T14:00:00Z",
  "new_ends_at": "2026-01-15T15:00:00Z",
  "new_duration_minutes": 60
}
```

## Endpoints internos

### Scheduler tick

```http
POST /api/v1/internal/scheduler/tick
X-Internal-Token: {scheduler_tick_secret}
```

## Webhooks

### Verificación de webhook

```http
GET /api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
```

### Recibir webhook

```http
POST /api/v1/webhooks/whatsapp
X-Hub-Signature-256: sha256=...
```

## Códigos de error

| Código | Descripción |
|--------|-------------|
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 422 | Validation Error |
| 429 | Rate Limit |
| 500 | Internal Server Error |
