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
    queue: std::sync::Mutex<std::collections::VecDeque<(String, String)>>,
    busy: std::sync::atomic::AtomicBool,
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

fn spawn_engine(app: &AppHandle, arxiv_id: &str, mode: &str) -> Result<(), String> {
    let exe = engine_path(app);
    if !exe.exists() {
        return Err(format!("引擎不存在：{}", exe.display()));
    }
    let mut command = Command::new(&exe);
    command
        .arg("--run")
        .arg(arxiv_id)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // 引擎侧 print() 默认走系统区域编码（中文 Windows 是 GBK），
        // 遇到 ö 这类字符会直接崩，必须强制 UTF-8
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");

    command.arg("--mode").arg(mode);

    // 单批字符数（推理模型的 thinking 也吃 max_tokens）
    if let Ok(text) = std::fs::read_to_string(settings_path()) {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(chars) = value.get("batch_chars").and_then(|v| v.as_u64()) {
                command.env("YIJIAN_BATCH_CHARS", chars.to_string());
            }
        }
    }

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

    // stderr 必须有人读：否则崩溃信息全堆在管道里，前端只看到“什么都没发生”
    if let Some(stderr) = child.stderr.take() {
        let handle = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let _ = handle.emit("engine-log", format!("[stderr] {line}"));
            }
        });
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
fn start_run(app: AppHandle, arxiv_id: String, mode: Option<String>) -> Result<(), String> {
    let id = arxiv_id.trim().to_string();
    if id.is_empty() {
        return Err("arXiv ID 为空".into());
    }
    let mode = mode.unwrap_or_else(|| "auto".to_string());

    {
        let state = app.state::<RunState>();
        let mut queue = state.queue.lock().map_err(|e| e.to_string())?;
        queue.push_back((id, mode));
        let _ = app.emit("queue", queue.len());
    }

    spawn_worker(app.clone());
    Ok(())
}

fn spawn_worker(app: AppHandle) {
    use std::sync::atomic::Ordering;

    {
        let state = app.state::<RunState>();
        if state.busy.swap(true, Ordering::SeqCst) {
            return; // 已有 worker 在跑，它会把队列里的活干完
        }
    }

    std::thread::spawn(move || {
        loop {
            let next = {
                let state = app.state::<RunState>();
                let mut queue = match state.queue.lock() {
                    Ok(q) => q,
                    Err(_) => break,
                };
                let item = queue.pop_front();
                let _ = app.emit("queue", queue.len());
                item
            };

            let Some((id, mode)) = next else { break };

            let _ = app.emit(
                "job-start",
                serde_json::json!({ "arxiv_id": id, "mode": mode }),
            );
            if let Err(message) = spawn_engine(&app, &id, &mode) {
                let _ = app.emit(
                    "engine-event",
                    serde_json::json!({ "event": "error", "message": message }),
                );
            }
        }

        let state = app.state::<RunState>();
        state.busy.store(false, Ordering::SeqCst);
        let _ = app.emit("engine-exit", ());
    });
}


#[tauri::command]
fn queue_list(app: AppHandle) -> Vec<String> {
    let state = app.state::<RunState>();
    let q = match state.queue.lock() {
        Ok(q) => q,
        Err(_) => return Vec::new(),
    };
    q.iter().map(|(id, _)| id.clone()).collect()
}

#[tauri::command]
fn queue_remove(app: AppHandle, index: usize) -> Result<(), String> {
    let state = app.state::<RunState>();
    let n = {
        let mut q = match state.queue.lock() {
            Ok(q) => q,
            Err(_) => return Err("队列被占用".into()),
        };
        if index < q.len() {
            q.remove(index);
        }
        q.len()
    };
    let _ = app.emit("queue", n);
    Ok(())
}

#[tauri::command]
fn queue_move(app: AppHandle, index: usize, delta: i32) -> Result<(), String> {
    let state = app.state::<RunState>();
    {
        let mut q = match state.queue.lock() {
            Ok(q) => q,
            Err(_) => return Err("队列被占用".into()),
        };
        let len = q.len();
        if index < len {
            let target = (index as i32 + delta).clamp(0, len as i32 - 1) as usize;
            if target != index {
                if let Some(item) = q.remove(index) {
                    q.insert(target, item);
                }
            }
        }
    }
    Ok(())
}

