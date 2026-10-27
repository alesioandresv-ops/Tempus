import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { MarcaTempus } from '@/components/MarcaTempus'

/**
 * Paginas legales de Tempus (Terminos y Condiciones / Politica de Privacidad).
 *
 * Un solo componente con dos variantes porque las dos comparten estructura:
 * fondo oscuro, contenedor angosto, tipografia y volver. El contenido es
 * estatico a proposito: no hay CMS ni fetch, y actualizarlo es un commit.
 */

/** Datos del responsable (mismo criterio que la landing: constantes arriba). */
export const RESPONSABLE = {
  nombre: 'Vieira Nogueira Alesio Andrés',
  cuil: '20-43.275.996-3',
  direccion: '5ta Sección Ombucito, Chacra 155, Paso de los Libres, Corrientes, Argentina',
  whatsapp: '+54 9 3772 468309',
  whatsappLink: 'https://wa.me/5493772468309',
  instagram: 'https://www.instagram.com/tempus.app',
  email: 'soportetempusapp@gmail.com',
}

type TipoDocumento = 'terminos' | 'privacidad'

interface Props {
  tipo: TipoDocumento
}

function Seccion({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="font-display text-lg font-semibold text-white">{titulo}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-slate-300">{children}</div>
    </section>
  )
}

