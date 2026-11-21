use std::collections::HashMap;
use std::fs;
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::doh::SuyliosDohResolver;
use crate::{APP_VERSION, DEFAULT_HOTFIX_PUBLIC_KEY_HEX};

const HOTFIX_MANIFEST_MIRRORS: &[&str] = &[
    "https://sayrias.github.io/suylios-downloader/hotfix/hotfix-manifest.json",
    "https://cdn.jsdelivr.net/gh/sayrias/suylios-downloader@gh-pages/hotfix/hotfix-manifest.json",
    "https://raw.githubusercontent.com/sayrias/suylios-downloader/gh-pages/hotfix/hotfix-manifest.json",
];

const MANAGED_PYPI_PACKAGES: &[&str] = &["yt-dlp", "gallery-dl", "cyberdrop-dl", "async-mega-py"];

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RuntimeState {
    pub manifest_seq: u64,
    pub manifest_etag: String,
    pub last_hotfix_check_epoch: u64,
    pub installed_bundles: HashMap<String, String>,
    pub installed_wheels: HashMap<String, String>,
    pub consecutive_failures: u32,
    pub bad_versions: Vec<String>,
}

pub fn parse_semver(ver: &str) -> (u64, u64, u64, u64) {
    let clean = ver.trim().trim_start_matches(['v', 'V']);
    let mut nums = [0u64; 4];
    for (idx, part) in clean.split(['.', '-', '_']).take(4).enumerate() {
        let digits: String = part.chars().take_while(|c| c.is_ascii_digit()).collect();
        if let Ok(n) = digits.parse::<u64>() {
            nums[idx] = n;
        }
    }
    (nums[0], nums[1], nums[2], nums[3])
}

pub fn verify_ed25519_signature(
    message: &[u8],
    sig_text: &str,
    pubkey_hex: &str,
) -> Result<()> {
    let pub_bytes = hex::decode(pubkey_hex.trim())
        .context("Invalid Ed25519 public key hex")?;
    let pub_arr: [u8; 32] = pub_bytes
        .try_into()
        .map_err(|_| anyhow!("Ed25519 public key must be 32 bytes"))?;
    let verifying_key = VerifyingKey::from_bytes(&pub_arr)
        .context("Failed to parse Ed25519 VerifyingKey")?;

    let first_line = sig_text.lines().next().unwrap_or("").trim();
    let sig_bytes = if first_line.len() == 128 {
        hex::decode(first_line).context("Invalid hex signature")?
    } else {
        base64::engine::general_purpose::STANDARD
            .decode(first_line)
            .context("Invalid base64 signature")?
    };

    let sig_arr: [u8; 64] = sig_bytes
        .try_into()
        .map_err(|_| anyhow!("Ed25519 signature must be 64 bytes"))?;
    let signature = Signature::from_bytes(&sig_arr);

    verifying_key
        .verify(message, &signature)
        .map_err(|e| anyhow!("Ed25519 cryptographic signature verification failed: {}", e))
}

pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex::encode(hasher.finalize())
}

pub struct SmartUpdater {
    pub app_root: PathBuf,
    pub data_dir: PathBuf,
    pub runtime_dir: PathBuf,
    pub client: reqwest::Client,
    pub public_key_hex: String,
}

impl SmartUpdater {
    pub fn new(app_root: &Path, data_dir: &Path) -> Self {
        let runtime_dir = data_dir.join("runtime");
        let _ = fs::create_dir_all(&runtime_dir);

        let resolver = Arc::new(SuyliosDohResolver::new(true));
        let client = reqwest::Client::builder()
            .dns_resolver(resolver)
            .timeout(Duration::from_secs(20))
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) SuyliosDownloader/2.0")
            .build()
            .unwrap_or_default();

        let public_key_hex = std::env::var("SUYLIOS_HOTFIX_PUBLIC_KEY")
            .ok()
            .filter(|s| s.trim().len() == 64)
            .unwrap_or_else(|| DEFAULT_HOTFIX_PUBLIC_KEY_HEX.to_string());

