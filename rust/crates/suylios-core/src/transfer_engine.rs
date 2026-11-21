use std::collections::{HashMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use futures_util::StreamExt;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue, ACCEPT_RANGES, CONTENT_LENGTH, CONTENT_RANGE, ETAG, RANGE};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::doh::SuyliosDohResolver;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferRequest {
    pub url: String,
    pub output_path: String,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    #[serde(default)]
    pub proxy: String,
    #[serde(default = "default_true")]
    pub segmented_download: bool,
    #[serde(default = "default_conns")]
    pub segmented_connections: usize,
    #[serde(default = "default_mode")]
    pub segmented_mode: String,
    #[serde(default)]
    pub speed_limit_bps: u64,
    #[serde(default)]
    pub known_size: u64,
    pub resumable: Option<bool>,
    #[serde(default)]
    pub max_host_conns: usize,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub item_index: usize,
    #[serde(default)]
    pub item_count: isize,
}

fn default_true() -> bool {
    true
}
fn default_conns() -> usize {
    8
}
fn default_mode() -> String {
    "adaptive".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PartCheckpoint {
    pub url: String,
    pub total_size: u64,
    pub etag: String,
    pub completed_ranges: Vec<(u64, u64)>, // [start_inclusive, end_inclusive]
}

impl PartCheckpoint {
    pub fn add_range(&mut self, start: u64, end: u64) {
        if start > end {
            return;
        }
        self.completed_ranges.push((start, end));
        self.completed_ranges.sort_by_key(|r| r.0);
        let mut merged: Vec<(u64, u64)> = Vec::with_capacity(self.completed_ranges.len());
        for (s, e) in self.completed_ranges.drain(..) {
            if let Some(last) = merged.last_mut() {
                if s <= last.1.saturating_add(1) {
                    last.1 = last.1.max(e);
                    continue;
                }
            }
            merged.push((s, e));
        }
        self.completed_ranges = merged;
    }

    pub fn completed_bytes(&self) -> u64 {
        self.completed_ranges
            .iter()
            .map(|(s, e)| e.saturating_sub(*s) + 1)
            .sum()
    }

    pub fn missing_gaps(&self, total_size: u64) -> Vec<(u64, u64)> {
        if total_size == 0 {
            return Vec::new();
        }
        let mut gaps = Vec::new();
        let mut cursor = 0u64;
        for &(s, e) in &self.completed_ranges {
            if s > cursor {
                gaps.push((cursor, s - 1));
            }
            cursor = cursor.max(e.saturating_add(1));
        }
        if cursor < total_size {
            gaps.push((cursor, total_size - 1));
        }
        gaps
    }
}

pub struct SlidingSpeedTracker {
    window: Duration,
    samples: Mutex<VecDeque<(Instant, u64)>>,
    total_in_window: AtomicU64,
    ema_rate: AtomicU64,
    started_at: Instant,
}

impl SlidingSpeedTracker {
    pub fn new(window_secs: f64) -> Self {
        Self {
            window: Duration::from_secs_f64(window_secs),
            samples: Mutex::new(VecDeque::with_capacity(128)),
            total_in_window: AtomicU64::new(0),
            ema_rate: AtomicU64::new(0),
            started_at: Instant::now(),
        }
    }

    pub fn add(&self, n_bytes: u64) {
        let now = Instant::now();
        if let Ok(mut q) = self.samples.lock() {
            q.push_back((now, n_bytes));
            let mut sum = self.total_in_window.fetch_add(n_bytes, Ordering::Relaxed) + n_bytes;
            while let Some(&(t, b)) = q.front() {
                if now.duration_since(t) > self.window {
                    q.pop_front();
                    sum = sum.saturating_sub(b);
                } else {
                    break;
                }
            }
            self.total_in_window.store(sum, Ordering::Relaxed);
        }
    }

    pub fn rate_bps(&self) -> f64 {
        let now = Instant::now();
        let (sum, span) = if let Ok(mut q) = self.samples.lock() {
            let mut sum = self.total_in_window.load(Ordering::Relaxed);
            while let Some(&(t, b)) = q.front() {
                if now.duration_since(t) > self.window {
                    q.pop_front();
                    sum = sum.saturating_sub(b);
                } else {
                    break;
                }
            }
            self.total_in_window.store(sum, Ordering::Relaxed);
            let oldest = q.front().map(|(t, _)| *t).unwrap_or(now);
            let dt = now
                .duration_since(oldest)
                .as_secs_f64()
                .max(now.duration_since(self.started_at).as_secs_f64().min(0.6))
                .max(0.12);
            (sum, dt)
        } else {
            (0, 1.0)
        };

        let instant = (sum as f64) / span;
        let prev = f64::from_bits(self.ema_rate.load(Ordering::Relaxed));
        let next = if prev <= 1.0 {
            instant
        } else {
            prev * 0.68 + instant * 0.32
        };
        self.ema_rate.store(next.to_bits(), Ordering::Relaxed);
        next
    }
}

struct WorkerSlot {
    next_offset: AtomicU64,
    end_offset: AtomicU64,
    active: AtomicBool,
}

impl WorkerSlot {
    fn new() -> Self {
        Self {
            next_offset: AtomicU64::new(1),
            end_offset: AtomicU64::new(0),
            active: AtomicBool::new(false),
        }
    }
}

#[cfg(unix)]
fn write_at_offset(file: &File, buf: &[u8], offset: u64) -> std::io::Result<()> {
    use std::os::unix::fs::FileExt;
    file.write_all_at(buf, offset)
}

#[cfg(windows)]
fn write_at_offset(file: &File, buf: &[u8], offset: u64) -> std::io::Result<()> {
    use std::os::windows::fs::FileExt;
    let mut written = 0;
    while written < buf.len() {
        let n = file.seek_write(&buf[written..], offset + written as u64)?;
        if n == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "failed to write whole buffer",
            ));
        }
        written += n;
    }
    Ok(())
}

