# ADR-0010: `business_id` derivado del JWT, nunca del path

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §25, §4
- **Contexto en:** ARCHITECTURE.md §15.1

## Contexto

La §25 pide "protección contra IDOR". IDOR significa que un usuario autenticado accede a
un recurso que no le pertenece cambiando un identificador en la petición.

La versión habitual de ese bug tiene esta forma:

```python
@router.get("/businesses/{business_id}/bookings")
def listar(business_id: UUID, user = Depends(current_user)):
    return repo.list(business_id)     # ¿y si user pertenece a otro business?
```

El código es correcto en apariencia y el bug aparece en producción, cuando un
administrador del negocio A adivina o obtiene un UUID del negocio B y lo pasa en la URL.
El identificador es opaco, así que no se puede "adivinar", pero se puede obtener de
mil maneras: un enlace compartido sin cuidado, un log, un reporte, una respuesta de API
que lo incluye por comodidad.

En un sistema multi-tenant, cada endpoint con un `business_id` en la ruta es una
oportunidad de fuga, y el número de endpoints crece con el producto.

## Decisión

**El `business_id` de una petición autenticada sale del token, nunca de la entrada del
usuario.**

El access token lleva el tenant en el claim `tid`. Una dependencia de FastAPI lo extrae
y construye un `TenantContext` que:
1. inyecta el `SET LOCAL app.current_business_id` al abrir la transacción, y
2. se exige en todo acceso a datos.

En la API interna **no existe** ningún endpoint que acepte `business_id` como parámetro
de path, query o body:

```
GET /api/v1/services                        correcto: el tenant sale del token
GET /api/v1/services?business_id=...        no existe
```

Sobre los cuerpos de escritura, el `business_id` no aparece en los schemas de entrada:
se establece en el servidor, nunca se acepta del cliente. Eso también cierra la
puerta del mass assignment sobre el tenant.

Los dos casos legítimos que quedan:

- **Platform admin**, que sí opera sobre varios tenants. Tiene su propio espacio de
  rutas (`/api/v1/platform/*`), su propio guard y su propia tabla de usuarios
  (`platform_users`, sin `business_id`). Ahí el `business_id` sí viene de la ruta,
  porque es un acceso privilegiado explícito y auditado.
- **API pública**, que resuelve por **slug** o por **secure_token**, nunca por
  `business_id`. El slug es un identificador público por diseño (§5), y el token
  resuelve una reserva concreta.

## Alternativas consideradas

### `business_id` en la ruta con verificación posterior — descartada

Es el patrón por defecto de la mayoría de APIs multi-tenant, y es correcto **si** cada
handler verifica. El problema es que la verificación es por handler, y con cientos de
endpoints significa cientos de oportunidades de olvidarse. Un handler sin la línea de
verificación es una fuga silenciosa que ningún test funcional va a delatar, porque la
funcionalidad "funciona".

Escribir el código para que sea imposible equivocarse es mejor que escribir el código
para que equivocarse sea improbable.

### Tenant implícito por sesión — descartada

Un cookie de sesión firmaría el tenant implícitamente. Es válido, pero oculta la
decisión: el código de aplicación deja de mostrar en qué tenant opera, y el
`business_id` se convierte en un parámetro oculto en todas partes, exactamente igual
que un parámetro de ruta pero menos visible. El claim explícito `tid` es más auditable.

### Un token por tenant, con el tenant en la URL del token — descartada

Complicaría la gestión de un usuario con acceso a varios negocios y no agrega nada:
el token ya está firmado, el claim no agrega un ataque.

### Validar el `business_id` en un middleware global — descartada

Un middleware no sabe qué recurso se está accediendo, así que solo podría comparar el
path con el token, lo que rompe en cuanto hay rutas anidadas y subrecursos. La
verificación tiene que resignar donde la autorización.

## Consecuencias

**Positivas**

- IDOR eliminado de raíz en la API interna, no mitigado.
- Cada handler es más corto: no repite la comparación de tenant.
- El código muestra en qué tenant opera, porque el `TenantContext` está en la firma.
- Un atacante que manipule cualquier campo de la petición no cambia de tenant, porque
  ningún campo de la petición se usa para determinarlo.

**Negativas / costos aceptados**

- Los endpoints de la API interna **no son direccionables por su recurso global**. No
  se puede hacer `GET /api/v1/businesses/{id}/services` para un negocio arbitrario. Es
  exactamente el objetivo, y tiene un costo: cualquier cliente de la plataforma que
  necesite operar cross-tenant debe usar el espacio de platform, que es auditado.
- Los tests tienen que construir un token por tenant. Se resuelve con una fixture de
  token.
- La ergonomicidad para desarrollo se pierde un poco: no se puede abrir la API y
  consultar un negocio sin tener un token. Se compensa con un seed de datos de
  desarrollo.
- Cuidado con la caché: cualquier caché de respuestas debe incluir el `tid` en la clave,
  o un tenant ve la respuesta de otro. La caché de disponibilidad lo hace por diseño.

**Revisar si**

- Un cliente institucional (por ejemplo, una cadena de franchicias) necesita operar
  sobre varios negocios con un mismo usuario. Es un caso distinto de platform admin:
  sería un rol nuevo, con su propio ADR.
