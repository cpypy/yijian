// 发布版用窗口子系统，否则双击 exe 会先弹一个控制台黑框
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};

use tauri::{AppHandle, Emitter, Manager};

/// Windows 上隐藏控制台窗口：GUI 程序拉起控制台子进程时，
/// 系统会默认弹一个黑框，必须显式带 CREATE_NO_WINDOW。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn engine_exe() -> &'static str {
    if cfg!(windows) {
        "yijian-engine.exe"
    } else {
        "yijian-engine"
    }
}

fn engine_path(app: &AppHandle) -> PathBuf {
    let name = engine_exe();
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(dir) = app.path().resource_dir() {
        candidates.push(dir.join("sidecar").join(name));
        candidates.push(dir.join("resources").join("sidecar").join(name));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("sidecar").join(name));
            candidates.push(dir.join("resources").join("sidecar").join(name));
            candidates.push(dir.join("_up_").join("sidecar").join(name));
        }
    }
    candidates.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("dist-sidecar")
            .join("yijian-engine")
            .join(name),
    );

    for path in &candidates {
        if path.exists() {
            return path.clone();
        }
    }
    candidates.remove(0)
}

#[derive(Default)]
struct RunState {
    pid: std::sync::Mutex<Option<u32>>,
}

#[tauri::command]
fn cancel_run(app: AppHandle) -> Result<(), String> {
    let state = app.state::<RunState>();
    let pid = state.pid.lock().map_err(|e| e.to_string())?.take();
    if let Some(pid) = pid {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .spawn();
        }
        #[cfg(not(windows))]
        {
            let _ = Command::new("kill").args(["-TERM", &pid.to_string()]).spawn();
        }
        let _ = app.emit(
            "engine-event",
            serde_json::json!({ "event": "error", "message": "已取消" }),
        );
        let _ = app.emit("engine-exit", ());
    }
    Ok(())
}

fn spawn_engine(app: &AppHandle, arxiv_id: &str) -> Result<(), String> {
    let exe = engine_path(app);
    if !exe.exists() {
        return Err(format!("引擎不存在：{}", exe.display()));
    }
    let mut command = Command::new(&exe);
    command
        .arg("--run")
        .arg(arxiv_id)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    // 把设置里的模型端点/密钥传给引擎（BYOK）
    if let Ok(settings) = load_settings() {
        for (key, env_key) in [
            ("base_url", "TEXLATE_BASE_URL"),
            ("api_key", "TEXLATE_API_KEY"),
            ("model", "TEXLATE_MODEL"),
        ] {
            if let Some(value) = settings.get(key).and_then(|v| v.as_str()) {
                let value = value.trim();
                if !value.is_empty() {
                    command.env(env_key, value);
                }
            }
        }
    }

    let mut child = command.spawn().map_err(|e| e.to_string())?;

    if let Some(state) = app.try_state::<RunState>() {
        if let Ok(mut slot) = state.pid.lock() {
            *slot = Some(child.id());
        }
    }

    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            match serde_json::from_str::<serde_json::Value>(&line) {
                Ok(value) => {
                    let _ = app.emit("engine-event", value);
                }
                Err(_) => {
                    let _ = app.emit("engine-log", line);
                }
            }
        }
    }
    let _ = child.wait();
    if let Some(state) = app.try_state::<RunState>() {
        if let Ok(mut slot) = state.pid.lock() {
            *slot = None;
        }
    }
    Ok(())
}

#[tauri::command]
fn start_run(app: AppHandle, arxiv_id: String) -> Result<(), String> {
    std::thread::spawn(move || {
        if let Err(message) = spawn_engine(&app, &arxiv_id) {
            let _ = app.emit(
                "engine-event",
                serde_json::json!({ "event": "error", "message": message }),
            );
        }
        let _ = app.emit("engine-exit", ());
    });
    Ok(())
}

fn app_dir() -> PathBuf {
    let base = std::env::var("LOCALAPPDATA").unwrap_or_default();
    if base.is_empty() {
        PathBuf::from(".")
    } else {
        PathBuf::from(base).join("Yijian")
    }
}

fn settings_path() -> PathBuf {
    app_dir().join("settings.json")
}

#[tauri::command]
fn load_glossary() -> Result<String, String> {
    let path = app_dir().join("glossary.json");
    if !path.exists() {
        return Ok("[]".into());
    }
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_glossary(text: String) -> Result<(), String> {
    let path = app_dir().join("glossary.json");
    std::fs::create_dir_all(app_dir()).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_library() -> Result<String, String> {
    let path = app_dir().join("library.json");
    if !path.exists() {
        return Ok("[]".into());
    }
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_library(text: String) -> Result<(), String> {
    std::fs::create_dir_all(app_dir()).map_err(|e| e.to_string())?;
    std::fs::write(app_dir().join("library.json"), text).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_notes(doc: String) -> Result<String, String> {
    let path = app_dir().join("notes").join(format!("{doc}.json"));
    if !path.exists() {
        return Ok("{}".into());
    }
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_notes(doc: String, text: String) -> Result<(), String> {
    let dir = app_dir().join("notes");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    std::fs::write(dir.join(format!("{doc}.json")), text).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_settings() -> Result<serde_json::Value, String> {
    let path = settings_path();
    if !path.exists() {
        return Ok(serde_json::json!({}));
    }
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_settings(value: serde_json::Value) -> Result<(), String> {
    let path = settings_path();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

#[tauri::command]
fn discover(app: AppHandle) -> Result<String, String> {
    let exe = engine_path(&app);
    if !exe.exists() {
        return Err(format!("引擎不存在：{}", exe.display()));
    }

    let mut command = Command::new(&exe);
    command.arg("--discover");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let output = command.output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).to_string());
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

#[tauri::command]
fn read_segments(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_pdf(path: String) -> Result<(), String> {
    if !std::path::Path::new(&path).exists() {
        return Err("PDF 不存在".into());
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        Command::new("cmd")
            .args(["/C", "start", ""])
            .arg(&path)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage(RunState::default())
        .invoke_handler(tauri::generate_handler![
            start_run,
            cancel_run,
            open_pdf,
            read_segments,
            load_settings,
            save_settings,
            load_glossary,
            save_glossary,
            load_notes,
            save_notes,
            load_library,
            save_library,
            discover
        ])
        .run(tauri::generate_context!())
        .expect("启动失败");
}
