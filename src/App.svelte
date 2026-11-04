<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { onMount, onDestroy } from "svelte";

  type Segment = { seq: number; kind: string; status: string; en: string; zh: string };
  type Settings = { base_url: string; model: string; api_key: string };

  let arxivId = "1706.03762";
  let running = false;
  let progress = 0;
  let stage = "idle";
  let logs: string[] = [];
  let pdf = "";
  let startedAt = 0;
  let elapsed = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

  let view: "work" | "read" = "work";
  let segments: Segment[] = [];
  let mode: "both" | "en" | "zh" = "both";
  let selected = -1;

  let showSettings = false;
  let savedTip = "";
  let settings: Settings = { base_url: "", model: "", api_key: "" };

  const STAGE_LABEL: Record<string, string> = {
    idle: "就绪",
    fetch: "取源",
    parse: "分段",
    translate: "翻译",
    compile: "编译",
    judge: "判定",
    done: "完成",
  };

  function pushLog(line: string) {
    logs = [...logs.slice(-400), line];
    const el = document.getElementById("logbox");
    if (el) el.scrollTop = el.scrollHeight;
  }

  async function loadSettings() {
    try {
      const s = await invoke<Record<string, string>>("load_settings");
      settings = {
        base_url: s.base_url ?? "",
        model: s.model ?? "",
        api_key: s.api_key ?? "",
      };
    } catch {
      /* 忽略：首次运行没有设置文件 */
    }
  }

  async function saveSettings() {
    try {
      await invoke("save_settings", { value: settings });
      savedTip = "已保存";
      setTimeout(() => (savedTip = ""), 1500);
      showSettings = false;
    } catch (e) {
      savedTip = "保存失败：" + String(e);
    }
  }

  onMount(() => {
    loadSettings();

    const un1 = listen<Record<string, unknown>>("engine-event", async (e) => {
      const p = e.payload as any;
      if (p.event === "stage") {
        stage = p.stage;
        progress = p.progress;
      } else if (p.event === "log") {
        pushLog(p.line);
      } else if (p.event === "done") {
        running = false;
        progress = 100;
        stage = "done";
        pdf = p.pdf || "";
        pushLog(`✓ 完成：${p.chunks ?? 0} 段`);
        if (p.segments) {
          try {
            const raw = await invoke<string>("read_segments", { path: p.segments });
            const data = JSON.parse(raw);
            segments = (data.chunks ?? []).map((c: any) => ({
              seq: c.seq,
              kind: c.kind || "para",
              status: c.status || "",
              en: c.en || "",
              zh: c.zh || "",
            }));
            if (segments.length) {
              view = "read";
              selected = -1;
            }
          } catch (err) {
            pushLog("读取段落失败：" + String(err));
          }
        }
      } else if (p.event === "error") {
        running = false;
        pushLog("ERROR: " + p.message);
      }
    });
    const un2 = listen("engine-exit", () => (running = false));
    return () => {
      un1.then((f) => f());
      un2.then((f) => f());
    };
  });

  onDestroy(() => {
    if (timer) clearInterval(timer);
  });

  async function start() {
    if (running || !arxivId.trim()) return;
    running = true;
    progress = 0;
    stage = "fetch";
    logs = [];
    pdf = "";
    segments = [];
    view = "work";
    startedAt = Date.now();
    elapsed = 0;
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      elapsed = Math.floor((Date.now() - startedAt) / 1000);
    }, 1000);
    try {
      await invoke("start_run", { arxivId: arxivId.trim() });
    } catch (e) {
      running = false;
      pushLog(String(e));
    }
  }

  async function cancel() {
    try {
      await invoke("cancel_run");
    } catch (e) {
      pushLog(String(e));
    }
  }

  async function openPdf() {
    if (pdf) await invoke("open_pdf", { path: pdf });
  }

  function toggleMode(next: "both" | "en" | "zh") {
    mode = mode === next ? "both" : next;
  }
