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

    let mut child = command.spawn().map_err(|e| e.to_string())?;

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
        .invoke_handler(tauri::generate_handler![start_run, open_pdf])
        .run(tauri::generate_context!())
        .expect("启动失败");
}
