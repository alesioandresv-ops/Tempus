# ADR-0009: Secure tokens opacos almacenados hasheados

- **Estado:** Aceptada
- **Fecha:** 2026-09-28
- **Referencia:** PROJECT_MASTER §7, §16, §17, §37
- **Contexto en:** [ARCHITECTURE.md §11](../../ARCHITECTURE.md#11-secure-tokens)

## Contexto

La §6 es categórica: el cliente no crea cuenta, no tiene email, contraseña ni login.
La §7 pide que cada reserva tenga un `secure_token` que sea criptográficamente seguro,
impredecible, no secuencial, no derivado del `booking_id` y apropiado para acciones sin
autenticación.

Ese token es la **única credencial** que tiene un cliente para gestionar su reserva. Es
la parte del sistema con menos defensa alrededor, porque el usuario final no tiene
nada: no hay segundo factor, no hay sesión, no hay recuperación. Si el token se
compromete, el atacante puede ver, cancelar y reprogramar la reserva de otra persona. Y
el caso 10 del §37 exige que un token inválido sea rechazado.

## Decisión

- **Generación:** `secrets.token_urlsafe(32)`, es decir 256 bits del CSPRNG del sistema
  operativo. No es JWT, no es derivable, no se construye a partir de `booking_id`.
- **Almacenamiento:** en la base solo su **SHA-256**, en `bookings.secure_token_hash`
  (`bytea`, `UNIQUE`). El token en claro **nunca se persiste**.
- **Resolución:** las rutas de gestión buscan por hash con una consulta exacta sobre el
  índice único. Con 256 bits de entropía, el tiempo de respuesta no filtra información
  relevante.
- **URL:** `https://dominio/r/<token>`. **No existe ningún endpoint público de gestión
  que acepte un `booking_id`.**

Eso último es lo que resuelve el caso 9 del §37 por construcción y no por validación:
no hay ninguna ruta donde la manipulación de un identificador pueda hacer algo, porque
ninguna ruta acepta identificadores. No es una validación que se pueda olvidar; es una
ruta que no existe.

- **Alcance:** el token da acceso a **su propia** reserva y solo a operaciones de
  cliente (ver, cancelar, reprogramar). No da acceso a la agenda, a otros clientes ni a
  nada del panel. La autorización se re-deriva del recurso, no del token.
- **Expiración:** no expira. El motivo está en
  [ARCHITECTURE.md §11](../../ARCHITECTURE.md#11-secure-tokens), y existe
  `bookings.token_rotated_at` para la invalidación manual por el admin.
- **Comparación de secretos** siempre en tiempo constante (`compare_digest`), aunque
  la búsqueda por hash en un índice no lo exija estrictamente.

## Alternativas consideradas

### Token derivado de `booking_id` con HMAC — descartada

Determinista, lo que permitiría al cliente "recuperar" su enlace. Descartada porque
significa que el identificador de la reserva es un secreto, y el identificador ya viaja
en los logs, en las URLs internas y en las relaciones. Un secreto que aparece en el
log no es un secreto.

### JWT firmado con el `booking_id` en el payload — descartada

Cómodo de generar y verificable sin base de datos. Se descarta por dos motivos: el
token no se puede revocar (necesitamos poder invalidar ante una queja de privacidad, y
necesitamos que la cancelación de la reserva sea verificable en el servidor), y anybody
que consiga el token puede leer su contenido. Un token opaco no revela nada.

### Guardar el token en claro en la base — descartada

Simplifica la comparación. Se descarta porque convierte una fuga de la base (un backup
filtrado, un dump en soporte, un `SELECT` sin filtro mal escrito) en un robo masivo de
reservas de todos los tenants. El costo de hashear es una columna `bytea` y un hash por
petición, que es despreciable.

### Expiración del token (por ejemplo 90 días) — descartada

Parece más segura y en la práctica rompe la experiencia: alguien que quiere consultar
una cita de hace seis meses no puede, y el negocio recibe un mensaje de soporte. Y no
aporta seguridad real, porque la entropía de 256 bits ya hace que el token sea
imposible de adivinar. Lo que sí protege —que alguien que tuvo acceso a un token viejo
no lo conserve para siempre— se resuelve mejor con rotación explícita bajo demanda que
con un vencimiento que castiga al usuario legítimo.

### Token como secuencia aleatoria corta (8 caracteres) — descartada

8 caracteres alfanuméricos son unos 47 bits, que se pueden enumerar: a 1000 intentos por
segundo, unos 10 millones de tokens se agotan en menos de tres horas. La §37 exige que
un intento con token inválido sea rechazado, y eso no se puede garantizar con un espacio
de búsqueda razonable. 256 bits no se pueden enumerar.

## Consecuencias

**Positivas**

- Una fuga de la base de datos no entrega ninguna capacidad de gestión de reservas.
- El caso 9 del §37 está cubierto por diseño, no por validación.
- El token no expone información: no se puede leer el `booking_id`, el nombre del
  cliente ni el horario sin consultarlo.
- El token se puede rotar e invalidar de forma selectiva.

**Negativas / costos aceptados**

- Cada petición de gestión hace un `SELECT` por hash. Con el índice único es un acceso
  de costo constante, aceptable para el volumen esperado. Y esas rutas ya pasan por el
  rate limiting (§10.5).
- El cliente tiene que conservar el enlace. Si lo pierde, no puede recuperar la reserva
  sin ayuda del negocio. **Es una consecuencia directa e inevitable de la §6**: sin
  cuenta no hay recuperación. Se mitiga enviando el enlace en el WhatsApp de
  confirmación, y el panel del negocio puede reenviarlo.
- El token en claro existe en la URL del navegador, con el riesgo de que quede en el
  historial o en un `Referer` si el usuario navega a un tercero desde esa página. Mitigación:
  la página de gestión no carga recursos externos, y la política de `Referer-Policy` es
  `no-referrer` en esas rutas.
- No hay expiración automática, así que un token robado sirve indefinidamente hasta que
  el admin lo rote. Aceptado: es el precio de no invalidar la experiencia del cliente.

**Revisar si**

- Un requisito regulatorio exige que los datos del cliente se purguen al cabo de un
  plazo. Ahí el token, que es un dato personal vinculado, entra en el cálculo de
  retención.
- Aparece un canal de gestión que requiera credenciales de larga duración (por ejemplo
  una app nativa del cliente). Eso ya no sería la §6, y sería un ADR nuevo.
