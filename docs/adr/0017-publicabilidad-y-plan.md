# ADR-0017: Estado administrativo y publicabilidad por plan y vigencia

- **Estado:** Aceptada
- **Fecha:** 2026-10-16
- **Referencia:** PROJECT_MASTER §2 (Producto), §33 (Dinero)
- **Contexto en:** ARCHITECTURE.md §5 (modelo de datos), §19 (roadmap)
- **Migración:** `0024_activar_al_alta`

## Contexto

`businesses.status` empezó con un único significado: `active` publicaba y cualquier otro
valor no. El alta nacía en el default de la columna, `trial`, así que un negocio recién
creado por `/register` **no era visible** hasta que algo lo pasaba a `active` a mano.

Al aparecer el plan Pro y el mes gratis, la publicabilidad pasó a depender de dos cosas
distintas: el **estado administrativo** (¿la plataforma suspendió este negocio?) y la
**regla de negocio** (¿tiene plan Pro, o sigue dentro del mes gratis?). Ese estado quedó
repartido en tres lugares que lo decidían por su cuenta:

- `Business.is_bookable` (modelo): miraba solo `status == 'active'`.
- El resolver público `_resolve_business_by_slug`: repetía `status == 'active'` en su
  propio `SELECT`.
- `service.es_negocio_reservable`: delegaba en el modelo.

Tres lugares decidiendo lo mismo es tener tres oportunidades de que digan cosas
distintas: el panel y la página pública podrían terminar habilitando negocios
diferentes. Y sobrecargar `status` con el vencimiento del mes gratis obligaría a la
columna a mentir el día que un negocio suspendido tenga Pro vigente.

## Decisión

**Una sola función decide la publicabilidad.**
`app.modules.businesses.publicacion.es_publicable(business)`:

```
publicable     = status == active  Y  (plan == pro  O  ahora < fin_del_trial)
fin_del_trial  = max(created_at, trial_grace_from) + trial_dias
```

`status` queda como **estado administrativo** (suspender / quitar de circulación) y el
plan + el tiempo como **regla de negocio**. Ninguna de las dos pisa a la otra: un
suspendido no se publica aunque tenga Pro, y un Pro vigente no rescata a un suspendido.

**El alta nace publicable.** La migración `0024_activar_al_alta` cambia
`tempus_create_business` para que el negocio se cree con `status = 'active'` (antes el
default `trial`). El downgrade lo devuelve a `trial`.

**El piso del trial** es `max(created_at, trial_grace_from)`. `trial_grace_from` default
es epoch (contar desde el alta de cada negocio) y está pensado para fijarse a la fecha de
despliegue, de modo que los negocios que ya existían no venzan el primer día.
`TRIAL_DIAS` default es 30 (días corridos).

**La guardia del panel corta escrituras, no lecturas.**
`app.api.routers.business.gating.exigir_plan_vigente` levanta **402**
(`code="plan_vencido"`) cuando el mes gratis venció. Corta por **método HTTP**
(`POST`/`PUT`/`PATCH`/`DELETE`), no por una lista de rutas: un endpoint de escritura
nuevo queda gateado solo. Las lecturas (`GET`/`HEAD`/`OPTIONS`) pasan siempre—un mes
vencido no es una sesión inválida. Los endpoints de suscripción viven en un
`billing_router` aparte, sin la guardia, porque un negocio vencido tiene que poder pagar
(y `activar` es una escritura).

La guardia usa `mes_gratis_vencido` (la mitad que **no** mira `status`), no
`es_publicable`: a un negocio suspendido con Pro vigente no se le puede decir "tu mes
gratis venció", que sería mentira.

## Consecuencias

### Positivas

- Un negocio recién dado de alta es visible y reservable sin intervención manual.
- Un solo lugar conoce la regla; el modelo, el servicio y el resolver público la llaman,
  ninguno la reimplementa.
- `status` sigue significando lo que dice: suspender un negocio no depende de su plan.
- El corte del mes gratis no puede desincronizarse entre el panel y la página pública: se
  calcula una vez.
- Un negocio vencido puede seguir leyendo y pagar; no queda encerrado sin salida.

### Negativas / costos aceptados

- Aparece un estado "puede leer, no escribir" que el frontend tiene que representar
  (`frontend/src/pages/admin/BloqueoVencimiento.tsx`).
- El `402` convive con el `403` (permisos): el frontend tiene que distinguirlos. Se
  eligió 402 justamente para que pueda.
- `billing_router` es un segundo router con el mismo prefijo `/business`: hay que
  recordar a dónde va cada endpoint nuevo (escritura de negocio → `router`; facturación →
  `billing_router`). La lista de rutas de cada uno la fija el diseño, no una convención.

## Alternativas consideradas

### A) Mantener el alta en `trial` y publicar por tiempo

Dejar el default de la columna en `trial` y que la regla tratara `trial` como publicable
dentro de la ventana. Descarte: hace que `trial` signifique dos cosas
(estado administrativo *y* "mes gratis" corriendo) y reintroduce la ambigüedad que este
ADR elimina. El estado del negocio y su plan son dos ejes; el valor de `status` no debe
cargar el segundo.

### B) Cortar por lista de rutas exentas en vez de por método HTTP

Descarte: una lista de rutas permitidas se desactualiza con cada endpoint nuevo, y el
error es silencioso (una escritura nueva sin gate). El método HTTP no se olvida.

### C) Bloquear también la lectura del panel

Descarte: dejaría al dueño sin poder ver qué pasa ni llegar a la pantalla de pago. Un mes
vencido no es una sesión inválida.

### D) Usar `es_publicable` en la guardia del panel

Descarte: `es_publicable` incluye `status`, así que a un negocio suspendido le mostraría
el mensaje "tu mes gratis venció" (falso) y, peor, un suspendido **dentro** del mes
quedaría con escritura abierta. La guardia solo debe mirar la mitad de plan y tiempo.

## Verificación

- `tests/unit/test_publicacion.py`: la regla pura (status, plan, piso del trial,
  `dias_restantes`).
- `tests/integration/test_plan_vencido.py`: lectura y `/suscripcion` sin gate; escritura
  → 402 con `code`, `trial_ends_at`, `dias_restantes`, `checkout_url`; Pro no bloqueado.
- `verificar_limites.py` → 13/13 y `verificar_produccion.py` → 15/15 contra head `0024`.

## Revisar si

- Aparece un segundo plan de pago: la comparación `plan == pro` deja de alcanzar y hay
  que generalizarla.
- Se quiere cortar también la lectura del panel: revisar C.
- El negocio necesita un estado propio más allá de suspender/publicar (por ejemplo
  `paused`).
