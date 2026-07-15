/* ══════════════════════════════════════════════════════════
   SUYLIOS DOWNLOADER — Application Logic
   Vanilla JS — No frameworks
   ══════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ─── STATE ───
  const state = {
    downloads: new Map(),   // task_id -> download object
    currentPage: 'main',
    currentSettingsTab: 'general',
    apiReady: false,
    pollInterval: null,
    settings: null,
  };

  // ─── DOM CACHE ───
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => ctx.querySelectorAll(sel);

  const dom = {};

  function cacheDom() {
    dom.titlebar = $('#titlebar');
    dom.btnMinimize = $('#btn-minimize');
    dom.btnMaximize = $('#btn-maximize');
    dom.btnClose = $('#btn-close');
    dom.navBtns = $$('.titlebar-nav-btn');

    dom.loadingOverlay = $('#loading-overlay');
    dom.appContainer = $('#app-container');

    dom.pageMain = $('#page-main');
    dom.pageSettings = $('#page-settings');
    dom.pageHistory = $('#page-history');
    dom.pageScheduled = $('#page-scheduled');
    dom.pageConverter = $('#page-converter');

    dom.clipboardBanner = $('#clipboard-banner');
    dom.historyList = $('#history-list');
    dom.historyEmpty = $('#history-empty');
    dom.scheduledList = $('#scheduled-list');
    dom.scheduledEmpty = $('#scheduled-empty');
    dom.scheduledBadge = $('#scheduled-badge');
    dom.btnClearHistory = $('#btn-clear-history');

    dom.urlInput = $('#url-input');
    dom.btnPaste = $('#btn-paste');
    dom.formatSelect = $('#format-select');
    dom.qualitySelect = $('#quality-select');
    dom.btnDownload = $('#btn-download');
    dom.urlContainer = $('.url-input-container');

    dom.emptyState = $('#empty-state');
    dom.downloadList = $('#download-list');

    dom.settingsNavItems = $$('.settings-nav-item');
    dom.settingsTabs = $$('.settings-tab');

    dom.statusActive = $('#status-active');
    dom.statusSpeed = $('#status-speed');
    dom.statusStorage = $('#status-storage');
    dom.statusDot = $('.status-dot');

    dom.toastContainer = $('#toast-container');
    dom.cardTemplate = $('#download-card-template');

    // Settings controls
    dom.settingConcurrent = $('#setting-concurrent');
    dom.concurrentValue = $('#concurrent-value');
    dom.btnBrowseFolder = $('#btn-browse-folder');
    dom.btnBrowseFFmpeg = $('#btn-browse-ffmpeg');
    dom.btnClearCache = $('#btn-clear-cache');
    dom.btnResetSettings = $('#btn-reset-settings');
    dom.settingDownloadPath = $('#setting-download-path');
  }

  // ─── BRIDGE HELPERS ───
  function getApi() {
    return window.pywebview && window.pywebview.api;
  }

  async function callApi(method, ...args) {
    const api = getApi();
    if (!api) {
      console.warn(`[Suylios] API not ready, cannot call: ${method}`);
      return null;
    }
    if (typeof api[method] !== 'function') {
      console.warn(`[Suylios] API method not found: ${method}`);
      return null;
    }
    try {
      return await api[method](...args);
    } catch (err) {
      console.error(`[Suylios] API error (${method}):`, err);
      return null;
    }
  }

  // Custom confirm modal helper
  let confirmActive = false;
  function customConfirm(arg1, arg2 = 'Onay', arg3 = 'Tamam', arg4 = false) {
    if (confirmActive || window._confirmActive) return Promise.resolve(false);
    confirmActive = true;
    window._confirmActive = true;

    let title = 'Onay';
    let message = '';
    let okText = 'Tamam';
    let isDanger = false;

    if (typeof arg1 === 'string' && typeof arg2 === 'string') {
      if (arg2.includes('?') || arg2.length > arg1.length) {
        title = arg1;
        message = arg2;
      } else {
        message = arg1;
        title = arg2;
      }
    } else {
      message = arg1;
    }

    if (typeof arg3 === 'boolean' && typeof arg4 === 'string') {
      isDanger = arg3;
      okText = arg4;
    } else if (typeof arg3 === 'string' && typeof arg4 === 'boolean') {
      okText = arg3;
      isDanger = arg4;
    } else if (typeof arg3 === 'boolean') {
      isDanger = arg3;
    } else if (typeof arg3 === 'string') {
      okText = arg3;
    }

    return new Promise((resolve) => {
      let modal = document.getElementById('confirm-modal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'confirm-modal';
        modal.className = 'modal-overlay hidden';
        modal.style.setProperty('z-index', '99999999', 'important');
        modal.innerHTML = `
          <div class="modal-content glass-panel" style="max-width: 400px; border: 1px solid rgba(255,255,255,0.15); box-shadow: 0 20px 50px rgba(0,0,0,0.8);">
            <div class="modal-header" style="border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 12px; margin-bottom: 12px;">
              <h3 id="confirm-title" style="font-size: 16px; font-weight: 600; color: var(--text-primary);">Onay</h3>
              <button id="btn-close-confirm" class="modal-close-btn">✕</button>
            </div>
            <div class="modal-body" style="padding: 10px 0 16px 0;">
              <p id="confirm-message" style="font-size: 14px; color: var(--text-secondary); line-height: 1.5;"></p>
            </div>
            <div class="modal-footer" style="gap: 12px; display: flex; justify-content: flex-end;">
              <button id="btn-confirm-cancel" class="btn-secondary" style="padding: 8px 16px;">İptal</button>
              <button id="btn-confirm-ok" class="btn-primary glow-btn" style="padding: 8px 18px;">Onay</button>
            </div>
          </div>
        `;
        document.body.appendChild(modal);
      }

      modal.style.setProperty('z-index', '99999999', 'important');

      const titleEl = modal.querySelector('#confirm-title');
      const msgEl = modal.querySelector('#confirm-message');
      const btnOk = modal.querySelector('#btn-confirm-ok');
      const btnCancel = modal.querySelector('#btn-confirm-cancel');
      const btnClose = modal.querySelector('#btn-close-confirm');

      if (titleEl) titleEl.textContent = title;
      if (msgEl) msgEl.textContent = message;
      if (btnOk) {
        btnOk.textContent = okText;
        if (isDanger) {
          btnOk.style.background = 'var(--color-error, #ff3366)';
          btnOk.style.borderColor = 'rgba(255,51,102,0.4)';
          btnOk.style.boxShadow = '0 0 15px rgba(255,51,102,0.3)';
        } else {
          btnOk.style.background = 'var(--accent-gradient)';
          btnOk.style.borderColor = 'transparent';
          btnOk.style.boxShadow = 'var(--accent-glow-cyan)';
        }
      }

      modal.classList.remove('hidden');

      const cleanup = (res) => {
        confirmActive = false;
        window._confirmActive = false;
        modal.classList.add('hidden');
        if (btnOk) btnOk.onclick = null;
        if (btnCancel) btnCancel.onclick = null;
        if (btnClose) btnClose.onclick = null;
        modal.onclick = null;
        resolve(res);
      };

      if (btnOk) btnOk.onclick = () => cleanup(true);
      if (btnCancel) btnCancel.onclick = () => cleanup(false);
      if (btnClose) btnClose.onclick = () => cleanup(false);
      modal.onclick = (e) => {
        if (e.target === modal) cleanup(false);
      };
    });
  }
  window.customConfirm = customConfirm;

  // ─── INITIALIZATION ───
  function init() {
    if (window._suyliosInitialized) return;
    window._suyliosInitialized = true;
    cacheDom();
    try {
      const savedLang = localStorage.getItem('suylios_language');
      if (savedLang) applyLanguage(savedLang);
    } catch(e) {}
    bindTitlebar();
    bindNavigation();
    bindUrlInput();
    bindSettingsSidebar();
    bindSettingsControls();
    bindKeyboard();
    bindThemes();
    bindSiteSettings();
    bindAddSiteModal();
    setupCustomSelects();
    waitForApi();
  }

  function waitForApi() {
    // PyWebView fires 'pywebviewready' when the bridge is ready
    if (getApi()) {
      onApiReady();
    } else {
      window.addEventListener('pywebviewready', onApiReady, { once: true });
      // Fallback: poll for 10 seconds
      let attempts = 0;
      const poller = setInterval(() => {
        attempts++;
        if (getApi()) {
          clearInterval(poller);
          onApiReady();
        } else if (attempts > 100) {
          clearInterval(poller);
          // Still show UI even without API (dev mode)
          onApiReady();
        }
      }, 100);
    }
  }

  async function onApiReady() {
    if (state.apiReady) return;
    state.apiReady = true;
    dom.loadingOverlay?.classList.add('hidden');

    // Load app info dynamically
    try {
      const appInfo = await callApi('get_app_info');
      if (appInfo && appInfo.version) {
        state.appVersion = appInfo.version;
        const verEl = $('#about-version-text');
        const curLang = localStorage.getItem('suylios_language') || 'tr';
        if (verEl) verEl.textContent = (curLang === 'en' ? 'Version ' : 'Sürüm ') + state.appVersion;
      }
    } catch(e) {}

    // Load settings
    const settings = await callApi('get_settings');
    if (settings) {
      state.settings = settings;
      if (settings.theme) {
        // selectTheme sets localStorage, dataset.theme, styles and UI active class
        if (typeof selectTheme === 'function') {
          selectTheme(settings.theme, true);
        } else {
          document.body.dataset.theme = settings.theme;
          localStorage.setItem('suylios_theme', settings.theme);
        }
      }
      applySettingsToUI(settings);
    }

    // Start polling downloads
    startDownloadPolling();
    startClipboardMonitor();
    setTimeout(() => checkForUpdates(false), 3000);
    $('#btn-manual-check-update')?.addEventListener('click', () => checkForUpdates(true));
  }

  async function checkForUpdates(manual = false) {
    const api = getApi();
    if (!api || !api.check_for_updates) return;
    const manualBtn = $('#btn-manual-check-update');
    const manualBtnTxt = $('#btn-manual-check-update-text');
    const svgIcon = $('#btn-update-svg-icon');
    const lang = localStorage.getItem('suylios_language') || 'tr';
    try {
      if (manual && manualBtnTxt) {
        manualBtnTxt.textContent = lang === 'en' ? 'Checking...' : 'Kontrol ediliyor...';
        if (manualBtn) manualBtn.disabled = true;
        if (svgIcon) svgIcon.style.animation = 'spin 1s linear infinite';
      }
      const startTime = Date.now();
      const res = await callApi('check_for_updates');
      if (manual) {
        const elapsed = Date.now() - startTime;
        const minWait = 2200;
        if (elapsed < minWait) {
          await new Promise(r => setTimeout(r, minWait - elapsed));
        }
      }
      if (manual && manualBtn) manualBtn.disabled = false;
      if (svgIcon) svgIcon.style.animation = 'none';

      if (res && res.ok && res.has_update) {
        if (!manual) {
          const reminded = localStorage.getItem('suylios_update_remind');
          if (reminded && (Date.now() - parseInt(reminded, 10)) < 12 * 3600 * 1000) {
            return;
          }
        }
        const modal = $('#update-modal');
        const verEl = $('#update-modal-ver');
        const notesEl = $('#update-modal-notes');
        const nowBtn = $('#btn-update-now');
        const remindBtn = $('#btn-update-remind');
        if (verEl) verEl.textContent = 'v' + res.latest_version;
        if (notesEl) {
          let rawNotes = res.release_notes || 'Yeni geliştirmeler ve hata düzeltmeleri içerir.';
          if (rawNotes.includes('---')) rawNotes = rawNotes.split('---')[0];
          if (rawNotes.includes('Which File Should I Download?')) rawNotes = rawNotes.split('Which File Should I Download?')[0];
          rawNotes = rawNotes.replace(/###\s*/g, '').replace(/\*\*/g, '').replace(/\*/g, '• ').trim();
          notesEl.textContent = rawNotes;
        }
        
        nowBtn.onclick = async () => {
          const progContainer = $('#update-progress-container');
          const progText = $('#update-progress-text');
          const progPercent = $('#update-progress-percent');
          const progFill = $('#update-progress-fill');
          const progDetails = $('#update-progress-details');
          const btnsContainer = nowBtn.parentElement;
          
          if (btnsContainer) btnsContainer.style.display = 'none';
          if (progContainer) progContainer.style.display = 'flex';
          
          window.addEventListener('updateProgress', (e) => {
            const { progress, downloaded, total, status, error } = e.detail;
            if (status === 'downloading') {
              if (progText) progText.textContent = lang === 'en' ? 'Downloading...' : 'İndiriliyor...';
              if (progPercent) progPercent.textContent = progress + '%';
              if (progFill) {
                progFill.style.width = progress + '%';
                progFill.style.animation = 'none';
              }
              if (progDetails && total > 0) {
                const dlMB = (downloaded / (1024 * 1024)).toFixed(1);
                const totMB = (total / (1024 * 1024)).toFixed(1);
                progDetails.textContent = `${dlMB} MB / ${totMB} MB`;
              }
            } else if (status === 'extracting') {
              if (progText) progText.textContent = lang === 'en' ? 'Extracting files...' : 'Dosyalar çıkarılıyor...';
              if (progPercent) progPercent.textContent = '100%';
              if (progFill) {
                progFill.style.width = '100%';
                progFill.style.animation = 'updatePulse 1.5s ease-in-out infinite';
              }
              if (progDetails) progDetails.textContent = lang === 'en' ? 'Please wait...' : 'Lütfen bekleyin...';
            } else if (status === 'installing') {
              if (progText) progText.textContent = lang === 'en' ? 'Installing update...' : 'Güncelleme kuruluyor...';
              if (progPercent) progPercent.textContent = '100%';
              if (progFill) {
                progFill.style.width = '100%';
                progFill.style.animation = 'updatePulse 1.5s ease-in-out infinite';
              }
              if (progDetails) progDetails.textContent = lang === 'en' ? 'Restarting application...' : 'Uygulama yeniden başlatılıyor...';
            } else if (status === 'error') {
              if (progText) {
                progText.textContent = lang === 'en' ? 'Update failed!' : 'Güncelleme başarısız!';
                progText.style.color = '#ef4444';
              }
              if (progFill) progFill.style.animation = 'none';
              if (progDetails) progDetails.textContent = error || 'Unknown error';
              setTimeout(() => {
                if (btnsContainer) btnsContainer.style.display = 'flex';
                if (progContainer) progContainer.style.display = 'none';
                if (progText) progText.style.color = '';
              }, 5000);
            }
          });

          await callApi('perform_update', res.download_url);
        };
        remindBtn.onclick = () => {
          localStorage.setItem('suylios_update_remind', Date.now().toString());
          modal.classList.add('hidden');
        };

        modal.classList.remove('hidden');
        if (manual && manualBtnTxt) manualBtnTxt.textContent = lang === 'en' ? 'Check for Updates' : 'Güncellemeleri Kontrol Et';
      } else if (manual) {
        if (manualBtnTxt) manualBtnTxt.textContent = lang === 'en' ? '✓ Up to Date!' : '✓ Sürümünüz Güncel!';
        if (svgIcon) svgIcon.innerHTML = '<path d="M20 6L9 17l-5-5"/>';
        setTimeout(() => {
          if (manualBtnTxt) manualBtnTxt.textContent = lang === 'en' ? 'Check for Updates' : 'Güncellemeleri Kontrol Et';
          if (svgIcon) svgIcon.innerHTML = '<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>';
        }, 3000);
      }
    } catch(e) {
      if (manual && manualBtn) {
        manualBtn.disabled = false;
        if (manualBtnTxt) manualBtnTxt.textContent = lang === 'en' ? 'Check for Updates' : 'Güncellemeleri Kontrol Et';
        if (svgIcon) {
          svgIcon.style.animation = 'none';
          svgIcon.innerHTML = '<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>';
        }
      }
    }
  }

  // ─── TITLEBAR ───
  function bindTitlebar() {
    if (window._titlebarBound) return;
    window._titlebarBound = true;

    dom.btnMinimize?.addEventListener('click', () => callApi('minimize_window'));
    dom.btnMaximize?.addEventListener('click', () => callApi('maximize_window'));
    dom.btnClose?.addEventListener('click', () => callApi('close_window'));

    document.getElementById('titlebar')?.addEventListener('dblclick', (e) => {
      if (e.target.closest('button')) return;
      callApi('maximize_window');
    });
  }

  // ─── PAGE NAVIGATION ───
  function bindNavigation() {
    dom.navBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const page = btn.dataset.page;
        if (page === state.currentPage) return;
        switchPage(page);
      });
    });

    dom.btnClearHistory?.addEventListener('click', async () => {
      const ok = await customConfirm('Geçmişi Temizle', 'Tüm indirme geçmişi silinecek. Emin misiniz?', true, 'Sil');
      if (ok) {
        await callApi('clear_history');
        renderHistory();
        showToast('Geçmiş temizlendi', 'info');
      }
    });
  }

  function switchPage(page) {
    const pages = { main: dom.pageMain, settings: dom.pageSettings, history: dom.pageHistory, scheduled: dom.pageScheduled, converter: dom.pageConverter };
    const currentEl = pages[state.currentPage];
    const targetEl = pages[page];
    if (!currentEl || !targetEl) return;

    dom.navBtns.forEach(b => b.classList.toggle('active', b.dataset.page === page));

    currentEl.classList.remove('active');
    currentEl.classList.add('exit-left');

    requestAnimationFrame(() => {
      targetEl.classList.remove('exit-left');
      targetEl.classList.add('active');
    });

    setTimeout(() => {
      currentEl.classList.remove('exit-left');
    }, 400);

    state.currentPage = page;
    if (page === 'history') {
      renderHistory();
    } else if (page === 'scheduled') {
      toggleScheduledEmptyState();
    } else if (page === 'converter') {
      // converter page shown — nothing extra needed
    }
  }

  // ─── URL INPUT & DOWNLOAD ───
  function bindUrlInput() {
    dom.btnDownload?.addEventListener('click', startDownload);

    dom.urlInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        startDownload();
      }
    });

    dom.btnPaste?.addEventListener('click', async () => {
      let text = await getClipboardText();
      if (!text && dom.urlInput) {
        dom.urlInput.focus();
        try { document.execCommand('paste'); text = dom.urlInput.value; } catch(e) {}
      }
      if (text) {
        dom.urlInput.value = text;
        dom.urlInput.focus();
        flashUrlBar();
      } else {
        const lang = window.CURRENT_LANG;
        showToast(lang === 'en' ? '⚠️ Clipboard is empty or inaccessible' : '⚠️ Pano boş veya erişilemedi', 'warning');
      }
    });
  }

  async function startDownload() {
    const url = dom.urlInput.value.trim();
    if (!url) {
      showToast('Lütfen bir URL girin', 'warning');
      dom.urlInput.focus();
      return;
    }

    if (!isValidUrl(url)) {
      showToast('Geçersiz URL formatı', 'error');
      return;
    }

    // Check if URL is already active
    for (const [id, dl] of state.downloads) {
      if (dl.url === url && ['downloading', 'queued', 'converting', 'merging'].includes(dl.status)) {
        showToast('Bu URL zaten indirme listesinde aktif!', 'warning');
        return;
      }
    }

    const format = dom.formatSelect.value;
    const quality = dom.qualitySelect.value;
    const startTime = ($('#trim-start')?.value || '').trim();
    const endTime = ($('#trim-end')?.value || '').trim();

    // Disable button temporarily
    dom.btnDownload.disabled = true;
    dom.btnDownload.style.opacity = '0.6';

    const embedMeta = $('#setting-embed-metadata')?.checked ?? true;
    const dlSubs = $('#setting-download-subtitles')?.checked ?? false;
    const keepOrig = $('#trim-keep-original')?.checked ?? false;
    const compressArchive = $('#home-compress-archive')?.checked || false;
    const compressFormat = $('#home-compress-format')?.value || 'zip';
    const result = await callApi('add_download', url, format, quality, startTime, endTime, embedMeta, dlSubs, keepOrig, compressArchive, compressFormat);

    dom.btnDownload.disabled = false;
    dom.btnDownload.style.opacity = '';

    if (result && (result.task || result.task_id || result.ok)) {
      dom.urlInput.value = '';
      // Reset home compress controls after download
      const hca = $('#home-compress-archive');
      if (hca) hca.checked = false;
      const container = $('#home-compress-format-container');
      if (container) container.style.display = 'none';
      const pill = $('#home-archive-pill');
      if (pill) { pill.style.borderColor = ''; pill.style.boxShadow = ''; }
      const archLabel = $('#home-archive-toggle-label');
      if (archLabel) { archLabel.style.color = ''; archLabel.style.background = ''; }
      const archIcon = $('#home-archive-icon');
      if (archIcon) archIcon.style.transform = '';
      if ($('#btn-trim-clear') && !$('#btn-trim-clear').classList.contains('hidden')) {
        $('#btn-trim-clear').click();
      }
      showToast('İndirme eklendi', 'success');
      flashUrlBar();
      await refreshDownloads();
    } else if (result === null) {
      addDemoDownload(url, format, quality);
    } else {
      showToast(result?.error || 'İndirme eklenemedi', 'error');
    }
  }

  function isValidUrl(str) {
    try {
      const url = new URL(str);
      return url.protocol === 'http:' || url.protocol === 'https:';
    } catch {
      return false;
    }
  }

  function flashUrlBar() {
    dom.urlContainer?.classList.add('ctrlv-flash');
    setTimeout(() => dom.urlContainer?.classList.remove('ctrlv-flash'), 600);
  }

  // ─── CLIPBOARD ───
  async function getClipboardText() {
    // Sadece native bridge çağrılır, böylece tarayıcı izin uyarısı asla çıkmaz
    const apiText = await callApi('get_clipboard_text');
    return apiText || '';
  }

  // ─── KEYBOARD (Ctrl+V auto-download) ───
  function bindKeyboard() {
    // 1. Global paste event listener (outside URL input starts download immediately)
    window.addEventListener('paste', async (e) => {
      if (state.currentPage !== 'main') return;
      if (document.activeElement === dom.urlInput || document.activeElement?.tagName === 'INPUT' || document.activeElement?.tagName === 'TEXTAREA') {
        return;
      }
      let text = (e.clipboardData || window.clipboardData)?.getData('text');
      if (!text) {
        text = await getClipboardText();
      }
      if (text && isValidUrl(text.trim())) {
        e.preventDefault();
        dom.urlInput.value = text.trim();
        flashUrlBar();
        startDownload();
      }
    });

    // 2. Direct paste event on URL input field (does NOT start download)
    dom.urlInput?.addEventListener('paste', () => {
      setTimeout(() => {
        const val = dom.urlInput.value.trim();
        if (isValidUrl(val)) {
          flashUrlBar();
        }
      }, 50);
    });
  }

  // ─── DOWNLOAD POLLING & CLIPBOARD MONITOR ───
  function startDownloadPolling() {
    refreshDownloads();
    state.pollInterval = setInterval(refreshDownloads, 1000);
  }

  let lastClipboardUrl = '';
  function startClipboardMonitor() {
    setInterval(async () => {
      const isEnabled = $('#setting-clipboard-monitor')?.checked ?? state.settings?.clipboard_monitor ?? true;
      if (!isEnabled || state.currentPage !== 'main') return;

      const text = await callApi('get_clipboard_text');
      if (text && typeof text === 'string') {
        const cleaned = text.trim();
        if (cleaned !== lastClipboardUrl && isValidUrl(cleaned)) {
          lastClipboardUrl = cleaned;
          if (dom.urlInput && dom.urlInput.value !== cleaned) {
            dom.urlInput.value = cleaned;
            flashUrlBar();
            showClipboardBanner(cleaned);
          }
        }
      }
    }, 1500);
  }

  function showClipboardBanner(url) {
    if (!dom.clipboardBanner) return;
    const urlTextEl = $('#clipboard-url-text');
    if (urlTextEl) urlTextEl.textContent = url;
    dom.clipboardBanner.classList.remove('hidden');

    const downloadBtn = $('#btn-clipboard-download');
    const closeBtn = $('#btn-clipboard-close');

    if (downloadBtn) {
      downloadBtn.onclick = () => {
        dom.clipboardBanner.classList.add('hidden');
        if (dom.urlInput) dom.urlInput.value = url;
        startDownload();
      };
    }
    if (closeBtn) {
      closeBtn.onclick = () => {
        dom.clipboardBanner.classList.add('hidden');
      };
    }
  }

  async function renderHistory() {
    if (!dom.historyList) return;
    const history = await callApi('get_history') || [];
    dom.historyList.innerHTML = '';
    
    const emptyEl = dom.historyEmpty || $('#history-empty');
    if (history.length === 0) {
      if (emptyEl) emptyEl.classList.remove('hidden');
      return;
    }
    if (emptyEl) emptyEl.classList.add('hidden');

    const lang = window.CURRENT_LANG || 'tr';
    const completedWord = lang === 'en' ? 'Completed' : 'Tamamlandı';
    const otherWord = lang === 'en' ? 'Other' : 'Diğer';
    const openFolderText = lang === 'en' ? 'Open Folder' : 'Klasörü Aç';
    const deleteText = lang === 'en' ? 'Remove from list' : 'Listeden Sil';

    history.forEach(item => {
      const card = document.createElement('div');
      card.className = 'history-card';
      const sizeStr = item.total_bytes ? formatSize(item.total_bytes) : completedWord;
      const extractor = item.extractor_name || otherWord;
      
      const thumbContent = item.thumbnail ? `<img src="${item.thumbnail}" style="width:100%; height:100%; object-fit:cover; border-radius:10px;">` : `📁`;
      const formatChip = item.format_type ? `<span style="background:rgba(0, 240, 255, 0.15); color:var(--accent-cyan); padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px; text-transform:uppercase; border:1px solid rgba(0,240,255,0.3);">${item.format_type}</span>` : '';
      const qualityChip = item.quality && item.quality !== 'best' ? `<span style="background:rgba(168, 85, 247, 0.15); color:#c084fc; padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px; border:1px solid rgba(168,85,247,0.3);">${item.quality}</span>` : '';

      card.innerHTML = `
        <div class="history-card-left">
          <div class="history-card-icon" style="overflow:hidden; position:relative; border:1px solid rgba(255,255,255,0.1);">${thumbContent}</div>
          <div class="history-card-info">
            <div class="history-card-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
            <div class="history-card-meta" style="flex-wrap:wrap; gap:8px;">
              ${formatChip}
              ${qualityChip}
              <span>📅 ${item.date_str || ''}</span>
              <span>📦 ${sizeStr}</span>
              <span style="color:var(--text-accent);">🏷️ ${escapeHtml(extractor)}</span>
            </div>
          </div>
        </div>
        <div class="history-card-actions">
          <button class="btn-secondary btn-sm btn-preview-hist" data-id="${item.id}" title="Önizle" style="color:var(--accent-cyan); border-color:rgba(0,240,255,0.3);">▶️</button>
          <button class="btn-secondary btn-sm btn-open-hist" data-id="${item.id}" title="${openFolderText}">📁 ${openFolderText}</button>
          <button class="btn-secondary btn-sm btn-del-hist" data-id="${item.id}" title="${deleteText}" style="color:#ef4444; border-color:rgba(239,68,68,0.3);">🗑️</button>
        </div>
      `;
      
      const previewBtn = card.querySelector('.btn-preview-hist');
      if (previewBtn) {
        previewBtn.addEventListener('click', () => {
          if (window.openPreviewPlayer) window.openPreviewPlayer(item.id);
        });
      }
      const openBtn = card.querySelector('.btn-open-hist');
      if (openBtn) {
        openBtn.addEventListener('click', () => {
          callApi('open_file_location', item.id);
        });
      }
      const delBtn = card.querySelector('.btn-del-hist');
      if (delBtn) {
        delBtn.addEventListener('click', async () => {
          await callApi('delete_history_item', item.id);
          renderHistory();
          const delToast = window.CURRENT_LANG === 'en' ? 'Record deleted' : 'Kayıt silindi';
          showToast(delToast, 'info');
        });
      }
      dom.historyList.appendChild(card);
    });
  }

  let _refreshingDownloads = false;
  async function refreshDownloads() {
    if (_refreshingDownloads) return;
    _refreshingDownloads = true;
    try {
      const downloads = await callApi('get_downloads');
      if (!downloads) return;

      const newIds = new Set();

      for (const dl of downloads) {
        try {
          newIds.add(dl.id);
          const existing = state.downloads.get(dl.id);
          state.downloads.set(dl.id, dl);

          if (existing) {
            if (existing.status !== 'completed' && dl.status === 'completed') {
              showToast(`${dl.title || 'İndirme'} tamamlandı!`, 'success');
              // Fire-and-forget: don't await notification to avoid blocking refresh
              callApi('show_desktop_notification', 'Suylios Downloader', `${dl.title || 'Dosya'} başarıyla indirildi!`).catch(() => {});
            } else if (existing.status !== 'error' && dl.status === 'error') {
              showToast(`${dl.title || 'İndirme'} başarısız oldu!`, 'error');
              callApi('show_desktop_notification', 'Suylios - Hata', `${dl.title || 'Dosya'} indirilemedi`).catch(() => {});
            }
            updateDownloadCard(dl);
          } else {
            createDownloadCard(dl);
          }
        } catch(cardErr) {
          console.error('[Suylios] Card update error:', cardErr);
        }
      }

      // Remove cards no longer in list
      for (const [id] of state.downloads) {
        if (!newIds.has(id)) {
          removeDownloadCard(id);
          state.downloads.delete(id);
        }
      }

      updateStatusBar(downloads);
      toggleEmptyState();
      toggleScheduledEmptyState();
      updateReorderButtonsVisibility();
    } finally {
      _refreshingDownloads = false;
    }
  }

  function toggleEmptyState() {
    const hasCards = dom.downloadList.children.length > 0;
    dom.emptyState.style.display = hasCards ? 'none' : '';
  }

  function toggleScheduledEmptyState() {
    if (!dom.scheduledList || !dom.scheduledEmpty) return;
    const count = dom.scheduledList.children.length;
    dom.scheduledEmpty.style.display = count > 0 ? 'none' : '';
    if (dom.scheduledBadge) {
      if (count > 0) {
        dom.scheduledBadge.classList.remove('hidden');
      } else {
        dom.scheduledBadge.classList.add('hidden');
      }
    }
  }

  function updateReorderButtonsVisibility() {
    [dom.downloadList, dom.scheduledList].forEach(list => {
      if (!list) return;
      const cards = Array.from(list.querySelectorAll('.download-card'));
      cards.forEach((card, index) => {
        const upBtn = card.querySelector('.btn-move-up');
        const downBtn = card.querySelector('.btn-move-down');
        if (upBtn) upBtn.style.display = (index === 0) ? 'none' : 'flex';
        if (downBtn) downBtn.style.display = (index === cards.length - 1) ? 'none' : 'flex';
      });
    });
  }

  // ─── DOWNLOAD CARD CREATION ───
  function createDownloadCard(dl) {
    // Guard: if card already exists in DOM, just update it
    const existing = $(`.download-card[data-task-id="${dl.id}"]`);
    if (existing) {
      updateDownloadCard(dl);
      return;
    }

    const template = dom.cardTemplate.content.cloneNode(true);
    const card = template.querySelector('.download-card');

    card.dataset.taskId = dl.id;
    card.dataset.status = dl.status || 'downloading';
    card.style.animationDelay = '0s';

    // Populate
    populateCard(card, dl);

    // Bind actions
    bindCardActions(card, dl.id);
    bindCardReorder(card);
    bindCardDragDrop(card);

    const isScheduled = dl.scheduled_at && dl.scheduled_at > 0;
    if (isScheduled && dom.scheduledList) {
      dom.scheduledList.appendChild(card);
      toggleScheduledEmptyState();
    } else {
      dom.downloadList.appendChild(card);
      toggleEmptyState();
    }
  }

  function updateDownloadCard(dl) {
    const card = $(`.download-card[data-task-id="${dl.id}"]`);
    if (!card) {
      // Card not in DOM yet — create it
      createDownloadCard(dl);
      return;
    }

    const isScheduled = dl.scheduled_at && dl.scheduled_at > 0;
    if (!isScheduled && dom.scheduledList && card.parentElement === dom.scheduledList) {
      dom.downloadList.appendChild(card);
      toggleScheduledEmptyState();
      toggleEmptyState();
    }

    card.dataset.status = dl.status || 'downloading';
    populateCard(card, dl);
  }

  function populateCard(card, dl) {
    const title = card.querySelector('.card-title');
    const subtitle = card.querySelector('.card-subtitle');
    const badge = card.querySelector('.status-badge');
    const progressFill = card.querySelector('.progress-fill');
    const progressGlow = card.querySelector('.progress-glow');
    const progressPercent = card.querySelector('.progress-percent');
    const progressSpeed = card.querySelector('.progress-speed');
    const progressSize = card.querySelector('.progress-size');
    const progressEta = card.querySelector('.progress-eta');

    let rawTitle = dl.title || dl.filename || 'İndirme başlıyor...';
    if (rawTitle.includes('/') || rawTitle.includes('\\')) {
      rawTitle = rawTitle.split(/[/\\]/).pop();
    }
    const isComplete = dl.status === 'complete' || dl.status === 'completed';
    if (dl.item_count && dl.item_count > 1 && !isComplete && dl.item_index > 0) {
      const idx = dl.item_index;
      const displayTitle = rawTitle;
      title.innerHTML = `<span style="background: linear-gradient(135deg, var(--accent-cyan), #0080ff); color: #000; padding: 2px 8px; border-radius: 12px; font-weight: 800; font-size: 12px; margin-right: 8px; box-shadow: 0 0 10px rgba(0,240,255,0.4); display: inline-block; vertical-align: middle; flex-shrink:0;">${idx}/${dl.item_count}</span><span style="vertical-align: middle;">${escapeHtml(displayTitle)}</span>`;
    } else if (!dl.item_count && dl.item_index > 0 && !isComplete) {
      // Gallery/watcher mode: unknown total, show just downloaded count
      const displayTitle = rawTitle;
      title.innerHTML = `<span style="background: linear-gradient(135deg, var(--accent-cyan), #0080ff); color: #000; padding: 2px 8px; border-radius: 12px; font-weight: 800; font-size: 12px; margin-right: 8px; box-shadow: 0 0 10px rgba(0,240,255,0.4); display: inline-block; vertical-align: middle; flex-shrink:0;">${dl.item_index} dosya</span><span style="vertical-align: middle;">${escapeHtml(displayTitle)}</span>`;
    } else if (dl.item_count && dl.item_count > 1 && isComplete) {
      // Completed playlist/archive — show title + total count chip
      title.innerHTML = `<span style="background: linear-gradient(135deg, #00e87a, #00b85a); color: #000; padding: 2px 8px; border-radius: 12px; font-weight: 800; font-size: 12px; margin-right: 8px; box-shadow: 0 0 10px rgba(0,232,122,0.4); display: inline-block; vertical-align: middle; flex-shrink:0;">${dl.item_count} öğe</span><span style="vertical-align: middle;">${escapeHtml(rawTitle)}</span>`;
    } else if (!dl.item_count && dl.item_index > 0 && isComplete) {
      // Gallery completed: show final file count
      title.innerHTML = `<span style="background: linear-gradient(135deg, #00e87a, #00b85a); color: #000; padding: 2px 8px; border-radius: 12px; font-weight: 800; font-size: 12px; margin-right: 8px; box-shadow: 0 0 10px rgba(0,232,122,0.4); display: inline-block; vertical-align: middle; flex-shrink:0;">${dl.item_index} dosya</span><span style="vertical-align: middle;">${escapeHtml(rawTitle)}</span>`;
    } else {
      title.textContent = rawTitle;
    }

    if (dl.scheduled_at && dl.scheduled_at > 0) {
      const dtStr = new Date(dl.scheduled_at * 1000).toLocaleString();
      subtitle.innerHTML = `<span style="color:var(--accent-purple); font-weight:700;">⏰ ${dtStr}</span> • ${escapeHtml(truncateUrl(dl.url || ''))}`;
    } else {
      subtitle.textContent = truncateUrl(dl.url || '');
    }

    const siteIcon = card.querySelector('.card-site-icon');
    if (siteIcon && dl.thumbnail) {
      siteIcon.innerHTML = `<img src="${dl.thumbnail}" class="card-thumb-img" alt="">`;
    }

    const progress = Math.min(100, Math.max(0, dl.progress || 0));
    progressFill.style.width = progress + '%';
    progressGlow.style.width = progress + '%';
    progressPercent.textContent = Math.round(progress) + '%';
    const lang = window.CURRENT_LANG || 'tr';
    const completedText = lang === 'en' ? 'Completed' : 'Tamamlandı';

    let pSpeed = dl.speed ? formatSpeed(dl.speed) : '— MB/s';
    let pSize = '';
    let pEta = '';

    if (dl.status === 'completed' || dl.status === 'complete') {
      const finalSize = dl.total_size || dl.downloaded_size || 0;
      pSize = finalSize > 0 ? formatSize(finalSize) : '';
      pEta = completedText;
    } else {
      if (dl.total_size && dl.total_size > 0) {
        pSize = formatSizeRange(dl.downloaded_size, dl.total_size);
      } else if (dl.downloaded_size && dl.downloaded_size > 0) {
        pSize = formatSize(dl.downloaded_size);
      }

      if (dl.scheduled_at && dl.scheduled_at > 0) {
        pEta = lang === 'en' ? '⏰ Waiting for scheduled time...' : '⏰ İndirme zamanı bekleniyor...';
      } else if (dl.status === 'converting') {
        pEta = dl.format_type === 'mp3' ? '🎵 MP3 formatına dönüştürülüyor...' : '⚙️ Dönüştürülüyor...';
      } else if (dl.status === 'merging') {
        pEta = '📦 Video ve ses birleştiriliyor...';
      } else {
        pEta = dl.eta ? formatEta(dl.eta) : ((dl.downloaded_size > 0 || dl.item_index > 0 || dl.progress > 0) ? '⏳ İndiriliyor...' : '🚀 Başlatılıyor...');
      }
    }

    const detailsContainer = card.querySelector('.progress-details');
    if (detailsContainer) {
      const parts = [];
      if (dl.status !== 'completed' && dl.status !== 'complete') parts.push(`<span class="progress-speed">${pSpeed}</span>`);
      if (pSize) parts.push(`<span class="progress-size">${pSize}</span>`);
      if (pEta) parts.push(`<span class="progress-eta">${pEta}</span>`);
      detailsContainer.innerHTML = parts.join(' <span class="progress-separator">•</span> ');
    }

    // Status badge
    let statusInfo;
    if (dl.scheduled_at && dl.scheduled_at > 0) {
      statusInfo = { text: lang === 'en' ? '⏰ SCHEDULED' : '⏰ ZAMANLANDI', class: 'converting' };
    } else {
      const statusMap = {
        downloading: { text: 'İndiriliyor', class: 'downloading' },
        paused:      { text: 'Duraklatıldı', class: 'paused' },
        converting:  { text: 'Dönüştürülüyor', class: 'converting' },
        complete:    { text: completedText, class: 'complete' },
        completed:   { text: completedText, class: 'complete' },
        cancelled:   { text: 'İptal Edildi', class: 'error' },
        error:       { text: 'Hata', class: 'error' },
        queued:      { text: 'Sırada', class: 'downloading' },
        merging:     { text: 'Birleştiriliyor', class: 'converting' },
      };
      statusInfo = statusMap[dl.status] || { text: dl.status, class: '' };
    }

    badge.textContent = statusInfo.text;
    badge.className = 'status-badge ' + statusInfo.class;

    const previewBtn = card.querySelector('.btn-preview');
    const pauseBtn = card.querySelector('.btn-pause');
    const cancelBtn = card.querySelector('.btn-cancel');

    if (pauseBtn) {
      if (dl.status === 'paused') {
        pauseBtn.title = 'Devam Et';
        pauseBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>`;
      } else {
        pauseBtn.title = 'Duraklat';
        pauseBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>`;
      }
    }

    // Complete: show ETA as "Tamamlandı", show preview button, hide pause/cancel
    const retryBtn = card.querySelector('.btn-retry');
    if (dl.status === 'complete' || dl.status === 'completed') {
      progressPercent.textContent = '100%';
      progressEta.textContent = completedText;
      progressSpeed.textContent = '';
      progressFill.style.width = '100%';
      progressGlow.style.width = '100%';
      if (previewBtn) previewBtn.classList.remove('hidden');
      if (pauseBtn) pauseBtn.classList.add('hidden');
      if (cancelBtn) cancelBtn.classList.add('hidden');
      if (retryBtn) { retryBtn.classList.remove('hidden'); retryBtn.title = 'Yeniden İndir'; }
    } else if (dl.status === 'error' || dl.status === 'cancelled') {
      if (previewBtn) previewBtn.classList.add('hidden');
      if (pauseBtn) pauseBtn.classList.add('hidden');
      if (cancelBtn) cancelBtn.classList.add('hidden');
      if (retryBtn) { retryBtn.classList.remove('hidden'); retryBtn.title = 'Yeniden Dene'; }
    } else {
      if (previewBtn) previewBtn.classList.add('hidden');
      if (retryBtn) retryBtn.classList.add('hidden');
      if (pauseBtn && dl.status !== 'error' && dl.status !== 'cancelled') pauseBtn.classList.remove('hidden');
      if (cancelBtn && dl.status !== 'error' && dl.status !== 'cancelled') cancelBtn.classList.remove('hidden');
    }

    if (dl.status === 'error') {
      progressEta.textContent = dl.error_message || dl.error || 'Hata oluştu';
    }
    if (dl.status === 'cancelled') {
      progressEta.textContent = 'İptal edildi';
      progressSpeed.textContent = '';
    }

    const map = lang === 'en' ? TR_TO_EN : EN_TO_TR;
    if (badge && map[badge.textContent.trim()]) badge.textContent = map[badge.textContent.trim()];
    if (progressEta && map[progressEta.textContent.trim()]) progressEta.textContent = map[progressEta.textContent.trim()];
    if (progressSize) {
      if (lang === 'en') progressSize.textContent = progressSize.textContent.replace('Tamamlandı', 'Completed');
      else progressSize.textContent = progressSize.textContent.replace('Completed', 'Tamamlandı');
    }
    card.querySelectorAll('.card-action-btn').forEach(b => {
      const t = b.getAttribute('title');
      if (t && map[t]) b.setAttribute('title', map[t]);
    });
  }

  function bindCardActions(card, taskId) {
    const previewBtn = card.querySelector('.btn-preview');
    const retryBtn = card.querySelector('.btn-retry');
    const pauseBtn = card.querySelector('.btn-pause');
    const cancelBtn = card.querySelector('.btn-cancel');
    const folderBtn = card.querySelector('.btn-folder');
    const removeBtn = card.querySelector('.btn-remove');

    previewBtn?.addEventListener('click', () => {
      if (window.openPreviewPlayer) window.openPreviewPlayer(taskId);
    });

    retryBtn?.addEventListener('click', async () => {
      const dl = state.downloads.get(taskId);
      const result = await callApi('retry_download', taskId);
      if (result?.ok) {
        showToast(`${dl?.title || 'İndirme'} yeniden başlatıldı.`, 'info');
      } else {
        showToast('Yeniden indirme başlatılamadı.', 'error');
      }
    });

    pauseBtn?.addEventListener('click', async () => {
      const dl = state.downloads.get(taskId);
      if (dl?.status === 'paused') {
        await callApi('resume_download', taskId);
      } else {
        await callApi('pause_download', taskId);
      }
    });

    cancelBtn?.addEventListener('click', async () => {
      await callApi('cancel_download', taskId);
      showToast('İndirme iptal edildi', 'info');
    });

    folderBtn?.addEventListener('click', () => {
      callApi('open_file_location', taskId);
    });

    removeBtn?.addEventListener('click', async () => {
      await callApi('remove_download', taskId);
      removeDownloadCard(taskId);
      state.downloads.delete(taskId);
      toggleEmptyState();
    });
  }

  function removeDownloadCard(taskId) {
    const card = $(`.download-card[data-task-id="${taskId}"]`);
    if (!card) return;
    card.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
    card.style.opacity = '0';
    card.style.transform = 'translateX(30px) scale(0.95)';
    setTimeout(() => {
      card.remove();
      toggleEmptyState();
      toggleScheduledEmptyState();
    }, 300);
  }

  // ─── DEMO DOWNLOAD (for UI testing without backend) ───
  let demoId = 0;

  function addDemoDownload(url, format, quality) {
    demoId++;
    const id = 'demo-' + demoId;
    const dl = {
      id,
      url,
      title: getDemoTitle(url),
      filename: 'video.mp4',
      status: 'downloading',
      progress: 0,
      speed: 0,
      total_size: 104857600, // 100MB
      downloaded_size: 0,
      format,
      eta: 0,
    };

    state.downloads.set(id, dl);
    createDownloadCard(dl);

    // Simulate progress
    simulateProgress(id);
  }

  function getDemoTitle(url) {
    try {
      const hostname = new URL(url).hostname.replace('www.', '');
      return `İndirme - ${hostname}`;
    } catch {
      return 'İndirme';
    }
  }

  function simulateProgress(id) {
    let progress = 0;
    const interval = setInterval(() => {
      const dl = state.downloads.get(id);
      if (!dl) { clearInterval(interval); return; }

      if (dl.status === 'paused') return;

      progress += Math.random() * 3 + 0.5;
      if (progress >= 100) {
        progress = 100;
        dl.status = 'complete';
        dl.progress = 100;
        dl.downloaded_size = dl.total_size;
        dl.speed = 0;
        updateDownloadCard(dl);
        clearInterval(interval);
        showToast(`${dl.title} tamamlandı!`, 'success');
        return;
      }

      dl.progress = progress;
      dl.downloaded_size = Math.floor((progress / 100) * dl.total_size);
      dl.speed = Math.random() * 5000000 + 500000;
      dl.eta = Math.floor((100 - progress) / 2);
      updateDownloadCard(dl);
    }, 300);
  }

  // ─── SETTINGS SIDEBAR ───
  function bindSettingsSidebar() {
    dom.settingsNavItems.forEach(item => {
      item.addEventListener('click', () => {
        const tab = item.dataset.tab;
        if (tab === state.currentSettingsTab) return;

        dom.settingsNavItems.forEach(i => i.classList.remove('active'));
        item.classList.add('active');

        dom.settingsTabs.forEach(t => t.classList.remove('active'));
        $(`#tab-${tab}`)?.classList.add('active');

        state.currentSettingsTab = tab;
      });
    });
  }

  // ─── SETTINGS CONTROLS ───
  function bindSettingsControls() {
    if (window._settingsControlsBound) return;
    window._settingsControlsBound = true;
    // Concurrent downloads input handled automatically by auto-save


    // Browse folder
    dom.btnBrowseFolder?.addEventListener('click', async () => {
      const path = await callApi('pick_folder');
      if (path) {
        dom.settingDownloadPath.value = path;
        saveCurrentSettings();
      }
    });

    // Browse FFmpeg
    dom.btnBrowseFFmpeg?.addEventListener('click', async () => {
      const path = await callApi('pick_folder');
      if (path) {
        $('#setting-ffmpeg-path').value = path;
        saveCurrentSettings();
      }
    });

    // Clear cache
    dom.btnClearCache?.addEventListener('click', () => {
      showToast('Önbellek temizlendi', 'success');
    });

    // Blocklist
    const blocklistInput = $('#setting-blocklist-input');
    const btnAddBlocklist = $('#btn-add-blocklist');
    
    btnAddBlocklist?.addEventListener('click', () => {
      const val = blocklistInput.value.trim();
      if (val) {
        if (!state.settings) state.settings = {};
        if (!state.settings.blocklist) state.settings.blocklist = [];
        if (!state.settings.blocklist.includes(val)) {
          state.settings.blocklist.push(val);
          blocklistInput.value = '';
          renderBlocklist();
          saveCurrentSettings();
        }
      }
    });

    blocklistInput?.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        btnAddBlocklist.click();
      }
    });

    // Reset settings
    if (dom.btnResetSettings) {
      dom.btnResetSettings.onclick = async (e) => {
        if (e) e.preventDefault();
        const ok = await customConfirm('Tüm ayarlar varsayılan değerlerine sıfırlanacak. Emin misiniz?', 'Ayarları Sıfırla', 'Sıfırla', true);
      if (ok) {
        if (window.pywebview && pywebview.api && pywebview.api.reset_settings) {
          const res = await pywebview.api.reset_settings();
          if (res && res.ok) {
            applySettingsToUI(res.settings);
            showToast('Ayarlar başarıyla sıfırlandı', 'success');
            return;
          }
        }
        showToast('Ayarlar sıfırlandı', 'info');
      }
    };
  }

    const ghBtn = $('#link-github');
    if (ghBtn) {
      ghBtn.addEventListener('click', (e) => {
        e.preventDefault();
        callApi('open_url', 'https://github.com/sayrias/suylios-downloader');
      });
    }

    // Auto-save on change for all settings inputs
    $$('.settings-tab input, .settings-tab select, #home-compress-archive, #home-compress-format').forEach(el => {
      if (el.dataset.autoSaveBound) return;
      el.dataset.autoSaveBound = 'true';
      el.addEventListener('change', () => {
        if (el.id === 'setting-language') {
          applyLanguage(el.value);
        } else if (el.id === 'home-compress-archive') {
          const container = $('#home-compress-format-container');
          if (container) container.style.display = el.checked ? 'block' : 'none';
          // Update pill visual state
          const pill = $('#home-archive-pill');
          const label = $('#home-archive-toggle-label');
          const icon = $('#home-archive-icon');
          if (pill) {
            pill.style.borderColor = el.checked ? 'var(--accent-cyan)' : '';
            pill.style.boxShadow = el.checked ? '0 0 12px rgba(0,240,255,0.2)' : '';
            pill.style.color = el.checked ? 'var(--accent-cyan)' : '';
            pill.style.background = el.checked ? 'rgba(0,240,255,0.08)' : '';
          }
          if (icon) icon.style.transform = el.checked ? 'scale(1.2)' : 'scale(1)';
        } else if (el.id === 'home-compress-format') {
          // Do nothing to settings
        } else if (el.id === 'setting-auto-compress') {
          const delRow = $('#setting-delete-archive-row');
          if (delRow) delRow.style.display = el.checked ? 'flex' : 'none';
        } else if (el.id === 'setting-compress-format') {
          const s = $('#setting-compress-format');
          if (s) {
            // update custom dropdown text for settings
            const textSpan = s.closest('.select-wrapper')?.querySelector('.cyber-dropdown-text');
            if (textSpan) textSpan.textContent = s.options[s.selectedIndex].text;
          }
        }
        saveCurrentSettings();
      });
    });
  }

  let TR_TO_EN = {};
  let EN_TO_TR = {};

  async function loadLocaleDictionary(lang) {
    if (lang === 'tr') return;
    try { localStorage.removeItem('suylios_locale_en'); localStorage.removeItem('suylios_locale_tr'); } catch(e) {}
    let dict = null;
    if (getApi()) {
      dict = await callApi('get_locale', lang);
    }
    if (!dict || Object.keys(dict).length === 0) {
      try {
        const res = await fetch(`locales/${lang}/${lang}.json`);
        if (res.ok) dict = await res.json();
      } catch(e) {}
    }

    if (dict && Object.keys(dict).length > 0) {
      TR_TO_EN = dict;
      EN_TO_TR = {};
      for (const [k, v] of Object.entries(dict)) EN_TO_TR[v] = k;
      try { localStorage.setItem('suylios_locale_' + lang, JSON.stringify(dict)); } catch(e) {}
    }
  }

  async function applyLanguage(lang) {
    window.CURRENT_LANG = lang;
    try { localStorage.setItem('suylios_language', lang); } catch(e) {}
    await loadLocaleDictionary(lang);
    const map = lang === 'en' ? TR_TO_EN : EN_TO_TR;

    // Traverse all potential text elements
    $$('h1, h2, h3, h4, p, span, label, option, button, .sidebar-header, small, .cyber-dropdown-item, .cyber-dropdown-text, .site-tag, .coming-soon-text, .about-version, .about-desc, .about-copyright').forEach(el => {
      const txt = el.textContent.trim();
      if (map[txt]) {
        if (el.children.length === 0) {
          el.textContent = map[txt];
        } else {
          Array.from(el.childNodes).forEach(node => {
            const nt = node.textContent.trim();
            if (node.nodeType === Node.TEXT_NODE && nt && map[nt]) {
              node.textContent = node.textContent.replace(nt, map[nt]);
            }
          });
        }
      }
    });

    // Specifically handle buttons with icons like Download / Clear History
    const btnDl = $('#btn-download span'); if (btnDl) btnDl.textContent = lang === 'en' ? 'Download' : 'İndir';
    const btnBatch = $('#btn-batch span'); if (btnBatch) btnBatch.textContent = lang === 'en' ? 'Batch' : 'Toplu';
    const btnSched = $('#btn-schedule span'); if (btnSched) btnSched.textContent = lang === 'en' ? 'Schedule' : 'Zamanla';
    const btnSchedConf = $('#btn-schedule-confirm span'); if (btnSchedConf) btnSchedConf.textContent = lang === 'en' ? 'Schedule' : 'Zamanla';
    const btnClip = $('#btn-clipboard-download'); if (btnClip) btnClip.textContent = lang === 'en' ? 'One-Click Download' : 'Tek Tıkla İndir';
    const clipLbl = $('#clipboard-label-text'); if (clipLbl) clipLbl.textContent = lang === 'en' ? 'New link detected in clipboard: ' : 'Panoda yeni link algılandı: ';
    const emptyTitle = $('#empty-state-title'); if (emptyTitle) emptyTitle.textContent = lang === 'en' ? 'Ready to Download' : 'İndirmeye Hazır';
    const emptyDesc = $('#empty-state-desc'); if (emptyDesc) emptyDesc.innerHTML = lang === 'en' ? 'Paste a URL or press <kbd>Ctrl</kbd> + <kbd>V</kbd> to start downloading' : 'URL yapıştırarak veya <kbd>Ctrl</kbd> + <kbd>V</kbd> basarak indirmeye başla';
    const btnHist = $('#btn-clear-history'); if (btnHist) btnHist.textContent = lang === 'en' ? 'Clear History' : 'Geçmişi Temizle';
    const spin = $('.loading-spinner span'); if (spin) spin.textContent = lang === 'en' ? 'Connecting...' : 'Bağlanıyor...';
    const trimLbl = $('#trim-label-text'); if (trimLbl) trimLbl.textContent = lang === 'en' ? 'Time Range:' : 'Zaman Aralığı:';
    const trimBtn = $('#trim-btn-text'); if (trimBtn && !window.TRIM_ACTIVE) trimBtn.textContent = lang === 'en' ? 'Trim' : 'Kes';
    if (trimBtn && window.TRIM_ACTIVE) trimBtn.textContent = lang === 'en' ? '✓ Trimmed' : '✓ Kesildi';
    const schedNav = $('button[data-page="scheduled"]'); if (schedNav) schedNav.title = lang === 'en' ? 'Scheduled Downloads' : 'Zamanlanmış İndirmeler';

    // Placeholders & Readonly Values
    $$('input[placeholder]').forEach(inp => {
      const ph = inp.getAttribute('placeholder');
      if (map[ph]) inp.setAttribute('placeholder', map[ph]);
    });
    const ffmpegEl = $('#setting-ffmpeg-path');
    if (ffmpegEl && (ffmpegEl.value === 'Otomatik algılandı' || ffmpegEl.value === 'Auto-detected')) {
      ffmpegEl.value = lang === 'en' ? 'Auto-detected' : 'Otomatik algılandı';
    }

    // Nav Tooltips
    $$('.titlebar-nav-btn').forEach(btn => {
      if (btn.dataset.page === 'main') btn.title = lang === 'en' ? 'Home' : 'Ana Sayfa';
      if (btn.dataset.page === 'history') btn.title = lang === 'en' ? 'History' : 'Geçmiş';
      if (btn.dataset.page === 'settings') btn.title = lang === 'en' ? 'Settings' : 'Ayarlar';
    });

    // Shutdown Modal & Filter Button Tooltip
    const sdTitle = $('#shutdown-modal-title'); if (sdTitle) sdTitle.textContent = lang === 'en' ? 'When Download Completes' : 'İndirme Bitince';
    const sdDesc = $('#shutdown-modal-desc'); if (sdDesc) sdDesc.textContent = lang === 'en' ? 'What should be done to the PC when all active downloads complete?' : 'Tüm aktif indirmeler tamamlandığında bilgisayara ne yapılsın?';
    const sdCancel = $('#btn-shutdown-cancel'); if (sdCancel) sdCancel.textContent = lang === 'en' ? 'Cancel' : 'İptal';
    const sdConfirm = $('#btn-shutdown-confirm'); if (sdConfirm) sdConfirm.textContent = lang === 'en' ? '✓ Apply' : '✓ Uygula';
    const btnOpenSd = $('#btn-open-shutdown-modal'); if (btnOpenSd) btnOpenSd.textContent = lang === 'en' ? '⏰ Configure' : '⏰ Ayarla';
    const btnShowOpt = $('#btn-show-options'); if (btnShowOpt) btnShowOpt.title = lang === 'en' ? 'Filter & Download Options' : 'Filtre & İndirme Seçenekleri';
    const pasteBtnEl = $('#btn-paste'); if (pasteBtnEl) pasteBtnEl.title = lang === 'en' ? 'Paste' : 'Yapıştır';
    const trimBtnEl = $('#btn-show-trim'); if (trimBtnEl) trimBtnEl.title = lang === 'en' ? 'Trim Video' : 'Zaman Aralığı ile Kes';
    const batchBtnEl = $('#btn-batch'); if (batchBtnEl) batchBtnEl.title = lang === 'en' ? 'Batch Download — Add multiple URLs at once' : "Toplu İndirme — Birden fazla URL'yi tek seferde ekle";
    const schedBtnEl = $('#btn-schedule'); if (schedBtnEl) schedBtnEl.title = lang === 'en' ? 'Scheduled Download — Set download for a specific time' : 'Zamanlanmış İndirme — Belirli bir saate indirme kur';
    const trimClearEl = $('#btn-trim-clear'); if (trimClearEl) trimClearEl.title = lang === 'en' ? 'Clear' : 'Temizle';
    const verEl = $('#about-version-text'); if (verEl) verEl.textContent = (lang === 'en' ? 'Version ' : 'Sürüm ') + (state.appVersion || '1.4.1');
    const chkUpdTxt = $('#btn-manual-check-update-text'); if (chkUpdTxt && !chkUpdTxt.textContent.includes('✓') && !chkUpdTxt.textContent.includes('...')) chkUpdTxt.textContent = lang === 'en' ? 'Check for Updates' : 'Güncellemeleri Kontrol Et';
    const trimKeepEl = $('#trim-keep-text'); if (trimKeepEl) trimKeepEl.textContent = lang === 'en' ? 'Keep original video' : 'Orijinal videoyu sakla';
    const trimKeepLbl = $('#trim-keep-label'); if (trimKeepLbl) trimKeepLbl.title = lang === 'en' ? 'Keep the original file without deleting and cut a copy' : 'Orijinal dosyayı silmeden sakla ve kopyası üzerinde kesim yap';
    const schedDtLbl = $('#schedule-datetime-label'); if (schedDtLbl) schedDtLbl.textContent = lang === 'en' ? 'Date & Time' : 'Tarih & Saat';
    const dtBtnTxt = $('#dt-picker-btn-text'); if (dtBtnTxt) dtBtnTxt.textContent = lang === 'en' ? 'Change' : 'Değiştir';
    const dtModalTitle = $('#dt-picker-modal-title'); if (dtModalTitle) dtModalTitle.textContent = lang === 'en' ? 'Select Date & Time' : 'Tarih & Saat Seçimi';
    const dtTimeLbl = $('#dt-time-label'); if (dtTimeLbl) dtTimeLbl.textContent = lang === 'en' ? 'Time Selection' : 'Saat Seçimi';
    const dtCancelBtn = $('#btn-dt-picker-cancel'); if (dtCancelBtn) dtCancelBtn.textContent = lang === 'en' ? 'Cancel' : 'İptal';
    const updTitle = $('#update-modal-title'); if (updTitle) updTitle.textContent = lang === 'en' ? 'New Version Available!' : 'Yeni Sürüm Mevcut!';
    const updNow = $('#btn-update-now'); if (updNow && !updNow.disabled) updNow.innerHTML = lang === 'en' ? '⚡ Update Now' : '⚡ Şimdi Güncelle';
    const updRemind = $('#btn-update-remind'); if (updRemind) updRemind.innerHTML = lang === 'en' ? '⏳ Remind Later' : '⏳ Daha Sonra Anımsat';
    
    const daysHeader = $('#cal-days-header');
    if (daysHeader) {
      daysHeader.innerHTML = lang === 'en' 
        ? '<span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span><span>Su</span>'
        : '<span>Pt</span><span>Sa</span><span>Ça</span><span>Pe</span><span>Cu</span><span>Ct</span><span>Pz</span>';
    }
    if (typeof renderCalendarGrid === 'function') renderCalendarGrid();
    if (typeof updateScheduleDisplayText === 'function') updateScheduleDisplayText();

    $$('.shutdown-option-card').forEach((card, idx) => {
      const titles = lang === 'en' ? ['❌ Do nothing', '💤 Put to Sleep', '⚡ Shutdown PC'] : ['❌ Hiçbir şey yapma', '💤 Uyku Moduna Al', '⚡ Bilgisayarı Kapat'];
      const descs = lang === 'en' ? [
        'App continues running normally when downloads complete.',
        'Windows enters sleep mode. Your session is preserved.',
        '60-second countdown begins. Can be cancelled via abort command.'
      ] : [
        'İndirmeler bitince uygulama normal çalışmaya devam eder.',
        'Windows uyku moduna alınır. Oturumunuz korunur.',
        '60 saniye geri sayım başlar. Abort komutu ile iptal edebilirsiniz.'
      ];
      const tDiv = card.querySelector('div > div:nth-child(1)');
      const dDiv = card.querySelector('div > div:nth-child(2)');
      if (tDiv && titles[idx]) tDiv.textContent = titles[idx];
      if (dDiv && descs[idx]) dDiv.textContent = descs[idx];
    });

    // Refresh Dynamic Quality Dropdowns
    if (typeof updateDynamicQualityOptions === 'function') {
      const fmtSelect = document.getElementById('format-select');
      if (fmtSelect) updateDynamicQualityOptions(fmtSelect.value);
    }
    $$('.cyber-dropdown').forEach(custom => {
      const select = custom.parentElement?.querySelector('select');
      if (select) {
        Array.from(select.options).forEach((opt, idx) => {
          const item = custom.querySelectorAll('.cyber-dropdown-item')[idx];
          if (item) item.textContent = opt.text;
        });
        if (select.selectedIndex >= 0) {
          const textSpan = custom.querySelector('.cyber-dropdown-text');
          if (textSpan) textSpan.textContent = select.options[select.selectedIndex]?.text;
        }
      }
    });

    // Refresh download cards & UI
    if (state && state.downloads) {
      state.downloads.forEach(dl => updateDownloadCard(dl));
    }
  }

  function applySettingsToUI(settings) {
    if (!settings) return;

    if (settings.language) {
      const langEl = $('#setting-language');
      if (langEl) langEl.value = settings.language;
      applyLanguage(settings.language);
    }
    if (settings.start_minimized !== undefined) {
      const el = $('#setting-start-minimized');
      if (el) el.checked = settings.start_minimized;
    }
    if (settings.clipboard_monitor !== undefined) {
      const el = $('#setting-clipboard-monitor');
      if (el) el.checked = settings.clipboard_monitor;
    }
    if (settings.auto_start_windows !== undefined) {
      const el = $('#setting-auto-start-windows');
      if (el) el.checked = settings.auto_start_windows;
    }
    if (settings.background_mode !== undefined) {
      const el = $('#setting-background-mode');
      if (el) el.checked = settings.background_mode;
    }
    if (settings.embed_metadata !== undefined) {
      const el = $('#setting-embed-metadata');
      if (el) el.checked = settings.embed_metadata;
    }
    if (settings.download_subtitles !== undefined) {
      const el = $('#setting-download-subtitles');
      if (el) el.checked = settings.download_subtitles;
    }
    if (settings.auto_compress !== undefined) {
      const el = $('#setting-auto-compress');
      if (el) {
        el.checked = settings.auto_compress;
        const delRow = $('#setting-delete-archive-row');
        if (delRow) delRow.style.display = el.checked ? 'flex' : 'none';
      }
    }
    if (settings.delete_after_archive !== undefined) {
      const el = $('#setting-delete-after-archive');
      if (el) el.checked = settings.delete_after_archive;
    }
    if (settings.compress_format) {
      const el = $('#setting-compress-format');
      if (el) el.value = settings.compress_format;
    }
    if (settings.download_path) {
      dom.settingDownloadPath.value = settings.download_path;
    }
    if (settings.subfolders !== undefined) {
      const el = $('#setting-subfolders');
      if (el) el.checked = settings.subfolders;
    }
    if (settings.filename_template) {
      const el = $('#setting-filename-template');
      if (el) el.value = settings.filename_template;
    }
    if (settings.video_format) {
      const el = $('#setting-video-format');
      if (el) el.value = settings.video_format;
    }
    if (settings.blocklist !== undefined) {
      state.settings.blocklist = settings.blocklist;
      renderBlocklist();
    }
    if (settings.audio_format) {
      const el = $('#setting-audio-format');
      if (el) el.value = settings.audio_format;
    }
    if (settings.default_quality) {
      const el = $('#setting-default-quality');
      if (el) el.value = settings.default_quality;
    }
    if (settings.mp3_bitrate) {
      const el = $('#setting-mp3-bitrate');
      if (el) el.value = String(settings.mp3_bitrate);
    }
    if (settings.concurrent_downloads !== undefined) {
      if (dom.settingConcurrent) dom.settingConcurrent.value = settings.concurrent_downloads;
    }
    if (settings.speed_limit !== undefined) {
      const el = $('#setting-speed-limit');
      const mbVal = settings.speed_limit >= 1048576 ? Math.round(settings.speed_limit / 1048576) : settings.speed_limit;
      if (el) el.value = mbVal;
    }
    if (settings.proxy) {
      const el = $('#setting-proxy');
      if (el) el.value = settings.proxy;
    }
    if (settings.ffmpeg_path) {
      const el = $('#setting-ffmpeg-path');
      if (el) el.value = settings.ffmpeg_path;
    }
    if (settings.start_minimized !== undefined) {
      const el = $('#setting-start-minimized');
      if (el) el.checked = settings.start_minimized;
    }
    if (settings.sequential_download !== undefined) {
      const el = $('#setting-sequential-download');
      if (el) {
        el.checked = settings.sequential_download;
        // If sequential is checked, force concurrent to 1 in the UI and disable it
        const concEl = dom.settingConcurrent;
        if (concEl) {
          if (settings.sequential_download) {
            concEl.dataset.prevValue = concEl.value;
            concEl.value = '1';
            concEl.disabled = true;
          } else {
            if (concEl.dataset.prevValue) {
              concEl.value = concEl.dataset.prevValue;
            }
            concEl.disabled = false;
          }
        }
      }
    }
    
    // Add real-time listener for sequential toggle
    const seqEl = $('#setting-sequential-download');
    if (seqEl && !seqEl.dataset.bound) {
      seqEl.dataset.bound = 'true';
      seqEl.addEventListener('change', () => {
        const concEl = dom.settingConcurrent;
        if (concEl) {
          if (seqEl.checked) {
            concEl.dataset.prevValue = concEl.value;
            concEl.value = '1';
            concEl.disabled = true;
          } else {
            if (concEl.dataset.prevValue) {
              concEl.value = concEl.dataset.prevValue;
            }
            concEl.disabled = false;
          }
        }
        saveCurrentSettings();
      });
    }

    syncCustomSelects();
    renderCustomSites();
  }

  async function saveCurrentSettings() {
    const settings = {
      language: $('#setting-language')?.value || 'tr',
      start_minimized: $('#setting-start-minimized')?.checked || false,
      clipboard_monitor: $('#setting-clipboard-monitor')?.checked ?? true,
      auto_start_windows: $('#setting-auto-start-windows')?.checked ?? false,
      background_mode: $('#setting-background-mode')?.checked ?? true,
      embed_metadata: $('#setting-embed-metadata')?.checked ?? true,
      download_subtitles: $('#setting-download-subtitles')?.checked ?? false,
      auto_compress: $('#setting-auto-compress')?.checked || false,
      delete_after_archive: $('#setting-delete-after-archive')?.checked || false,
      compress_format: $('#setting-compress-format')?.value || 'zip',
      download_path: dom.settingDownloadPath?.value || '',
      subfolders: $('#setting-subfolders')?.checked ?? true,
      filename_template: $('#setting-filename-template')?.value || '%(title)s.%(ext)s',
      video_format: $('#setting-video-format')?.value || 'mp4',
      audio_format: $('#setting-audio-format')?.value || 'mp3',
      default_quality: $('#setting-default-quality')?.value || 'best',
      mp3_bitrate: parseInt($('#setting-mp3-bitrate')?.value) || 192,
      concurrent_downloads: $('#setting-sequential-download')?.checked ? 1 : (dom.settingConcurrent && dom.settingConcurrent.value !== '' && parseInt(dom.settingConcurrent.value, 10) > 0 ? parseInt(dom.settingConcurrent.value, 10) : 3),
      sequential_download: $('#setting-sequential-download')?.checked ?? false,
      speed_limit: (parseFloat($('#setting-speed-limit')?.value) || 0) * 1024 * 1024,
      proxy: $('#setting-proxy')?.value || '',
      ffmpeg_path: $('#setting-ffmpeg-path')?.value || '',
      theme: state.settings?.theme || document.body.dataset.theme || 'suylios',
      site_settings: state.settings?.site_settings || {},
      custom_sites: state.settings?.custom_sites || [],
      custom_themes: state.settings?.custom_themes || [],
      blocklist: state.settings?.blocklist || [],
    };

    const result = await callApi('save_settings', JSON.stringify(settings));
    if (result?.success) {
      state.settings = settings;
    }
  }

  function renderBlocklist() {
    const container = $('#blocklist-container');
    if (!container) return;
    container.innerHTML = '';
    const list = state.settings?.blocklist || [];
    if (list.length === 0) {
      container.innerHTML = '<span style="font-size:12px; color:var(--text-tertiary);">Henüz engellenen link eklenmedi.</span>';
      return;
    }
    list.forEach(item => {
      const tag = document.createElement('div');
      tag.className = 'site-tag tag-cyan';
      tag.style.display = 'flex';
      tag.style.alignItems = 'center';
      tag.style.gap = '5px';
      tag.style.paddingRight = '5px';
      
      const text = document.createElement('span');
      text.textContent = item;
      
      const removeBtn = document.createElement('span');
      removeBtn.innerHTML = '✕';
      removeBtn.style.cursor = 'pointer';
      removeBtn.style.opacity = '0.7';
      removeBtn.style.fontSize = '10px';
      removeBtn.addEventListener('click', () => {
        state.settings.blocklist = state.settings.blocklist.filter(x => x !== item);
        renderBlocklist();
        saveCurrentSettings();
      });
      removeBtn.addEventListener('mouseover', () => removeBtn.style.opacity = '1');
      removeBtn.addEventListener('mouseout', () => removeBtn.style.opacity = '0.7');
      
      tag.appendChild(text);
      tag.appendChild(removeBtn);
      container.appendChild(tag);
    });
  }

  // ─── STATUS BAR ───
  function updateStatusBar(downloads) {
    if (!downloads) return;

    const active = downloads.filter(d => d.status === 'downloading' || d.status === 'converting' || d.status === 'merging');
    const totalSpeed = active.reduce((sum, d) => sum + (d.speed || 0), 0);

    const activeLabel = window.CURRENT_LANG === 'en' ? 'active downloads' : 'aktif indirme';
    dom.statusActive.textContent = `${active.length} ${activeLabel}`;

    // Update dot
    if (active.length > 0) {
      dom.statusDot.classList.add('active');
      dom.statusDot.classList.remove('pulse');
    } else {
      dom.statusDot.classList.remove('active');
      dom.statusDot.classList.add('pulse');
    }

    // Speed
    const speedSvg = `<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>`;
    dom.statusSpeed.innerHTML = `${speedSvg} ${formatSpeed(totalSpeed)}`;

    // Storage — static for now
    const storageSvg = `<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M2 20h20v-4H2v4zm2-3h2v2H4v-2zM2 4v4h20V4H2zm4 3H4V5h2v2zm-4 7h20v-4H2v4zm2-3h2v2H4v-2z"/></svg>`;
    const fileLabel = window.CURRENT_LANG === 'en' ? 'files' : 'dosya';
    dom.statusStorage.innerHTML = `${storageSvg} ${downloads.length} ${fileLabel}`;
  }

  // ─── TOAST NOTIFICATIONS ───
  function showToast(message, type = 'info', duration = 3500) {
    const lang = window.CURRENT_LANG || 'tr';
    const map = lang === 'en' ? TR_TO_EN : EN_TO_TR;
    let msg = map[message] || message;
    if (lang === 'en') {
      if (msg.endsWith(' tamamlandı!')) msg = msg.replace(' tamamlandı!', ' completed!');
      if (msg.endsWith(' başarısız oldu!')) msg = msg.replace(' başarısız oldu!', ' failed!');
    } else {
      if (msg.endsWith(' completed!')) msg = msg.replace(' completed!', ' tamamlandı!');
      if (msg.endsWith(' failed!')) msg = msg.replace(' failed!', ' başarısız oldu!');
    }

    const existing = dom.toastContainer?.querySelectorAll('.toast-message');
    if (existing) {
      for (const el of existing) {
        if (el.textContent === msg) return;
      }
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icons = {
      success: '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
      error:   '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>',
      warning: '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>',
      info:    '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>',
    };

    toast.innerHTML = `
      <span class="toast-icon">${icons[type] || icons.info}</span>
      <span class="toast-message">${escapeHtml(msg)}</span>
    `;

    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('toast-exit');
      setTimeout(() => toast.remove(), 350);
    }, duration);
  }

  // ─── FORMATTING HELPERS ───
  function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec <= 0) return '0 B/s';
    const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
    let i = 0;
    let val = bytesPerSec;
    while (val >= 1024 && i < units.length - 1) {
      val /= 1024;
      i++;
    }
    return val.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
  }

  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let val = bytes;
    while (val >= 1024 && i < units.length - 1) {
      val /= 1024;
      i++;
    }
    return val.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
  }

  function formatSizeRange(downloaded, total) {
    const dl = formatSize(downloaded || 0);
    const tot = total ? formatSize(total) : '—';
    return `${dl} / ${tot}`;
  }

  function formatEta(seconds) {
    if (!seconds || seconds <= 0) return 'Hesaplanıyor...';
    if (seconds < 60) return `${Math.round(seconds)}s kaldı`;
    if (seconds < 3600) {
      const m = Math.floor(seconds / 60);
      const s = Math.round(seconds % 60);
      return `${m}dk ${s}s kaldı`;
    }
    const h = Math.floor(seconds / 3600);
    const m = Math.round((seconds % 3600) / 60);
    return `${h}sa ${m}dk kaldı`;
  }

  function truncateUrl(url, maxLen = 60) {
    if (!url) return '';
    if (url.length <= maxLen) return url;
    return url.substring(0, maxLen - 3) + '...';
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ─── THEMES & APPEARANCE ───
  
  function applyCustomTheme(themeId, themesArray = null) {
    let styleTag = document.getElementById('custom-theme-style-tag');
    if (!styleTag) {
      styleTag = document.createElement('style');
      styleTag.id = 'custom-theme-style-tag';
      document.head.appendChild(styleTag);
    }
    
    if (themeId && themeId.startsWith('custom_')) {
      let themes = themesArray || (state.settings?.custom_themes || []);
      if (themes.length === 0) {
        try { themes = JSON.parse(localStorage.getItem('suylios_custom_themes') || '[]'); } catch(e) {}
      }
      const theme = themes.find(t => t.id === themeId);
      if (theme) {
        // We set data-theme to the base template to inherit properties,
        // then override the CSS variables using the style tag.
        document.body.dataset.theme = theme.base;
        
        let css = `body[data-theme="${theme.base}"] {\n`;
        if (theme.colors.bgPrimary) css += `  --bg-primary: ${theme.colors.bgPrimary} !important;\n`;
        if (theme.colors.bgSecondary) css += `  --bg-secondary: ${theme.colors.bgSecondary} !important;\n  --bg-card: ${theme.colors.bgSecondary} !important;\n`;
        if (theme.colors.textPrimary) css += `  --text-primary: ${theme.colors.textPrimary} !important;\n`;
        if (theme.colors.textSecondary) css += `  --text-secondary: ${theme.colors.textSecondary} !important;\n`;
        if (theme.colors.accent1) css += `  --accent-cyan: ${theme.colors.accent1} !important;\n`;
        if (theme.colors.accent2) css += `  --accent-purple: ${theme.colors.accent2} !important;\n`;
        
        // Update gradient based on accents
        if (theme.colors.accent1 && theme.colors.accent2) {
          css += `  --accent-gradient: linear-gradient(135deg, ${theme.colors.accent1} 0%, ${theme.colors.accent2} 100%) !important;\n`;
          css += `  --accent-gradient-h: linear-gradient(90deg, ${theme.colors.accent1} 0%, ${theme.colors.accent2} 100%) !important;\n`;
        }
        
        css += `}\n`;
        
        // Also override titlebar colors if white base
        if (theme.base === 'basit-beyaz') {
           css += `body[data-theme="basit-beyaz"] .app-title { color: ${theme.colors.textPrimary} !important; }\n`;
           css += `body[data-theme="basit-beyaz"] #titlebar { background: ${theme.colors.bgSecondary} !important; }\n`;
        }
        
        styleTag.textContent = css;
        return;
      }
    }
    
    // Built-in theme or fallback
    document.body.dataset.theme = themeId || 'suylios';
    styleTag.textContent = '';
  }

  function renderCustomThemes() {
    const grid = document.getElementById('custom-themes-grid');
    const emptyState = document.getElementById('custom-themes-empty');
    if (!grid || !emptyState) return;
    
    // Remove existing custom cards
    grid.querySelectorAll('.theme-card.custom-card').forEach(el => el.remove());
    
    let themes = state.settings?.custom_themes || [];
    if (themes.length === 0) {
      try { themes = JSON.parse(localStorage.getItem('suylios_custom_themes') || '[]'); } catch(e) {}
    }
    if (themes.length === 0) {
      emptyState.style.display = 'block';
      return;
    }
    
    emptyState.style.display = 'none';
    
    const savedTheme = localStorage.getItem('suylios_theme') || 'suylios';
    
    themes.forEach(theme => {
      const card = document.createElement('div');
      card.className = 'theme-card custom-card';
      if (savedTheme === theme.id) card.classList.add('active');
      card.dataset.theme = theme.id;
      card.dataset.isCustom = 'true';
      
      const baseImgMap = {
        'suylios': 'suylios.png',
        'basit-beyaz': 'beyaz.png',
        'basit-koyu': 'koyu.png',
        'matrix': 'matrix.png',
        'blood': 'crimson.png',
        'sunset': 'gold.png'
      };
      const imgFile = baseImgMap[theme.base] || 'suylios.png';
      const bgStyle = `background-image: linear-gradient(135deg, rgba(0,0,0,0.25) 0%, ${theme.colors.accent1}66 100%), url('../assets/themes/${imgFile}'); background-size: cover; background-position: top center;`;
      
      card.innerHTML = `
        <div class="theme-preview" style="${bgStyle} position:relative; overflow:hidden;">
           <div style="position:absolute; bottom:0; left:0; width:100%; height:4px; background: linear-gradient(90deg, ${theme.colors.accent1}, ${theme.colors.accent2});"></div>
           <button class="btn-edit-theme" title="Düzenle">
             <svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor" d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
             <span>Düzenle</span>
           </button>
        </div>
        <span class="theme-name">${escapeHtml(theme.name)}</span>
      `;
      
      card.onclick = (e) => {
        if (e.target.closest('.btn-edit-theme')) {
          e.stopPropagation();
          openThemeEditor(theme);
          return;
        }
        selectTheme(theme.id);
      };
      
      grid.appendChild(card);
    });
  }

  function selectTheme(themeId, silent) {
    applyCustomTheme(themeId);
    localStorage.setItem('suylios_theme', themeId);
    
    document.querySelectorAll('.theme-card').forEach(c => c.classList.remove('active'));
    const activeCard = document.querySelector(`.theme-card[data-theme="${themeId}"]`);
    if (activeCard) activeCard.classList.add('active');
    
    if (state.settings) {
      state.settings.theme = themeId;
      if (!silent) saveCurrentSettings();
    }
    if (!silent) showToast('Tema güncellendi', 'success');
  }

  function bindThemes() {
    if (window._themesBound) return;
    window._themesBound = true;

    const savedTheme = localStorage.getItem('suylios_theme') || 'basit-beyaz';
    applyCustomTheme(savedTheme);

    const builtInCards = document.querySelectorAll('.theme-cards-grid:not(#custom-themes-grid) .theme-card');
    builtInCards.forEach(card => {
      card.classList.toggle('active', card.dataset.theme === savedTheme);
      card.onclick = () => {
        selectTheme(card.dataset.theme);
      };
    });
    
    renderCustomThemes();
    bindThemeEditor();
  }

  function bindThemeEditor() {
    if (window._themeEditorBound) return;
    window._themeEditorBound = true;
    const modal = document.getElementById('custom-theme-modal');
    const btnCreate = document.getElementById('btn-create-theme');
    const btnClose = document.getElementById('btn-close-theme-modal');
    const btnCancel = document.getElementById('btn-cancel-theme');
    const btnSave = document.getElementById('btn-save-theme');
    const btnDelete = document.getElementById('btn-delete-theme');
    
    if (!modal || !btnCreate) return;
    
    const colorInputs = document.querySelectorAll('.theme-color-picker');
    const baseSelect = document.getElementById('custom-theme-base');
    
    function updatePreview() {
      const base = baseSelect.value;
      const fakeThemeId = 'custom_preview';
      const fakeTheme = {
        id: fakeThemeId,
        base: base,
        colors: {
          bgPrimary: document.getElementById('color-bg-primary').value,
          bgSecondary: document.getElementById('color-bg-secondary').value,
          textPrimary: document.getElementById('color-text-primary').value,
          textSecondary: document.getElementById('color-text-secondary').value,
          accent1: document.getElementById('color-accent-1').value,
          accent2: document.getElementById('color-accent-2').value,
        }
      };
      applyCustomTheme(fakeThemeId, [fakeTheme]);
    }
    
    colorInputs.forEach(input => {
      input.oninput = (e) => {
        const hexSpan = document.getElementById('hex-' + e.target.id.replace('color-', ''));
        if (hexSpan) hexSpan.textContent = e.target.value;
        updatePreview();
      };
    });
    
    const templatePresets = {
      'suylios': { bgPrimary: '#0a0a0f', bgSecondary: '#0f0f18', textPrimary: '#e8eaed', textSecondary: '#9aa0a6', accent1: '#00f0ff', accent2: '#b44aff' },
      'basit-beyaz': { bgPrimary: '#f0f2f5', bgSecondary: '#ffffff', textPrimary: '#1a1a24', textSecondary: '#4a5568', accent1: '#0066ff', accent2: '#0052cc' },
      'basit-koyu': { bgPrimary: '#121214', bgSecondary: '#18181b', textPrimary: '#f4f4f5', textSecondary: '#a1a1aa', accent1: '#a1a1aa', accent2: '#71717a' },
      'matrix': { bgPrimary: '#050b06', bgSecondary: '#0a140c', textPrimary: '#e0ffe0', textSecondary: '#66aa66', accent1: '#00ff66', accent2: '#00b347' },
      'blood': { bgPrimary: '#0d0608', bgSecondary: '#160a0e', textPrimary: '#ffe6ed', textSecondary: '#aa667a', accent1: '#ff2a6d', accent2: '#b31243' },
      'sunset': { bgPrimary: '#0f0a14', bgSecondary: '#181022', textPrimary: '#fffbf0', textSecondary: '#baa2d6', accent1: '#ff9e00', accent2: '#ff5200' }
    };

    baseSelect.onchange = () => {
      const preset = templatePresets[baseSelect.value];
      if (preset) {
        const setCol = (id, val) => {
          const input = document.getElementById('color-' + id);
          if (input) input.value = val;
          const hexSpan = document.getElementById('hex-' + id);
          if (hexSpan) hexSpan.textContent = val;
        };
        setCol('bg-primary', preset.bgPrimary);
        setCol('bg-secondary', preset.bgSecondary);
        setCol('text-primary', preset.textPrimary);
        setCol('text-secondary', preset.textSecondary);
        setCol('accent-1', preset.accent1);
        setCol('accent-2', preset.accent2);
      }
      updatePreview();
    };
    
    btnCreate.onclick = (e) => {
      if (e) { e.preventDefault(); e.stopPropagation(); }
      document.getElementById('custom-theme-id').value = '';
      document.getElementById('custom-theme-name').value = '';
      btnDelete.style.display = 'none';
      modal.classList.remove('hidden');
      
      const currentThemeId = localStorage.getItem('suylios_theme') || document.body.dataset.theme || 'suylios';
      let activeBase = 'suylios';
      let activeColors = templatePresets['suylios'];

      if (currentThemeId.startsWith('custom_')) {
        let customThemes = state.settings?.custom_themes || [];
        if (customThemes.length === 0) {
          try { customThemes = JSON.parse(localStorage.getItem('suylios_custom_themes') || '[]'); } catch(e){}
        }
        const found = customThemes.find(t => t.id === currentThemeId);
        if (found) {
          activeBase = found.base || 'suylios';
          activeColors = found.colors || templatePresets[activeBase] || templatePresets['suylios'];
        }
      } else if (templatePresets[currentThemeId]) {
        activeBase = currentThemeId;
        activeColors = templatePresets[currentThemeId];
      }

      const baseSel = document.getElementById('custom-theme-base');
      if (baseSel) {
        baseSel.value = activeBase;
        const opt = baseSel.options[baseSel.selectedIndex];
        const textSpan = baseSel.closest('div')?.querySelector('.cyber-dropdown-text');
        if (textSpan && opt) textSpan.textContent = opt.text;
      }
      
      const setCol = (id, val) => {
        const input = document.getElementById('color-' + id);
        if (input) input.value = val;
        const hexSpan = document.getElementById('hex-' + id);
        if (hexSpan) hexSpan.textContent = val;
      };
      
      setCol('bg-primary', activeColors.bgPrimary || '#0a0a0f');
      setCol('bg-secondary', activeColors.bgSecondary || '#0f0f18');
      setCol('text-primary', activeColors.textPrimary || '#e8eaed');
      setCol('text-secondary', activeColors.textSecondary || '#9aa0a6');
      setCol('accent-1', activeColors.accent1 || '#00f0ff');
      setCol('accent-2', activeColors.accent2 || '#b44aff');
      
      updatePreview();
    };
    
    window.openThemeEditor = function(theme) {
      if (!modal.classList.contains('hidden')) return;
      document.getElementById('custom-theme-id').value = theme.id;
      document.getElementById('custom-theme-name').value = theme.name;
      
      const baseSel = document.getElementById('custom-theme-base');
      if (baseSel) {
        baseSel.value = theme.base;
        const opt = baseSel.options[baseSel.selectedIndex];
        const textSpan = baseSel.closest('div')?.querySelector('.cyber-dropdown-text');
        if (textSpan && opt) textSpan.textContent = opt.text;
      }
      
      btnDelete.style.display = 'flex';
      
      const setCol = (id, val) => {
        const input = document.getElementById('color-' + id);
        if (input) input.value = val;
        const hexSpan = document.getElementById('hex-' + id);
        if (hexSpan) hexSpan.textContent = val;
      };
      
      setCol('bg-primary', theme.colors.bgPrimary);
      setCol('bg-secondary', theme.colors.bgSecondary);
      setCol('text-primary', theme.colors.textPrimary);
      setCol('text-secondary', theme.colors.textSecondary);
      setCol('accent-1', theme.colors.accent1);
      setCol('accent-2', theme.colors.accent2);
      
      modal.classList.remove('hidden');
      updatePreview();
    };
    
    const closeModal = () => {
      modal.classList.add('hidden');
      const currentTheme = localStorage.getItem('suylios_theme') || 'basit-beyaz';
      applyCustomTheme(currentTheme);
    };
    
    btnClose.onclick = closeModal;
    btnCancel.onclick = closeModal;
    modal.onclick = (e) => {
      if (e.target === modal) closeModal();
    };
    
    btnSave.onclick = () => {
      const id = document.getElementById('custom-theme-id').value || ('custom_' + Date.now());
      let name = document.getElementById('custom-theme-name').value.trim();
      if (!name) name = 'Yeni Tema';
      
      const theme = {
        id: id,
        name: name,
        base: document.getElementById('custom-theme-base').value,
        colors: {
          bgPrimary: document.getElementById('color-bg-primary').value,
          bgSecondary: document.getElementById('color-bg-secondary').value,
          textPrimary: document.getElementById('color-text-primary').value,
          textSecondary: document.getElementById('color-text-secondary').value,
          accent1: document.getElementById('color-accent-1').value,
          accent2: document.getElementById('color-accent-2').value,
        }
      };
      
      if (!state.settings) state.settings = {};
      let themes = [];
      try { themes = JSON.parse(localStorage.getItem('suylios_custom_themes') || '[]'); } catch(e) {}
      if (themes.length === 0 && state.settings.custom_themes) themes = [...state.settings.custom_themes];
      
      const existingIdx = themes.findIndex(t => t.id === id);
      if (existingIdx >= 0) {
        themes[existingIdx] = theme;
      } else {
        themes.push(theme);
      }
      
      state.settings.custom_themes = themes;
      try { localStorage.setItem('suylios_custom_themes', JSON.stringify(themes)); } catch(e) {}
      saveCurrentSettings();
      renderCustomThemes();
      selectTheme(id);
      
      modal.classList.add('hidden');
    };
    
    btnDelete.onclick = async (e) => {
      if (e) e.preventDefault();
      const id = document.getElementById('custom-theme-id').value;
      if (!id) return;
      const ok = await customConfirm('Temayı Sil', 'Bu temayı silmek istediğinize emin misiniz?', true, 'Sil');
      if (!ok) return;
      
      if (!state.settings) state.settings = {};
      let themes = [];
      try { themes = JSON.parse(localStorage.getItem('suylios_custom_themes') || '[]'); } catch(e) {}
      if (themes.length === 0 && state.settings.custom_themes) themes = [...state.settings.custom_themes];

      themes = themes.filter(t => t.id !== id);
      state.settings.custom_themes = themes;
      try { localStorage.setItem('suylios_custom_themes', JSON.stringify(themes)); } catch(e) {}
      
      const currentTheme = localStorage.getItem('suylios_theme') || document.body.dataset.theme;
      if (currentTheme === id) {
        selectTheme('basit-beyaz');
      }
      
      renderCustomThemes();
      saveCurrentSettings();
      
      modal.classList.add('hidden');
    };
  }

  // ─── SITE CONFIGURATION MODAL ───
  function bindSiteSettings() {
    if (window._siteSettingsBound) return;
    window._siteSettingsBound = true;
    const modal = document.getElementById('site-modal');
    const closeBtn = document.getElementById('btn-close-modal');
    const saveBtn = document.getElementById('btn-save-site');
    let currentSiteKey = '';

    document.querySelectorAll('.btn-site-config').forEach(btn => {
      const newBtn = btn.cloneNode(true);
      btn.parentNode.replaceChild(newBtn, btn);
      newBtn.onclick = (e) => {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        if (!modal || !modal.classList.contains('hidden')) return;
        currentSiteKey = newBtn.dataset.site;
        const siteName = newBtn.dataset.name || 'Platform';
        const defaultFolder = newBtn.dataset.folder || 'Folder';
        
        document.getElementById('modal-site-title').textContent = `${siteName} Yapılandırması`;
        
        const siteCfg = state.settings?.site_settings?.[currentSiteKey] || {};
        document.getElementById('modal-site-folder').value = siteCfg.folder || defaultFolder;
        document.getElementById('modal-site-cookies').value = siteCfg.cookies || '';
        document.getElementById('modal-site-quality').value = siteCfg.quality || 'best';
        
        const tokenRow = document.getElementById('modal-token-row');
        const accountIdRow = document.getElementById('modal-account-id-row');
        const tokenInput = document.getElementById('modal-site-token');
        const accountIdInput = document.getElementById('modal-site-account-id');
        const isGofile = (currentSiteKey === 'gofile' || currentSiteKey?.toLowerCase() === 'gofile');

        if (isGofile) {
          if (tokenRow) {
            tokenRow.classList.remove('hidden');
            tokenRow.style.removeProperty('display');
          }
          if (accountIdRow) {
            accountIdRow.classList.remove('hidden');
            accountIdRow.style.removeProperty('display');
          }
          if (tokenInput) {
            const rawTok = siteCfg.token || '';
            tokenInput.value = (rawTok === 'xSpfjPMJNfMWKw4cKOaJVBmbzjxeGr3Y') ? '' : rawTok;
            tokenInput.placeholder = 'Örn: xSpfjPM... (boş bırakırsanız varsayılan kullanılır)';
          }
          if (accountIdInput) {
            const rawAcc = siteCfg.account_id || '';
            accountIdInput.value = (rawAcc === '9cd8af62-f3ea-4d2a-88d0-25b0ae9c2506') ? '' : rawAcc;
            accountIdInput.placeholder = 'Örn: 9cd8af... (veya boş bırakın)';
          }
        } else {
          if (tokenRow) {
            tokenRow.classList.add('hidden');
            tokenRow.style.setProperty('display', 'none', 'important');
          }
          if (accountIdRow) {
            accountIdRow.classList.add('hidden');
            accountIdRow.style.setProperty('display', 'none', 'important');
          }
          if (tokenInput) tokenInput.value = '';
          if (accountIdInput) accountIdInput.value = '';
        }
        
        modal.classList.remove('hidden');
      };
    });

    if (closeBtn) {
      const newClose = closeBtn.cloneNode(true);
      closeBtn.parentNode.replaceChild(newClose, closeBtn);
      newClose.addEventListener('click', () => modal?.classList.add('hidden'));
    }

    const pickBtn = document.getElementById('btn-pick-cookies');
    if (pickBtn) {
      const newPick = pickBtn.cloneNode(true);
      pickBtn.parentNode.replaceChild(newPick, pickBtn);
      newPick.addEventListener('click', async (e) => {
        e.preventDefault();
        const path = await callApi('pick_file', [['Text Dosyaları (*.txt)', '*.txt'], ['Tüm Dosyalar (*.*)', '*.*']]);
        if (path) {
          document.getElementById('modal-site-cookies').value = path;
        }
      });
    }

    modal?.addEventListener('click', (e) => {
      if (e.target === modal) modal.classList.add('hidden');
    });

    if (saveBtn) {
      const newSave = saveBtn.cloneNode(true);
      saveBtn.parentNode.replaceChild(newSave, saveBtn);
      newSave.addEventListener('click', () => {
        if (!state.settings) state.settings = {};
        if (!state.settings.site_settings) state.settings.site_settings = {};
        
        const folderInput = document.getElementById('modal-site-folder').value.trim();
        const siteObj = {
          folder: folderInput || 'Folder',
          cookies: document.getElementById('modal-site-cookies').value.trim(),
          quality: document.getElementById('modal-site-quality').value
        };
        const isGofileSave = (currentSiteKey === 'gofile' || currentSiteKey?.toLowerCase() === 'gofile');
        if (isGofileSave) {
          siteObj.token = document.getElementById('modal-site-token')?.value.trim() || '';
          siteObj.account_id = document.getElementById('modal-site-account-id')?.value.trim() || '';
        }
        state.settings.site_settings[currentSiteKey] = siteObj;

        saveCurrentSettings();
        modal?.classList.add('hidden');
        showToast('Platform ayarları kaydedildi', 'success');
      });
    }
  }

  function renderCustomSites() {
    const grid = document.getElementById('custom-sites-grid');
    if (!grid) return;
    const customSites = state.settings?.custom_sites || [];
    if (customSites.length === 0) {
      grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 28px; color: var(--text-muted); font-size: 13.5px; border: 1px dashed var(--border-color); border-radius: 12px; background: var(--bg-secondary);">Henüz özel bir site klasörü eklenmedi. &quot;Yeni Site Ekle&quot; butonuna basarak 1775+ desteklenen platform arasından seçip ekleyebilirsiniz.</div>';
      return;
    }

    grid.innerHTML = customSites.map(item => {
      const siteCfg = state.settings?.site_settings?.[item.key] || {};
      const folderName = siteCfg.folder || item.folder || item.name;
      const engineTag = item.engine || 'yt-dlp';
      const tagClass = engineTag === 'gallery-dl' ? 'tag-purple' : (engineTag === 'cyberdrop-dl' ? 'tag-cyan' : 'tag-green');
      return `
        <div class="site-card glass-panel" style="position: relative;">
          <div class="site-card-header">
            <div class="site-logo">
              <img src="https://www.google.com/s2/favicons?domain=${item.domain}&sz=128" onerror="this.src='https://icons.duckduckgo.com/ip3/${item.domain}.ico'" class="site-icon-img" alt="${item.name}">
            </div>
            <div class="site-title">
              <h4>${item.name}</h4>
              <span>downloads/${folderName}</span>
            </div>
          </div>
          <div class="site-tags">
            <span class="site-tag ${tagClass}">${engineTag}</span>
            <span class="site-tag tag-cyan">${(item.features || item.Capabilities) ? (item.features || item.Capabilities).slice(0, 16) + ((item.features || item.Capabilities).length > 16 ? '...' : '') : 'Özel Alt Klasör'}</span>
          </div>
          <div style="display: flex; gap: 8px; width: 100%; margin-top: auto;">
            <button class="btn-site-config glow-btn" data-site="${item.key}" data-name="${item.name}" data-folder="${folderName}" style="flex: 1;">⚙️ Ayarla</button>
            <button class="btn-delete-custom-site btn-secondary" data-key="${item.key}" data-name="${item.name}" title="Bu siteyi ve ayarlarını kaldır" style="padding: 6px 10px; font-size: 13px; border-radius: var(--radius-md); border: 1px solid rgba(255,90,90,0.3); color: #ff5a5a; background: rgba(255,90,90,0.08); cursor: pointer;">🗑️</button>
          </div>
        </div>
      `;
    }).join('');

    // Rebind config modal triggers for these dynamic cards
    bindSiteSettings();

    // Bind delete triggers
    grid.querySelectorAll('.btn-delete-custom-site').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const key = btn.dataset.key;
        const name = btn.dataset.name;
        if (!state.settings) state.settings = {};
        if (state.settings.custom_sites) {
          state.settings.custom_sites = state.settings.custom_sites.filter(s => s.key !== key);
        }
        if (state.settings.site_settings && state.settings.site_settings[key]) {
          delete state.settings.site_settings[key];
        }
        saveCurrentSettings();
        renderCustomSites();
        showToast(`${name} kartı ve tüm ayarları silindi`, 'info');
      });
    });
  }

  function bindAddSiteModal() {
    if (window._addSiteModalBound) return;
    window._addSiteModalBound = true;
    const modal = document.getElementById('add-site-modal');
    const openBtn = document.getElementById('btn-add-custom-site');
    const closeBtn = document.getElementById('btn-close-add-site-modal');
    const cancelBtn = document.getElementById('btn-cancel-add-site');
    const confirmBtn = document.getElementById('btn-confirm-add-site');
    const searchInput = document.getElementById('add-site-search');
    const listContainer = document.getElementById('add-site-list');
    const countSpan = document.getElementById('add-site-selected-count');

    if (!modal || !openBtn) return;

    let availableSites = [];
    let selectedSiteKeys = new Set();

    const BUILTIN_KEYS = new Set([
      'youtube', 'bunkr', 'gofile', 'pixeldrain', 'tiktok', 'twitter', 'instagram', 'reddit',
      'pornhub', 'xvideos', 'rule34', 'hanime', 'hitomi', 'ehentai', 'twitch', 'vimeo',
      'soundcloud', 'dailymotion', 'imgur', 'flickr', 'kemono', 'coomer', 'erome', 'xhamster',
      'cyberdrop', 'mega', '4chan', 'bluesky', 'other'
    ]);

    async function openModal() {
      modal.classList.remove('hidden');
      selectedSiteKeys.clear();
      updateSelectedCount();
      if (searchInput) searchInput.value = '';

      if (listContainer) {
        listContainer.innerHTML = '<div style="text-align: center; padding: 24px; color: var(--text-muted); font-size: 13px;">Siteler yükleniyor...</div>';
      }

      // Load supported sites if not cached
      if (!window._cachedSupportedSites) {
        try {
          const res = await callApi('get_supported_sites');
          if (Array.isArray(res)) window._cachedSupportedSites = res;
        } catch (e) {
          console.error('Failed to get_supported_sites via API:', e);
        }
      }
      const allSites = window._cachedSupportedSites || [];

      // Filter out built-in and already added custom sites
      const existingCustomDomains = new Set((state.settings?.custom_sites || []).map(s => s.domain.toLowerCase()));
      const existingCustomKeys = new Set((state.settings?.custom_sites || []).map(s => s.key.toLowerCase()));

      availableSites = allSites.filter(item => {
        const dom = item.domain.toLowerCase();
        const cleanKey = dom.replace(/\./g, '_').replace(/[^a-z0-9_]/g, '');
        const nameKey = item.name.toLowerCase().replace(/[^a-z0-9]/g, '');
        if (BUILTIN_KEYS.has(cleanKey) || BUILTIN_KEYS.has(nameKey)) return false;
        if (existingCustomDomains.has(dom) || existingCustomKeys.has(cleanKey)) return false;
        return true;
      });

      renderFilteredList('');
    }

    function updateSelectedCount() {
      if (countSpan) countSpan.textContent = `${selectedSiteKeys.size} site seçildi`;
    }

    function renderFilteredList(query) {
      if (!listContainer) return;
      const q = (query || '').trim().toLowerCase();
      const filtered = availableSites.filter(s => {
        if (!q) return true;
        return s.name.toLowerCase().includes(q) || s.domain.toLowerCase().includes(q) || (s.features && s.features.toLowerCase().includes(q));
      }).slice(0, 800); // render up to 800 sites for fast mouse dragging and rich scrollbar

      if (filtered.length === 0) {
        listContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-muted); font-size: 13px;">Aramanızla eşleşen yeni site bulunamadı.</div>';
        return;
      }

      listContainer.innerHTML = filtered.map(item => {
        const cleanKey = item.domain.replace(/\./g, '_').replace(/[^a-zA-Z0-9_]/g, '');
        const checked = selectedSiteKeys.has(cleanKey) ? 'checked' : '';
        const tagClass = item.engine === 'gallery-dl' ? 'tag-purple' : (item.engine === 'cyberdrop-dl' ? 'tag-cyan' : 'tag-green');
        return `
          <label class="add-site-row" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-radius: 8px; cursor: pointer; transition: background 0.15s; border-bottom: 1px solid rgba(255,255,255,0.03);">
            <div style="display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;">
              <input type="checkbox" class="add-site-chk" data-key="${cleanKey}" ${checked} style="width: 16px; height: 16px; cursor: pointer;">
              <img src="https://www.google.com/s2/favicons?domain=${item.domain}&sz=128" onerror="this.src='https://icons.duckduckgo.com/ip3/${item.domain}.ico'" style="width: 18px; height: 18px; border-radius: 4px; flex-shrink: 0;" alt="">
              <div style="display: flex; flex-direction: column; overflow: hidden;">
                <span style="font-size: 13.5px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${item.name}</span>
                <span style="font-size: 11px; color: var(--text-muted);">${item.domain}</span>
              </div>
            </div>
            <div style="display: flex; gap: 6px; align-items: center; flex-shrink: 0;">
              <span class="site-tag ${tagClass}" style="font-size: 10px; padding: 2px 6px;">${item.engine}</span>
            </div>
          </label>
        `;
      }).join('');

      listContainer.querySelectorAll('.add-site-chk').forEach(chk => {
        chk.addEventListener('change', (e) => {
          const k = chk.dataset.key;
          if (chk.checked) selectedSiteKeys.add(k);
          else selectedSiteKeys.delete(k);
          updateSelectedCount();
        });
      });
    }

    openBtn.onclick = (e) => { if (e) e.preventDefault(); if (!modal.classList.contains('hidden')) return; openModal(); };
    if (closeBtn) closeBtn.onclick = () => modal.classList.add('hidden');
    if (cancelBtn) cancelBtn.onclick = () => modal.classList.add('hidden');
    modal.onclick = (e) => { if (e.target === modal) modal.classList.add('hidden'); };

    if (searchInput) {
      searchInput.addEventListener('input', (e) => renderFilteredList(e.target.value));
    }

    if (confirmBtn) {
      confirmBtn.addEventListener('click', () => {
        if (selectedSiteKeys.size === 0) {
          showToast('Lütfen eklemek için en az 1 site seçin', 'warning');
          return;
        }

        if (!state.settings) state.settings = {};
        if (!state.settings.custom_sites) state.settings.custom_sites = [];

        let addedCount = 0;
        const allSites = window._cachedSupportedSites || [];

        selectedSiteKeys.forEach(key => {
          const found = allSites.find(s => s.domain.replace(/\./g, '_').replace(/[^a-zA-Z0-9_]/g, '') === key);
          if (found) {
            const cleanFolder = found.name.replace(/[^a-zA-Z0-9_-]/g, '') || found.name;
            state.settings.custom_sites.push({
              key: key,
              name: found.name,
              domain: found.domain,
              folder: cleanFolder,
              engine: found.engine,
              features: found.features
            });
            addedCount++;
          }
        });

        saveCurrentSettings();
        renderCustomSites();
        modal.classList.add('hidden');
        showToast(`${addedCount} yeni site klasörü eklendi!`, 'success');
      });
    }
  }

  function syncCustomSelects() {
    document.querySelectorAll('.cyber-dropdown').forEach(custom => {
      const wrapper = custom.parentElement;
      const select = wrapper.querySelector('select');
      if (!select) return;
      const selectedOpt = select.options[select.selectedIndex];
      const textSpan = custom.querySelector('.cyber-dropdown-text');
      if (textSpan && selectedOpt) textSpan.textContent = selectedOpt.text;
      custom.querySelectorAll('.cyber-dropdown-item').forEach(i => {
        i.classList.toggle('selected', i.dataset.value === select.value);
      });
    });
  }

  const VIDEO_QUALITIES = [
    { value: 'best', text: '⚡ Otomatik (Orijinal Kalite)' },
    { value: '2160p', text: '🌟 4K Ultra HD (60fps)' },
    { value: '1440p', text: '⚡ 2K Quad HD (1440p)' },
    { value: '1080p', text: '🎯 Full HD (1080p)' },
    { value: '720p', text: '📺 HD Ready (720p)' },
    { value: '480p', text: '📱 Standart SD (480p)' },
    { value: '360p', text: '💾 Tasarruflu (360p)' }
  ];

  const AUDIO_QUALITIES = [
    { value: 'best', text: '⚡ Otomatik (Orijinal Kalite)' },
    { value: 'flac', text: '💎 Kayıpsız (24-bit FLAC)' },
    { value: '320kbps', text: '🔥 320 kbps (HQ MP3)' },
    { value: '256kbps', text: '✨ 256 kbps (AAC Müzik)' },
    { value: '192kbps', text: '⚡ 192 kbps (Standart)' },
    { value: '128kbps', text: '📻 128 kbps (Hızlı Ses)' },
    { value: '64kbps', text: '💾 64 kbps (Mini Boyut)' }
  ];

  function updateDynamicQualityOptions(formatValue) {
    const isAudio = ['mp3', 'flac', 'm4a', 'wav'].includes(formatValue);
    const rawOptions = isAudio ? AUDIO_QUALITIES : VIDEO_QUALITIES;
    const lang = window.CURRENT_LANG || 'tr';
    const map = lang === 'en' ? TR_TO_EN : EN_TO_TR;
    const options = rawOptions.map(o => ({
      value: o.value,
      text: map[o.text] || o.text
    }));
    const select = document.getElementById('quality-select');
    if (!select) return;

    select.innerHTML = options.map(o => `<option value="${o.value}">${o.text}</option>`).join('');
    select.value = options[0].value;

    const wrapper = select.parentElement;
    const custom = wrapper?.querySelector('.cyber-dropdown');
    if (custom) {
      const textSpan = custom.querySelector('.cyber-dropdown-text');
      const menu = custom.querySelector('.cyber-dropdown-menu');
      if (textSpan) textSpan.textContent = options[0].text;
      if (menu) {
        menu.innerHTML = options.map((o, idx) => `
          <div class="cyber-dropdown-item ${idx === 0 ? 'selected' : ''}" data-value="${o.value}">${o.text}</div>
        `).join('');

        custom.querySelectorAll('.cyber-dropdown-item').forEach(item => {
          item.addEventListener('click', (e) => {
            e.stopPropagation();
            select.value = item.dataset.value;
            select.dispatchEvent(new Event('change'));
            if (textSpan) textSpan.textContent = item.textContent;
            custom.querySelectorAll('.cyber-dropdown-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
            menu.classList.add('hidden');
            custom.classList.remove('active');
          });
        });
      }
    }
  }

  // ─── CUSTOM CYBER DROPDOWNS ───
  function setupCustomSelects() {
    document.querySelectorAll('.select-wrapper, .setting-control').forEach(wrapper => {
      const select = wrapper.querySelector('select');
      if (!select || wrapper.querySelector('.cyber-dropdown')) return;
      select.style.display = 'none';

      const custom = document.createElement('div');
      custom.className = 'cyber-dropdown';

      const selectedOpt = select.options[select.selectedIndex];
      custom.innerHTML = `
        <div class="cyber-dropdown-trigger">
          <span class="cyber-dropdown-text">${selectedOpt?.text || ''}</span>
          <svg class="cyber-arrow" viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M7 10l5 5 5-5z"/></svg>
        </div>
        <div class="cyber-dropdown-menu hidden">
          ${Array.from(select.options).map(opt => `
            <div class="cyber-dropdown-item ${opt.selected ? 'selected' : ''}" data-value="${opt.value}">${opt.text}</div>
          `).join('')}
        </div>
      `;

      wrapper.appendChild(custom);

      const trigger = custom.querySelector('.cyber-dropdown-trigger');
      const menu = custom.querySelector('.cyber-dropdown-menu');
      const textSpan = custom.querySelector('.cyber-dropdown-text');

      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const isHidden = menu.classList.contains('hidden');
        document.querySelectorAll('.cyber-dropdown-menu').forEach(m => {
          m.classList.add('hidden');
          m.classList.remove('open-up');
        });
        document.querySelectorAll('.cyber-dropdown').forEach(d => d.classList.remove('active'));
        if (isHidden) {
          // Check if there is enough space below
          const rect = trigger.getBoundingClientRect();
          if (window.innerHeight - rect.bottom < 200) {
            menu.classList.add('open-up');
          }
          menu.classList.remove('hidden');
          custom.classList.add('active');
        }
      });

      custom.querySelectorAll('.cyber-dropdown-item').forEach(item => {
        item.addEventListener('click', (e) => {
          e.stopPropagation();
          const val = item.dataset.value;
          select.value = val;
          select.dispatchEvent(new Event('change'));
          textSpan.textContent = item.textContent;
          custom.querySelectorAll('.cyber-dropdown-item').forEach(i => i.classList.remove('selected'));
          item.classList.add('selected');
          menu.classList.add('hidden');
          custom.classList.remove('active');

          if (select.id === 'format-select') {
            updateDynamicQualityOptions(val);
          }
        });
      });
    });

    const fmtSelect = document.getElementById('format-select');
    fmtSelect?.addEventListener('change', () => updateDynamicQualityOptions(fmtSelect.value));

    document.addEventListener('click', () => {
      document.querySelectorAll('.cyber-dropdown-menu').forEach(m => m.classList.add('hidden'));
      document.querySelectorAll('.cyber-dropdown').forEach(d => d.classList.remove('active'));
    });
  }

  // ─── BOOT ───
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ BATCH DOWNLOAD MODAL
  // ═══════════════════════════════════════════════════════
  function initBatchModal() {
    if (window._batchModalBound) return;
    window._batchModalBound = true;
    const modal = $('#batch-modal');
    const btnOpen = $('#btn-batch');
    const btnClose = $('#btn-close-batch');
    const btnCancel = $('#btn-batch-cancel');
    const btnStart = $('#btn-batch-start');

    if (!modal || !btnOpen) return;

    btnOpen.onclick = (e) => { if (e) e.preventDefault(); if (!modal.classList.contains('hidden')) return; modal.classList.remove('hidden'); };
    const closeModal = () => modal.classList.add('hidden');
    if (btnClose) btnClose.onclick = closeModal;
    if (btnCancel) btnCancel.onclick = closeModal;
    modal.onclick = e => { if (e.target === modal) closeModal(); };

    const txtInput = $('#batch-txt-input');
    txtInput?.addEventListener('change', (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (evt) => {
        const content = evt.target?.result || '';
        const textarea = $('#batch-urls');
        if (textarea) {
          const existing = textarea.value.trim();
          textarea.value = existing ? `${existing}\n${content.trim()}` : content.trim();
          showToast('TXT içeriği eklendi', 'success');
        }
      };
      reader.readAsText(file);
      e.target.value = ''; // Reset so same file can be picked again
    });

    btnStart?.addEventListener('click', async () => {
      const textarea = $('#batch-urls');
      const urlsText = textarea?.value?.trim() || '';
      if (!urlsText) { showToast('URL giriniz', 'error'); return; }

      const format = $('#batch-format-select')?.value || 'auto';
      const quality = $('#batch-quality-select')?.value || 'best';
      const embedMeta = $('#setting-embed-metadata')?.checked ?? true;
      const dlSubs = $('#setting-download-subtitles')?.checked ?? false;
      const compressArchive = $('#home-compress-archive')?.checked || $('#setting-auto-compress')?.checked || false;
      const compressFormat = $('#home-compress-format')?.value || $('#setting-compress-format')?.value || 'zip';

      btnStart.disabled = true;
      btnStart.innerHTML = '⏳ <span>Ekleniyor...</span>';
      const result = await callApi('add_batch_downloads', urlsText, format, quality, embedMeta, dlSubs, compressArchive, compressFormat);
      btnStart.disabled = false;
      btnStart.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg><span>Tümünü Sıraya Ekle</span>';

      if (result?.ok) {
        closeModal();
        if (textarea) textarea.value = '';
        const lang = window.CURRENT_LANG;
        showToast(`${result.count} ${lang === 'en' ? 'downloads added' : 'indirme eklendi'}`, 'success');
      } else {
        showToast(result?.error || 'Hata', 'error');
      }
    });
  }

  let pickerTargetDate = new Date();
  pickerTargetDate.setMinutes(pickerTargetDate.getMinutes() + 5);
  let calViewMonth = pickerTargetDate.getMonth();
  let calViewYear = pickerTargetDate.getFullYear();

  function updateScheduleDisplayText() {
    const dispInp = $('#schedule-display-input');
    const hiddenInp = $('#schedule-datetime');
    if (!dispInp || !hiddenInp || !hiddenInp.value) return;
    const parts = hiddenInp.value.split('T');
    if (parts.length !== 2) return;
    const dateParts = parts[0].split('-');
    if (dateParts.length !== 3) return;
    dispInp.value = `${dateParts[2]}.${dateParts[1]}.${dateParts[0]} ${parts[1]}`;
  }
  window.updateScheduleDisplayText = updateScheduleDisplayText;

  function parseInputToTargetDate(str) {
    if (!str) return null;
    str = str.trim();
    let dStr = str, tStr = "00:00";
    if (str.includes(' ')) {
      const p = str.split(' ');
      dStr = p[0]; tStr = p[1];
    } else if (str.includes('T')) {
      const p = str.split('T');
      dStr = p[0]; tStr = p[1];
    }
    let y = 0, m = 0, d = 0;
    if (dStr.includes('.')) {
      const dp = dStr.split('.');
      if (dp.length === 3) { d = parseInt(dp[0], 10); m = parseInt(dp[1], 10) - 1; y = parseInt(dp[2], 10); }
    } else if (dStr.includes('-')) {
      const dp = dStr.split('-');
      if (dp.length === 3) { y = parseInt(dp[0], 10); m = parseInt(dp[1], 10) - 1; d = parseInt(dp[2], 10); }
    }
    const tp = (tStr || "").split(':');
    let h = 0, min = 0;
    if (tp.length >= 2) { h = parseInt(tp[0], 10); min = parseInt(tp[1], 10); }
    if (!isNaN(y) && y > 2000 && !isNaN(m) && m >= 0 && m <= 11 && !isNaN(d) && d >= 1 && d <= 31) {
      return new Date(y, m, d, h || 0, min || 0);
    }
    return null;
  }

  function renderCalendarGrid() {
    const grid = $('#cal-days-grid');
    const myStr = $('#cal-month-year-str');
    if (!grid || !myStr) return;
    const lang = window.CURRENT_LANG || 'tr';
    const monthsTr = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
    const monthsEn = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    myStr.textContent = `${lang === 'en' ? monthsEn[calViewMonth] : monthsTr[calViewMonth]} ${calViewYear}`;

    grid.innerHTML = '';
    const firstDay = new Date(calViewYear, calViewMonth, 1).getDay();
    let emptyCount = firstDay === 0 ? 6 : firstDay - 1;
    for (let i = 0; i < emptyCount; i++) {
      const span = document.createElement('div');
      span.className = 'cal-day-cell empty';
      grid.appendChild(span);
    }
    const daysInMonth = new Date(calViewYear, calViewMonth + 1, 0).getDate();
    for (let d = 1; d <= daysInMonth; d++) {
      const cell = document.createElement('div');
      cell.className = 'cal-day-cell';
      cell.textContent = d;
      if (calViewYear === pickerTargetDate.getFullYear() && calViewMonth === pickerTargetDate.getMonth() && d === pickerTargetDate.getDate()) {
        cell.classList.add('selected');
      }
      cell.addEventListener('click', () => {
        pickerTargetDate.setFullYear(calViewYear, calViewMonth, d);
        syncPickerToHidden();
        renderCalendarGrid();
      });
      grid.appendChild(cell);
    }
  }
  window.renderCalendarGrid = renderCalendarGrid;

  function syncPickerToHidden() {
    const hiddenInp = $('#schedule-datetime');
    if (!hiddenInp) return;
    const yyyy = pickerTargetDate.getFullYear();
    const mm = String(pickerTargetDate.getMonth() + 1).padStart(2, '0');
    const dd = String(pickerTargetDate.getDate()).padStart(2, '0');
    const hh = String(pickerTargetDate.getHours()).padStart(2, '0');
    const mmi = String(pickerTargetDate.getMinutes()).padStart(2, '0');
    hiddenInp.value = `${yyyy}-${mm}-${dd}T${hh}:${mmi}`;
    updateScheduleDisplayText();
  }

  function setupDrumPicker(containerId, count, valGetter, valSetter) {
    const container = $(containerId);
    if (!container) return;
    container.innerHTML = '';
    for (let i = 0; i < count; i++) {
      const div = document.createElement('div');
      div.className = 'drum-item';
      div.textContent = i < 10 ? '0' + i : i;
      div.addEventListener('click', () => {
        valSetter(i);
        syncPickerToHidden();
        updateDrumSelections();
      });
      container.appendChild(div);
    }

    let isDragging = false, startY, startScrollTop;
    container.addEventListener('mousedown', e => { isDragging = true; startY = e.pageY; startScrollTop = container.scrollTop; container.style.cursor = 'grabbing'; });
    window.addEventListener('mouseup', () => { if (isDragging) { isDragging = false; container.style.cursor = 'grab'; snapDrum(); } });
    window.addEventListener('mousemove', e => { if (!isDragging) return; e.preventDefault(); container.scrollTop = startScrollTop - (e.pageY - startY); });

    let scrollTimeout;
    container.addEventListener('scroll', () => {
      clearTimeout(scrollTimeout);
      scrollTimeout = setTimeout(snapDrum, 120);
    });

    function snapDrum() {
      const idx = Math.round(container.scrollTop / 36);
      const clamped = Math.max(0, Math.min(count - 1, idx));
      if (valGetter() !== clamped) {
        valSetter(clamped);
        syncPickerToHidden();
      }
      updateDrumSelections();
    }
  }

  function updateDrumSelections() {
    ['#drum-hour', '#drum-minute'].forEach((id, isMin) => {
      const c = $(id); if (!c) return;
      const val = isMin ? pickerTargetDate.getMinutes() : pickerTargetDate.getHours();
      Array.from(c.children).forEach((el, idx) => {
        el.classList.toggle('selected', idx === val);
      });
      if (Math.abs(c.scrollTop - val * 36) > 2) {
        c.scrollTo({ top: val * 36, behavior: 'smooth' });
      }
    });
  }

  function populateCustomDateFields(lang, targetDate = null) {
    if (targetDate) {
      pickerTargetDate = new Date(targetDate.getTime());
      calViewMonth = pickerTargetDate.getMonth();
      calViewYear = pickerTargetDate.getFullYear();
    }
    syncPickerToHidden();
    renderCalendarGrid();
    updateDrumSelections();
  }
  window.populateCustomDateFields = populateCustomDateFields;

  // ─── CUSTOM CYBER DATE/TIME PICKER INIT ───
  function initCustomDateTimePicker() {
    const dispInp = $('#schedule-display-input');
    if (dispInp) {
      dispInp.addEventListener('change', () => {
        const dt = parseInputToTargetDate(dispInp.value);
        if (dt) {
          pickerTargetDate = dt;
          calViewMonth = dt.getMonth();
          calViewYear = dt.getFullYear();
          syncPickerToHidden();
          renderCalendarGrid();
          updateDrumSelections();
        }
      });
    }

    setupDrumPicker('#drum-hour', 24, () => pickerTargetDate.getHours(), h => pickerTargetDate.setHours(h));
    setupDrumPicker('#drum-minute', 60, () => pickerTargetDate.getMinutes(), m => pickerTargetDate.setMinutes(m));

    $('#cal-prev-month')?.addEventListener('click', () => { calViewMonth--; if (calViewMonth < 0) { calViewMonth = 11; calViewYear--; } renderCalendarGrid(); });
    $('#cal-next-month')?.addEventListener('click', () => { calViewMonth++; if (calViewMonth > 11) { calViewMonth = 0; calViewYear++; } renderCalendarGrid(); });

    const btnOpenPicker = $('#btn-open-dt-picker');
    const pickerModal = $('#dt-picker-modal');
    const btnClosePicker = $('#btn-close-dt-picker');
    const btnCancelPicker = $('#btn-dt-picker-cancel');
    const btnConfirmPicker = $('#btn-dt-picker-confirm');

    if (btnOpenPicker && pickerModal) {
      btnOpenPicker.addEventListener('click', () => {
        const dt = parseInputToTargetDate($('#schedule-display-input')?.value);
        if (dt) { pickerTargetDate = dt; calViewMonth = dt.getMonth(); calViewYear = dt.getFullYear(); syncPickerToHidden(); }
        renderCalendarGrid();
        updateDrumSelections();
        pickerModal.classList.remove('hidden');
      });
      const closePicker = () => pickerModal.classList.add('hidden');
      btnClosePicker?.addEventListener('click', closePicker);
      btnCancelPicker?.addEventListener('click', closePicker);
      btnConfirmPicker?.addEventListener('click', () => {
        syncPickerToHidden();
        closePicker();
      });
      pickerModal.addEventListener('click', e => { if (e.target === pickerModal) closePicker(); });
    }

    const now = new Date();
    now.setMinutes(now.getMinutes() + 5);
    populateCustomDateFields(window.CURRENT_LANG || 'tr', now);
  }

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ SCHEDULED DOWNLOAD MODAL
  // ═══════════════════════════════════════════════════════
  function initScheduleModal() {
    if (window._scheduleModalBound) return;
    window._scheduleModalBound = true;
    const modal = $('#schedule-modal');
    const btnOpen = $('#btn-schedule');
    const btnClose = $('#btn-close-schedule');
    const btnCancel = $('#btn-schedule-cancel');
    const btnConfirm = $('#btn-schedule-confirm');

    if (!modal || !btnOpen) return;

    btnOpen.onclick = (e) => {
      if (e) e.preventDefault();
      if (!modal.classList.contains('hidden')) return;
      const urlInput = $('#url-input');
      const schedUrl = $('#schedule-url');
      if (schedUrl && urlInput?.value) schedUrl.value = urlInput.value;
      const now = new Date();
      now.setMinutes(now.getMinutes() + 5);
      if (typeof populateCustomDateFields === 'function') populateCustomDateFields(window.CURRENT_LANG || 'tr', now);
      modal.classList.remove('hidden');
    };

    const closeModal = () => modal.classList.add('hidden');
    if (btnClose) btnClose.onclick = closeModal;
    if (btnCancel) btnCancel.onclick = closeModal;
    modal.onclick = e => { if (e.target === modal) closeModal(); };

    btnConfirm?.addEventListener('click', async () => {
      const lang = window.CURRENT_LANG || 'tr';
      const url = $('#schedule-url')?.value?.trim();
      const dtVal = $('#schedule-datetime')?.value;
      if (!url) { showToast(lang === 'en' ? 'Please enter URL' : 'URL giriniz', 'error'); return; }
      if (!dtVal) { showToast(lang === 'en' ? 'Please select date & time' : 'Tarih ve saat seçiniz', 'error'); return; }

      const scheduledAt = Math.floor(new Date(dtVal).getTime() / 1000);
      if (scheduledAt <= Math.floor(Date.now() / 1000)) {
        showToast(lang === 'en' ? 'Selected date is in the past' : 'Geçmiş bir tarih seçtiniz', 'error'); return;
      }

      const format = $('#schedule-format-select')?.value || 'auto';
      const quality = $('#schedule-quality-select')?.value || 'best';

      btnConfirm.disabled = true;
      const result = await callApi('schedule_download', url, format, quality, scheduledAt);
      btnConfirm.disabled = false;

      if (result?.ok) {
        closeModal();
        if ($('#schedule-url')) $('#schedule-url').value = '';
        if ($('#schedule-datetime')) $('#schedule-datetime').value = '';
        if ($('#url-input')) $('#url-input').value = '';

        const dt = new Date(dtVal).toLocaleString();
        const lang = window.CURRENT_LANG || 'tr';
        showToast(`${lang === 'en' ? 'Scheduled for:' : 'Zamanlandı:'} ${dt}`, 'success');
        await refreshDownloads();

        const targetBtn = document.querySelector('button[data-page="scheduled"]');
        if (targetBtn) {
          const targetRect = targetBtn.getBoundingClientRect();
          const flyer = document.createElement('div');
          flyer.className = 'glass-panel';
          flyer.style.cssText = `
            position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%) scale(1);
            background: linear-gradient(135deg, rgba(168,85,247,0.9), rgba(124,58,237,0.9));
            color: #fff; padding: 10px 18px; border-radius: 20px; font-weight: 700; font-size: 13px;
            box-shadow: 0 0 20px rgba(168,85,247,0.6); z-index: 999999; pointer-events: none;
            transition: all 0.6s cubic-bezier(0.2, 0.8, 0.2, 1);
          `;
          flyer.innerHTML = `⏰ ${lang === 'en' ? 'Scheduled' : 'Zamanlandı'}`;
          document.body.appendChild(flyer);

          setTimeout(() => {
            const flyerRect = flyer.getBoundingClientRect();
            const deltaX = targetRect.left + (targetRect.width/2) - (flyerRect.left + flyerRect.width/2);
            const deltaY = targetRect.top + (targetRect.height/2) - (flyerRect.top + flyerRect.height/2);
            flyer.style.transform = `translate(calc(-50% + ${deltaX}px), calc(-50% + ${deltaY}px)) scale(0.2)`;
            flyer.style.opacity = '0';
          }, 50);

          setTimeout(() => { if (flyer.parentElement) flyer.remove(); }, 700);
        }
      } else {
        showToast(result?.error || 'Hata', 'error');
      }
    });
  }

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ TRIM ROW TOGGLE
  // ═══════════════════════════════════════════════════════
  function initTrimRow() {
    const urlInputSection = $('.url-input-container');
    if (!urlInputSection) return;

    const btnTrimToggle = document.createElement('button');
    btnTrimToggle.id = 'btn-show-trim';
    btnTrimToggle.title = 'Zaman Aralığı ile Kes';
    btnTrimToggle.className = 'url-action-btn';
    btnTrimToggle.innerHTML = '⏱️';
    btnTrimToggle.style.cssText = 'font-size:15px; padding: 0 8px;';

    const pasteBtn = $('#btn-paste');
    if (pasteBtn) pasteBtn.insertAdjacentElement('afterend', btnTrimToggle);

    const trimRow = $('#trim-row');
    btnTrimToggle.addEventListener('click', () => {
      trimRow?.classList.toggle('hidden');
      btnTrimToggle.style.color = (trimRow?.classList.contains('hidden') && !window.TRIM_ACTIVE) ? '' : 'var(--accent-cyan)';
    });

    function timeToSec(str) {
      if (!str) return 0;
      const parts = str.split(':').map(x => parseInt(x || '0', 10));
      if (parts.length === 1) return parts[0] || 0;
      if (parts.length === 2) return (parts[0] || 0) * 60 + (parts[1] || 0);
      if (parts.length >= 3) return (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
      return 0;
    }

    function secToTime(totalSec) {
      if (!totalSec || totalSec <= 0 || isNaN(totalSec)) return '';
      const h = Math.floor(totalSec / 3600);
      const m = Math.floor((totalSec % 3600) / 60);
      const s = totalSec % 60;
      if (h > 0) return `${h}:${m < 10 ? '0' + m : m}:${s < 10 ? '0' + s : s}`;
      return `${m}:${s < 10 ? '0' + s : s}`;
    }

    function smartFormatInput(inp) {
      if (!inp || !inp.value) return;
      const val = inp.value.trim();
      if (!val) return;
      const sec = timeToSec(val);
      if (sec > 0) inp.value = secToTime(sec);
    }

    const startInp = $('#trim-start');
    const endInp = $('#trim-end');
    startInp?.addEventListener('blur', () => smartFormatInput(startInp));
    endInp?.addEventListener('blur', () => {
      smartFormatInput(endInp);
      const sSec = timeToSec(startInp?.value?.trim());
      const eSec = timeToSec(endInp?.value?.trim());
      if (sSec > 0 && eSec > 0 && eSec <= sSec) {
        endInp.value = secToTime(sSec + eSec);
        const lang = window.CURRENT_LANG;
        showToast(lang === 'en' ? `⚡ Duration (+${eSec}s) added to start time!` : `⚡ Bitişe ek süre (+${eSec} sn) başlangıca eklendi!`, 'info');
      }
    });

    const btnShowOpt = $('#btn-show-options');
    const optRow = $('#options-row');
    btnShowOpt?.addEventListener('click', () => {
      optRow?.classList.toggle('hidden');
      btnShowOpt.style.color = optRow?.classList.contains('hidden') ? '' : 'var(--accent-cyan)';
    });

    const actionBtn = $('#btn-trim-action');
    const clearBtn = $('#btn-trim-clear');

    actionBtn?.addEventListener('click', () => {
      smartFormatInput(startInp);
      smartFormatInput(endInp);
      let s = startInp?.value?.trim();
      let e = endInp?.value?.trim();
      const lang = window.CURRENT_LANG;
      if (!s && !e) {
        showToast(lang === 'en' ? 'Please enter start or end time' : 'Lütfen başlangıç veya bitiş süresi girin', 'error');
        return;
      }
      const sSec = timeToSec(s);
      let eSec = timeToSec(e);
      if (sSec > 0 && eSec > 0 && eSec <= sSec) {
        eSec = sSec + eSec;
        endInp.value = secToTime(eSec);
      } else if (eSec > 0 && eSec <= sSec) {
        endInp.value = secToTime(sSec + 60);
      }
      window.TRIM_ACTIVE = true;
      btnTrimToggle.style.color = '#10b981';
      actionBtn.style.borderColor = '#10b981';
      actionBtn.style.color = '#10b981';
      const trimBtnTxt = $('#trim-btn-text');
      if (trimBtnTxt) trimBtnTxt.textContent = lang === 'en' ? '✓ Trimmed' : '✓ Kesildi';
      clearBtn?.classList.remove('hidden');
      showToast(lang === 'en' ? 'Time range saved for download!' : 'Zaman aralığı indirme için kaydedildi!', 'success');
    });

    clearBtn?.addEventListener('click', () => {
      window.TRIM_ACTIVE = false;
      if (startInp) startInp.value = '';
      if (endInp) endInp.value = '';
      btnTrimToggle.style.color = trimRow?.classList.contains('hidden') ? '' : 'var(--accent-cyan)';
      if (actionBtn) {
        actionBtn.style.borderColor = 'rgba(0,240,255,0.4)';
        actionBtn.style.color = 'var(--accent-cyan)';
      }
      const trimBtnTxt = $('#trim-btn-text');
      const lang = window.CURRENT_LANG;
      if (trimBtnTxt) trimBtnTxt.textContent = lang === 'en' ? 'Trim' : 'Kes';
      clearBtn.classList.add('hidden');
      showToast(lang === 'en' ? 'Time range cleared' : 'Zaman aralığı sıfırlandı', 'info');
    });
  }

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ AUTO SHUTDOWN MODAL
  // ═══════════════════════════════════════════════════════
  function initShutdownModal() {
    if (window._shutdownModalBound) return;
    window._shutdownModalBound = true;
    const modal = $('#shutdown-modal');
    const openBtn = $('#btn-open-shutdown-modal');
    const btnClose = $('#btn-close-shutdown');
    const btnCancel = $('#btn-shutdown-cancel');
    const btnConfirm = $('#btn-shutdown-confirm');

    if (!modal) return;

    if (openBtn) {
      openBtn.onclick = (e) => {
        if (e) e.preventDefault();
        if (!modal.classList.contains('hidden')) return;
        modal.classList.remove('hidden');
      };
    }

    const closeModal = () => modal.classList.add('hidden');
    if (btnClose) btnClose.onclick = closeModal;
    if (btnCancel) btnCancel.onclick = closeModal;
    modal.onclick = e => { if (e.target === modal) closeModal(); };

    btnConfirm?.addEventListener('click', async () => {
      const selected = document.querySelector('input[name="shutdown-mode"]:checked');
      const mode = selected?.value || '';

      if (mode) {
        // Show confirmation first
        const lang = window.CURRENT_LANG;
        const confirmMsg = mode === 'shutdown'
          ? (lang === 'en' ? 'PC will shut down when all downloads finish. A 60-second countdown will appear. Continue?' : 'Tüm indirmeler bitince bilgisayar kapanacak. 60 saniyelik geri sayım başlayacak. Devam edilsin mi?')
          : (lang === 'en' ? 'PC will go to sleep when all downloads finish. Continue?' : 'Tüm indirmeler bitince bilgisayar uyku moduna alınacak. Devam edilsin mi?');

        showConfirmModal(
          lang === 'en' ? 'Confirm Auto-Action' : 'İşlemi Onayla',
          confirmMsg,
          async () => {
            await callApi('set_auto_shutdown', mode);
            closeModal();
            const label = mode === 'shutdown'
              ? (lang === 'en' ? '⚡ Shutdown on complete' : '⚡ Bitince kapat')
              : (lang === 'en' ? '💤 Sleep on complete' : '💤 Bitince uyu');
            showToast(label, 'warning');
          },
          lang === 'en' ? 'Confirm' : 'Onayla'
        );
      } else {
        await callApi('set_auto_shutdown', '');
        closeModal();
      }
    });

    // Global trigger from Python backend
    window._triggerAutoShutdown = async (mode) => {
      const overlay = $('#shutdown-countdown-overlay');
      const countNum = $('#shutdown-countdown-num');
      const titleEl = $('#shutdown-countdown-title');
      const msgEl = $('#shutdown-countdown-msg');
      const iconEl = $('#shutdown-countdown-icon');
      if (!overlay) return;

      const lang = window.CURRENT_LANG;
      if (mode === 'sleep') {
        if (iconEl) iconEl.textContent = '💤';
        if (titleEl) titleEl.textContent = lang === 'en' ? 'Going to Sleep...' : 'Uyku Moduna Geçiliyor...';
        if (msgEl) msgEl.textContent = lang === 'en' ? 'Downloads complete. Sleeping in 10 seconds.' : 'İndirmeler tamamlandı. 10 saniye sonra uyku modu.';
        if (countNum) countNum.textContent = '10';
        overlay.classList.remove('hidden');
        let c = 10;
        const iv = setInterval(async () => {
          c--;
          if (countNum) countNum.textContent = c;
          if (c <= 0) {
            clearInterval(iv);
            overlay.classList.add('hidden');
            await callApi('run_system_command', 'sleep');
          }
        }, 1000);
        $('#btn-abort-shutdown')?.addEventListener('click', () => { clearInterval(iv); overlay.classList.add('hidden'); }, {once:true});
      } else {
        overlay.classList.remove('hidden');
        let c = 60;
        if (countNum) countNum.textContent = c;
        await callApi('run_system_command', 'shutdown_init');
        const iv = setInterval(() => {
          c--;
          if (countNum) countNum.textContent = c;
          if (c <= 0) { clearInterval(iv); overlay.classList.add('hidden'); }
        }, 1000);
        $('#btn-abort-shutdown')?.addEventListener('click', async () => {
          clearInterval(iv);
          overlay.classList.add('hidden');
          await callApi('run_system_command', 'shutdown_abort');
          showToast(lang === 'en' ? 'Shutdown aborted' : 'Kapatma iptal edildi', 'success');
        }, {once:true});
      }
    };
  }

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ MEDIA PREVIEW PLAYER MODAL
  // ═══════════════════════════════════════════════════════
  let _currentPreviewTaskId = null;
  let _previewPlaylist = [];
  let _previewIndex = 0;

  function loadPreviewItem(idx) {
    if (!_previewPlaylist || !_previewPlaylist.length) return;
    if (idx < 0) idx = _previewPlaylist.length - 1;
    if (idx >= _previewPlaylist.length) idx = 0;
    _previewIndex = idx;

    const item = _previewPlaylist[_previewIndex];
    if (!item) return;

    const videoEl = $('#preview-video');
    const audioEl = $('#preview-audio');
    const imgEl = $('#preview-image');
    const titleEl = $('#preview-title');
    const metaEl = $('#preview-meta');
    const counterEl = $('#preview-counter');
    const playlistBar = $('#preview-playlist-bar');

    if (videoEl) { videoEl.style.display = 'none'; videoEl.src = ''; videoEl.pause?.(); }
    if (audioEl) { audioEl.style.display = 'none'; audioEl.src = ''; audioEl.pause?.(); }
    if (imgEl) { imgEl.style.display = 'none'; imgEl.src = ''; }

    if (titleEl) titleEl.textContent = item.title || '▶️ Önizleme';
    if (metaEl) metaEl.textContent = item.filepath || '';
    if (counterEl) counterEl.textContent = `${_previewIndex + 1} / ${_previewPlaylist.length}`;

    if (playlistBar) {
      playlistBar.style.display = _previewPlaylist.length > 1 ? 'flex' : 'none';
    }

    if (item.type === 'video' && videoEl) {
      videoEl.src = item.url;
      videoEl.style.display = 'block';
      videoEl.play().catch(() => {});
    } else if (item.type === 'audio' && audioEl) {
      audioEl.src = item.url;
      audioEl.style.display = 'block';
      audioEl.play().catch(() => {});
    } else if (imgEl) {
      imgEl.src = item.url;
      imgEl.style.display = 'block';
    }
  }

  async function openPreviewPlayer(taskId) {
    const modal = $('#preview-modal');
    if (!modal) return;

    _currentPreviewTaskId = taskId;
    const loadingEl = $('#preview-loading');
    const videoEl = $('#preview-video');
    const audioEl = $('#preview-audio');
    const imgEl = $('#preview-image');
    const titleEl = $('#preview-title');
    const metaEl = $('#preview-meta');
    const playlistBar = $('#preview-playlist-bar');

    if (videoEl) { videoEl.style.display = 'none'; videoEl.src = ''; videoEl.pause?.(); }
    if (audioEl) { audioEl.style.display = 'none'; audioEl.src = ''; audioEl.pause?.(); }
    if (imgEl) { imgEl.style.display = 'none'; imgEl.src = ''; }
    if (playlistBar) playlistBar.style.display = 'none';
    if (loadingEl) loadingEl.style.display = 'flex';
    if (titleEl) titleEl.textContent = '⏳ Yükleniyor...';
    if (metaEl) metaEl.textContent = '';
    modal.classList.remove('hidden');

    const result = await callApi('get_file_for_preview', taskId);
    if (loadingEl) loadingEl.style.display = 'none';

    if (!result?.ok) {
      if (titleEl) titleEl.textContent = '❌ Dosya bulunamadı';
      if (metaEl) metaEl.textContent = result?.error || '';
      return;
    }

    if (result.playlist && result.playlist.length) {
      _previewPlaylist = result.playlist;
      _previewIndex = result.current_index || 0;
      loadPreviewItem(_previewIndex);
    } else {
      _previewPlaylist = [{ title: result.title, url: result.url, type: result.type, filepath: '' }];
      _previewIndex = 0;
      loadPreviewItem(0);
    }
  }

  function initPreviewModal() {
    if (window._previewModalBound) return;
    window._previewModalBound = true;
    const modal = $('#preview-modal');
    const btnClose = $('#btn-close-preview');
    if (!modal) return;

    const closeModal = () => {
      modal.classList.add('hidden');
      $('#preview-video')?.pause?.();
      $('#preview-audio')?.pause?.();
      if ($('#preview-video')) $('#preview-video').src = '';
      if ($('#preview-audio')) $('#preview-audio').src = '';
      if ($('#preview-image')) $('#preview-image').src = '';
      _previewPlaylist = [];
    };

    if (btnClose) btnClose.onclick = closeModal;
    modal.onclick = e => { if (e.target === modal) closeModal(); };

    const prevBtn = $('#btn-preview-prev');
    const nextBtn = $('#btn-preview-next');
    const openFolderBtn = $('#btn-preview-open-folder');

    if (prevBtn) prevBtn.onclick = () => loadPreviewItem(_previewIndex - 1);
    if (nextBtn) nextBtn.onclick = () => loadPreviewItem(_previewIndex + 1);

    if (openFolderBtn) {
      openFolderBtn.onclick = () => {
        if (_previewPlaylist && _previewPlaylist[_previewIndex] && _previewPlaylist[_previewIndex].filepath) {
          callApi('open_file_location', _previewPlaylist[_previewIndex].filepath);
        } else if (_currentPreviewTaskId) {
          callApi('open_file_location', _currentPreviewTaskId);
        }
      };
    }

    // Auto-play next track when audio/video ends in a playlist!
    $('#preview-video')?.addEventListener('ended', () => {
      if (_previewPlaylist && _previewPlaylist.length > 1) loadPreviewItem(_previewIndex + 1);
    });
    $('#preview-audio')?.addEventListener('ended', () => {
      if (_previewPlaylist && _previewPlaylist.length > 1) loadPreviewItem(_previewIndex + 1);
    });

    // Expose globally so history cards can call it
    window.openPreviewPlayer = openPreviewPlayer;
  }

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ INIT ALL NEW FEATURES
  // ═══════════════════════════════════════════════════════
  document.addEventListener('DOMContentLoaded', () => {
    initBatchModal();
    initCustomDateTimePicker();
    initScheduleModal();
    initTrimRow();
    initShutdownModal();
    initPreviewModal();
    initConverterPage();
  });

  // ═══════════════════════════════════════════════════════
  // ═══════════════════════════════════════════════════════
  // CARD REORDER ─ Up / Down Buttons
  // ═══════════════════════════════════════════════════════
  function syncTaskOrder() {
    const list = document.getElementById('download-list');
    if (!list) return;
    const cards = list.querySelectorAll('.download-card');
    const ids = Array.from(cards).map(c => c.dataset.taskId).filter(Boolean);
    if (ids.length > 0) {
      callApi('reorder_tasks', ids);
    }
  }

  function bindCardReorder(card) {
    const upBtn = card.querySelector('.btn-move-up');
    const downBtn = card.querySelector('.btn-move-down');
    if (!upBtn || !downBtn) return;

    upBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const prev = card.previousElementSibling;
      if (prev && prev.classList.contains('download-card')) {
        card.parentElement.insertBefore(card, prev);
        flashCard(card);
        updateReorderButtonsVisibility();
        syncTaskOrder();
      }
    });

    downBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const next = card.nextElementSibling;
      if (next && next.classList.contains('download-card')) {
        card.parentElement.insertBefore(next, card);
        flashCard(card);
        updateReorderButtonsVisibility();
        syncTaskOrder();
      }
    });
  }

  function flashCard(card) {
    card.style.transition = 'box-shadow 0.15s ease';
    card.style.boxShadow = '0 0 0 2px rgba(0,240,255,0.45)';
    setTimeout(() => { card.style.boxShadow = ''; }, 350);
  }

  // ═══════════════════════════════════════════════════════
  // CARD DRAG & DROP Reordering
  // ═══════════════════════════════════════════════════════
  let _dragSrc = null;

  function bindCardDragDrop(card) {
    card.addEventListener('dragstart', (e) => {
      _dragSrc = card;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.taskId || '');
    });

    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      document.querySelectorAll('.download-card.drag-over').forEach(c => c.classList.remove('drag-over'));
      _dragSrc = null;
    });

    card.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (_dragSrc && _dragSrc !== card) {
        document.querySelectorAll('.download-card.drag-over').forEach(c => c.classList.remove('drag-over'));
        card.classList.add('drag-over');
      }
    });

    card.addEventListener('dragleave', () => {
      card.classList.remove('drag-over');
    });

    card.addEventListener('drop', (e) => {
      e.preventDefault();
      card.classList.remove('drag-over');
      if (!_dragSrc || _dragSrc === card) return;

      const list = card.parentElement;
      const cards = [...list.querySelectorAll('.download-card')];
      const srcIdx = cards.indexOf(_dragSrc);
      const dstIdx = cards.indexOf(card);

      if (srcIdx < dstIdx) {
        list.insertBefore(_dragSrc, card.nextSibling);
      } else {
        list.insertBefore(_dragSrc, card);
      }
      flashCard(_dragSrc);
      syncTaskOrder();
    });
  }

  // ═══════════════════════════════════════════════════════
  // SEQUENTIAL DOWNLOAD ─ Settings wiring
  // ═══════════════════════════════════════════════════════
  // Sequential download is handled by watching the toggle in settings.
  // When enabled: concurrent_downloads is set to 1 on save.
  // When disabled: restored to whatever the user had before.
  // This is a UI-level preference that maps to concurrent_downloads=1.

  // ═══════════════════════════════════════════════════════
  // CONVERTER PAGE
  // ═══════════════════════════════════════════════════════
  const CONVERTER_FORMATS = {
    // video inputs → possible outputs
    video: ['mp4', 'mkv', 'avi', 'mov', 'webm', 'gif'],
    // audio inputs → possible outputs
    audio: ['mp3', 'aac', 'flac', 'wav', 'ogg', 'm4a'],
    // image inputs → possible outputs
    image: ['jpg', 'png', 'webp', 'gif', 'bmp'],
  };

  const VIDEO_EXTS = new Set(['mp4','mkv','avi','mov','webm','flv','wmv','m4v','ts','mts','m2ts','vob','3gp','ogv']);
  const AUDIO_EXTS = new Set(['mp3','aac','flac','wav','ogg','m4a','opus','wma','aiff','alac']);
  const IMAGE_EXTS = new Set(['jpg','jpeg','png','webp','gif','bmp','tiff','tga','ico']);

  let converterState = {
    srcFile: null,
    srcExt: '',
    srcType: '',
    srcThumb: '',
    dstExt: '',
    jobs: [],
  };
  let converterJobId = 0;

  function getFileType(ext) {
    ext = ext.toLowerCase();
    if (VIDEO_EXTS.has(ext)) return 'video';
    if (AUDIO_EXTS.has(ext)) return 'audio';
    if (IMAGE_EXTS.has(ext)) return 'image';
    return 'video';
  }

  function initConverterPage() {
    const pickBtn = $('#btn-converter-pick');
    const fileInput = $('#converter-file-input');
    const addBtn = $('#btn-converter-add');
    const dstBtn = $('#converter-dst-ext-btn');
    const dropdown = $('#converter-format-dropdown');
    const srcExtLabel = $('#converter-src-ext-label');
    const dstExtLabel = $('#converter-dst-ext-label');

    if (!pickBtn) return;

    pickBtn.addEventListener('click', () => fileInput?.click());

    fileInput?.addEventListener('change', async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      converterState.srcFile = file;
      const ext = file.name.split('.').pop().toLowerCase();
      converterState.srcExt = ext;
      converterState.srcType = getFileType(ext);
      converterState.dstExt = '';

      if (srcExtLabel) srcExtLabel.textContent = ext.toUpperCase();
      if (dstExtLabel) dstExtLabel.textContent = 'Seç';
      if (addBtn) addBtn.disabled = true;
      
      const iconEl = $('#btn-converter-pick-icon');
      const textEl = $('#btn-converter-pick-text');
      const imgEl = $('#btn-converter-pick-img');

      pickBtn.style.borderStyle = 'solid';
      pickBtn.style.borderColor = 'transparent';
      
      if (iconEl) iconEl.style.display = 'none';
      if (textEl) {
        textEl.style.display = 'block';
        textEl.textContent = file.name.length > 10 ? file.name.substring(0,8) + '..' : file.name;
        textEl.style.position = 'absolute';
        textEl.style.bottom = '8px';
        textEl.style.background = 'rgba(0,0,0,0.6)';
        textEl.style.padding = '2px 6px';
        textEl.style.borderRadius = '4px';
        textEl.style.color = '#fff';
        textEl.style.fontSize = '11px';
        textEl.style.zIndex = '2';
      }

      if (imgEl) {
        imgEl.style.display = 'block';
        if (converterState.srcType === 'image') {
          imgEl.src = URL.createObjectURL(file);
          converterState.srcThumb = imgEl.src;
        } else if (converterState.srcType === 'video') {
          try {
            if (file.name.toLowerCase().endsWith('.ts')) {
              // Extract from TS using backend
              const slice = file.slice(0, 5 * 1024 * 1024); // 5MB
              const reader = new FileReader();
              reader.onload = async (e) => {
                try {
                  const res = await callApi('extract_thumbnail', JSON.stringify({
                    src_ext: 'ts',
                    data_url: e.target.result
                  }));
                  if (res && res.success && res.thumbnail) {
                    imgEl.src = res.thumbnail;
                    converterState.srcThumb = res.thumbnail;
                  } else {
                    imgEl.style.display = 'none';
                  }
                } catch (err) {
                  imgEl.style.display = 'none';
                }
              };
              reader.readAsDataURL(slice);
            } else {
              const videoUrl = URL.createObjectURL(file);
              const video = document.createElement('video');
              video.src = videoUrl;
              video.muted = true;
              await new Promise((resolve) => {
                video.onloadeddata = () => {
                  video.currentTime = 1;
                };
                video.onseeked = () => {
                  const canvas = document.createElement('canvas');
                  canvas.width = video.videoWidth;
                  canvas.height = video.videoHeight;
                  const ctx = canvas.getContext('2d');
                  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                  imgEl.src = canvas.toDataURL('image/jpeg');
                  converterState.srcThumb = imgEl.src;
                  resolve();
                };
                video.onerror = resolve;
              });
            }
          } catch (err) {
            imgEl.style.display = 'none';
            converterState.srcThumb = '';
          }
        } else {
          // just show an icon for audio or others
          imgEl.style.display = 'none';
          converterState.srcThumb = '';
          if (iconEl) iconEl.style.display = 'block';
          textEl.style.position = 'static';
          textEl.style.background = 'transparent';
          textEl.style.color = 'var(--accent-cyan)';
          pickBtn.style.borderStyle = 'dashed';
          pickBtn.style.borderColor = 'rgba(0,240,255,0.35)';
        }
      }

      buildFormatDropdown();
      fileInput.value = '';
    });

    // Toggle dropdown
    dstBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!converterState.srcExt) return;
      dropdown?.classList.toggle('hidden');
    });

    document.addEventListener('click', () => {
      dropdown?.classList.add('hidden');
    });

    addBtn?.addEventListener('click', () => {
      if (!converterState.srcFile || !converterState.dstExt) return;
      addConverterJob(converterState.srcFile, converterState.srcExt, converterState.dstExt);

      // Reset UI
      converterState.srcFile = null;
      converterState.srcExt = '';
      converterState.dstExt = '';
      if (srcExtLabel) srcExtLabel.textContent = '—';
      if (dstExtLabel) dstExtLabel.textContent = 'Seç';
      if (addBtn) addBtn.disabled = true;
      
      pickBtn.style.backgroundImage = 'none';
      pickBtn.style.boxShadow = 'none';
      pickBtn.style.borderStyle = 'dashed';
      const iconEl = $('#btn-converter-pick-icon');
      const textEl = $('#btn-converter-pick-text');
      if (iconEl) iconEl.style.display = 'block';
      if (textEl) {
        textEl.style.display = 'block';
        textEl.textContent = 'Dosya Seç';
      }
    });
  }

  function buildFormatDropdown() {
    const dropdown = $('#converter-format-dropdown');
    if (!dropdown) return;
    dropdown.innerHTML = '';

    const type = converterState.srcType;
    const formats = CONVERTER_FORMATS[type] || CONVERTER_FORMATS.video;
    const srcExt = converterState.srcExt.toLowerCase();

    formats.forEach(fmt => {
      if (fmt === srcExt) return; // skip same format
      const opt = document.createElement('div');
      opt.className = 'converter-format-option';
      opt.textContent = fmt.toUpperCase();
      if (fmt === converterState.dstExt) opt.classList.add('active');
      opt.addEventListener('click', (e) => {
        e.stopPropagation();
        converterState.dstExt = fmt;
        const dstExtLabel = $('#converter-dst-ext-label');
        if (dstExtLabel) dstExtLabel.textContent = fmt.toUpperCase();
        dropdown.classList.add('hidden');
        // Update active class
        dropdown.querySelectorAll('.converter-format-option').forEach(el => {
          el.classList.toggle('active', el.textContent.toLowerCase() === fmt);
        });
        const addBtn = $('#btn-converter-add');
        if (addBtn) addBtn.disabled = false;
      });
      dropdown.appendChild(opt);
    });

    dropdown.classList.remove('hidden');
  }

  function addConverterJob(file, srcExt, dstExt) {
    converterJobId++;
    const id = `conv-${converterJobId}`;
    const job = {
      id,
      file,
      srcExt,
      dstExt,
      name: file.name,
      status: 'pending',
      progress: 0,
      error: '',
    };
    converterState.jobs.push(job);
    renderConverterCard(job);
    runConverterJob(job);
  }

  function renderConverterCard(job) {
    const queue = $('#converter-queue');
    const empty = $('#converter-queue-empty');
    if (!queue) return;
    if (empty) empty.style.display = 'none';

    const card = document.createElement('div');
    card.className = 'converter-card glass-panel';
    card.id = `conv-card-${job.id}`;
    let iconHtml = `<div class="converter-card-icon">${job.srcExt.toUpperCase()}</div>`;
    if (converterState.srcThumb) {
      iconHtml = `<div class="converter-card-icon" style="padding:0; overflow:hidden;"><img src="${converterState.srcThumb}" style="width:100%; height:100%; object-fit:cover;"></div>`;
    }

    card.innerHTML = `
      ${iconHtml}
      <div class="converter-card-info">
        <div class="converter-card-name" title="${escapeHtml(job.name)}">${escapeHtml(job.name)}</div>
        <div class="converter-card-meta">
          <span style="color:#c084fc; font-weight:700;">${job.srcExt.toUpperCase()}</span>
          <span>→</span>
          <span style="color:var(--accent-cyan); font-weight:700;">${job.dstExt.toUpperCase()}</span>
        </div>
      </div>
      <div class="converter-card-progress">
        <div class="converter-progress-bar">
          <div class="converter-progress-fill" style="width:0%"></div>
        </div>
        <div class="converter-card-status">Hazırlanıyor...</div>
      </div>
      <div class="converter-card-actions">
        <button class="card-action-btn btn-conv-cancel btn-cancel" title="İptal Et">
          <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
        </button>
        <button class="card-action-btn btn-conv-remove btn-remove" title="Listeden Kaldır" style="display:none">
          <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
        </button>
      </div>
    `;

    card.querySelector('.btn-conv-cancel')?.addEventListener('click', () => {
      job.cancelled = true;
      updateConverterCard(job.id, 'İptal edildi', 0, 'error');
    });
    card.querySelector('.btn-conv-remove')?.addEventListener('click', () => {
      card.style.transition = 'opacity 0.25s, transform 0.25s';
      card.style.opacity = '0';
      card.style.transform = 'translateX(20px)';
      setTimeout(() => {
        card.remove();
        converterState.jobs = converterState.jobs.filter(j => j.id !== job.id);
        if ($('#converter-queue')?.children.length === 0 || $('#converter-queue .converter-card') === null) {
          const empty = $('#converter-queue-empty');
          if (empty && !$('#converter-queue .converter-card')) empty.style.display = '';
        }
      }, 280);
    });

    queue.appendChild(card);
  }

  function updateConverterCard(id, statusText, progress, statusClass) {
    const card = $(`#conv-card-${id}`);
    if (!card) return;
    const fill = card.querySelector('.converter-progress-fill');
    const statusEl = card.querySelector('.converter-card-status');
    const cancelBtn = card.querySelector('.btn-conv-cancel');
    const removeBtn = card.querySelector('.btn-conv-remove');

    if (fill) fill.style.width = `${Math.min(100, Math.max(0, progress))}%`;
    if (statusEl) statusEl.textContent = statusText;

    card.className = `converter-card glass-panel${statusClass ? ' status-' + statusClass : ''}`;
    if (statusClass === 'done' || statusClass === 'error') {
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (removeBtn) removeBtn.style.display = '';

      if (statusClass === 'done') {
        const job = converterState.jobs.find(j => j.id === id);
        if (job && job.output_file) {
          const actionWrap = card.querySelector('.converter-card-actions');
          if (actionWrap && !actionWrap.querySelector('.btn-conv-folder')) {
            const fBtn = document.createElement('button');
            fBtn.className = 'card-action-btn btn-conv-folder btn-folder';
            fBtn.title = 'Dosya Konumunu Aç';
            fBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>`;
            fBtn.addEventListener('click', () => callApi('open_file_location', job.output_file));
            actionWrap.insertBefore(fBtn, removeBtn);
          }
        }
      }
    }

    // Update icon
    const iconEl = card.querySelector('.converter-card-icon');
    if (iconEl) {
      const job = converterState.jobs.find(j => j.id === id);
      if (job) {
        if (statusClass === 'done') iconEl.textContent = '✓';
        else if (statusClass === 'error') iconEl.textContent = '✗';
        else iconEl.textContent = job.srcExt.toUpperCase();
      }
    }
  }

  async function runConverterJob(job) {
    if (!job.file) return;

    updateConverterCard(job.id, 'Dönüştürülüyor...', 10, 'converting');

    try {
      // Read file as base64 to send to backend
      const reader = new FileReader();
      const fileDataPromise = new Promise((resolve, reject) => {
        reader.onload = e => resolve(e.target.result);
        reader.onerror = reject;
        reader.readAsDataURL(job.file);
      });

      const dataUrl = await fileDataPromise;
      if (job.cancelled) return;

      updateConverterCard(job.id, 'Dosya yükleniyor...', 25, 'converting');

      const result = await callApi('convert_file', JSON.stringify({
        filename: job.name,
        src_ext: job.srcExt,
        dst_ext: job.dstExt,
        data_url: dataUrl,
      }));

      if (job.cancelled) return;

      if (result && result.success) {
        job.output_file = result.output_file;
        updateConverterCard(job.id, 'Tamamlandı', 100, 'done');
      } else {
        const errMsg = result?.error || 'Dönüştürme başarısız';
        updateConverterCard(job.id, `Hata: ${errMsg}`, 0, 'error');
      }
    } catch (err) {
      if (!job.cancelled) {
        updateConverterCard(job.id, `Hata: ${err.message || 'Bilinmeyen hata'}`, 0, 'error');
      }
    }
  }

})();
