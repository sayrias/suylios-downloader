use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr, ToSocketAddrs};
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};

use reqwest::dns::{Addrs, Name, Resolve, Resolving};
use serde::Deserialize;

const DOH_TTL: Duration = Duration::from_secs(600);

const DEFAULT_DOH_ENDPOINTS: &[&str] = &[
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
    "https://dns.quad9.net/dns-query",
];

#[derive(Debug, Deserialize)]
struct DohAnswer {
    #[serde(rename = "type")]
    record_type: Option<u16>,
    data: Option<String>,
}

#[derive(Debug, Deserialize)]
struct DohResponse {
    #[serde(rename = "Status")]
    status: Option<u16>,
    #[serde(rename = "Answer")]
    answer: Option<Vec<DohAnswer>>,
}

#[derive(Clone)]
struct CacheEntry {
    ips: Vec<IpAddr>,
    inserted_at: Instant,
}

#[derive(Clone)]
pub struct SuyliosDohResolver {
    cache: Arc<RwLock<HashMap<String, CacheEntry>>>,
    doh_client: reqwest::Client,
    ipv4_first: bool,
}

impl Default for SuyliosDohResolver {
    fn default() -> Self {
        Self::new(true)
    }
}

impl SuyliosDohResolver {
    pub fn new(ipv4_first: bool) -> Self {
        let doh_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(3))
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0")
            .build()
            .unwrap_or_default();

        Self {
            cache: Arc::new(RwLock::new(HashMap::new())),
            doh_client,
            ipv4_first,
        }
    }

    pub async fn resolve_host(&self, host: &str) -> Vec<IpAddr> {
        if let Ok(ip) = host.parse::<IpAddr>() {
            return vec![ip];
        }

        if let Ok(guard) = self.cache.read() {
            if let Some(entry) = guard.get(host) {
                if entry.inserted_at.elapsed() < DOH_TTL && !entry.ips.is_empty() {
                    return entry.ips.clone();
                }
            }
        }

        // 1. Önce sistemin yerel DNS çözümleyicisini dene (hızlı yol)
        let host_owned = host.to_string();
        let ipv4_first = self.ipv4_first;
        if let Ok(Ok(sys_ips)) = tokio::task::spawn_blocking(move || {
            (host_owned.as_str(), 443).to_socket_addrs().map(|iter| {
                let mut v4 = Vec::new();
                let mut v6 = Vec::new();
                for addr in iter {
                    match addr.ip() {
                        IpAddr::V4(ip) if !ip.is_unspecified() && !ip.is_loopback() => {
                            v4.push(IpAddr::V4(ip))
                        }
                        IpAddr::V6(ip) if !ip.is_unspecified() && !ip.is_loopback() => {
                            v6.push(IpAddr::V6(ip))
                        }
                        _ => {}
                    }
                }
                if ipv4_first {
                    v4.extend(v6);
                    v4
                } else {
                    v6.extend(v4);
                    v6
                }
            })
        })
        .await
        {
            if !sys_ips.is_empty() {
                if let Ok(mut guard) = self.cache.write() {
                    guard.insert(
                        host.to_string(),
                        CacheEntry {
                            ips: sys_ips.clone(),
                            inserted_at: Instant::now(),
                        },
                    );
                }
                return sys_ips;
            }
        }

        // 2. Sistem DNS engelliyse / başarısızsa DoH (Cloudflare -> Google -> Quad9) kullan
        let mut resolved = Vec::new();
        for endpoint in DEFAULT_DOH_ENDPOINTS {
            let url = format!("{}?name={}&type=A", endpoint, host);
            if let Ok(resp) = self
                .doh_client
                .get(&url)
                .header("Accept", "application/dns-json")
                .send()
                .await
            {
                if let Ok(parsed) = resp.json::<DohResponse>().await {
                    if parsed.status.unwrap_or(1) == 0 {
                        if let Some(answers) = parsed.answer {
                            for ans in answers {
                                if ans.record_type == Some(1) || ans.record_type == Some(28) {
                                    if let Some(data) = ans.data {
                                        if let Ok(ip) = data.parse::<IpAddr>() {
                                            if !resolved.contains(&ip) {
                                                resolved.push(ip);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            if !resolved.is_empty() {
                break;
            }
        }

        if !resolved.is_empty() {
            if let Ok(mut guard) = self.cache.write() {
                guard.insert(
                    host.to_string(),
                    CacheEntry {
                        ips: resolved.clone(),
                        inserted_at: Instant::now(),
                    },
                );
            }
        }

        resolved
    }
}

impl Resolve for SuyliosDohResolver {
    fn resolve(&self, name: Name) -> Resolving {
        let resolver = self.clone();
        let host = name.as_str().to_string();
        Box::pin(async move {
            let ips = resolver.resolve_host(&host).await;
            if ips.is_empty() {
                let err: Box<dyn std::error::Error + Send + Sync> =
                    format!("DNS resolution failed for {}", host).into();
                return Err(err);
            }
            let addrs: Vec<SocketAddr> = ips.into_iter().map(|ip| SocketAddr::new(ip, 0)).collect();
            let boxed: Addrs = Box::new(addrs.into_iter());
            Ok(boxed)
        })
    }
}
