/* ══════════════════════════════════════════════════════════
   SUYLIOS DOWNLOADER — Hybrid IPC Bridge Adapter
   Supports Tauri v2 (`__TAURI_INTERNALS__`) & PyWebView (`pywebview.api`)
   ══════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  function isTauri() {
    return Boolean(window.__TAURI_INTERNALS__ || (window.__TAURI__ && window.__TAURI__.core));
  }

  function getTauriInvoke() {
    if (window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.invoke === 'function') {
      return window.__TAURI__.core.invoke;
    }
    if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === 'function') {
      return window.__TAURI_INTERNALS__.invoke;
    }
    return null;
  }

  function convertPreviewSrc(urlOrPath) {
    if (!urlOrPath) return '';
    if (isTauri()) {
      const cleanPath = String(urlOrPath).replace(/^file:\/\/\/?/i, '');
      if (window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.convertFileSrc === 'function') {
        return window.__TAURI__.core.convertFileSrc(cleanPath);
      }
    }
    return urlOrPath;
  }

  const tauriProxy = new Proxy(
    {},
    {
      get(_target, prop) {
        if (typeof prop !== 'string') return undefined;
        return async (...args) => {
          const invoke = getTauriInvoke();
          if (!invoke) return null;
          const payload = args.length === 1 && typeof args[0] === 'object' && args[0] !== null
            ? args[0]
            : { args };
          return await invoke(prop, payload);
        };
      },
    }
  );

  window.SuyliosBridge = {
    isTauri,
    convertPreviewSrc,
    getApi() {
      if (window.pywebview && window.pywebview.api) {
        return window.pywebview.api;
      }
      if (isTauri()) {
        return tauriProxy;
      }
      return null;
    },
    onEvent(eventName, handler) {
      if (isTauri() && window.__TAURI__ && window.__TAURI__.event && typeof window.__TAURI__.event.listen === 'function') {
        return window.__TAURI__.event.listen(eventName, (ev) => handler(ev.payload));
      }
      const domHandler = (e) => handler(e.detail);
      window.addEventListener(eventName, domHandler);
      return Promise.resolve(() => window.removeEventListener(eventName, domHandler));
    },
  };
})();