pub struct TransferControl {
    pub cancel: AtomicBool,
    pub paused: AtomicBool,
}

impl Default for TransferControl {
    fn default() -> Self {
        Self {
            cancel: AtomicBool::new(false),
            paused: AtomicBool::new(false),
        }
    }
}

pub struct TransferEngine {
    resolver: Arc<SuyliosDohResolver>,
}

impl Default for TransferEngine {
    fn default() -> Self {
        Self::new()
    }
}

impl TransferEngine {
    pub fn new() -> Self {
        Self {
            resolver: Arc::new(SuyliosDohResolver::new(true)),
        }
    }

    fn build_client(&self, req: &TransferRequest) -> Result<reqwest::Client> {
        let mut headers = HeaderMap::new();
        let mut has_ua = false;
        for (k, v) in &req.headers {
            if k.eq_ignore_ascii_case("user-agent") {
                has_ua = true;
            }
            if let (Ok(name), Ok(val)) = (
                HeaderName::from_bytes(k.as_bytes()),
                HeaderValue::from_str(v),
            ) {
                headers.insert(name, val);
            }
        }
        if !has_ua {
            headers.insert(
                reqwest::header::USER_AGENT,
                HeaderValue::from_static(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                ),
            );
        }

        let mut builder = reqwest::Client::builder()
            .dns_resolver(self.resolver.clone())
            .default_headers(headers)
            .danger_accept_invalid_certs(true)
            .pool_max_idle_per_host(32)
            .tcp_nodelay(true)
            .connect_timeout(Duration::from_secs(12));

        if !req.proxy.trim().is_empty() {
            if let Ok(px) = reqwest::Proxy::all(req.proxy.trim()) {
                builder = builder.proxy(px);
            }
        }

        Ok(builder.build()?)
    }

    fn should_start_direct_segmented(mode: &str, total_size: u64, is_bunkr: bool) -> bool {
        if total_size == 0 {
            return false;
        }
        match mode.trim().to_lowercase().as_str() {
            "turbo" => total_size >= 512 * 1024,
            "always" => total_size >= 1024 * 1024,
            "large" => total_size >= 5 * 1024 * 1024,
            _ => {
                if is_bunkr {
                    total_size >= 2 * 1024 * 1024
                } else {
                    total_size >= 10 * 1024 * 1024
                }
            }
        }
    }

    fn compute_smart_workers(max_conns: usize, total_size: u64, mode: &str) -> usize {
        let cap = max_conns.clamp(1, 32);
        if cap <= 1 || total_size == 0 {
            return 1;
        }
        let m = mode.trim().to_lowercase();
        if m == "turbo" {
            if total_size < 2 * 1024 * 1024 {
                return cap.min(4);
            }
            return cap;
        }
        if total_size < 4 * 1024 * 1024 {
            cap.min(2)
        } else if total_size < 16 * 1024 * 1024 {
            cap.min(4)
        } else if total_size < 64 * 1024 * 1024 {
            cap.min(6)
        } else {
            cap
        }
    }