        Self {
            app_root: app_root.to_path_buf(),
            data_dir: data_dir.to_path_buf(),
            runtime_dir,
            client,
            public_key_hex,
        }
    }

    pub fn load_state(&self) -> RuntimeState {
        let state_path = self.runtime_dir.join("state.json");
        if let Ok(raw) = fs::read_to_string(&state_path) {
            if let Ok(st) = serde_json::from_str::<RuntimeState>(&raw) {
                return st;
            }
        }
        RuntimeState::default()
    }

    pub fn save_state(&self, state: &RuntimeState) -> Result<()> {
        let state_path = self.runtime_dir.join("state.json");
        let tmp_path = self.runtime_dir.join("state.json.tmp");
        let serialized = serde_json::to_string_pretty(state)?;
        fs::write(&tmp_path, serialized)?;
        fs::rename(&tmp_path, &state_path)?;
        Ok(())
    }

    /// Kanal 1: Tam Uygulama Sürümü (App Release) Denetimi (Gerçek SemVer ile)
    pub async fn check_app_release(&self, current_ver: &str) -> Value {
        let url = format!(
            "https://api.github.com/repos/sayrias/suylios-downloader/releases/latest?t={}",
            Self::now_epoch()
        );
        let resp = match self
            .client
            .get(&url)
            .header("Accept", "application/vnd.github+json")
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => r,
            Ok(r) => {
                return json!({
                    "has_update": false,
                    "current_version": current_ver,
                    "error": format!("GitHub HTTP {}", r.status())
                })
            }
            Err(e) => {
                return json!({
                    "has_update": false,
                    "current_version": current_ver,
                    "error": e.to_string()
                })
            }
        };

        let data: Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                return json!({
                    "has_update": false,
                    "current_version": current_ver,
                    "error": e.to_string()
                })
            }
        };

        let tag_raw = data.get("tag_name").and_then(|v| v.as_str()).unwrap_or("");
        let tag_clean = tag_raw.trim_start_matches(['v', 'V']);
        let has_update = !tag_clean.is_empty() && parse_semver(tag_clean) > parse_semver(current_ver);

        let is_linux = cfg!(target_os = "linux");
        let mut download_url = data
            .get("html_url")
            .and_then(|v| v.as_str())
            .unwrap_or("https://github.com/sayrias/suylios-downloader/releases")
            .to_string();

        if let Some(assets) = data.get("assets").and_then(|v| v.as_array()) {
            for asset in assets {
                let name = asset.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let b_url = asset
                    .get("browser_download_url")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if is_linux {
                    if name.ends_with(".AppImage")
                        || name.contains("Linux")
                        || name.ends_with(".tar.gz")
                    {
                        download_url = b_url.to_string();
                        break;
                    }
                } else if name.ends_with(".zip") && name.to_lowercase().contains("portable") {
                    download_url = b_url.to_string();
                    break;
                } else if name.ends_with(".exe") {
                    download_url = b_url.to_string();
                }
            }
        }

        json!({
            "has_update": has_update,
            "current_version": current_ver,
            "latest_version": tag_clean,
            "release_notes": data.get("body").and_then(|v| v.as_str()).unwrap_or(""),
            "download_url": download_url,
            "release_name": data.get("name").and_then(|v| v.as_str()).unwrap_or(tag_raw)
        })
    }

    /// Kanal 2: Tek-Repo Sıfır-Release Hotfix Beslemesi (Ed25519 Doğrulamalı)
    pub async fn sync_hotfix_feed(&self, custom_manifest_url: Option<&str>) -> Result<Value> {
        let mut state = self.load_state();
        let mut mirrors: Vec<String> = Vec::new();
        if let Some(u) = custom_manifest_url {
            if !u.trim().is_empty() {
                mirrors.push(u.trim().to_string());
            }
        }
        if let Ok(env_url) = std::env::var("SUYLIOS_HOTFIX_MANIFEST_URL") {
            if !env_url.trim().is_empty() {
                mirrors.push(env_url.trim().to_string());
            }
        }
        for m in HOTFIX_MANIFEST_MIRRORS {
            mirrors.push((*m).to_string());
        }

        let mut fetched_bytes: Option<Vec<u8>> = None;
        let mut fetched_sig: Option<String> = None;
        let mut new_etag = state.manifest_etag.clone();
        let mut source_used = String::new();

        for manifest_url in &mirrors {
            if let Some(local_path) = manifest_url.strip_prefix("file://") {
                let p = PathBuf::from(local_path);
                let sig_p = p.with_extension("sig");
                if p.is_file() && sig_p.is_file() {
                    fetched_bytes = Some(fs::read(&p)?);
                    fetched_sig = Some(fs::read_to_string(&sig_p)?);
                    source_used = manifest_url.clone();
                    break;
                }
                continue;
            }

            let sig_url = if let Some(base) = manifest_url.strip_suffix(".json") {
                format!("{}.sig", base)
            } else {
                format!("{}.sig", manifest_url)
            };

            let mut req = self.client.get(manifest_url);
            if !state.manifest_etag.is_empty() {
                req = req.header("If-None-Match", &state.manifest_etag);
            }

            match req.send().await {
                Ok(resp) if resp.status() == reqwest::StatusCode::NOT_MODIFIED => {
                    state.last_hotfix_check_epoch = Self::now_epoch();
                    let _ = self.save_state(&state);
                    return Ok(json!({
                        "ok": true,
                        "status": "up_to_date",
                        "manifest_seq": state.manifest_seq,
                        "source": manifest_url
                    }));
                }
                Ok(resp) if resp.status().is_success() => {
                    if let Some(et) = resp.headers().get("etag").and_then(|v| v.to_str().ok()) {
                        new_etag = et.to_string();
                    }
                    if let Ok(bytes) = resp.bytes().await {
                        if let Ok(sig_resp) = self.client.get(&sig_url).send().await {
                            if sig_resp.status().is_success() {
                                if let Ok(sig_txt) = sig_resp.text().await {
                                    fetched_bytes = Some(bytes.to_vec());
                                    fetched_sig = Some(sig_txt);
                                    source_used = manifest_url.clone();
                                    break;
                                }
                            }
                        }
                    }
                }
                _ => continue,
            }
        }

        // Uzak ayna henüz yayınlanmadıysa yerel imzalı dist paketini doğrula
        if fetched_bytes.is_none() {
            let local_dist = self.app_root.join("hotfixes").join("dist");
            let m_path = local_dist.join("hotfix-manifest.json");
            let s_path = local_dist.join("hotfix-manifest.sig");
            if m_path.is_file() && s_path.is_file() {
                fetched_bytes = Some(fs::read(&m_path)?);
                fetched_sig = Some(fs::read_to_string(&s_path)?);
                source_used = "local_signed_dist".to_string();
            } else {
                bail!("No reachable hotfix feed mirror or local signed dist found");
            }
        }

        let manifest_bytes = fetched_bytes.unwrap();
        let sig_text = fetched_sig.unwrap();

        // 1. Ed25519 kriptografik imza doğrulaması
        verify_ed25519_signature(&manifest_bytes, &sig_text, &self.public_key_hex)?;

        // 2. JSON ayrıştırma ve Monotonik manifest_seq (Downgrade Saldırısı) kontrolü
        let manifest: Value =
            serde_json::from_slice(&manifest_bytes).context("Invalid hotfix-manifest.json")?;
        let incoming_seq = manifest
            .get("manifest_seq")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| anyhow!("Missing integer manifest_seq"))?;

        if state.manifest_seq > 0 && incoming_seq < state.manifest_seq {
            bail!(
                "Downgrade attack rejected: incoming manifest_seq ({}) < current ({})",
                incoming_seq,
                state.manifest_seq
            );
        }

        // 3. İmzalı manifest ve imzasını runtime/ dizinine atomik yaz
        let runtime_manifest = self.runtime_dir.join("hotfix-manifest.json");
        let runtime_sig = self.runtime_dir.join("hotfix-manifest.sig");
        fs::write(&runtime_manifest, &manifest_bytes)?;
        fs::write(&runtime_sig, &sig_text)?;

        // 4. Değişen imzalı eklenti/override paketlerini doğrula ve atomik swap yap
        let mut updated_bundles: Vec<String> = Vec::new();
        if let Some(bundles) = manifest.get("bundles").and_then(|v| v.as_object()) {
            for (bundle_key, b_meta) in bundles {
                let ver = b_meta.get("version").and_then(|v| v.as_str()).unwrap_or("");
                let expected_sha = b_meta.get("sha256").and_then(|v| v.as_str()).unwrap_or("");
                let url = b_meta.get("url").and_then(|v| v.as_str()).unwrap_or("");
                if ver.is_empty() || expected_sha.is_empty() {
                    continue;
                }
                if state.installed_bundles.get(bundle_key).map(|s| s.as_str()) == Some(expected_sha)
                {
                    continue;
                }
                if let Ok(true) = self
                    .install_bundle_artifact(bundle_key, url, expected_sha)
                    .await
                {
                    state
                        .installed_bundles
                        .insert(bundle_key.clone(), expected_sha.to_string());
                    updated_bundles.push(bundle_key.clone());
                }
            }
        }

        // 5. Site bazlı Hotfix Override paketlerini kontrol et (ve retired olanları temizle)
        if let Some(sites) = manifest.get("sites").and_then(|v| v.as_object()) {
            for (site_key, s_cfg) in sites {
                if let Some(ov) = s_cfg.get("hotfix_override") {
                    if ov.is_object() {
                        let retired = ov
                            .get("retired_in_app_version")
                            .and_then(|v| v.as_str())
                            .unwrap_or("99.0.0");
                        if parse_semver(APP_VERSION) >= parse_semver(retired) {
                            let active_ov_dir =
                                self.runtime_dir.join("hotfixes_active").join(site_key);
                            let _ = fs::remove_dir_all(&active_ov_dir);
                        } else {
                            let sha = ov.get("sha256").and_then(|v| v.as_str()).unwrap_or("");
                            let url = ov.get("url").and_then(|v| v.as_str()).unwrap_or("");
                            let b_key = format!("override_{}", site_key);
                            if !sha.is_empty()
                                && state.installed_bundles.get(&b_key).map(|s| s.as_str())
                                    != Some(sha)
                            {
                                if let Ok(true) =
                                    self.install_site_override(site_key, url, sha).await
                                {
                                    state.installed_bundles.insert(b_key, sha.to_string());
                                    updated_bundles.push(format!("override:{}", site_key));
                                }
                            }
                        }
                    }
                }
            }
        }

        state.manifest_seq = incoming_seq;
        state.manifest_etag = new_etag;
        state.last_hotfix_check_epoch = Self::now_epoch();
        self.save_state(&state)?;

        Ok(json!({
            "ok": true,
            "status": "updated",
            "manifest_seq": incoming_seq,
            "source": source_used,
            "updated_bundles": updated_bundles
        }))
    }

    async fn fetch_artifact_bytes(&self, url: &str) -> Result<Vec<u8>> {
        if let Some(local_path) = url.strip_prefix("file://") {
            return Ok(fs::read(local_path)?);
        }
        // Yerel dist içinde aynı dosya varsa ve ağ erişilemezse fallback
        if let Ok(resp) = self.client.get(url).send().await {
            if resp.status().is_success() {
                return Ok(resp.bytes().await?.to_vec());
            }
        }
        if let Some(fname) = url.rsplit('/').next() {
            let candidate1 = self
                .app_root
                .join("hotfixes")
                .join("dist")
                .join("bundles")
                .join(fname);
            if candidate1.is_file() {
                return Ok(fs::read(candidate1)?);
            }
            let candidate2 = self.app_root.join("hotfixes").join("dist").join(fname);
            if candidate2.is_file() {
                return Ok(fs::read(candidate2)?);
            }
        }
        bail!("Could not download artifact from {}", url)
    }

    async fn install_bundle_artifact(
        &self,
        bundle_key: &str,
        url: &str,
        expected_sha: &str,
    ) -> Result<bool> {
        let raw = self.fetch_artifact_bytes(url).await?;
        let actual_sha = sha256_hex(&raw);
        if !actual_sha.eq_ignore_ascii_case(expected_sha) {
            bail!(
                "SHA-256 mismatch for {}: expected {}, got {}",
                bundle_key,
                expected_sha,
                actual_sha
            );
        }

        if bundle_key == "supported_sites_delta" {
            let dst = self.runtime_dir.join("supported_sites.delta.json");
            fs::write(dst, &raw)?;
            return Ok(true);
        }

        let staging_dir = self.runtime_dir.join(format!("plugins_staging_{}", bundle_key));
        let active_dir = self.runtime_dir.join("plugins_active").join(bundle_key);
        let prev_dir = self.runtime_dir.join("plugins_prev").join(bundle_key);

        let _ = fs::remove_dir_all(&staging_dir);
        fs::create_dir_all(&staging_dir)?;
        Self::extract_zip_bytes(&raw, &staging_dir)?;

        if let Some(parent) = active_dir.parent() {
            fs::create_dir_all(parent)?;
        }
        if let Some(parent) = prev_dir.parent() {
            fs::create_dir_all(parent)?;
        }

        let _ = fs::remove_dir_all(&prev_dir);
        if active_dir.exists() {
            let _ = fs::rename(&active_dir, &prev_dir);
        }
        fs::rename(&staging_dir, &active_dir)?;
        Ok(true)
    }

    async fn install_site_override(
        &self,
        site_key: &str,
        url: &str,
        expected_sha: &str,
    ) -> Result<bool> {
        let raw = self.fetch_artifact_bytes(url).await?;
        let actual_sha = sha256_hex(&raw);
        if !actual_sha.eq_ignore_ascii_case(expected_sha) {
            bail!(
                "Override SHA-256 mismatch for {}: expected {}, got {}",
                site_key,
                expected_sha,
                actual_sha
            );
        }

        let staging = self.runtime_dir.join(format!("hotfix_staging_{}", site_key));
        let active = self.runtime_dir.join("hotfixes_active").join(site_key);
        let prev = self.runtime_dir.join("hotfixes_prev").join(site_key);

        let _ = fs::remove_dir_all(&staging);
        fs::create_dir_all(&staging)?;
        Self::extract_zip_bytes(&raw, &staging)?;

        if let Some(p) = active.parent() {
            fs::create_dir_all(p)?;
        }
        if let Some(p) = prev.parent() {
            fs::create_dir_all(p)?;
        }
        let _ = fs::remove_dir_all(&prev);
        if active.exists() {
            let _ = fs::rename(&active, &prev);
        }
        fs::rename(&staging, &active)?;
        Ok(true)
    }

    /// Kanal 3: PyPI JSON API Üzerinden Üçüncü Parti Motor (.whl) Denetimi ve Güncellemesi
    pub async fn check_pypi_engines(
        &self,
        local_versions: &HashMap<String, String>,
        manifest: &Value,
    ) -> Value {
        let mut results = serde_json::Map::new();
        let pins = manifest.get("pypi_pins").cloned().unwrap_or(json!({}));

        for pkg in MANAGED_PYPI_PACKAGES {
            let current_ver = local_versions
                .get(*pkg)
                .cloned()
                .unwrap_or_else(|| "0.0.0".to_string());
            let pin_cfg = pins.get(*pkg).cloned().unwrap_or(json!({}));

            match self.query_pypi_latest(pkg, &pin_cfg).await {
                Ok((latest_ver, wheel_url, sha256)) => {
                    let has_update = parse_semver(&latest_ver) > parse_semver(&current_ver);
                    results.insert(
                        (*pkg).to_string(),
                        json!({
                            "package": pkg,
                            "current_version": current_ver,
                            "latest_version": latest_ver,
                            "has_update": has_update,
                            "wheel_url": wheel_url,
                            "sha256": sha256
                        }),
                    );
                }
                Err(e) => {
                    results.insert(
                        (*pkg).to_string(),
                        json!({
                            "package": pkg,
                            "current_version": current_ver,
                            "has_update": false,
                            "error": e.to_string()
                        }),
                    );
                }
            }
        }

        Value::Object(results)
    }

    pub async fn query_pypi_latest(
        &self,
        pkg: &str,
        pin_cfg: &Value,
    ) -> Result<(String, String, String)> {
        if pin_cfg.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
            bail!("Updates temporarily pinned/disabled for {}", pkg);
        }

        let blocked: Vec<String> = pin_cfg
            .get("blocked_versions")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        let max_ver = pin_cfg
            .get("max_version")
            .and_then(|v| v.as_str())
            .map(|s| s.trim_start_matches('<').trim().to_string());

        let url = format!("https://pypi.org/pypi/{}/json", pkg);
        let resp = self.client.get(&url).send().await?;
        if !resp.status().is_success() {
            bail!("PyPI HTTP {}", resp.status());
        }
        let data: Value = resp.json().await?;
        let latest = data
            .get("info")
            .and_then(|i| i.get("version"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow!("Missing info.version in PyPI response"))?;

        if blocked.iter().any(|b| b == latest) {
            bail!("Version {} is blocked by hotfix manifest pin", latest);
        }
        if let Some(ref max_v) = max_ver {
            if !max_v.is_empty() && parse_semver(latest) >= parse_semver(max_v) {
                bail!("Version {} exceeds max_version pin <{}", latest, max_v);
            }
        }

        let files = data
            .get("releases")
            .and_then(|r| r.get(latest))
            .and_then(|v| v.as_array())
            .ok_or_else(|| anyhow!("No release files found for {} {}", pkg, latest))?;

        for f in files {
            if f.get("yanked").and_then(|v| v.as_bool()) == Some(true) {
                continue;
            }
            let ptype = f.get("packagetype").and_then(|v| v.as_str()).unwrap_or("");
            let fname = f.get("filename").and_then(|v| v.as_str()).unwrap_or("");
            if ptype == "bdist_wheel" && fname.ends_with("py3-none-any.whl") {
                let w_url = f
                    .get("url")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let sha = f
                    .get("digests")
                    .and_then(|d| d.get("sha256"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if !w_url.is_empty() && !sha.is_empty() {
                    return Ok((latest.to_string(), w_url, sha));
                }
            }
        }

        bail!("No compatible pure-python wheel found for {} {}", pkg, latest)
    }

    pub async fn install_pypi_wheel(
        &self,
        pkg: &str,
        version: &str,
        wheel_url: &str,
        expected_sha256: &str,
        python_bin: &str,
    ) -> Result<Value> {
        let resp = self.client.get(wheel_url).send().await?;
        if !resp.status().is_success() {
            bail!("Wheel download failed: HTTP {}", resp.status());
        }
        let wheel_bytes = resp.bytes().await?.to_vec();
        let actual_sha = sha256_hex(&wheel_bytes);
        if !actual_sha.eq_ignore_ascii_case(expected_sha256) {
            bail!(
                "Wheel SHA-256 mismatch for {}: expected {}, got {}",
                pkg,
                expected_sha256,
                actual_sha
            );
        }

        let active_dir = self.runtime_dir.join("python_overlay_active");
        let prev_dir = self.runtime_dir.join("python_overlay_prev");
        let staging_dir = self.runtime_dir.join("python_overlay_staging");

        let _ = fs::remove_dir_all(&staging_dir);
        fs::create_dir_all(&staging_dir)?;

        // Mevcut active overlay içindeki diğer paketleri staging'e kopyala
        if active_dir.is_dir() {
            Self::copy_dir_recursive(&active_dir, &staging_dir)?;
            let norm_prefix = pkg.replace('-', "_");
            if let Ok(entries) = fs::read_dir(&staging_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name == norm_prefix || name.starts_with(&format!("{}-", norm_prefix)) {
                        let _ = fs::remove_dir_all(entry.path());
                    }
                }
            }
        }

        Self::extract_zip_bytes(&wheel_bytes, &staging_dir)?;

        // Eğer async-mega-py güncellendiyse mega_vault_patched.py yamasını uygula
        let mega_vault_target = staging_dir.join("mega").join("vault.py");
        let mega_patch_src = self
            .app_root
            .join("src")
            .join("extractors")
            .join("mega_vault_patched.py");
        if mega_vault_target.is_file() && mega_patch_src.is_file() {
            let _ = fs::copy(&mega_patch_src, &mega_vault_target);
        }

        // İzole Smoke Test: paketi staging PYTHONPATH ile import et
        let import_mod = match pkg {
            "yt-dlp" => "yt_dlp",
            "gallery-dl" => "gallery_dl",
            "cyberdrop-dl" => "cyberdrop_dl",
            "async-mega-py" => "mega",
            other => other,
        };

        let smoke_status = Command::new(python_bin)
            .arg("-c")
            .arg(format!(
                "import sys; sys.path.insert(0, r'{}'); import {}; print('SMOKE_OK')",
                staging_dir.display(),
                import_mod
            ))
            .output();

        if let Ok(out) = smoke_status {
            let stdout = String::from_utf8_lossy(&out.stdout);
            if !out.status.success() || !stdout.contains("SMOKE_OK") {
                let stderr = String::from_utf8_lossy(&out.stderr);
                let _ = fs::remove_dir_all(&staging_dir);
                bail!("Smoke test failed for {} {}: {}", pkg, version, stderr);
            }
        }

        // Atomik klasör takası: active -> prev, staging -> active
        let _ = fs::remove_dir_all(&prev_dir);
        if active_dir.is_dir() {
            let _ = fs::rename(&active_dir, &prev_dir);
        }
        fs::rename(&staging_dir, &active_dir)?;

        let mut state = self.load_state();
        state
            .installed_wheels
            .insert(pkg.to_string(), version.to_string());
        state.consecutive_failures = 0;
        self.save_state(&state)?;

        Ok(json!({
            "ok": true,
            "package": pkg,
            "version": version,
            "sha256": actual_sha,
            "overlay_dir": active_dir.to_string_lossy()
        }))
    }

    pub fn rollback_python_overlay(&self) -> Result<Value> {
        let active_dir = self.runtime_dir.join("python_overlay_active");
        let prev_dir = self.runtime_dir.join("python_overlay_prev");
        if !prev_dir.is_dir() {
            bail!("Geri dönülecek önceki bir motor sürümü (python_overlay_prev) bulunamadı.");
        }
        let broken_dir = self.runtime_dir.join("python_overlay_broken");
        let _ = fs::remove_dir_all(&broken_dir);
        if active_dir.is_dir() {
            let _ = fs::rename(&active_dir, &broken_dir);
        }
        fs::rename(&prev_dir, &active_dir)?;
        let _ = fs::remove_dir_all(&broken_dir);

        let mut state = self.load_state();
        state.consecutive_failures = 0;
        self.save_state(&state)?;

        Ok(json!({
            "ok": true,
            "status": "rolled_back"
        }))
    }

    fn extract_zip_bytes(raw_zip: &[u8], dest_dir: &Path) -> Result<()> {
        let reader = Cursor::new(raw_zip);
        let mut archive = zip::ZipArchive::new(reader)?;
        for i in 0..archive.len() {
            let mut file = archive.by_index(i)?;
            let Some(enclosed_path) = file.enclosed_name().map(|p| p.to_path_buf()) else {
                continue;
            };
            let outpath = dest_dir.join(enclosed_path);
            if file.name().ends_with('/') {
                fs::create_dir_all(&outpath)?;
            } else {
                if let Some(p) = outpath.parent() {
                    fs::create_dir_all(p)?;
                }
                let mut buf = Vec::with_capacity(file.size() as usize);
                file.read_to_end(&mut buf)?;
                fs::write(&outpath, &buf)?;
            }
        }
        Ok(())
    }

    fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
        fs::create_dir_all(dst)?;
        for entry in fs::read_dir(src)? {
            let entry = entry?;
            let ty = entry.file_type()?;
            let dst_path = dst.join(entry.file_name());
            if ty.is_dir() {
                Self::copy_dir_recursive(&entry.path(), &dst_path)?;
            } else {
                fs::copy(entry.path(), dst_path)?;
            }
        }
        Ok(())
    }

    fn now_epoch() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    }
}
