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

    dom.clipboardBanner = $('#clipboard-banner');
    dom.historyList = $('#history-list');
    dom.historyEmpty = $('#history-empty');
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
    state.apiReady = true;
    dom.loadingOverlay?.classList.add('hidden');

    // Load settings
    const settings = await callApi('get_settings');
    if (settings) {
      state.settings = settings;
      if (settings.theme) {
        document.body.dataset.theme = settings.theme;
        localStorage.setItem('suylios_theme', settings.theme);
        document.querySelectorAll('.theme-card').forEach(c => {
          c.classList.toggle('active', c.dataset.theme === settings.theme);
        });
      }
      applySettingsToUI(settings);
    }

    // Start polling downloads
    startDownloadPolling();
    startClipboardMonitor();
  }

  // ─── TITLEBAR ───
  function bindTitlebar() {
    dom.btnMinimize?.addEventListener('click', () => callApi('minimize_window'));
    dom.btnMaximize?.addEventListener('click', () => callApi('maximize_window'));
    dom.btnClose?.addEventListener('click', () => callApi('close_window'));

    // Double-click titlebar to maximize / restore
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
      if (confirm('Tüm indirme geçmişi silinecek. Emin misiniz?')) {
        await callApi('clear_history');
        renderHistory();
        showToast('Geçmiş temizlendi', 'info');
      }
    });
  }

  function switchPage(page) {
    const pages = { main: dom.pageMain, settings: dom.pageSettings, history: dom.pageHistory };
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
      const text = await getClipboardText();
      if (text) {
        dom.urlInput.value = text;
        dom.urlInput.focus();
        flashUrlBar();
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

    // Disable button temporarily
    dom.btnDownload.disabled = true;
    dom.btnDownload.style.opacity = '0.6';

    const result = await callApi('add_download', url, format, quality);

    dom.btnDownload.disabled = false;
    dom.btnDownload.style.opacity = '';

    if (result && (result.task || result.task_id || result.ok)) {
      dom.urlInput.value = '';
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
    // Only call pywebview native bridge to avoid browser permission popups
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

    history.forEach(item => {
      const card = document.createElement('div');
      card.className = 'history-card';
      const sizeStr = item.total_bytes ? formatSize(item.total_bytes) : 'Tamamlandı';
      card.innerHTML = `
        <div class="history-card-left">
          <div class="history-card-icon">📁</div>
          <div class="history-card-info">
            <div class="history-card-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
            <div class="history-card-meta">
              <span>📅 ${item.date_str || ''}</span>
              <span>📦 ${sizeStr}</span>
              <span>🏷️ ${escapeHtml(item.extractor_name || 'Diğer')}</span>
            </div>
          </div>
        </div>
        <div class="history-card-actions">
          <button class="btn-secondary btn-sm btn-open-hist" data-id="${item.id}" title="Klasörü Aç">📁 Klasörü Aç</button>
          <button class="btn-secondary btn-sm btn-del-hist" data-id="${item.id}" title="Listeden Sil" style="color:#ef4444;">🗑️</button>
        </div>
      `;
      
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
          showToast('Kayıt silindi', 'info');
        });
      }
      dom.historyList.appendChild(card);
    });
  }

  async function refreshDownloads() {
    const downloads = await callApi('get_downloads');
    if (!downloads) return;

    const newIds = new Set();

    for (const dl of downloads) {
      newIds.add(dl.id);
      const existing = state.downloads.get(dl.id);
      state.downloads.set(dl.id, dl);

      if (existing) {
        if (existing.status !== 'completed' && dl.status === 'completed') {
          showToast(`${dl.title || 'İndirme'} tamamlandı!`, 'success');
          callApi('show_desktop_notification', ['Suylios Downloader', `${dl.title || 'Dosya'} başarıyla indirildi!`]);
        } else if (existing.status !== 'error' && dl.status === 'error') {
          showToast(`${dl.title || 'İndirme'} başarısız oldu!`, 'error');
          callApi('show_desktop_notification', ['Suylios - Hata', `${dl.title || 'Dosya'} indirilemedi`]);
        }
        updateDownloadCard(dl);
      } else {
        createDownloadCard(dl);
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
  }

  function toggleEmptyState() {
    const hasCards = dom.downloadList.children.length > 0;
    dom.emptyState.style.display = hasCards ? 'none' : '';
  }

  // ─── DOWNLOAD CARD CREATION ───
  function createDownloadCard(dl) {
    const template = dom.cardTemplate.content.cloneNode(true);
    const card = template.querySelector('.download-card');

    card.dataset.taskId = dl.id;
    card.dataset.status = dl.status || 'downloading';
    card.style.animationDelay = '0s';

    // Populate
    populateCard(card, dl);

    // Bind actions
    bindCardActions(card, dl.id);

    dom.downloadList.prepend(card);
    toggleEmptyState();
  }

  function updateDownloadCard(dl) {
    const card = $(`.download-card[data-task-id="${dl.id}"]`);
    if (!card) return;

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
    const isComplete = dl.status === 'complete' || dl.status === 'completed';
    if (dl.item_count && dl.item_count > 1 && !isComplete && dl.item_index > 0) {
      const idx = dl.item_index;
      const displayTitle = dl.title || 'İndiriliyor...';
      title.innerHTML = `<span style="background: linear-gradient(135deg, var(--accent-cyan), #0080ff); color: #000; padding: 2px 8px; border-radius: 12px; font-weight: 800; font-size: 12px; margin-right: 8px; box-shadow: 0 0 10px rgba(0,240,255,0.4); display: inline-block; vertical-align: middle; flex-shrink:0;">${idx}/${dl.item_count}</span><span style="vertical-align: middle;">${escapeHtml(displayTitle)}</span>`;
    } else if (dl.item_count && dl.item_count > 1 && isComplete) {
      // Completed playlist/archive — show title + total count chip
      title.innerHTML = `<span style="background: linear-gradient(135deg, #00e87a, #00b85a); color: #000; padding: 2px 8px; border-radius: 12px; font-weight: 800; font-size: 12px; margin-right: 8px; box-shadow: 0 0 10px rgba(0,232,122,0.4); display: inline-block; vertical-align: middle; flex-shrink:0;">${dl.item_count} öğe</span><span style="vertical-align: middle;">${escapeHtml(rawTitle)}</span>`;
    } else {
      title.textContent = rawTitle;
    }
    subtitle.textContent = truncateUrl(dl.url || '');

    const siteIcon = card.querySelector('.card-site-icon');
    if (siteIcon && dl.thumbnail) {
      siteIcon.innerHTML = `<img src="${dl.thumbnail}" class="card-thumb-img" alt="">`;
    }

    const progress = Math.min(100, Math.max(0, dl.progress || 0));
    progressFill.style.width = progress + '%';
    progressGlow.style.width = progress + '%';
    progressPercent.textContent = Math.round(progress) + '%';
    progressSpeed.textContent = dl.speed ? formatSpeed(dl.speed) : '— MB/s';
    
    if (dl.status === 'completed' || dl.status === 'complete') {
      const finalSize = dl.total_size || dl.downloaded_size || 0;
      progressSize.textContent = finalSize > 0 ? `${formatSize(finalSize)} • Tamamlandı` : 'Tamamlandı';
      progressEta.textContent = '';
    } else {
      progressSize.textContent = formatSizeRange(dl.downloaded_size, dl.total_size);
      if (dl.status === 'converting') {
        progressEta.textContent = dl.format_type === 'mp3' ? '🎵 MP3 formatına dönüştürülüyor...' : '⚙️ Dönüştürülüyor...';
      } else if (dl.status === 'merging') {
        progressEta.textContent = '📦 Video ve ses birleştiriliyor...';
      } else {
        progressEta.textContent = dl.eta ? formatEta(dl.eta) : (dl.downloaded_size > 0 ? '⏳ İndiriliyor...' : '🚀 Başlatılıyor...');
      }
    }

    // Status badge
    const statusMap = {
      downloading: { text: 'İndiriliyor', class: 'downloading' },
      paused:      { text: 'Duraklatıldı', class: 'paused' },
      converting:  { text: 'Dönüştürülüyor', class: 'converting' },
      complete:    { text: 'Tamamlandı', class: 'complete' },
      completed:   { text: 'Tamamlandı', class: 'complete' },
      cancelled:   { text: 'İptal Edildi', class: 'error' },
      error:       { text: 'Hata', class: 'error' },
      queued:      { text: 'Sırada', class: 'downloading' },
      merging:     { text: 'Birleştiriliyor', class: 'converting' },
    };

    const st = statusMap[dl.status] || statusMap.downloading;
    badge.textContent = st.text;
    badge.className = 'status-badge ' + st.class;

    // Update pause/resume button icon
    const pauseBtn = card.querySelector('.btn-pause');
    if (pauseBtn) {
      if (dl.status === 'paused') {
        pauseBtn.title = 'Devam Et';
        pauseBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>`;
      } else {
        pauseBtn.title = 'Duraklat';
        pauseBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>`;
      }
    }

    // Complete: show ETA as "Tamamlandı"
    if (dl.status === 'complete' || dl.status === 'completed') {
      progressPercent.textContent = '100%';
      progressEta.textContent = 'Tamamlandı';
      progressSpeed.textContent = '';
      progressFill.style.width = '100%';
      progressGlow.style.width = '100%';
    }

    if (dl.status === 'error') {
      progressEta.textContent = dl.error_message || dl.error || 'Hata oluştu';
    }
    if (dl.status === 'cancelled') {
      progressEta.textContent = 'İptal edildi';
      progressSpeed.textContent = '';
    }

    const lang = window.CURRENT_LANG || 'tr';
    const map = lang === 'en' ? TR_TO_EN : EN_TO_TR;
    if (badge && map[badge.textContent.trim()]) badge.textContent = map[badge.textContent.trim()];
    if (progressEta && map[progressEta.textContent.trim()]) progressEta.textContent = map[progressEta.textContent.trim()];
    card.querySelectorAll('.card-action-btn').forEach(b => {
      const t = b.getAttribute('title');
      if (t && map[t]) b.setAttribute('title', map[t]);
    });
  }

  function bindCardActions(card, taskId) {
    const pauseBtn = card.querySelector('.btn-pause');
    const cancelBtn = card.querySelector('.btn-cancel');
    const folderBtn = card.querySelector('.btn-folder');
    const removeBtn = card.querySelector('.btn-remove');

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
    setTimeout(() => card.remove(), 300);
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

    // Custom confirm modal helper
    let confirmActive = false;
    function customConfirm(message, title = 'Onay', okText = 'Tamam', isDanger = false) {
      if (confirmActive) return Promise.resolve(false);
      confirmActive = true;
      return new Promise((resolve) => {
        let modal = document.getElementById('confirm-modal');
        // If it doesn't exist, create it dynamically
        if (!modal) {
          modal = document.createElement('div');
          modal.id = 'confirm-modal';
          modal.className = 'modal-overlay hidden';
          modal.style.zIndex = '100000';
          modal.innerHTML = `
            <div class="modal-content glass-panel" style="max-width: 400px;">
              <div class="modal-header">
                <h3 id="confirm-title">Onay</h3>
                <button id="btn-close-confirm" class="modal-close-btn">✕</button>
              </div>
              <div class="modal-body" style="padding: 10px 0;">
                <p id="confirm-message" style="font-size: 13.5px; color: var(--text-secondary); line-height: 1.5;"></p>
              </div>
              <div class="modal-footer" style="gap: 10px;">
                <button id="btn-confirm-cancel" class="btn-secondary">İptal</button>
                <button id="btn-confirm-ok" class="btn-primary glow-btn">Onay</button>
              </div>
            </div>
          `;
          document.body.appendChild(modal);
        }

        const titleEl = modal.querySelector('#confirm-title');
        const msgEl = modal.querySelector('#confirm-message');
        const btnOk = modal.querySelector('#btn-confirm-ok');
        const btnCancel = modal.querySelector('#btn-confirm-cancel');
        const btnClose = modal.querySelector('#btn-close-confirm');

        titleEl.textContent = title;
        msgEl.textContent = message;
        btnOk.textContent = okText;
        if (isDanger) {
          btnOk.style.background = 'var(--color-error)';
          btnOk.style.borderColor = 'rgba(255,82,82,0.3)';
        } else {
          btnOk.style.background = 'var(--accent-gradient)';
          btnOk.style.borderColor = 'transparent';
        }

        modal.classList.remove('hidden');

        const cleanup = (res) => {
          confirmActive = false;
          modal.classList.add('hidden');
          btnOk.onclick = null;
          btnCancel.onclick = null;
          btnClose.onclick = null;
          modal.onclick = null;
          resolve(res);
        };

        btnOk.onclick = () => cleanup(true);
        btnCancel.onclick = () => cleanup(false);
        btnClose.onclick = () => cleanup(false);
        modal.onclick = (e) => {
          if (e.target === modal) cleanup(false);
        };
      });
    }

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
    $$('.settings-tab input, .settings-tab select').forEach(el => {
      el.addEventListener('change', () => {
        if (el.id === 'setting-language') {
          applyLanguage(el.value);
        }
        saveCurrentSettings();
      });
    });
  }

  const TR_TO_EN = {
    'AYARLAR': 'SETTINGS',
    'Ayarlar': 'Settings',
    'Genel Ayarlar': 'General Settings',
    'Uygulama genel tercihlerini yapılandırın': 'Configure general application preferences',
    'Görünüm & Temalar': 'Appearance & Themes',
    'Arayüz renk paleti ve estetik görünüm modları': 'Interface color palette and aesthetic display modes',
    'İndirme Ayarları': 'Download Settings',
    'İndirme konumu ve dosya organizasyonu': 'Download location and file organization',
    'Desteklenen Platformlar & +18 Siteler': 'Supported Platforms & +18 Sites',
    'Önemli sitelerin özel kayıt dizinleri, VIP çerezleri (cookie) ve kalite ayarları': 'Special save directories for sites, VIP cookies and quality settings',
    'Format & Kalite': 'Format & Quality',
    'Varsayılan format ve kalite ayarları': 'Default format and quality preferences',
    'Ağ Ayarları': 'Network Settings',
    'Bağlantı ve indirme hızı yapılandırması': 'Connection and download speed configuration',
    'Gelişmiş Ayarlar': 'Advanced Settings',
    'İleri düzey yapılandırma seçenekleri': 'Advanced configuration options',
    'Hakkında': 'About',
    'Uygulama bilgileri ve lisans': 'Application information and license',
    'İndirme Geçmişi': 'Download History',
    'Daha önce indirilen tüm dosyalar ve arşivler': 'All previously downloaded files and archives',
    'Geçmiş Boş': 'History Empty',
    'Henüz tamamlanmış bir indirme bulunmuyor.': 'No completed downloads found yet.',
    'Dil': 'Language',
    'Simge durumuna küçültülmüş başlat': 'Start minimized',
    'Pano izleme': 'Clipboard monitor',
    'İndirme klasörü': 'Download folder',
    'Site bazlı alt klasörler': 'Site-based subfolders',
    'Dosya adı şablonu': 'Filename template',
    'Varsayılan video formatı': 'Default video format',
    'Varsayılan ses formatı': 'Default audio format',
    'Varsayılan video kalitesi': 'Default video quality',
    'MP3 bit hızı': 'MP3 bitrate',
    'Eş zamanlı indirme sayısı': 'Concurrent downloads',
    'Hız sınırı': 'Speed limit',
    'Proxy sunucusu': 'Proxy server',
    'FFmpeg yolu': 'FFmpeg path',
    'Önbelleği temizle': 'Clear cache',
    'Ayarları sıfırla': 'Reset settings',
    'Alt Klasör Adı': 'Subfolder Name',
    'Çerezler (cookies.txt)': 'Cookies (cookies.txt)',
    'Maksimum Kalite Tercihi': 'Maximum Quality Preference',
    'Arayüz dili': 'Interface language',
    'Uygulama açıldığında sistem tepsisinde başlat': 'Start in system tray when application opens',
    'Panoya kopyalanan URL\'leri otomatik algıla': 'Automatically detect copied URLs in clipboard',
    'Dosyaların indirileceği varsayılan konum': 'Default location where files will be saved',
    'Her site için ayrı klasör oluştur (YouTube, Twitter vb.)': 'Create separate folders for each site (YouTube, Twitter etc.)',
    'İndirilen dosyalar için ad şablonu': 'Naming template for downloaded files',
    'İndirme için tercih edilen video formatı': 'Preferred video format for downloads',
    'İndirme için tercih edilen ses formatı': 'Preferred audio format for downloads',
    'Video indirmeler için varsayılan çözünürlük': 'Default resolution for video downloads',
    'MP3 dönüştürme için bit hızı': 'Bitrate for MP3 conversion',
    '0 = Sınırsız (Aynı anda inecek maksimum dosya)': '0 = Unlimited (Maximum concurrent downloads)',
    '0 = Sınırsız (MB/s cinsinden)': '0 = Unlimited (in MB/s)',
    'HTTP/SOCKS proxy adresi (isteğe bağlı)': 'HTTP/SOCKS proxy address (optional)',
    'FFmpeg yürütülebilir dosya konumu (otomatik algılanır)': 'FFmpeg executable location (auto-detected)',
    'Geçici dosyaları ve önbellek verilerini sil': 'Delete temporary files and cache data',
    'Tüm ayarları fabrika varsayılanlarına döndür': 'Restore all settings to factory defaults',
    'Downloads klasörü içindeki alt klasör adı': 'Subfolder name inside Downloads directory',
    'Özel oturum veya yaş doğrulaması gerektiren siteler için cookies.txt dosyası': 'cookies.txt file for sites requiring authentication or age verification',
    'Bu platform için indirme kalitesi': 'Download quality for this platform',
    'URL yapıştır veya Ctrl+V bas...': 'Paste URL or press Ctrl+V...',
    'Klasör adı...': 'Folder name...',
    'Dosya seçilmedi...': 'No file selected...',
    'Geçmişi Temizle': 'Clear History',
    'Ayarları Kaydet': 'Save Settings',
    'Dosya Seç': 'Select File',
    'Temizle': 'Clear',
    'İndir': 'Download',
    'Sıfırla': 'Reset',
    'Tek Tıkla İndir': 'One-Click Download',
    'Gözat': 'Browse',
    'Format': 'Format',
    'Kalite': 'Quality',
    '✨ Otomatik (Dengeli)': '✨ Automatic (Balanced)',
    '🎞️ MKV Video (Kayıpsız)': '🎞️ MKV Video (Lossless)',
    '🎵 MP3 Ses (HQ)': '🎵 MP3 Audio (HQ)',
    '💎 FLAC Ses (Stüdyo)': '💎 FLAC Audio (Studio)',
    '📱 M4A Ses (Apple)': '📱 M4A Audio (Apple)',
    '🎙️ WAV Ham Ses': '🎙️ WAV Raw Audio',
    '⚡ Otomatik (Orijinal Kalite)': '⚡ Automatic (Original Quality)',
    'Yüksek Çözünürlük (720p)': 'High Definition (720p)',
    '📱 Standart SD (480p)': '📱 Standard SD (480p)',
    '💾 Tasarruflu (360p)': '💾 Data Saver (360p)',
    'İndirmeye Hazır': 'Ready to Download',
    'URL yapıştırarak veya Ctrl + V basarak indirmeye başla': 'Paste a URL or press Ctrl + V to start downloading',
    'Bağlanıyor...': 'Connecting...',
    'Ana Sayfa': 'Home',
    'Geçmiş': 'History',
    'Hazır': 'Ready',
    'VIP Çerez': 'VIP Cookie',
    'Bulut': 'Cloud',
    'Filigransız': 'No Watermark',
    'Sesli Video': 'Video with Audio',
    'Hızlı Akış': 'Fast Stream',
    'Hızlı': 'Fast',
    'Aktif': 'Active',
    '+18 Destekli': '+18 Supported',
    '1000+ Platform': '1000+ Platforms',
    'Arşiv': 'Archive',
    'ExHentai Çerez': 'ExHentai Cookie',
    '⚙️ Ayarla': '⚙️ Configure',
    'Platform Ayarı': 'Platform Configuration',
    '⏳ Yakında (Ongoing)': '⏳ Coming Soon (Ongoing)',
    'Sürüm 1.1.0': 'Version 1.1.0',
    'Hızlı, güvenilir ve şık medya indirici': 'Fast, reliable and sleek media downloader',
    '© 2026 Suylios. Tüm hakları saklıdır.': '© 2026 Suylios. All rights reserved.',
    'Otomatik algılandı': 'Auto-detected',
    'Duraklat': 'Pause',
    'Devam Et': 'Resume',
    'İptal': 'Cancel',
    'Klasörü Aç': 'Open Folder',
    'Kaldır': 'Remove',
    'İndiriliyor': 'Downloading',
    'Duraklatıldı': 'Paused',
    'Dönüştürülüyor': 'Converting',
    'Tamamlandı': 'Completed',
    'İptal Edildi': 'Cancelled',
    'Hata': 'Error',
    'Sırada': 'Queued',
    'Birleştiriliyor': 'Merging',
    'Hesaplanıyor...': 'Calculating...',
    'İndirme başlıyor...': 'Starting download...',
    'İndiriliyor...': 'Downloading...',
    'öğe': 'items',
    '🎵 MP3 formatına dönüştürülüyor...': '🎵 Converting to MP3 format...',
    '⚙️ Dönüştürülüyor...': '⚙️ Converting...',
    '📦 Video ve ses birleştiriliyor...': '📦 Merging video and audio...',
    '⏳ İndiriliyor...': '⏳ Downloading...',
    '🚀 Başlatılıyor...': '🚀 Initializing...',
    'Hata oluştu': 'Error occurred',
    'İptal edildi': 'Cancelled',
    'Geçmiş temizlendi': 'History cleared',
    'Lütfen bir URL girin': 'Please enter a URL',
    'Geçersiz URL formatı': 'Invalid URL format',
    'Bu URL zaten indirme listesinde aktif!': 'This URL is already active in the download list!',
    'İndirme eklendi': 'Download added',
    'İndirme eklenemedi': 'Failed to add download',
    'Kayıt silindi': 'Record deleted',
    'İndirme iptal edildi': 'Download cancelled',
    'Önbellek temizlendi': 'Cache cleared',
    'Ayarlar başarıyla sıfırlandı': 'Settings reset successfully',
    'Ayarlar sıfırlandı': 'Settings reset',
    'Görünüm modu güncellendi': 'Appearance mode updated',
    'Platform ayarları kaydedildi': 'Platform settings saved',
    'Panoda yeni link algılandı: ': 'New link detected in clipboard: ',
    'Tüm ayarlar varsayılan değerlerine sıfırlanacak. Emin misiniz?': 'All settings will be reset to default values. Are you sure?',
    'Ayarları Sıfırla': 'Reset Settings',
    'Onay': 'Confirm',
    'Tamam': 'OK',
    '🌟 4K Ultra HD (60fps)': '🌟 4K Ultra HD (60fps)',
    '⚡ 2K Quad HD (1440p)': '⚡ 2K Quad HD (1440p)',
    '🎯 Full HD (1080p)': '🎯 Full HD (1080p)',
    '📺 HD Ready (720p)': '📺 HD Ready (720p)',
    '💎 Kayıpsız (24-bit FLAC)': '💎 Lossless (24-bit FLAC)',
    '🔥 320 kbps (HQ MP3)': '🔥 320 kbps (HQ MP3)',
    '✨ 256 kbps (AAC Müzik)': '✨ 256 kbps (AAC Music)',
    '⚡ 192 kbps (Standart)': '⚡ 192 kbps (Standard)',
    '📻 128 kbps (Hızlı Ses)': '📻 128 kbps (Fast Audio)',
    '💾 64 kbps (Mini Boyut)': '💾 64 kbps (Mini Size)',
    'Genel': 'General',
    'İndirme': 'Download',
    'Platform & Siteler': 'Platforms & Sites',
    'Ağ': 'Network',
    'Gelişmiş': 'Advanced',
    'Suylios Siber (Varsayılan)': 'Suylios Cyber (Default)',
    'Açık Tema (Gündüz)': 'Light Theme (Day)',
    'Koyu Tema (Kurşuni)': 'Dark Theme (Slate)',
    'Zümrüt Hacker (Yeşil)': 'Emerald Hacker (Green)',
    'Crimson Gece (Kırmızı)': 'Crimson Night (Red)',
    'Günbatımı Gold (Sarı)': 'Sunset Gold (Yellow)',
    '+18 Akış': '+18 Stream',
    '+18 Hentai': '+18 Hentai',
    '+18 Video': '+18 Video',
    '4K Akış': '4K Stream',
    'Reels & Story': 'Reels & Story',
    'Video & GIF': 'Video & GIF',
    'Galeri': 'Gallery',
    'Sosyal': 'Social',
    'Evrensel (Diğer)': 'Universal (Other)',
    'Dosya': 'File',
    'dosya': 'files'
  };

  const EN_TO_TR = {};
  for (const [tr, en] of Object.entries(TR_TO_EN)) {
    EN_TO_TR[en] = tr;
  }

  function applyLanguage(lang) {
    window.CURRENT_LANG = lang;
    try { localStorage.setItem('suylios_language', lang); } catch(e) {}
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
    const btnClip = $('#btn-clipboard-download'); if (btnClip) btnClip.textContent = lang === 'en' ? 'One-Click Download' : 'Tek Tıkla İndir';
    const btnHist = $('#btn-clear-history'); if (btnHist) btnHist.textContent = lang === 'en' ? 'Clear History' : 'Geçmişi Temizle';
    const spin = $('.loading-spinner span'); if (spin) spin.textContent = lang === 'en' ? 'Connecting...' : 'Bağlanıyor...';

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

    // Refresh Dynamic Quality Dropdowns
    if (typeof updateDynamicQualityOptions === 'function') {
      const fmtSelect = document.getElementById('format-select');
      if (fmtSelect) updateDynamicQualityOptions(fmtSelect.value);
    }
    $$('.cyber-dropdown').forEach(custom => {
      const select = custom.parentElement?.querySelector('select');
      if (select && select.selectedIndex >= 0) {
        const textSpan = custom.querySelector('.cyber-dropdown-text');
        const optText = select.options[select.selectedIndex]?.text;
        if (textSpan && optText) textSpan.textContent = map[optText] || optText;
      }
    });

    // Refresh download cards & UI
    if (state && state.downloads) {
      state.downloads.forEach(dl => updateDownloadCard(dl));
    }
    updateUI();
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
    syncCustomSelects();
  }

  async function saveCurrentSettings() {
    const settings = {
      language: $('#setting-language')?.value || 'tr',
      start_minimized: $('#setting-start-minimized')?.checked || false,
      clipboard_monitor: $('#setting-clipboard-monitor')?.checked ?? true,
      download_path: dom.settingDownloadPath?.value || '',
      subfolders: $('#setting-subfolders')?.checked ?? true,
      filename_template: $('#setting-filename-template')?.value || '%(title)s.%(ext)s',
      video_format: $('#setting-video-format')?.value || 'mp4',
      audio_format: $('#setting-audio-format')?.value || 'mp3',
      default_quality: $('#setting-default-quality')?.value || 'best',
      mp3_bitrate: parseInt($('#setting-mp3-bitrate')?.value) || 192,
      concurrent_downloads: dom.settingConcurrent && dom.settingConcurrent.value !== '' ? parseInt(dom.settingConcurrent.value, 10) : 3,
      speed_limit: (parseFloat($('#setting-speed-limit')?.value) || 0) * 1024 * 1024,
      proxy: $('#setting-proxy')?.value || '',
      ffmpeg_path: $('#setting-ffmpeg-path')?.value || '',
      start_minimized: $('#setting-start-minimized')?.checked ?? false,
      theme: document.body.dataset.theme || 'suylios',
      site_settings: state.settings?.site_settings || {},
    };

    const result = await callApi('save_settings', JSON.stringify(settings));
    if (result?.success) {
      state.settings = settings;
    }
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
  function bindThemes() {
    const savedTheme = localStorage.getItem('suylios_theme') || 'basit-beyaz';
    document.body.dataset.theme = savedTheme;

    const cards = document.querySelectorAll('.theme-card');
    cards.forEach(card => {
      card.classList.toggle('active', card.dataset.theme === savedTheme);
      card.addEventListener('click', () => {
        const theme = card.dataset.theme;
        document.body.dataset.theme = theme;
        localStorage.setItem('suylios_theme', theme);
        cards.forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        saveCurrentSettings();
        showToast('Görünüm modu güncellendi', 'success');
      });
    });
  }

  // ─── SITE CONFIGURATION MODAL ───
  function bindSiteSettings() {
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
        state.settings.site_settings[currentSiteKey] = {
          folder: folderInput || 'Folder',
          cookies: document.getElementById('modal-site-cookies').value.trim(),
          quality: document.getElementById('modal-site-quality').value
        };

        saveCurrentSettings();
        modal?.classList.add('hidden');
        showToast('Platform ayarları kaydedildi', 'success');
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
        document.querySelectorAll('.cyber-dropdown-menu').forEach(m => m.classList.add('hidden'));
        document.querySelectorAll('.cyber-dropdown').forEach(d => d.classList.remove('active'));
        if (isHidden) {
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
})();