    pub async fn download<F>(
        &self,
        req: TransferRequest,
        ctrl: Arc<TransferControl>,
        mut on_progress: F,
    ) -> Result<PathBuf>
    where
        F: FnMut(Value) + Send + 'static,
    {
        let client = self.build_client(&req)?;
        let target_path = PathBuf::from(&req.output_path);
        if let Some(parent) = target_path.parent() {
            fs::create_dir_all(parent)?;
        }

        if target_path.is_file() && target_path.metadata().map(|m| m.len()).unwrap_or(0) > 0 {
            on_progress(json!({
                "status": "downloading",
                "status_detail": "Tamamlandı",
                "downloaded_bytes": target_path.metadata()?.len(),
                "total_bytes": target_path.metadata()?.len(),
                "speed": 0.0,
                "eta": 0,
                "filename": req.display_name
            }));
            return Ok(target_path);
        }

        let tmp_path = PathBuf::from(format!("{}.downloading", req.output_path));
        let part_meta_path = PathBuf::from(format!("{}.suylios-part", req.output_path));

        let mut total_size = req.known_size;
        let mut accept_ranges = req.resumable.unwrap_or(false);
        let mut server_etag = String::new();

        let max_workers = if req.max_host_conns > 0 {
            req.segmented_connections.min(req.max_host_conns).max(1)
        } else {
            req.segmented_connections.clamp(1, 32)
        };

        // 1. Boyut veya Range desteği bilinmiyorsa Range: bytes=0-0 ile hızlı probe
        if req.segmented_download && max_workers > 1 && (total_size == 0 || req.resumable.is_none())
        {
            if let Ok(resp) = client
                .get(&req.url)
                .header(RANGE, "bytes=0-0")
                .send()
                .await
            {
                let status = resp.status().as_u16();
                if let Some(et) = resp.headers().get(ETAG).and_then(|v| v.to_str().ok()) {
                    server_etag = et.to_string();
                }
                if status == 206 {
                    accept_ranges = true;
                    if let Some(cr) = resp
                        .headers()
                        .get(CONTENT_RANGE)
                        .and_then(|v| v.to_str().ok())
                    {
                        if let Some(slash_part) = cr.rsplit('/').next() {
                            if let Ok(sz) = slash_part.trim().parse::<u64>() {
                                total_size = sz;
                            }
                        }
                    }
                } else if status == 200 {
                    if let Some(ar) = resp
                        .headers()
                        .get(ACCEPT_RANGES)
                        .and_then(|v| v.to_str().ok())
                    {
                        if ar.to_lowercase().contains("bytes") {
                            accept_ranges = true;
                        }
                    }
                    if total_size == 0 {
                        if let Some(cl) = resp
                            .headers()
                            .get(CONTENT_LENGTH)
                            .and_then(|v| v.to_str().ok())
                        {
                            if let Ok(sz) = cl.trim().parse::<u64>() {
                                total_size = sz;
                            }
                        }
                    }
                }
            }
        }

        let is_bunkr = req.url.to_lowercase().contains("bunkr");
        let direct_segmented = req.segmented_download
            && max_workers > 1
            && accept_ranges
            && Self::should_start_direct_segmented(&req.segmented_mode, total_size, is_bunkr);

        if direct_segmented {
            self.download_segmented_pwrite(
                &client,
                &req,
                &tmp_path,
                &part_meta_path,
                total_size,
                &server_etag,
                max_workers,
                0,
                ctrl.clone(),
                &mut on_progress,
            )
            .await?;
        } else {
            // Tek bağlantı başlat; adaptive modda yavaşlarsa anında parçalı moda yükselt
            let switched = self
                .download_single_or_upgrade(
                    &client,
                    &req,
                    &tmp_path,
                    &part_meta_path,
                    &mut total_size,
                    &mut accept_ranges,
                    &server_etag,
                    max_workers,
                    ctrl.clone(),
                    &mut on_progress,
                )
                .await?;

            if let Some(switch_offset) = switched {
                self.download_segmented_pwrite(
                    &client,
                    &req,
                    &tmp_path,
                    &part_meta_path,
                    total_size,
                    &server_etag,
                    max_workers,
                    switch_offset,
                    ctrl.clone(),
                    &mut on_progress,
                )
                .await?;
            }
        }

        if ctrl.cancel.load(Ordering::Relaxed) {
            let _ = fs::remove_file(&tmp_path);
            let _ = fs::remove_file(&part_meta_path);
            bail!("İndirme iptal edildi.");
        }

        if tmp_path.exists() {
            fs::rename(&tmp_path, &target_path)?;
        }
        let _ = fs::remove_file(&part_meta_path);

        let final_len = target_path.metadata().map(|m| m.len()).unwrap_or(total_size);
        on_progress(json!({
            "status": "downloading",
            "status_detail": "Tamamlandı",
            "downloaded_bytes": final_len,
            "total_bytes": final_len,
            "speed": 0.0,
            "eta": 0,
            "filename": req.display_name
        }));

        Ok(target_path)
    }

