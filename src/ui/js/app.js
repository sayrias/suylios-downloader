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
    if (state.apiReady) return;
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

    // Wire shutdown modal from settings page
    document.addEventListener('click', (e) => {
      if (e.target?.closest('#btn-open-shutdown-modal')) {
        $('#shutdown-modal')?.classList.remove('hidden');
      }
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
    const pages = { main: dom.pageMain, settings: dom.pageSettings, history: dom.pageHistory, scheduled: dom.pageScheduled };
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
    const result = await callApi('add_download', url, format, quality, startTime, endTime, embedMeta, dlSubs, keepOrig);

    dom.btnDownload.disabled = false;
    dom.btnDownload.style.opacity = '';

    if (result && (result.task || result.task_id || result.ok)) {
      dom.urlInput.value = '';
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
    toggleScheduledEmptyState();
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

    const isScheduled = dl.scheduled_at && dl.scheduled_at > 0;
    if (isScheduled && dom.scheduledList) {
      dom.scheduledList.prepend(card);
      toggleScheduledEmptyState();
    } else {
      dom.downloadList.prepend(card);
      toggleEmptyState();
    }
  }

  function updateDownloadCard(dl) {
    const card = $(`.download-card[data-task-id="${dl.id}"]`);
    if (!card) return;

    const isScheduled = dl.scheduled_at && dl.scheduled_at > 0;
    if (!isScheduled && dom.scheduledList && card.parentElement === dom.scheduledList) {
      dom.downloadList.prepend(card);
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
    progressSpeed.textContent = dl.speed ? formatSpeed(dl.speed) : '— MB/s';
    
    const lang = window.CURRENT_LANG || 'tr';
    const completedText = lang === 'en' ? 'Completed' : 'Tamamlandı';

    if (dl.status === 'completed' || dl.status === 'complete') {
      const finalSize = dl.total_size || dl.downloaded_size || 0;
      progressSize.textContent = finalSize > 0 ? `${formatSize(finalSize)} • ${completedText}` : completedText;
      progressEta.textContent = '';
    } else {
      progressSize.textContent = formatSizeRange(dl.downloaded_size, dl.total_size);
      if (dl.scheduled_at && dl.scheduled_at > 0) {
        progressEta.textContent = lang === 'en' ? '⏰ Waiting for scheduled time...' : '⏰ İndirme zamanı bekleniyor...';
      } else if (dl.status === 'converting') {
        progressEta.textContent = dl.format_type === 'mp3' ? '🎵 MP3 formatına dönüştürülüyor...' : '⚙️ Dönüştürülüyor...';
      } else if (dl.status === 'merging') {
        progressEta.textContent = '📦 Video ve ses birleştiriliyor...';
      } else {
        progressEta.textContent = dl.eta ? formatEta(dl.eta) : (dl.downloaded_size > 0 ? '⏳ İndiriliyor...' : '🚀 Başlatılıyor...');
      }
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
      progressEta.textContent = completedText;
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
    const verEl = $('#about-version-text'); if (verEl) verEl.textContent = lang === 'en' ? 'Version 1.2.0' : 'Sürüm 1.2.0';
    const trimKeepEl = $('#trim-keep-text'); if (trimKeepEl) trimKeepEl.textContent = lang === 'en' ? 'Keep original video' : 'Orijinal videoyu sakla';
    const trimKeepLbl = $('#trim-keep-label'); if (trimKeepLbl) trimKeepLbl.title = lang === 'en' ? 'Keep the original file without deleting and cut a copy' : 'Orijinal dosyayı silmeden sakla ve kopyası üzerinde kesim yap';
    const schedDtLbl = $('#schedule-datetime-label'); if (schedDtLbl) schedDtLbl.textContent = lang === 'en' ? 'Date & Time' : 'Tarih & Saat';
    const dtBtnTxt = $('#dt-picker-btn-text'); if (dtBtnTxt) dtBtnTxt.textContent = lang === 'en' ? 'Change' : 'Değiştir';
    const dtModalTitle = $('#dt-picker-modal-title'); if (dtModalTitle) dtModalTitle.textContent = lang === 'en' ? 'Select Date & Time' : 'Tarih & Saat Seçimi';
    const dtTimeLbl = $('#dt-time-label'); if (dtTimeLbl) dtTimeLbl.textContent = lang === 'en' ? 'Time Selection' : 'Saat Seçimi';
    const dtConfirmTxt = $('#dt-confirm-text'); if (dtConfirmTxt) dtConfirmTxt.textContent = lang === 'en' ? 'Select' : 'Seç';
    const dtCancelBtn = $('#btn-dt-picker-cancel'); if (dtCancelBtn) dtCancelBtn.textContent = lang === 'en' ? 'Cancel' : 'İptal';
    
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
      auto_start_windows: $('#setting-auto-start-windows')?.checked ?? false,
      background_mode: $('#setting-background-mode')?.checked ?? true,
      embed_metadata: $('#setting-embed-metadata')?.checked ?? true,
      download_subtitles: $('#setting-download-subtitles')?.checked ?? false,
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

  // ═══════════════════════════════════════════════════════
  // v1.2.0 ─ BATCH DOWNLOAD MODAL
  // ═══════════════════════════════════════════════════════
  function initBatchModal() {
    const modal = $('#batch-modal');
    const btnOpen = $('#btn-batch');
    const btnClose = $('#btn-close-batch');
    const btnCancel = $('#btn-batch-cancel');
    const btnStart = $('#btn-batch-start');

    if (!modal || !btnOpen) return;

    btnOpen.addEventListener('click', () => modal.classList.remove('hidden'));
    const closeModal = () => modal.classList.add('hidden');
    btnClose?.addEventListener('click', closeModal);
    btnCancel?.addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

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

      btnStart.disabled = true;
      btnStart.innerHTML = '⏳ <span>Ekleniyor...</span>';
      const result = await callApi('add_batch_downloads', urlsText, format, quality);
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
    const modal = $('#schedule-modal');
    const btnOpen = $('#btn-schedule');
    const btnClose = $('#btn-close-schedule');
    const btnCancel = $('#btn-schedule-cancel');
    const btnConfirm = $('#btn-schedule-confirm');

    if (!modal || !btnOpen) return;

    btnOpen.addEventListener('click', () => {
      const urlInput = $('#url-input');
      const schedUrl = $('#schedule-url');
      if (schedUrl && urlInput?.value) schedUrl.value = urlInput.value;
      const now = new Date();
      now.setMinutes(now.getMinutes() + 5);
      if (typeof populateCustomDateFields === 'function') populateCustomDateFields(window.CURRENT_LANG || 'tr', now);
      modal.classList.remove('hidden');
    });

    const closeModal = () => modal.classList.add('hidden');
    btnClose?.addEventListener('click', closeModal);
    btnCancel?.addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

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
    const modal = $('#shutdown-modal');
    const btnClose = $('#btn-close-shutdown');
    const btnCancel = $('#btn-shutdown-cancel');
    const btnConfirm = $('#btn-shutdown-confirm');

    if (!modal) return;

    const closeModal = () => modal.classList.add('hidden');
    btnClose?.addEventListener('click', closeModal);
    btnCancel?.addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

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

  async function openPreviewPlayer(taskId) {
    const modal = $('#preview-modal');
    if (!modal) return;

    _currentPreviewTaskId = taskId;
    const loadingEl = $('#preview-loading');
    const videoEl = $('#preview-video');
    const audioEl = $('#preview-audio');
    const titleEl = $('#preview-title');
    const metaEl = $('#preview-meta');

    // Reset state
    if (videoEl) { videoEl.style.display = 'none'; videoEl.src = ''; videoEl.pause?.(); }
    if (audioEl) { audioEl.style.display = 'none'; audioEl.src = ''; audioEl.pause?.(); }
    if (loadingEl) loadingEl.style.display = 'flex';
    if (titleEl) titleEl.textContent = '⏳ Yükleniyor...';
    modal.classList.remove('hidden');

    const result = await callApi('get_file_for_preview', taskId);
    if (loadingEl) loadingEl.style.display = 'none';

    if (!result?.ok) {
      if (titleEl) titleEl.textContent = '❌ Dosya bulunamadı';
      if (metaEl) metaEl.textContent = result?.error || '';
      return;
    }

    if (titleEl) titleEl.textContent = result.title || '▶️ Önizleme';

    if (result.type === 'video' && videoEl) {
      videoEl.src = result.url;
      videoEl.style.display = 'block';
      videoEl.play().catch(() => {});
    } else if (audioEl) {
      audioEl.src = result.url;
      audioEl.style.display = 'block';
      audioEl.play().catch(() => {});
    }
  }

  function initPreviewModal() {
    const modal = $('#preview-modal');
    const btnClose = $('#btn-close-preview');
    if (!modal) return;

    const closeModal = () => {
      modal.classList.add('hidden');
      $('#preview-video')?.pause?.();
      $('#preview-audio')?.pause?.();
      if ($('#preview-video')) $('#preview-video').src = '';
      if ($('#preview-audio')) $('#preview-audio').src = '';
    };

    btnClose?.addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

    $('#btn-preview-open-folder')?.addEventListener('click', () => {
      if (_currentPreviewTaskId) callApi('open_file_location', _currentPreviewTaskId);
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
  });

})();