export default function LegalPage({ tipo }: Props) {
  const esTerminos = tipo === 'terminos'

  return (
    <div className="min-h-screen bg-[#070B1A] font-inter text-slate-100">
      <div className="mx-auto w-full max-w-3xl px-6 py-12">
        {/* Encabezado */}
        <div className="flex flex-col gap-6">
          <Link
            to="/"
            className="inline-flex w-fit items-center gap-2 text-sm text-slate-400 transition-colors hover:text-white"
          >
            <ArrowLeft className="h-4 w-4" />
            Volver
          </Link>
          <div className="flex items-center gap-4">
            <MarcaTempus size={44} />
            <div>
              <h1 className="font-display text-2xl font-semibold tracking-tight text-white">
                {esTerminos ? 'Términos y Condiciones' : 'Política de Privacidad'}
              </h1>
              <p className="mt-1 text-sm text-slate-400">
                Tempus · Última actualización: 27 de octubre de 2026
              </p>
            </div>
          </div>
        </div>

        {/* Contenido */}
        <div className="mt-10 space-y-8">
          {esTerminos ? (
            <>
          {/* ================================================== INTRO */}
          <Seccion titulo="1. Quiénes somos">
            <p>
              Tempus es una plataforma digital de gestión de turnos y reservas online para
              peluquerías, barberías, centros de estética y negocios afines, operada por{' '}
              <strong className="text-white">{RESPONSABLE.nombre}</strong> (CUIT/CUIL{' '}
              {RESPONSABLE.cuil}), con domicilio en {RESPONSABLE.direccion} (en adelante, «Tempus»,
              «nosotros» o «el operador»).
            </p>
            <p>
              Estos términos regulan el uso de la plataforma Tempus, su sitio web, el panel de
              administración de negocios y las páginas públicas de reservas de cada negocio. Al
              crear una cuenta, el titular del negocio («el Profesional» o «el Usuario») declara
              haber leído, entendido y aceptado íntegramente estos términos.
            </p>
          </Seccion>

          {/* ================================================== SERVICIO */}
          <Seccion titulo="2. Descripción del servicio">
            <p>Tempus provee a cada negocio que se registra:</p>
            <ul className="list-disc space-y-1.5 pl-5">
              <li>Una página pública de reservas con URL propia y código QR.</li>
              <li>Gestión de servicios, profesionales, horarios y turnos.</li>
              <li>Recordatorios automáticos de turnos por WhatsApp.</li>
              <li>
                Cobros y señas a través de la cuenta de Mercado Pago propia de cada negocio (la
                plataforma no retiene fondos).
              </li>
              <li>Reportes de actividad dentro del panel.</li>
            </ul>
            <p>
              El servicio no incluye asesoramiento legal, contable, médico ni de ningún otro tipo.
              Tempus es una herramienta de organización de agendas y comunicación: el contenido de
              los turnos, los precios y las condiciones de cada prestación son responsabilidad
              exclusiva del negocio.
            </p>
          </Seccion>

          {/* ================================================== CUENTA */}
          <Seccion titulo="3. Creación de cuenta y condiciones de uso">
            <p>
              Para usar Tempus es necesario crear una cuenta con nombre del negocio, URL, nombre del
              titular, correo electrónico y contraseña. El Usuario se compromete a:
            </p>
            <ul className="list-disc space-y-1.5 pl-5">
              <li>Proveer información veraz, completa y actualizada.</li>
              <li>
                Mantener la confidencialidad de su contraseña y de toda actividad que ocurra bajo su
                cuenta.
              </li>
              <li>
                No usar la plataforma para fines ilícitos, engañosos, difamatorios, discriminatorios
                o que vulneren derechos de terceros.
              </li>
              <li>
                No intentar acceder a datos de otros negocios, vulnerar la seguridad de la
                plataforma, automatizar el scraping del servicio o revenderlo sin autorización.
              </li>
              <li>
                Ser el titular o contar con autorización para operar el negocio que registra.
              </li>
            </ul>
            <p>
              Tempus puede suspender o cerrar cuentas que incumplan estas reglas, con aviso razonable
              salvo que la gravedad del hecho justifique la suspensión inmediata.
            </p>
          </Seccion>

          {/* ================================================== PLANES Y PAGOS */}
          <Seccion titulo="4. Planes, precios y facturación">
            <p>Tempus ofrece dos planes:</p>
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong className="text-white">Básico (gratis):</strong> incluye un período de prueba
                de 14 días sin costo ni tarjeta de crédito. Vencido el período, la cuenta queda
                pausada para escrituras (cargas de turnos, servicios y cambios) hasta activar el plan
                Pro; la lectura de la agenda sigue disponible.
              </li>
              <li>
                <strong className="text-white">Pro:</strong> suscripción mensual de $ 45.000 (ARS),
                que habilita recordatorios por WhatsApp, reportes avanzados y demás funciones
                premium.
              </li>
            </ul>
            <p>
              <strong className="text-white">Forma de pago del plan Pro:</strong> transferencia
              bancaria a la cuenta del operador (banco Naranja X, CBU/CVU 4530000800010461169871,
              alias Tempus.app, titular Yesica Soledad Medina). El pago se registra desde el checkout
              de la plataforma enviando el comprobante por WhatsApp; la activación es manual y se
              realiza en un plazo objetivo de hasta 2 horas hábiles desde la verificación del
              comprobante.
            </p>
            <p>
              El plan Pro se activa por períodos de 30 días corridos desde la aprobación del pago. La
              baja es voluntaria, sin penalidad: el negocio simplemente no renueva y vuelve al plan
              Básico pausado al vencer el período pagado. Los precios pueden actualizarse con aviso
              previo de al menos 30 días por los canales de contacto (email, WhatsApp o Instagram).
            </p>
            <p>
              Tempus no almacena datos de tarjetas de crédito ni realiza retenciones de fondos de los
              cobros que el negocio hace a sus propios clientes (señas vía Mercado Pago): esas
              transacciones son directas entre el negocio y su cliente.
            </p>
          </Seccion>

          {/* ================================================== WHATSAPP */}
          <Seccion titulo="5. Recordatorios por WhatsApp">
            <p>
              La función de recordatorios por WhatsApp es parte del plan Pro. El negocio que la
              activa autoriza a Tempus a enviar mensajes a los números de teléfono que los propios
              clientes cargan al reservar (confirmación, recordatorio 2 horas antes, recordatorio 1
              hora antes, cancelación y reprogramación).
            </p>
            <p>
              El negocio declara contar con consentimiento de sus clientes para recibir estas
              notificaciones. Los mensajes de recordatorio son de servicio: no incluyen publicidad ni
              contenido de terceros. El negocio puede darse de baja de la función en cualquier
              momento desde el panel.
            </p>
          </Seccion>

          {/* ================================================== RESPONSABILIDAD */}
          <Seccion titulo="6. Limitación de responsabilidad">
            <p>
              Tempus se provee «tal cual» y «según disponibilidad». Trabajamos con estándares altos
              de disponibilidad y seguridad, pero no garantizamos un funcionamiento ininterrumpido o
              libre de errores: pueden existir interrupciones por mantenimiento, fallas de
              infraestructura o de servicios de terceros (WhatsApp/Meta, Mercado Pago, proveedor de
              base de datos, hosting).
            </p>
            <p>
              En la máxima medida permitida por la ley, Tempus no será responsable por:
            </p>
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                Lucro cesante o pérdida de clientes derivada de la indisponibilidad del servicio.
              </li>
              <li>Turnos perdidos, duplicados o mal cargados por el propio Usuario.</li>
              <li>
                El contenido de los servicios que el negocio presta, ni la relación comercial entre
                el negocio y sus clientes.
              </li>
              <li>
                Fallas imputables a terceros (WhatsApp, Mercado Pago, registradores de dominio,
                proveedores de infraestructura).
              </li>
            </ul>
          </Seccion>

          {/* ================================================== SUSPENSION */}
          <Seccion titulo="7. Suspensión y finalización">
            <p>
              El Usuario puede dar de baja su cuenta en cualquier momento solicitándolo por los
              canales de soporte ({RESPONSABLE.email} o {RESPONSABLE.whatsapp}). Tempus puede
              suspender o cancelar cuentas por incumplimiento de estos términos, uso abusivo de la
              plataforma o falta de pago del plan Pro.
            </p>
            <p>
              Producida la baja, los datos del negocio se conservan 90 días para permitir
              reactivación, y luego se eliminan de forma definitiva.
            </p>
          </Seccion>

        {/* ================================================== CAMBIOS */}
        <Seccion titulo="8. Modificaciones">
          <p>
            Estos términos pueden actualizarse. Los cambios sustanciales se comunican por email o
            por los canales de soporte con al menos 15 días de anticipación. Seguir usando la
            plataforma después de publicado un cambio implica aceptarlo.
          </p>
        </Seccion>

        {/* ================================================== DERECHO */}
        <Seccion titulo="9. Ley aplicable y jurisdicción">
          <p>
            Estos términos se rigen por las leyes de la República Argentina. Para cualquier
            controversia, las partes se someten a los tribunales ordinarios del Departamento Paso
            de los Libres, Provincia de Corrientes, con renuncia expresa a cualquier otro fuero o
            jurisdicción que pudiera corresponderles.
          </p>
        </Seccion>

        {/* ================================================== CONTACTO */}
        <Seccion titulo="10. Contacto">
          <p>Para consultas sobre estos términos:</p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              WhatsApp:{' '}
              <a
                href={RESPONSABLE.whatsappLink}
                target="_blank"
                rel="noopener noreferrer"
                className="text-violet-300 hover:text-violet-200"
              >
                {RESPONSABLE.whatsapp}
              </a>
            </li>
            <li>
              Email:{' '}
              <a
                href={`mailto:${RESPONSABLE.email}`}
                className="text-violet-300 hover:text-violet-200"
              >
                {RESPONSABLE.email}
              </a>
            </li>
            <li>
              Instagram:{' '}
              <a
                href={RESPONSABLE.instagram}
                hrefLang="es"
                className="text-violet-300 hover:text-violet-200"
              >
                @tempus.app
              </a>
            </li>
          </ul>
        </Seccion>
            </>
          ) : (
            <>
              {/* ================================================ PRIVACIDAD */}
              <Seccion titulo="1. Responsable del tratamiento">
                <p>
                  El responsable del tratamiento de los datos de Tempus es{' '}
                  <strong className="text-white">{RESPONSABLE.nombre}</strong> (CUIT/CUIL{' '}
                  {RESPONSABLE.cuil}), con domicilio en {RESPONSABLE.direccion}. Para ejercer tus
                  derechos (acceso, rectificación, actualización, supresión) escribinos a{' '}
                  {RESPONSABLE.email} o por WhatsApp al {RESPONSABLE.whatsapp}.
                </p>
              </Seccion>

              <Seccion titulo="2. Datos que recopilamos">
                <p>Tempus recopila dos categorías de datos:</p>
                <p>
                  <strong className="text-white">Del titular del negocio:</strong> nombre del
                  negocio, URL, nombre del titular, correo electrónico, contraseña (almacenada
                  hasheada, nunca en texto plano), zona horaria y horarios de atención.
                </p>
                <p>
                  <strong className="text-white">De los clientes del negocio:</strong> al reservar
                  turno, nombre, número de teléfono (para confirmaciones y recordatorios) y, si el
                  negocio carga turnos manuales, cualquier nota que el propio negocio cargue. La
                  plataforma no recopila datos de tarjetas de crédito ni datos sensibles.
                </p>
              </Seccion>

              <Seccion titulo="3. Para qué los usamos">
                <p>Los datos se usan exclusivamente para:</p>
                <ul className="list-disc space-y-1.5 pl-5">
                  <li>Prestar el servicio de gestión de turnos del negocio.</li>
                  <li>Enviar confirmaciones, recordatorios y avisos de turno al cliente.</li>
                  <li>Operar la cuenta del titular (recuperación de contraseña, soporte).</li>
                  <li>Procesar el pago del plan Pro (comprobante por WhatsApp/transferencia).</li>
                  <li>Prevenir abuso de la plataforma y asegurar su disponibilidad.</li>
                </ul>
                <p>
                  Tempus no vende, alquila ni cede datos personales a terceros con fines
                  publicitarios. Los únicos servicios de terceros con los que interactuamos son
                  WhatsApp/Meta (envío de recordatorios), Naranja X (cuenta bancaria del operador) y
                  Mercado Pago (señas que el negocio cobra directamente: Tempus no participa en esas
                  transacciones).
                </p>
              </Seccion>

              <Seccion titulo="4. Dónde se almacenan y por cuánto tiempo">
                <p>
                  Los datos se almacenan en servidores en la nube con acceso restringido al operador.
                  La contraseña del titular se guarda hasheada. Los datos del negocio se conservan
                  mientras la cuenta esté activa y 90 días después de la baja, para permitir
                  reactivación; vencido ese plazo se eliminan de forma definitiva.
                </p>
              </Seccion>

              <Seccion titulo="5. Tus derechos">
                <p>
                  Cualquier persona cuyos datos estén en Tempus (titular del negocio o cliente que
                  reservó turno) puede solicitar: acceso a sus datos, rectificación, actualización,
                  supresión, o la eliminación completa de su cuenta y turnos. Basta con escribirnos a{' '}
                  {RESPONSABLE.email} o por WhatsApp al {RESPONSABLE.whatsapp}, identificándote. La
                  solicitud se procesa en un plazo objetivo de 48 horas hábiles.
                </p>
                <p>
                  Los clientes de los negocios (no titulares) también pueden pedir que se elimine su
                  número de teléfono y turnos de la página de un negocio; se procesa igual que las
                  demás solicitudes.
                </p>
              </Seccion>

              <Seccion titulo="6. Cookies y analíticas">
                <p>
                  Tempus no usa cookies publicitarias ni de seguimiento. Se usan cookies técnicas de
                  sesión (mantener la cuenta abierta) y el servicio de verificación anti-bot de
                  Cloudflare Turnstile en el registro y reservas. No hay Google Analytics ni
                  herramientas de analíticas de terceros.
                </p>
              </Seccion>

              <Seccion titulo="7. Menores">
                <p>
                  Tempus está dirigida a negocios y titulares mayores de 18 años. No recopilamos
                  intencionalmente datos de menores de edad; si detectamos uno, lo eliminamos.
                </p>
              </Seccion>

              <Seccion titulo="8. Cambios y contacto">
                <p>
                  Esta política puede actualizarse; los cambios se publican en esta página. Para
                  consultas de privacidad: {RESPONSABLE.email}, WhatsApp {RESPONSABLE.whatsapp} o
                  Instagram{' '}
                  <a
                    href={RESPONSABLE.instagram}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-violet-300 hover:text-violet-200"
                  >
                    @tempus.app
                  </a>
                </p>
              </Seccion>
            </>
          )}
        </div>

        <p className="mt-12 border-t border-white/10 pt-6 text-xs text-slate-500">
          Documento legal de Tempus — Plataforma de gestión de turnos. Versión 1.0, vigente desde el
          27 de octubre de 2026.
        </p>
      </div>
    </div>
  )
}
