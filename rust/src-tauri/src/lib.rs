use std::collections::HashMap;
use std::path::PathBuf;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

use suylios_core::extractors::NativeExtractorSuite;
use suylios_core::registry::SiteRegistry;
use suylios_core::updater::SmartUpdater;
use suylios_core::APP_VERSION;

fn resolve_paths() -> (PathBuf, PathBuf) {
    let app_root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let data_dir = if app_root.join("portable.flag").is_file() {
        app_root.join("data")
    } else if cfg!(target_os = "windows") {
        std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| app_root.join("data"))
            .join("Suylios Downloader")
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(|h| PathBuf::from(h).join(".local").join("share"))
            })
            .unwrap_or_else(|| app_root.join("data"))
            .join("Suylios Downloader")
    };
    (app_root, data_dir)
}

#[tauri::command]
async fn get_app_info() -> Value {
    json!({
        "name": "Suylios Downloader",
        "version": APP_VERSION,
        "engine": "rust-hybrid-v2",
        "github_url": "https://github.com/sayrias/suylios-downloader"
    })
}

#[tauri::command]
async fn get_supported_sites() -> Vec<Value> {
    let (app_root, data_dir) = resolve_paths();
    let registry = SiteRegistry::load(&app_root, &data_dir);
    registry.merged_supported_sites()
}

#[tauri::command]
async fn check_for_updates() -> Value {
    let (app_root, data_dir) = resolve_paths();
    let updater = SmartUpdater::new(&app_root, &data_dir);
    updater.check_app_release(APP_VERSION).await
}

#[tauri::command]
async fn check_hotfix_updates() -> Value {
    let (app_root, data_dir) = resolve_paths();
    let updater = SmartUpdater::new(&app_root, &data_dir);
    match updater.sync_hotfix_feed(None).await {
        Ok(res) => res,
        Err(e) => json!({ "ok": false, "error": e.to_string() }),
    }
}

#[tauri::command]
async fn check_engine_updates(local_wheels: HashMap<String, String>) -> Value {
    let (app_root, data_dir) = resolve_paths();
    let registry = SiteRegistry::load(&app_root, &data_dir);
    let updater = SmartUpdater::new(&app_root, &data_dir);
    let engines = updater
        .check_pypi_engines(&local_wheels, &registry.manifest)
        .await;
    json!({ "ok": true, "engines": engines })
}

#[tauri::command]
async fn extract_direct_rust(url: String, token: Option<String>) -> Value {
    let (app_root, data_dir) = resolve_paths();
    let registry = SiteRegistry::load(&app_root, &data_dir);
    let suite = NativeExtractorSuite::new();
    match suite.extract_direct(&url, &registry, token.as_deref()).await {
        Ok(gal) => json!({ "ok": true, "data": gal }),
        Err(e) => json!({ "ok": false, "error": e.to_string() }),
    }
}

#[tauri::command]
async fn minimize_window(app: AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.minimize();
    }
}

#[tauri::command]
async fn maximize_window(app: AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        if w.is_maximized().unwrap_or(false) {
            let _ = w.unmaximize();
        } else {
            let _ = w.maximize();
        }
    }
}

#[tauri::command]
async fn close_window(app: AppHandle) -> Value {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.close();
    }
    let _ = app.emit("window-closing", json!({}));
    json!({ "ok": true })
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![
            get_app_info,
            get_supported_sites,
            check_for_updates,
            check_hotfix_updates,
            check_engine_updates,
            extract_direct_rust,
            minimize_window,
            maximize_window,
            close_window,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Suylios Tauri v2 application");
}
