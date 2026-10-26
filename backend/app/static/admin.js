// Modal de activación del panel. Vive en un archivo y no inline porque la CSP
// del panel es `script-src 'self'`: un <script> embebido no se ejecutaría.
(() => {
  const modal = document.getElementById('modal-activar');
  const form = document.getElementById('form-activar');
  const nombre = document.getElementById('modal-negocio');
  const dias = document.getElementById('dias');

  if (!modal || !form) {
    return;
  }

  const abrir = () => {
    if (typeof modal.showModal === 'function') {
      modal.showModal();
    } else {
      modal.setAttribute('open', '');
    }
    if (dias) {
      dias.focus();
      dias.select();
    }
  };

  const cerrar = () => {
    if (typeof modal.close === 'function') {
      modal.close();
    } else {
      modal.removeAttribute('open');
    }
  };

  for (const boton of document.querySelectorAll('[data-activar]')) {
    boton.addEventListener('click', () => {
      const id = boton.getAttribute('data-id') || '';
      form.setAttribute('action', `/admin/businesses/${id}/activar`);
      if (nombre) {
        nombre.textContent = boton.getAttribute('data-nombre') || '';
      }
      if (dias) {
        dias.value = '30';
      }
      abrir();
    });
  }

  for (const boton of modal.querySelectorAll('[data-cerrar]')) {
    boton.addEventListener('click', cerrar);
  }

  // Cerrar al hacer clic fuera del cuadro (el backdrop es el propio dialog).
  modal.addEventListener('click', (evento) => {
    if (evento.target === modal) {
      cerrar();
    }
  });
})();
// Modal de eliminacion de negocio. Mismo patron que el de activacion: la CSP
// del panel es `script-src 'self'`, asi que vive en este archivo y no inline.
//
// La confirmacion exige escribir el slug del negocio: un click en un boton
// peligroso nunca deberia bastar para borrar reservas de clientes reales.
(() => {
  const modal = document.getElementById('modal-eliminar');
  const form = document.getElementById('form-eliminar');
  const negocio = document.getElementById('modal-eliminar-negocio');
  const slug = document.getElementById('modal-eliminar-slug');
  const input = document.getElementById('confirmar-slug');
  const submit = form ? form.querySelector('button[type="submit"]') : null;
  if (!modal || !form || !input) {
    return;
  }

  const abrir = () => {
    if (typeof modal.showModal === 'function') {
      modal.showModal();
    } else {
      modal.setAttribute('open', '');
    }
    input.focus();
  };
  const cerrar = () => {
    if (typeof modal.close === 'function') {
      modal.close();
    } else {
      modal.removeAttribute('open');
    }
  };

  for (const boton of document.querySelectorAll('[data-eliminar]')) {
    boton.addEventListener('click', () => {
      const valorSlug = boton.getAttribute('data-slug') || '';
      form.setAttribute('action', `/admin/businesses/${boton.getAttribute('data-id')}/eliminar`);
      if (negocio) {
        negocio.textContent = boton.getAttribute('data-nombre') || '';
      }
      if (slug) {
        slug.textContent = valorSlug;
      }
      // El submit arranca deshabilitado y se habilita solo cuando lo escrito
      // coincide con el slug: sin eso, "escribi el slug" es decorativo.
      if (submit) {
        submit.disabled = true;
      }
      input.value = '';
      abrir();
    });
  }

  input.addEventListener('input', () => {
    if (submit) {
      submit.disabled = input.value.trim() !== (slug ? slug.textContent : '');
    }
  });

  for (const boton of modal.querySelectorAll('[data-cerrar]')) {
    boton.addEventListener('click', cerrar);
  }
  modal.addEventListener('click', (evento) => {
    if (evento.target === modal) {
      cerrar();
    }
  });
})();
