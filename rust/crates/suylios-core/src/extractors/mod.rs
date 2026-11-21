use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{anyhow, bail, Result};
use base64::Engine as _;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use url::Url;

use crate::doh::SuyliosDohResolver;
use crate::registry::SiteRegistry;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DirectMediaItem {
    pub title: String,
    pub filename: String,
    pub url: String,
    pub thumbnail: Option<String>,
    pub filesize: u64,
    pub headers: HashMap<String, String>,
    pub max_host_conns: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedGallery {
    pub extractor: String,
    pub title: String,
    pub thumbnail: Option<String>,
    pub is_playlist: bool,
    pub items: Vec<DirectMediaItem>,
}

pub struct NativeExtractorSuite {
    client: reqwest::Client,
}

impl Default for NativeExtractorSuite {
    fn default() -> Self {
        Self::new()
    }
}

impl NativeExtractorSuite {
    pub fn new() -> Self {
        let resolver = Arc::new(SuyliosDohResolver::new(true));
        let client = reqwest::Client::builder()
            .dns_resolver(resolver)
            .danger_accept_invalid_certs(true)
            .timeout(Duration::from_secs(20))
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            .build()
            .unwrap_or_default();
        Self { client }
    }

    pub async fn extract_direct(
        &self,
        url: &str,
        registry: &SiteRegistry,
        extra_token: Option<&str>,
    ) -> Result<ExtractedGallery> {
        let res = registry.resolve_url(url, &[]);
        // Eğer site için aktif bir Hotfix Override varsa Python Hotfix katmanına devredilmeli
        if res.hotfix_override.is_some() {
            bail!("HOTFIX_OVERRIDE_ACTIVE:{}", res.site_key);
        }

        match res.engine.as_str() {
            "pixeldrain" => self.extract_pixeldrain(url, &res.extract_rules).await,
            "gofile" => self.extract_gofile(url, &registry.manifest, extra_token).await,
            "bunkr" => self.extract_bunkr_sign_api(url, &registry.manifest).await,
            "reddit" => self.extract_reddit_gallery(url).await,
            "video_host" => self.extract_video_host(url, &res.extract_rules).await,
            other => bail!("DELEGATE_PYTHON:{}", other),
        }
    }

    /// 1. Pixeldrain Native Rust Extractor (Mirror-Aware)
    pub async fn extract_pixeldrain(&self, url: &str, _rules: &Value) -> Result<ExtractedGallery> {
        let re = Regex::new(r"https?://(?:www\.)?pixeldrain\.[a-z]+/(?P<kind>[ul])/(?P<id>[A-Za-z0-9_-]+)")?;
        let caps = re
            .captures(url)
            .ok_or_else(|| anyhow!("Invalid Pixeldrain URL: {}", url))?;
        let kind = caps.name("kind").unwrap().as_str();
        let id = caps.name("id").unwrap().as_str();

        let mirrors = ["https://pixeldrain.com/api", "https://pixeldrain.net/api"];
        let mut last_err = anyhow!("All Pixeldrain mirrors failed");

        for api_base in mirrors {
            if kind == "u" {
                let info_url = format!("{}/file/{}/info", api_base, id);
                if let Ok(resp) = self.client.get(&info_url).send().await {
                    if resp.status().is_success() {
                        if let Ok(info) = resp.json::<Value>().await {
                            let name = info
                                .get("name")
                                .and_then(|v| v.as_str())
                                .unwrap_or(id)
                                .to_string();
                            let size = info.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
                            let dl_url = format!("{}/file/{}?download", api_base, id);
                            let thumb = Some(format!("{}/file/{}/thumbnail", api_base, id));
                            return Ok(ExtractedGallery {
                                extractor: "pixeldrain".to_string(),
                                title: name.clone(),
                                thumbnail: thumb.clone(),
                                is_playlist: false,
                                items: vec![DirectMediaItem {
                                    title: name.clone(),
                                    filename: Self::safe_filename(&name),
                                    url: dl_url,
                                    thumbnail: thumb,
                                    filesize: size,
                                    headers: HashMap::new(),
                                    max_host_conns: 8,
                                }],
                            });
                        }
                    } else {
                        last_err = anyhow!("Pixeldrain HTTP {}", resp.status());
                    }
                }
            } else {
                let list_url = format!("{}/list/{}", api_base, id);
                if let Ok(resp) = self.client.get(&list_url).send().await {
                    if resp.status().is_success() {
                        if let Ok(data) = resp.json::<Value>().await {
                            let title = data
                                .get("title")
                                .and_then(|v| v.as_str())
                                .unwrap_or(id)
                                .to_string();
                            let mut items = Vec::new();
                            if let Some(files) = data.get("files").and_then(|v| v.as_array()) {
                                for f in files {
                                    let fid = f.get("id").and_then(|v| v.as_str()).unwrap_or("");
                                    let fname =
                                        f.get("name").and_then(|v| v.as_str()).unwrap_or(fid);
                                    let fsize = f.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
                                    if !fid.is_empty() {
                                        items.push(DirectMediaItem {
                                            title: fname.to_string(),
                                            filename: Self::safe_filename(fname),
                                            url: format!("{}/file/{}?download", api_base, fid),
                                            thumbnail: Some(format!(
                                                "{}/file/{}/thumbnail",
                                                api_base, fid
                                            )),
                                            filesize: fsize,
                                            headers: HashMap::new(),
                                            max_host_conns: 6,
                                        });
                                    }
                                }
                            }
                            let thumb = items.first().and_then(|i| i.thumbnail.clone());
                            return Ok(ExtractedGallery {
                                extractor: "pixeldrain".to_string(),
                                title,
                                thumbnail: thumb,
                                is_playlist: items.len() > 1,
                                items,
                            });
                        }
                    }
                }
            }
        }
        Err(last_err)
    }

    /// 2. Gofile Native Rust Extractor (DoH + Dinamik WT SHA-256 + Fallback Salt)
    pub async fn extract_gofile(
        &self,
        url: &str,
        manifest: &Value,
        user_token: Option<&str>,
    ) -> Result<ExtractedGallery> {
        let re = Regex::new(r"https?://(?:www\.)?gofile\.io/(?:d/|/?c=)(?P<id>[A-Za-z0-9_-]+)")?;
        let caps = re
            .captures(url)
            .ok_or_else(|| anyhow!("Invalid Gofile URL: {}", url))?;
        let content_id = caps.name("id").unwrap().as_str();

        let gf_cfg = manifest
            .get("sites")
            .and_then(|s| s.get("gofile"))
            .cloned()
            .unwrap_or(json!({}));
        let api_base = gf_cfg
            .get("api_base")
            .and_then(|v| v.as_str())
            .unwrap_or("https://api.gofile.io");
        let ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
        let x_bl = gf_cfg
            .get("wt_config")
            .and_then(|w| w.get("x_bl"))
            .and_then(|v| v.as_str())
            .unwrap_or("en-US");

        // Token temini: kullanıcı tokenı yoksa yeni guest account token oluştur
        let token = if let Some(t) = user_token.filter(|s| !s.trim().is_empty()) {
            t.trim().to_string()
        } else {
            self.create_gofile_guest_token(api_base, ua).await?
        };

        let salts = ["12af056dacea0b", "9844d94d963d30"];
        let time_bucket = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() / 14400)
            .unwrap_or(0);

        for salt in salts {
            let raw_wt = format!("{}::{}::{}::{}::{}", ua, x_bl, token, time_bucket, salt);
            let mut hasher = Sha256::new();
            hasher.update(raw_wt.as_bytes());
            let wt = hex::encode(hasher.finalize());

            let api_url = format!("{}/contents/{}?wt={}&cache=true", api_base, content_id, wt);
            let resp = self
                .client
                .get(&api_url)
                .header("User-Agent", ua)
                .header("Authorization", format!("Bearer {}", token))
                .header("X-Website-Token", &wt)
                .header("X-BL", x_bl)
                .header("Origin", "https://gofile.io")
                .header("Referer", "https://gofile.io/")
                .send()
                .await?;

            if let Ok(json_body) = resp.json::<Value>().await {
                if json_body.get("status").and_then(|v| v.as_str()) == Some("ok") {
                    if let Some(data) = json_body.get("data") {
                        let title = data
                            .get("name")
                            .and_then(|v| v.as_str())
                            .unwrap_or(content_id)
                            .to_string();
                        let mut items = Vec::new();
                        let mut dl_headers = HashMap::new();
                        dl_headers.insert("User-Agent".to_string(), ua.to_string());
                        dl_headers.insert("Referer".to_string(), "https://gofile.io/".to_string());
                        dl_headers.insert(
                            "Cookie".to_string(),
                            format!("accountToken={}; wt={}", token, wt),
                        );

                        if let Some(children) = data.get("children").and_then(|v| v.as_object()) {
                            for (_cid, child) in children {
                                if child.get("type").and_then(|v| v.as_str()) == Some("file") {
                                    if let Some(link) = child.get("link").and_then(|v| v.as_str()) {
                                        let fname = child
                                            .get("name")
                                            .and_then(|v| v.as_str())
                                            .unwrap_or("file");
                                        let fsize =
                                            child.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
                                        let thumb = child
                                            .get("thumbnail")
                                            .and_then(|v| v.as_str())
                                            .map(|s| s.to_string());
                                        items.push(DirectMediaItem {
                                            title: fname.to_string(),
                                            filename: Self::safe_filename(fname),
                                            url: link.to_string(),
                                            thumbnail: thumb,
                                            filesize: fsize,
                                            headers: dl_headers.clone(),
                                            max_host_conns: 8,
                                        });
                                    }
                                }
                            }
                        }

                        if !items.is_empty() {
                            let thumb = items.iter().find_map(|i| i.thumbnail.clone());
                            return Ok(ExtractedGallery {
                                extractor: "gofile".to_string(),
                                title,
                                thumbnail: thumb,
                                is_playlist: items.len() > 1,
                                items,
                            });
                        }
                    }
                }
            }
        }

        bail!("Gofile direct Rust API extraction required fallback")
    }

    async fn create_gofile_guest_token(&self, api_base: &str, ua: &str) -> Result<String> {
        let url = format!("{}/accounts", api_base);
        let resp = self
            .client
            .post(&url)
            .header("User-Agent", ua)
            .header("Origin", "https://gofile.io")
            .header("Referer", "https://gofile.io/")
            .send()
            .await?;
        let data: Value = resp.json().await?;
        if let Some(tok) = data
            .get("data")
            .and_then(|d| d.get("token"))
            .and_then(|v| v.as_str())
        {
            return Ok(tok.to_string());
        }
        bail!("Could not create Gofile guest token")
    }

    /// 3. Bunkr Native Rust Signing & Mirror Resolver (/api/_001_v2 XOR decode + glb-apisign)
    pub async fn extract_bunkr_sign_api(&self, url: &str, manifest: &Value) -> Result<ExtractedGallery> {
        let parsed = Url::parse(url)?;
        let slug = parsed.path().trim_matches('/').rsplit('/').next().unwrap_or("");
        if slug.is_empty() || parsed.path().starts_with("/a/") {
            // Albüm kazıma ve karmaşık DOM için Python/Hibrit lookahead veya tekil API
            bail!("BUNKR_ALBUM_OR_COMPLEX");
        }

        let api_url = "https://bunkr.cr/api/vs";
        let resp = self
            .client
            .post(api_url)
            .header("Referer", url)
            .json(&json!({ "slug": slug }))
            .send()
            .await?;

        if resp.status().is_success() {
            if let Ok(data) = resp.json::<Value>().await {
                if data.get("encrypted").and_then(|v| v.as_bool()) == Some(true) {
                    if let (Some(ts), Some(enc_url)) = (
                        data.get("timestamp").and_then(|v| v.as_u64()),
                        data.get("url").and_then(|v| v.as_str()),
                    ) {
                        if let Ok(decrypted_url) = Self::decrypt_bunkr_xor_url(enc_url, ts) {
                            let fname = Url::parse(&decrypted_url)
                                .ok()
                                .and_then(|u| {
                                    u.path()
                                        .rsplit('/')
                                        .next()
                                        .map(|s| s.to_string())
                                })
                                .filter(|s| !s.is_empty())
                                .unwrap_or_else(|| format!("{}.mp4", slug));
                            let mut headers = HashMap::new();
                            headers.insert("Referer".to_string(), "https://get.bunkrr.su/".to_string());
                            let _ = manifest;
                            return Ok(ExtractedGallery {
                                extractor: "bunkr".to_string(),
                                title: fname.clone(),
                                thumbnail: None,
                                is_playlist: false,
                                items: vec![DirectMediaItem {
                                    title: fname.clone(),
                                    filename: Self::safe_filename(&fname),
                                    url: decrypted_url,
                                    thumbnail: None,
                                    filesize: 0,
                                    headers,
                                    max_host_conns: 2, // Bunkr 503 koruması: maks 2 soket!
                                }],
                            });
                        }
                    }
                }
            }
        }

        bail!("Bunkr direct vs API fallback needed")
    }

    pub fn decrypt_bunkr_xor_url(enc_b64: &str, timestamp: u64) -> Result<String> {
        let raw = base64::engine::general_purpose::STANDARD.decode(enc_b64.trim())?;
        let key = format!("SECRET_KEY_{}", timestamp / 3600);
        let key_bytes = key.as_bytes();
        let out: Vec<u8> = raw
            .iter()
            .enumerate()
            .map(|(i, b)| b ^ key_bytes[i % key_bytes.len()])
            .collect();
        Ok(String::from_utf8(out)?)
    }

    /// 4. Reddit Native Rust Gallery Extractor
    pub async fn extract_reddit_gallery(&self, url: &str) -> Result<ExtractedGallery> {
        let re = Regex::new(r"(?:comments|gallery)/([a-zA-Z0-9]+)")?;
        let caps = re
            .captures(url)
            .ok_or_else(|| anyhow!("Reddit post ID not found in {}", url))?;
        let post_id = caps.get(1).unwrap().as_str();

        let api_url = format!(
            "https://old.reddit.com/comments/{}/.json?limit=0&raw_json=1",
            post_id
        );
        let resp = self
            .client
            .get(&api_url)
            .header("Accept", "application/json")
            .send()
            .await?;

        if !resp.status().is_success() {
            bail!("Reddit JSON API HTTP {}", resp.status());
        }

        let arr: Value = resp.json().await?;
        let post = arr
            .get(0)
            .and_then(|v| v.get("data"))
            .and_then(|v| v.get("children"))
            .and_then(|v| v.get(0))
            .and_then(|v| v.get("data"))
            .ok_or_else(|| anyhow!("Invalid Reddit JSON structure"))?;

        if post.get("is_video").and_then(|v| v.as_bool()) == Some(true) {
            bail!("DELEGATE_PYTHON:yt-dlp");
        }

        let title = post
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or(post_id)
            .to_string();

        let mut items = Vec::new();
        if let Some(meta) = post.get("media_metadata").and_then(|v| v.as_object()) {
            for (idx, (mid, mval)) in meta.iter().enumerate() {
                if let Some(u) = mval
                    .get("s")
                    .and_then(|s| s.get("u").or_else(|| s.get("gif")))
                    .and_then(|v| v.as_str())
                {
                    let mime = mval.get("m").and_then(|v| v.as_str()).unwrap_or("image/jpg");
                    let ext = mime.rsplit('/').next().unwrap_or("jpg");
                    let fname = format!("{:02}_{}.{}", idx + 1, mid, ext);
                    items.push(DirectMediaItem {
                        title: fname.clone(),
                        filename: Self::safe_filename(&fname),
                        url: u.replace("&amp;", "&"),
                        thumbnail: None,
                        filesize: 0,
                        headers: HashMap::new(),
                        max_host_conns: 4,
                    });
                }
            }
        } else if let Some(direct_u) = post.get("url_overridden_by_dest").and_then(|v| v.as_str()) {
            if direct_u.contains("i.redd.it") || direct_u.contains("i.imgur.com") {
                let ext = direct_u
                    .rsplit('.')
                    .next()
                    .and_then(|s| s.split('?').next())
                    .unwrap_or("jpg");
                let fname = format!("{}.{}", Self::safe_filename(&title), ext);
                items.push(DirectMediaItem {
                    title: title.clone(),
                    filename: fname,
                    url: direct_u.to_string(),
                    thumbnail: Some(direct_u.to_string()),
                    filesize: 0,
                    headers: HashMap::new(),
                    max_host_conns: 4,
                });
            }
        }

        if items.is_empty() {
            bail!("DELEGATE_PYTHON:yt-dlp");
        }

        let thumb = items.first().map(|i| i.url.clone());
        Ok(ExtractedGallery {
            extractor: "reddit".to_string(),
            title,
            thumbnail: thumb,
            is_playlist: items.len() > 1,
            items,
        })
    }

    /// 5. VideoHost Native Rust Extractor (Streamtape / DoodStream / Generic MP4)
    pub async fn extract_video_host(&self, url: &str, _rules: &Value) -> Result<ExtractedGallery> {
        let resp = self.client.get(url).header("Referer", url).send().await?;
        let html = resp.text().await?;

        let parsed = Url::parse(url)?;
        let host = parsed.host_str().unwrap_or("video");
        let base_origin = format!("{}://{}", parsed.scheme(), host);

        let title_re = Regex::new(r"(?i)<title>([^<]+)</title>")?;
        let title = title_re
            .captures(&html)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().trim().to_string())
            .unwrap_or_else(|| "Video".to_string());

        // Streamtape robotlink / get_video
        let st_re = Regex::new(r#"['"](//[^'"]+/get_video\?[^'"]+)['"]"#)?;
        if let Some(caps) = st_re.captures(&html) {
            let raw_link = caps.get(1).unwrap().as_str();
            let direct_url = format!("https:{}&stream=1", raw_link);
            let mut headers = HashMap::new();
            headers.insert("Referer".to_string(), url.to_string());
            let fname = format!("{}.mp4", Self::safe_filename(&title));
            return Ok(ExtractedGallery {
                extractor: "video_host".to_string(),
                title: title.clone(),
                thumbnail: None,
                is_playlist: false,
                items: vec![DirectMediaItem {
                    title,
                    filename: fname,
                    url: direct_url,
                    thumbnail: None,
                    filesize: 0,
                    headers,
                    max_host_conns: 8,
                }],
            });
        }

        // Generic direct MP4
        let mp4_re = Regex::new(r#"['"](https?://[^'"]+\.mp4(?:\?[^'"]+)?)['"]"#)?;
        if let Some(caps) = mp4_re.captures(&html) {
            let direct_url = caps.get(1).unwrap().as_str().to_string();
            let mut headers = HashMap::new();
            headers.insert("Referer".to_string(), format!("{}/", base_origin));
            let fname = format!("{}.mp4", Self::safe_filename(&title));
            return Ok(ExtractedGallery {
                extractor: "video_host".to_string(),
                title: title.clone(),
                thumbnail: None,
                is_playlist: false,
                items: vec![DirectMediaItem {
                    title,
                    filename: fname,
                    url: direct_url,
                    thumbnail: None,
                    filesize: 0,
                    headers,
                    max_host_conns: 8,
                }],
            });
        }

        bail!("VideoHost complex JS obfuscation requires fallback")
    }

    fn safe_filename(name: &str) -> String {
        let cleaned: String = name
            .chars()
            .map(|c| match c {
                '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
                c if c.is_control() => '_',
                other => other,
            })
            .collect();
        let trimmed = cleaned.trim_matches(['.', ' ']);
        if trimmed.is_empty() {
            "download".to_string()
        } else {
            trimmed.chars().take(180).collect()
        }
    }
}