    #[allow(clippy::too_many_arguments)]
    async fn download_single_or_upgrade<F>(
        &self,
        client: &reqwest::Client,
        req: &TransferRequest,
        tmp_path: &Path,
        part_meta_path: &Path,
        total_size: &mut u64,
        accept_ranges: &mut bool,
        server_etag: &str,
        max_workers: usize,
        ctrl: Arc<TransferControl>,
        on_progress: &mut F,
    ) -> Result<Option<u64>>
    where
        F: FnMut(Value) + Send + 'static,
    {
        let mut downloaded = if tmp_path.is_file() {
            tmp_path.metadata().map(|m| m.len()).unwrap_or(0)
        } else {
            0
        };

        let tracker = SlidingSpeedTracker::new(3.0);
        let can_upgrade = req.segmented_download
            && max_workers > 1
            && req.segmented_mode.eq_ignore_ascii_case("adaptive");

        let mut request = client.get(&req.url);
        if downloaded > 0 {
            request = request.header(RANGE, format!("bytes={}-", downloaded));
        }

        let resp = request.send().await.context("Single stream request failed")?;
        let status = resp.status().as_u16();
        if status != 200 && status != 206 {
            bail!("HTTP {}", status);
        }

        if status == 206 {
            *accept_ranges = true;
        } else {
            downloaded = 0;
            if let Some(ar) = resp
                .headers()
                .get(ACCEPT_RANGES)
                .and_then(|v| v.to_str().ok())
            {
                if ar.to_lowercase().contains("bytes") {
                    *accept_ranges = true;
                }
            }
        }

        if *total_size == 0 {
            if let Some(cl) = resp
                .headers()
                .get(CONTENT_LENGTH)
                .and_then(|v| v.to_str().ok())
            {
                if let Ok(sz) = cl.trim().parse::<u64>() {
                    *total_size = downloaded + sz;
                }
            }
        }

        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .read(true)
            .open(tmp_path)?;
        if *total_size > 0 && downloaded == 0 {
            let _ = file.set_len(*total_size);
        }

        let started_stream = Instant::now();
        let mut last_emit = Instant::now() - Duration::from_secs(1);
        let mut stream = resp.bytes_stream();

        while let Some(chunk_res) = stream.next().await {
            if ctrl.cancel.load(Ordering::Relaxed) {
                bail!("İndirme iptal edildi.");
            }
            while ctrl.paused.load(Ordering::Relaxed) {
                if ctrl.cancel.load(Ordering::Relaxed) {
                    bail!("İndirme iptal edildi.");
                }
                tokio::time::sleep(Duration::from_millis(200)).await;
            }

            let chunk = chunk_res?;
            if chunk.is_empty() {
                continue;
            }
            write_at_offset(&file, &chunk, downloaded)?;
            let len = chunk.len() as u64;
            downloaded += len;
            tracker.add(len);

            if req.speed_limit_bps > 0 {
                let rate = tracker.rate_bps();
                if rate > req.speed_limit_bps as f64 {
                    let sleep_s = (len as f64 / req.speed_limit_bps as f64).min(0.4);
                    tokio::time::sleep(Duration::from_secs_f64(sleep_s)).await;
                }
            }

            let now = Instant::now();
            if now.duration_since(last_emit) >= Duration::from_millis(120) {
                last_emit = now;
                let spd = tracker.rate_bps();
                let eta = if spd > 1.0 && *total_size > downloaded {
                    ((*total_size - downloaded) as f64 / spd) as u64
                } else {
                    0
                };
                on_progress(json!({
                    "status": "downloading",
                    "status_detail": "Tek bağlantı iniyor",
                    "downloaded_bytes": downloaded,
                    "total_bytes": *total_size,
                    "speed": spd,
                    "eta": eta,
                    "filename": req.display_name,
                    "item_index": req.item_index,
                    "item_count": req.item_count
                }));

                // Adaptif modda yavaş akış tespiti -> Parçalı work-stealing motora yükselt!
                if can_upgrade
                    && *accept_ranges
                    && *total_size > downloaded + 1536 * 1024
                    && now.duration_since(started_stream) >= Duration::from_millis(3200)
                    && spd < 450.0 * 1024.0
                {
                    let mut cp = PartCheckpoint {
                        url: req.url.clone(),
                        total_size: *total_size,
                        etag: server_etag.to_string(),
                        completed_ranges: Vec::new(),
                    };
                    if downloaded > 0 {
                        cp.add_range(0, downloaded - 1);
                    }
                    if let Ok(s) = serde_json::to_string(&cp) {
                        let _ = fs::write(part_meta_path, s);
                    }
                    return Ok(Some(downloaded));
                }
            }
        }

        if *total_size > 0 && downloaded < *total_size {
            let _ = file.set_len(downloaded);
        }
        Ok(None)
    }

