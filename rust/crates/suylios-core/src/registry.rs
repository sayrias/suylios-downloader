use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use url::Url;

use crate::updater::parse_semver;
use crate::APP_VERSION;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SiteResolution {
    pub site_key: String,
    pub name: String,
    pub folder: String,
    pub engine: String,
    pub is_gallery: bool,
    pub matched_domain: String,
    pub transport: Value,
    pub extract_rules: Value,
    pub hotfix_override: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct SiteRegistry {
    pub manifest: Value,
    pub app_root: PathBuf,
    pub data_dir: PathBuf,
}

impl SiteRegistry {
    pub fn load(app_root: &Path, data_dir: &Path) -> Self {
        let candidates = [
            data_dir.join("runtime").join("hotfix-manifest.json"),
            app_root.join("hotfixes").join("dist").join("hotfix-manifest.json"),
            app_root.join("hotfixes").join("manifest.source.json"),
        ];

        let mut manifest = json!({});
        for path in &candidates {
            if path.is_file() {
                if let Ok(content) = std::fs::read_to_string(path) {
                    if let Ok(parsed) = serde_json::from_str::<Value>(&content) {
                        if parsed.get("schema_version").is_some() {
                            manifest = parsed;
                            break;
                        }
                    }
                }
            }
        }

        Self {
            manifest,
            app_root: app_root.to_path_buf(),
            data_dir: data_dir.to_path_buf(),
        }
    }

    pub fn resolve_url(&self, raw_url: &str, custom_sites: &[Value]) -> SiteResolution {
        let host = Url::parse(raw_url)
            .ok()
            .and_then(|u| u.host_str().map(|s| s.to_lowercase()))
            .unwrap_or_else(|| raw_url.to_lowercase());
        let clean_host = host.strip_prefix("www.").unwrap_or(&host);
        let url_lower = raw_url.to_lowercase();

        // 1. Kullanıcının eklediği özel siteler (custom_sites)
        for cs in custom_sites {
            let domain = cs
                .get("domain")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_lowercase();
            let domain_clean = domain.strip_prefix("www.").unwrap_or(&domain);
            if !domain_clean.is_empty()
                && (clean_host == domain_clean
                    || clean_host.ends_with(&format!(".{}", domain_clean))
                    || url_lower.contains(domain_clean))
            {
                let key = cs
                    .get("key")
                    .and_then(|v| v.as_str())
                    .unwrap_or("other")
                    .to_string();
                let folder = cs
                    .get("folder")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .unwrap_or("Others")
                    .to_string();
                let name = cs
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&folder)
                    .to_string();
                let engine = cs
                    .get("engine")
                    .and_then(|v| v.as_str())
                    .unwrap_or("yt-dlp")
                    .to_string();
                return SiteResolution {
                    site_key: key,
                    name,
                    folder,
                    engine,
                    is_gallery: false,
                    matched_domain: domain_clean.to_string(),
                    transport: json!({}),
                    extract_rules: json!({}),
                    hotfix_override: None,
                };
            }
        }

        // 2. Manifest içindeki builtin_platforms eşlemesi (31+ yerleşik platform)
        if let Some(platforms) = self
            .manifest
            .get("builtin_platforms")
            .and_then(|v| v.as_object())
        {
            for (site_key, meta) in platforms {
                if let Some(domains) = meta.get("domains").and_then(|v| v.as_array()) {
                    for d in domains.iter().filter_map(|v| v.as_str()) {
                        let d_low = d.to_lowercase();
                        if clean_host == d_low
                            || clean_host.ends_with(&format!(".{}", d_low))
                            || (d_low.starts_with("bunkr") && clean_host.starts_with("bunkr"))
                        {
                            let site_cfg = self
                                .manifest
                                .get("sites")
                                .and_then(|s| s.get(site_key))
                                .cloned()
                                .unwrap_or_else(|| json!({}));

                            let folder = meta
                                .get("folder")
                                .and_then(|v| v.as_str())
                                .or_else(|| site_cfg.get("folder").and_then(|v| v.as_str()))
                                .unwrap_or("Others")
                                .to_string();
                            let name = meta
                                .get("name")
                                .and_then(|v| v.as_str())
                                .unwrap_or(&folder)
                                .to_string();
                            let engine = meta
                                .get("engine")
                                .and_then(|v| v.as_str())
                                .unwrap_or("yt-dlp")
                                .to_string();
                            let is_gallery = meta
                                .get("is_gallery")
                                .and_then(|v| v.as_bool())
                                .unwrap_or(false);

                            let hotfix_override = self.active_hotfix_for(&site_cfg);

                            return SiteResolution {
                                site_key: site_key.clone(),
                                name,
                                folder,
                                engine,
                                is_gallery,
                                matched_domain: d_low,
                                transport: site_cfg.get("transport").cloned().unwrap_or(json!({})),
                                extract_rules: site_cfg
                                    .get("extract_rules")
                                    .cloned()
                                    .unwrap_or(json!({})),
                                hotfix_override,
                            };
                        }
                    }
                }
            }
        }

        // 3. Video Host (Streamtape / DoodStream / StreamWish / Filemoon / VoE) kontrolü
        if let Some(vhosts) = self
            .manifest
            .get("sites")
            .and_then(|s| s.get("video_hosts"))
            .and_then(|v| v.as_object())
        {
            for (vh_key, vh_cfg) in vhosts {
                if let Some(domains) = vh_cfg.get("domains").and_then(|v| v.as_array()) {
                    for d in domains.iter().filter_map(|v| v.as_str()) {
                        let d_low = d.to_lowercase();
                        if clean_host == d_low || clean_host.ends_with(&format!(".{}", d_low)) {
                            return SiteResolution {
                                site_key: "other".to_string(),
                                name: vh_key.to_string(),
                                folder: "Others".to_string(),
                                engine: "video_host".to_string(),
                                is_gallery: false,
                                matched_domain: d_low,
                                transport: json!({}),
                                extract_rules: vh_cfg.clone(),
                                hotfix_override: None,
                            };
                        }
                    }
                }
            }
        }

        // 4. Varsayılan: Diğer
        SiteResolution {
            site_key: "other".to_string(),
            name: "Others".to_string(),
            folder: "Others".to_string(),
            engine: "yt-dlp".to_string(),
            is_gallery: false,
            matched_domain: clean_host.to_string(),
            transport: json!({}),
            extract_rules: json!({}),
            hotfix_override: None,
        }
    }

    fn active_hotfix_for(&self, site_cfg: &Value) -> Option<Value> {
        let ov = site_cfg.get("hotfix_override")?;
        if ov.is_null() || !ov.is_object() {
            return None;
        }
        // Emekliye ayırma (retired_in_app_version) kontrolü:
        // Eğer mevcut uygulama sürümü >= retired_in_app_version ise override devre dışı kalır!
        if let Some(retired_ver) = ov.get("retired_in_app_version").and_then(|v| v.as_str()) {
            if parse_semver(APP_VERSION) >= parse_semver(retired_ver) {
                return None;
            }
        }
        Some(ov.clone())
    }

    pub fn merged_supported_sites(&self) -> Vec<Value> {
        let base_candidates = [
            self.app_root.join("hotfixes").join("SUPPORTED_SITES.json"),
            self.app_root.join("SUPPORTED_SITES.json"),
        ];
        let mut by_domain: HashMap<String, Value> = HashMap::new();
        let mut ordered: Vec<Value> = Vec::new();

        for base_path in &base_candidates {
            if let Ok(raw) = std::fs::read_to_string(base_path) {
                if let Ok(arr) = serde_json::from_str::<Vec<Value>>(&raw) {
                    for mut item in arr {
                        Self::normalize_site_entry(&mut item);
                        if let Some(dom) = item.get("domain").and_then(|v| v.as_str()) {
                            by_domain.insert(dom.to_lowercase(), item.clone());
                        }
                        ordered.push(item);
                    }
                    break;
                }
            }
        }

        let delta_candidates = [
            self.data_dir
                .join("runtime")
                .join("supported_sites.delta.json"),
            self.app_root
                .join("hotfixes")
                .join("dist")
                .join("supported_sites.delta.json"),
            self.app_root
                .join("hotfixes")
                .join("supported_sites.delta.json"),
        ];

        for dpath in &delta_candidates {
            if dpath.is_file() {
                if let Ok(raw) = std::fs::read_to_string(dpath) {
                    if let Ok(delta_arr) = serde_json::from_str::<Vec<Value>>(&raw) {
                        for mut item in delta_arr {
                            Self::normalize_site_entry(&mut item);
                            let dom = item
                                .get("domain")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_lowercase();
                            if !dom.is_empty() && !by_domain.contains_key(&dom) {
                                by_domain.insert(dom, item.clone());
                                ordered.push(item);
                            }
                        }
                    }
                }
                break;
            }
        }

        ordered
    }

    fn normalize_site_entry(item: &mut Value) {
        if let Some(obj) = item.as_object_mut() {
            let caps = obj
                .get("Capabilities")
                .and_then(|v| v.as_str())
                .or_else(|| obj.get("features").and_then(|v| v.as_str()))
                .unwrap_or("")
                .to_string();
            obj.insert("Capabilities".to_string(), Value::String(caps.clone()));
            obj.insert("features".to_string(), Value::String(caps));
        }
    }
}
