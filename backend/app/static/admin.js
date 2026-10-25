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