</script>

<header>
  <div class="brand"><span class="logo">译</span> 译见</div>
  <span class="hint">arXiv 论文 → 中英对照阅读</span>
  <span class="grow"></span>
  {#if savedTip}<span class="saved">{savedTip}</span>{/if}
  <button class="icon" title="模型设置" on:click={() => (showSettings = true)}>⚙</button>
</header>

{#if view === "work"}
  <main>
    <div class="row">
      <input
        bind:value={arxivId}
        placeholder="arXiv ID 或链接，例如 1706.03762"
        on:keydown={(e) => e.key === "Enter" && start()}
        disabled={running}
      />
      {#if running}
        <button class="ghost" on:click={cancel}>取消</button>
      {:else}
        <button class="primary" on:click={start}>开始</button>
      {/if}
    </div>

    {#if !settings.api_key}
      <div class="banner">
        未配置模型，译文为占位文本。点右上角 ⚙ 填 Base URL / 模型 / Key。
      </div>
    {/if}

    <div class="status">
      <span class="stage">{STAGE_LABEL[stage] ?? stage}</span>
      <div class="bar"><i style={`width:${progress}%`} /></div>
      <span class="pct">{Math.round(progress)}%</span>
      {#if running}
        <span class="elapsed">{String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(
            elapsed % 60
          ).padStart(2, "0")}</span>
      {/if}
      {#if segments.length}
        <button class="ghost" on:click={() => (view = "read")}>打开阅读器</button>
      {/if}
    </div>

    <pre class="log" id="logbox">{logs.join("\n") || "等待任务…"}</pre>
  </main>
{:else}
  <main class="reader">
    <div class="rbar">
      <button class="ghost" on:click={() => (view = "work")}>← 返回</button>
      <span class="rid">{arxivId}</span>
      <span class="rmeta">{segments.length} 段</span>
      <span class="grow"></span>
      <button class="seg-btn" class:on={mode === "en"} on:click={() => toggleMode("en")}>原文</button>
      <button class="seg-btn" class:on={mode === "both"} on:click={() => toggleMode("both")}>对照</button>
      <button class="seg-btn" class:on={mode === "zh"} on:click={() => toggleMode("zh")}>译文</button>
      {#if pdf}
        <button class="ghost" on:click={openPdf}>PDF</button>
      {/if}
    </div>

    <div class="doc" class:only-en={mode === "en"} class:only-zh={mode === "zh"}>
      {#each segments as s (s.seq)}
        <div
          class="seg"
          class:title={s.kind === "section_title"}
          class:active={selected === s.seq}
          on:click={() => (selected = selected === s.seq ? -1 : s.seq)}
          role="button"
          tabindex="0"
          on:keydown={(e) => e.key === "Enter" && (selected = s.seq)}
        >
          <div class="en">{s.en}</div>
          <div class="zh">{s.zh || "（未翻译）"}</div>
        </div>
      {/each}
    </div>
  </main>
{/if}

{#if showSettings}
  <div class="overlay" on:click={() => (showSettings = false)} role="presentation">
    <div class="panel" role="dialog" aria-label="模型设置" on:click|stopPropagation>
      <h2>模型设置</h2>
      <label>
        Base URL
        <input bind:value={settings.base_url} placeholder="https://api.deepseek.com/v1" />
      </label>
      <label>
        模型
        <input bind:value={settings.model} placeholder="deepseek-chat" />
      </label>
      <label>
        API Key
        <input type="password" bind:value={settings.api_key} placeholder="sk-..." />
      </label>
      <p class="tip">兼容 OpenAI 的端点都可用。Key 保存在本机 settings.json，不会上传。</p>
      <div class="actions">
        <button on:click={() => (showSettings = false)}>取消</button>
        <button class="primary" on:click={saveSettings}>保存</button>
      </div>
    </div>
  </div>
{/if}
