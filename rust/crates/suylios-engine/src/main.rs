use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use anyhow::Result;
use serde_json::{json, Value};

use suylios_core::extractors::NativeExtractorSuite;
use suylios_core::registry::SiteRegistry;
use suylios_core::transfer_engine::{TransferControl, TransferEngine, TransferRequest};
use suylios_core::updater::{parse_semver, verify_ed25519_signature, SmartUpdater};
use suylios_core::APP_VERSION;

fn emit_json(val: &Value) {
    if let Ok(line) = serde_json::to_string(val) {
        let mut out = io::stdout().lock();
        let _ = writeln!(out, "{}", line);
        let _ = out.flush();
    }
}

fn resolve_data_dir(payload: &Value) -> PathBuf {
    if let Some(s) = payload.get("data_dir").and_then(|v| v.as_str()) {
        if !s.is_empty() {
            return PathBuf::from(s);
        }
    }
    #[cfg(target_os = "windows")]
    {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return PathBuf::from(appdata).join("Suylios Downloader");
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(xdg) = std::env::var("XDG_DATA_HOME") {
            return PathBuf::from(xdg).join("Suylios Downloader");
        }
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(".local/share/Suylios Downloader");
        }
    }
    std::env::temp_dir().join("Suylios Downloader")
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(|s| s.as_str()).unwrap_or("--version");

    match cmd {
        "--version" | "version" => {
            emit_json(&json!({
                "ok": true,
                "engine": "suylios-rust-hybrid-core",
                "version": env!("CARGO_PKG_VERSION"),
                "app_version": APP_VERSION
            }));
        }

        // 1. Tek-satir JSON argümanla Yüksek Hızlı Rust HTTP Range / pwrite Transfer Motoru
        // stdin üzerinden gerçek-zamanlı {"cmd": "pause" | "resume" | "cancel"} sinyallerini dinler!
        "download" => {
            let payload_str = args.get(2).cloned().unwrap_or_else(|| {
                let mut buf = String::new();
                let _ = io::stdin().read_line(&mut buf);
                buf
            });
            let req: TransferRequest = serde_json::from_str(&payload_str)?;
            let ctrl = Arc::new(TransferControl::default());

            // Stdin kontrol dinleyicisi (pause / resume / cancel)
            let ctrl_stdin = ctrl.clone();
            std::thread::spawn(move || {
                let stdin = io::stdin();
                for line in stdin.lock().lines().map_while(Result::ok) {
                    let t = line.trim();
                    if t.contains("\"cancel\"") || t == "cancel" {
                        ctrl_stdin.cancel.store(true, Ordering::Relaxed);
                        break;
                    } else if t.contains("\"pause\"") || t == "pause" {
                        ctrl_stdin.paused.store(true, Ordering::Relaxed);
                    } else if t.contains("\"resume\"") || t == "resume" {
                        ctrl_stdin.paused.store(false, Ordering::Relaxed);
                    }
                }
            });

            let engine = TransferEngine::new();
            match engine
                .download(req, ctrl, |prog| {
                    emit_json(&prog);
                })
                .await
            {
                Ok(final_path) => {
                    emit_json(&json!({
                        "status": "finished",
                        "ok": true,
                        "filename": final_path.to_string_lossy()
                    }));
                }
                Err(e) => {
                    emit_json(&json!({
                        "status": "error",
                        "ok": false,
                        "error": e.to_string()
                    }));
                    std::process::exit(1);
                }
            }
        }

        // 2. SiteRegistry URL çözümleme ve birleşik katalog sorgusu
        "resolve-site" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let app_root = PathBuf::from(
                payload
                    .get("app_root")
                    .and_then(|v| v.as_str())
                    .unwrap_or("."),
            );
            let data_dir = resolve_data_dir(&payload);
            let url = payload.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let custom_sites = payload
                .get("custom_sites")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();

            let registry = SiteRegistry::load(&app_root, &data_dir);
            let res = registry.resolve_url(url, &custom_sites);
            emit_json(&serde_json::to_value(res)?);
        }

        // 3. Kanal 1 + Kanal 2 + Kanal 3 Akıllı Güncelleyici Komutları
        "sync-hotfix" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let app_root = PathBuf::from(
                payload
                    .get("app_root")
                    .and_then(|v| v.as_str())
                    .unwrap_or("."),
            );
            let data_dir = resolve_data_dir(&payload);
            let custom_url = payload.get("manifest_url").and_then(|v| v.as_str());

            let updater = SmartUpdater::new(&app_root, &data_dir);
            match updater.sync_hotfix_feed(custom_url).await {
                Ok(res) => emit_json(&res),
                Err(e) => {
                    emit_json(&json!({ "ok": false, "error": e.to_string() }));
                    std::process::exit(1);
                }
            }
        }

        "check-all-updates" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let app_root = PathBuf::from(
                payload
                    .get("app_root")
                    .and_then(|v| v.as_str())
                    .unwrap_or("."),
            );
            let data_dir = resolve_data_dir(&payload);
            let current_app_ver = payload
                .get("app_version")
                .and_then(|v| v.as_str())
                .unwrap_or(APP_VERSION);
            let local_wheels: HashMap<String, String> = payload
                .get("local_wheels")
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .unwrap_or_default();

            let updater = SmartUpdater::new(&app_root, &data_dir);
            let hotfix_res = updater
                .sync_hotfix_feed(payload.get("manifest_url").and_then(|v| v.as_str()))
                .await
                .unwrap_or_else(|e| json!({ "ok": false, "error": e.to_string() }));
            let registry = SiteRegistry::load(&app_root, &data_dir);
            let app_res = updater.check_app_release(current_app_ver).await;
            let engines_res = updater
                .check_pypi_engines(&local_wheels, &registry.manifest)
                .await;
            let state = updater.load_state();

            emit_json(&json!({
                "ok": true,
                "channel1_app": app_res,
                "channel2_hotfix": hotfix_res,
                "channel3_engines": engines_res,
                "runtime_state": state
            }));
        }

        "install-wheel" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let app_root = PathBuf::from(
                payload
                    .get("app_root")
                    .and_then(|v| v.as_str())
                    .unwrap_or("."),
            );
            let data_dir = resolve_data_dir(&payload);
            let pkg = payload.get("package").and_then(|v| v.as_str()).unwrap_or("");
            let ver = payload.get("version").and_then(|v| v.as_str()).unwrap_or("");
            let w_url = payload
                .get("wheel_url")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let sha = payload.get("sha256").and_then(|v| v.as_str()).unwrap_or("");
            let py_bin = payload
                .get("python_bin")
                .and_then(|v| v.as_str())
                .unwrap_or("python3");

            let updater = SmartUpdater::new(&app_root, &data_dir);
            match updater
                .install_pypi_wheel(pkg, ver, w_url, sha, py_bin)
                .await
            {
                Ok(res) => emit_json(&res),
                Err(e) => {
                    emit_json(&json!({ "ok": false, "error": e.to_string() }));
                    std::process::exit(1);
                }
            }
        }

        "rollback-wheels" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let app_root = PathBuf::from(
                payload
                    .get("app_root")
                    .and_then(|v| v.as_str())
                    .unwrap_or("."),
            );
            let data_dir = resolve_data_dir(&payload);
            let updater = SmartUpdater::new(&app_root, &data_dir);
            match updater.rollback_python_overlay() {
                Ok(res) => emit_json(&res),
                Err(e) => {
                    emit_json(&json!({ "ok": false, "error": e.to_string() }));
                    std::process::exit(1);
                }
            }
        }

        // 4. Native Rust Extractor Çıkarımı
        "extract-direct" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let app_root = PathBuf::from(
                payload
                    .get("app_root")
                    .and_then(|v| v.as_str())
                    .unwrap_or("."),
            );
            let data_dir = resolve_data_dir(&payload);
            let url = payload.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let token = payload.get("token").and_then(|v| v.as_str());

            let registry = SiteRegistry::load(&app_root, &data_dir);
            let suite = NativeExtractorSuite::new();
            match suite.extract_direct(url, &registry, token).await {
                Ok(gal) => emit_json(&json!({ "ok": true, "data": gal })),
                Err(e) => {
                    emit_json(&json!({ "ok": false, "error": e.to_string() }));
                    std::process::exit(1);
                }
            }
        }

        "verify-signature" => {
            let payload: Value = serde_json::from_str(args.get(2).map(|s| s.as_str()).unwrap_or("{}"))?;
            let msg = payload
                .get("message")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let sig = payload
                .get("signature")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let pubkey = payload
                .get("public_key")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let _ = parse_semver("1.0.0");
            match verify_ed25519_signature(msg.as_bytes(), sig, pubkey) {
                Ok(()) => emit_json(&json!({ "ok": true, "valid": true })),
                Err(e) => emit_json(&json!({ "ok": false, "valid": false, "error": e.to_string() })),
            }
        }

        _ => {
            emit_json(&json!({
                "ok": false,
                "error": format!("Unknown command: {}", cmd)
            }));
            std::process::exit(1);
        }
    }

    Ok(())
}