    #[allow(clippy::too_many_arguments)]
    async fn download_segmented_pwrite<F>(
        &self,
        client: &reqwest::Client,
        req: &TransferRequest,
        tmp_path: &Path,
        part_meta_path: &Path,
        total_size: u64,
        server_etag: &str,
        max_workers: usize,
        initial_contiguous_bytes: u64,
        ctrl: Arc<TransferControl>,
        on_progress: &mut F,
    ) -> Result<()>
    where
        F: FnMut(Value) + Send + 'static,
    {
        let workers = Self::compute_smart_workers(
            max_workers,
            total_size.saturating_sub(initial_contiguous_bytes),
            &req.segmented_mode,
        );

        let file = Arc::new(
            OpenOptions::new()
                .create(true)
                .write(true)
                .read(true)
                .open(tmp_path)?,
        );
        file.set_len(total_size)?;

        // .suylios-part checkpoint yükle veya oluştur
        let mut checkpoint = if part_meta_path.is_file() {
            fs::read_to_string(part_meta_path)
                .ok()
                .and_then(|s| serde_json::from_str::<PartCheckpoint>(&s).ok())
                .filter(|cp| {
                    cp.total_size == total_size
                        && (cp.etag.is_empty() || server_etag.is_empty() || cp.etag == server_etag)
                })
                .unwrap_or_else(|| PartCheckpoint {
                    url: req.url.clone(),
                    total_size,
                    etag: server_etag.to_string(),
                    completed_ranges: Vec::new(),
                })
        } else {
            PartCheckpoint {
                url: req.url.clone(),
                total_size,
                etag: server_etag.to_string(),
                completed_ranges: Vec::new(),
            }
        };

        if initial_contiguous_bytes > 0 {
            checkpoint.add_range(0, initial_contiguous_bytes - 1);
        }

        let gaps = checkpoint.missing_gaps(total_size);
        if gaps.is_empty() {
            return Ok(());
        }

        // Başlangıç kuyruğunu dengeli parçalara böl
        let piece_size: u64 = if workers <= 2 && total_size >= 32 * 1024 * 1024 {
            4 * 1024 * 1024
        } else {
            2 * 1024 * 1024
        };
        let min_split_bytes: u64 = 384 * 1024;

        let mut initial_queue: VecDeque<(u64, u64)> = VecDeque::new();
        for (gs, ge) in gaps {
            let mut cur = gs;
            while cur <= ge {
                let end = (cur + piece_size - 1).min(ge);
                initial_queue.push_back((cur, end));
                cur = end.saturating_add(1);
            }
        }

        let queue = Arc::new(Mutex::new(initial_queue));
        let cp_arc = Arc::new(Mutex::new(checkpoint));
        let downloaded_total = Arc::new(AtomicU64::new(
            cp_arc.lock().map(|c| c.completed_bytes()).unwrap_or(0),
        ));
        let tracker = Arc::new(SlidingSpeedTracker::new(3.0));
        let slots: Arc<Vec<WorkerSlot>> =
            Arc::new((0..workers).map(|_| WorkerSlot::new()).collect());

        let mut handles = Vec::with_capacity(workers);
        for wid in 0..workers {
            let client_c = client.clone();
            let url_c = req.url.clone();
            let file_c = file.clone();
            let queue_c = queue.clone();
            let cp_c = cp_arc.clone();
            let dl_c = downloaded_total.clone();
            let tracker_c = tracker.clone();
            let slots_c = slots.clone();
            let ctrl_c = ctrl.clone();
            let speed_limit = req.speed_limit_bps;

            handles.push(tokio::spawn(async move {
                loop {
                    if ctrl_c.cancel.load(Ordering::Relaxed) {
                        break;
                    }
                    while ctrl_c.paused.load(Ordering::Relaxed) {
                        if ctrl_c.cancel.load(Ordering::Relaxed) {
                            return Ok::<(), anyhow::Error>(());
                        }
                        tokio::time::sleep(Duration::from_millis(200)).await;
                    }

                    // 1. Kuyruktan parça al; kuyruk boşsa en büyük kalan parçayı ortadan çal (Work-Stealing!)
                    let segment = {
                        let mut q = queue_c.lock().unwrap();
                        q.pop_front()
                    }
                    .or_else(|| {
                        let mut best_idx = None;
                        let mut best_rem = 0u64;
                        for (idx, sl) in slots_c.iter().enumerate() {
                            if idx == wid || !sl.active.load(Ordering::Relaxed) {
                                continue;
                            }
                            let n = sl.next_offset.load(Ordering::Acquire);
                            let e = sl.end_offset.load(Ordering::Acquire);
                            if e > n {
                                let rem = e - n + 1;
                                if rem >= min_split_bytes * 2 && rem > best_rem {
                                    best_rem = rem;
                                    best_idx = Some(idx);
                                }
                            }
                        }
                        if let Some(donor_idx) = best_idx {
                            let donor = &slots_c[donor_idx];
                            let n = donor.next_offset.load(Ordering::Acquire);
                            let e = donor.end_offset.load(Ordering::Acquire);
                            if e > n && (e - n + 1) >= min_split_bytes * 2 {
                                let mid = n + (e - n) / 2;
                                if donor
                                    .end_offset
                                    .compare_exchange(e, mid, Ordering::AcqRel, Ordering::Acquire)
                                    .is_ok()
                                {
                                    return Some((mid + 1, e));
                                }
                            }
                        }
                        None
                    });

                    let Some((seg_start, seg_end)) = segment else {
                        break;
                    };

                    let slot = &slots_c[wid];
                    slot.next_offset.store(seg_start, Ordering::Release);
                    slot.end_offset.store(seg_end, Ordering::Release);
                    slot.active.store(true, Ordering::Release);

                    let mut cur = seg_start;
                    let mut retries = 0u32;

                    while cur <= slot.end_offset.load(Ordering::Acquire) {
                        if ctrl_c.cancel.load(Ordering::Relaxed) {
                            break;
                        }
                        if ctrl_c.paused.load(Ordering::Relaxed) {
                            let cur_end = slot.end_offset.load(Ordering::Acquire);
                            if cur <= cur_end {
                                queue_c.lock().unwrap().push_front((cur, cur_end));
                            }
                            break;
                        }

                        let req_end = slot.end_offset.load(Ordering::Acquire);
                        let resp_res = client_c
                            .get(&url_c)
                            .header(RANGE, format!("bytes={}-{}", cur, req_end))
                            .send()
                            .await;

                        let Ok(resp) = resp_res else {
                            retries += 1;
                            if retries > 4 {
                                let cur_end = slot.end_offset.load(Ordering::Acquire);
                                if cur <= cur_end {
                                    queue_c.lock().unwrap().push_back((cur, cur_end));
                                }
                                break;
                            }
                            tokio::time::sleep(Duration::from_millis(300 * retries as u64)).await;
                            continue;
                        };

                        if resp.status().as_u16() != 206 {
                            retries += 1;
                            if retries > 4 {
                                break;
                            }
                            tokio::time::sleep(Duration::from_millis(350 * retries as u64)).await;
                            continue;
                        }

                        let mut stream = resp.bytes_stream();
                        let mut stream_err = false;

                        while let Some(item) = stream.next().await {
                            if ctrl_c.cancel.load(Ordering::Relaxed)
                                || ctrl_c.paused.load(Ordering::Relaxed)
                            {
                                break;
                            }
                            let Ok(chunk) = item else {
                                stream_err = true;
                                break;
                            };
                            if chunk.is_empty() {
                                continue;
                            }

                            let allowed_end = slot.end_offset.load(Ordering::Acquire);
                            if cur > allowed_end {
                                break;
                            }
                            let max_len = (allowed_end - cur + 1) as usize;
                            let write_slice = if chunk.len() > max_len {
                                &chunk[..max_len]
                            } else {
                                &chunk[..]
                            };

                            write_at_offset(&file_c, write_slice, cur)?;
                            let w_len = write_slice.len() as u64;
                            if let Ok(mut cp) = cp_c.lock() {
                                cp.add_range(cur, cur + w_len - 1);
                            }
                            cur += w_len;
                            slot.next_offset.store(cur, Ordering::Release);
                            dl_c.fetch_add(w_len, Ordering::Relaxed);
                            tracker_c.add(w_len);

                            if speed_limit > 0 {
                                let rate = tracker_c.rate_bps();
                                if rate > speed_limit as f64 {
                                    let delay = (w_len as f64 / speed_limit as f64).min(0.25);
                                    tokio::time::sleep(Duration::from_secs_f64(delay)).await;
                                }
                            }

                            if cur > allowed_end {
                                break;
                            }
                        }

                        if !stream_err {
                            break;
                        }
                        retries += 1;
                        if retries > 4 {
                            let cur_end = slot.end_offset.load(Ordering::Acquire);
                            if cur <= cur_end {
                                queue_c.lock().unwrap().push_back((cur, cur_end));
                            }
                            break;
                        }
                    }

                    slot.active.store(false, Ordering::Release);
                }
                Ok::<(), anyhow::Error>(())
            }));
        }

        // Ana ilerleme ve .suylios-part checkpoint döngüsü
        let mut last_cp_save = Instant::now();
        loop {
            let all_done = handles.iter().all(|h| h.is_finished());
            let dl_now = downloaded_total.load(Ordering::Relaxed).min(total_size);
            let spd = tracker.rate_bps();
            let active_cnt = slots
                .iter()
                .filter(|s| s.active.load(Ordering::Relaxed))
                .count();
            let eta = if spd > 1.0 && total_size > dl_now {
                ((total_size - dl_now) as f64 / spd) as u64
            } else {
                0
            };

            let detail = if dl_now * 100 >= total_size * 95 && active_cnt > 1 {
                format!("Kuyruk sonu hızlandırılıyor ({} parça)", active_cnt)
            } else {
                format!("{} parça paralel iniyor", active_cnt.max(1))
            };

            on_progress(json!({
                "status": "downloading",
                "status_detail": detail,
                "downloaded_bytes": dl_now,
                "total_bytes": total_size,
                "speed": spd,
                "eta": eta,
                "filename": req.display_name,
                "item_index": req.item_index,
                "item_count": req.item_count
            }));

            if last_cp_save.elapsed() >= Duration::from_millis(800) {
                last_cp_save = Instant::now();
                if let Ok(cp) = cp_arc.lock() {
                    if let Ok(s) = serde_json::to_string(&*cp) {
                        let _ = fs::write(part_meta_path, s);
                    }
                }
            }

            if all_done || ctrl.cancel.load(Ordering::Relaxed) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(120)).await;
        }

        for h in handles {
            let _ = h.await;
        }

        // Son kontrol: eksik kalan ufak aralık varsa kontrol et
        let final_missing = cp_arc
            .lock()
            .map(|c| c.missing_gaps(total_size))
            .unwrap_or_default();
        if !final_missing.is_empty() && !ctrl.cancel.load(Ordering::Relaxed) {
            for (ms, me) in final_missing {
                let resp = client
                    .get(&req.url)
                    .header(RANGE, format!("bytes={}-{}", ms, me))
                    .send()
                    .await?;
                let bytes = resp.bytes().await?;
                write_at_offset(&file, &bytes, ms)?;
            }
        }

        Ok(())
    }
}
