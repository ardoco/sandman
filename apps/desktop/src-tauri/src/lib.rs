mod api;
mod classify;
mod env;
mod ingest;
mod keys;
mod stack;
mod store;

use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::{Arc, Mutex},
};
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

pub struct AppState {
    pub dir: PathBuf,
    pub tokens: Mutex<HashMap<String, String>>,
    pub starting: Mutex<HashSet<String>>,
    pub ingests: Mutex<HashMap<String, Arc<tokio::sync::Notify>>>,
}

#[tauri::command]
fn list_projects(state: State<AppState>) -> Result<Vec<store::Project>, String> {
    store::load_projects(&state.dir)
}

#[tauri::command]
fn save_project(state: State<AppState>, mut project: store::Project) -> Result<Option<String>, String> {
    let warning = store::validate(&mut project)?;
    project.last_used = store::now();
    store::upsert(&state.dir, project)?;
    Ok(warning)
}

#[tauri::command]
fn delete_project(state: State<AppState>, project_id: String) -> Result<(), String> {
    store::remove(&state.dir, &project_id)?;
    state.tokens.lock().unwrap().remove(&project_id);
    store::secret_delete(&format!("token:{project_id}"))
}

#[tauri::command]
fn get_defaults(state: State<AppState>) -> Result<env::SettingsView, String> {
    env::default_view(&store::load_defaults(&state.dir))
}

#[tauri::command]
fn save_defaults(state: State<AppState>, settings: env::Settings) -> Result<(), String> {
    env::apply("", "", &settings)?;
    for (field, _) in env::SECRET_FIELDS {
        if let Some(v) = settings.secret(field) {
            store::secret_set(&format!("default:{field}"), v)?;
        }
    }
    let mut d = store::load_defaults(&state.dir);
    if let Some(u) = settings.selfhosted_llm_url.filter(|v| !v.is_empty()) {
        d.selfhosted_llm_url = u;
    }
    if let Some(m) = settings.corenlp_mode.filter(|v| !v.is_empty()) {
        d.corenlp_mode = m;
    }
    store::save_defaults(&state.dir, &d)
}

#[tauri::command]
fn read_local_settings(checkout: String) -> Result<env::SettingsView, String> {
    let (gateway, dotenv, example) = env::paths(&checkout)?;
    let dotenv_text = if dotenv.is_file() { env::read_text(&dotenv) } else { env::read_text(&example) };
    Ok(env::view(&env::read_text(&gateway), &dotenv_text))
}

#[tauri::command]
fn write_local_settings(
    checkout: String,
    mut settings: env::Settings,
    from_defaults: bool,
) -> Result<(), String> {
    let (gateway, dotenv, example) = env::paths(&checkout)?;
    if !gateway.is_file() {
        return Err("Secrets are missing. Generate secrets first.".into());
    }
    if from_defaults {
        for (field, _) in env::SECRET_FIELDS {
            if settings.secret(field).is_none() {
                let v = store::secret_get(&format!("default:{field}"))?;
                match field {
                    "openaiApiKey" => settings.openai_api_key = v,
                    "openrouterApiKey" => settings.openrouter_api_key = v,
                    _ => settings.selfhosted_llm_key = v,
                }
            }
        }
    }
    let dotenv_text = if dotenv.is_file() { env::read_text(&dotenv) } else { env::read_text(&example) };
    let (g, d) = env::apply(&env::read_text(&gateway), &dotenv_text, &settings)?;
    std::fs::write(&gateway, g).map_err(|e| e.to_string())?;
    std::fs::write(&dotenv, d).map_err(|e| e.to_string())
}

#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    app.dialog().file().blocking_pick_folder().and_then(|p| p.into_path().ok()).map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
async fn pick_files(app: AppHandle) -> Vec<String> {
    app.dialog()
        .file()
        .blocking_pick_files()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned())
        .collect()
}

#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    let (url, _) = store::valid_url(&url)?;
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn set_title(window: tauri::WebviewWindow, title: String) -> Result<(), String> {
    window.set_title(&title).map_err(|e| e.to_string())
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            app.manage(AppState {
                dir: app.path().app_config_dir()?,
                tokens: Mutex::new(HashMap::new()),
                starting: Mutex::new(HashSet::new()),
                ingests: Mutex::new(HashMap::new()),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.emit("close-requested", ());
            }
        })
        .invoke_handler(tauri::generate_handler![
            list_projects,
            save_project,
            delete_project,
            api::set_token,
            api::check_token,
            api::connect_system,
            api::admin_password,
            stack::missing_tools,
            ingest::ingest_cancel,
            set_title,
            api::has_token,
            get_defaults,
            save_defaults,
            read_local_settings,
            write_local_settings,
            stack::secrets_state,
            stack::generate_secrets,
            keys::list_keys,
            keys::save_key,
            keys::get_key_value,
            keys::rename_key,
            keys::delete_key,
            stack::stack_state,
            stack::stack_up,
            stack::stack_down,
            api::api,
            pick_folder,
            pick_files,
            ingest::ingest,
            classify::classify_scan,
            classify::classify_rules,
            open_external,
            quit_app,
        ])
        .run(tauri::generate_context!())
        .expect("error while running SANDMAN desktop");
}
