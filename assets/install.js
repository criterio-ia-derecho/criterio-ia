// Optional installation. No service worker, storage, permissions, or telemetry.
(() => {
  'use strict';
  if (document.documentElement.dataset.mode !== 'static') return;
  const controls = Array.from(document.querySelectorAll('[data-install-control]'));
  const dialog = document.getElementById('install-dialog');
  if (!controls.length || !dialog || typeof dialog.showModal !== 'function') return;
  const closeButton = dialog.querySelector('.install-close');
  const nativeButton = document.getElementById('install-native');
  const status = document.getElementById('install-status');
  const standalone = window.matchMedia('(display-mode: standalone)');
  let deferredPrompt = null;
  let installedThisPage = false;
  let prompting = false;
  let opener = null;
  const isInstalled = () => installedThisPage || standalone.matches || navigator.standalone === true;
  const update = () => {
    controls.forEach(button => {
      button.hidden = isInstalled();
      button.disabled = prompting;
      button.setAttribute('aria-busy', String(prompting));
    });
    nativeButton.hidden = !deferredPrompt || isInstalled();
    nativeButton.disabled = prompting;
    if (isInstalled() && dialog.open) dialog.close();
  };
  const setStatus = message => {
    status.textContent = message;
    status.hidden = !message;
  };
  const showHelp = () => {
    if (dialog.open || isInstalled()) return;
    // Detection only chooses an initially open guide; all instructions remain available.
    const ua = navigator.userAgent || '';
    const ios = /iPhone|iPad|iPod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const guide = ios ? 'ios' : /SamsungBrowser/i.test(ua) ? 'samsung' : /Android/i.test(ua) ? 'android' : 'desktop';
    dialog.querySelectorAll('[data-install-guide]').forEach(details => { details.open = details.dataset.installGuide === guide; });
    dialog.showModal();
  };
  const requestInstall = async () => {
    if (prompting || isInstalled()) return;
    if (!deferredPrompt) { setStatus(''); showHelp(); return; }
    // A browser-provided event can be prompted only once, and only after a click.
    const event = deferredPrompt;
    deferredPrompt = null;
    prompting = true;
    update();
    if (dialog.open) dialog.close();
    try {
      await event.prompt();
      const choice = await event.userChoice;
      if (!isInstalled()) {
        setStatus(choice && choice.outcome === 'accepted'
          ? 'Seguí los pasos del navegador para completar la instalación.'
          : 'Podés volver a intentarlo desde el menú del navegador cuando quieras.');
        showHelp();
      }
    } catch (_) {
      setStatus('El navegador no pudo abrir la instalación. Podés usar las instrucciones de abajo.');
      showHelp();
    } finally {
      prompting = false;
      update();
    }
  };
  controls.forEach(button => button.addEventListener('click', () => {
    opener = button;
    requestInstall();
  }));
  nativeButton.addEventListener('click', requestInstall);
  closeButton.addEventListener('click', () => dialog.close());
  // Native dialog handles Escape, focus trapping and inert background content.
  // Click outside its actual bounds closes without making clicks inside destructive.
  dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    if (opener && !opener.hidden && !opener.disabled) opener.focus({ preventScroll: true });
  });
  window.addEventListener('beforeinstallprompt', event => {
    if (isInstalled() || typeof event.prompt !== 'function') return;
    event.preventDefault();
    deferredPrompt = event;
    update();
  });
  window.addEventListener('appinstalled', () => {
    installedThisPage = true;
    deferredPrompt = null;
    update();
  });
  if (standalone.addEventListener) standalone.addEventListener('change', update);
  else if (standalone.addListener) standalone.addListener(update);
  // Do not restore an open sheet after Back/Forward cache navigation.
  window.addEventListener('pageshow', event => {
    if (event.persisted && dialog.open) dialog.close();
    update();
  });
  update();
})();