#[tauri::command]
fn clear_queue(app: AppHandle) -> Result<usize, String> {
    let state = app.state::<RunState>();
    let mut queue = state.queue.lock().map_err(|e| e.to_string())?;
    let n = queue.len();
    queue.clear();
    let _ = app.emit("queue", 0);
    Ok(n)
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
fn load_collections() -> Result<String, String> {
    let path = app_dir().join("collections.json");
    if !path.exists() {
        return Ok("[]".into());
    }
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_collections(text: String) -> Result<(), String> {
    std::fs::create_dir_all(app_dir()).map_err(|e| e.to_string())?;
    std::fs::write(app_dir().join("collections.json"), text).map_err(|e| e.to_string())
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

#[derive(serde::Serialize)]
struct TextHit {
    arxiv_id: String,
    seq: u32,
    kind: String,
    snippet: String,
}

#[tauri::command]
fn search_text(docs: Vec<(String, String)>, query: String) -> Result<Vec<TextHit>, String> {
    let needle = query.trim().to_lowercase();
    if needle.is_empty() {
        return Ok(Vec::new());
    }

    let mut hits: Vec<TextHit> = Vec::new();
    for (arxiv_id, path) in docs.iter().take(500) {
        let text = match std::fs::read_to_string(path) {
            Ok(t) => t,
            Err(_) => continue,
        };
        let data: serde_json::Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let Some(chunks) = data.get("chunks").and_then(|c| c.as_array()) else {
            continue;
        };

        for chunk in chunks {
            let en = chunk.get("en").and_then(|v| v.as_str()).unwrap_or("");
            let zh = chunk.get("zh").and_then(|v| v.as_str()).unwrap_or("");
            let hay = format!("{en} {zh}").to_lowercase();
            let Some(pos) = hay.find(&needle) else { continue };

            let mut start = pos.saturating_sub(40);
            while start > 0 && !hay.is_char_boundary(start) {
                start -= 1;
            }
            let mut end = (pos + needle.len() + 60).min(hay.len());
            while end < hay.len() && !hay.is_char_boundary(end) {
                end += 1;
            }

            hits.push(TextHit {
                arxiv_id: arxiv_id.clone(),
                seq: chunk.get("seq").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                kind: chunk
                    .get("kind")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                snippet: hay[start..end].replace('\n', " "),
            });
            if hits.len() >= 200 {
                return Ok(hits);
            }
        }
    }
    Ok(hits)
}




#[tauri::command]
fn rebuild_alignment(app: AppHandle, segments: String, pdf: String) -> Result<String, String> {
    let exe = engine_path(&app);
    if !exe.exists() {
        return Err(format!("引擎不存在：{}", exe.display()));
    }
    let mut command = Command::new(&exe);
    command
        .arg("--align")
        .arg(&segments)
        .arg(&pdf)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");

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
fn delete_doc_files(paths: Vec<String>) -> Result<(), String> {
    for raw in paths {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            continue;
        }
        let path = std::path::PathBuf::from(trimmed);
        let parent = path.parent().map(|p| p.to_path_buf());
        if path.is_file() {
            let _ = std::fs::remove_file(&path);
        }
        if let Some(dir) = parent {
            let name = dir
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            let under_jobs = dir
                .parent()
                .and_then(|p| p.file_name())
                .map(|n| n == "jobs")
                .unwrap_or(false);
            // 任务目录（t_xxx）或本次作业目录（jobs/<id>）整体删掉
            if name.starts_with("t_") || under_jobs {
                let _ = std::fs::remove_dir_all(&dir);
            }
        }
    }
    Ok(())
}

#[tauri::command]
fn translate_texts(app: AppHandle, texts: Vec<String>) -> Result<String, String> {
    let exe = engine_path(&app);
    if !exe.exists() {
        return Err(format!("引擎不存在：{}", exe.display()));
    }
    std::fs::create_dir_all(app_dir()).map_err(|e| e.to_string())?;
    let req = app_dir().join("translate-req.json");
    let payload = serde_json::json!({ "texts": texts });
    std::fs::write(&req, payload.to_string()).map_err(|e| e.to_string())?;

    let mut command = Command::new(&exe);
    command
        .arg("--translate")
        .arg(&req)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }

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
            discover,
            clear_queue,
            queue_list,
            queue_remove,
            queue_move,
            load_collections,
            save_collections,
            search_text,
            translate_texts,
            delete_doc_files,
            rebuild_alignment
        ])
        .run(tauri::generate_context!())
        .expect("启动失败");
}
