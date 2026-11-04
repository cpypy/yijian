<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { onMount, onDestroy } from "svelte";

  let arxivId = "1706.03762";
  let running = false;
  let progress = 0;
  let stage = "idle";
  let logs: string[] = [];
  let pdf = "";
  let startedAt = 0;
  let elapsed = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

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

  onMount(() => {
    const un1 = listen<Record<string, unknown>>("engine-event", (e) => {
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
        pdf = p.pdf;
        pushLog("✓ 完成：" + p.pdf);
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
</script>

<header>
  <div class="brand"><span class="logo">译</span> 译见</div>
  <span class="hint">arXiv 论文 → 中英对照 PDF</span>
</header>

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

  <div class="status">
    <span class="stage">{STAGE_LABEL[stage] ?? stage}</span>
    <div class="bar"><i style={`width:${progress}%`} /></div>
    <span class="pct">{Math.round(progress)}%</span>
    {#if running}
      <span class="elapsed">{String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(elapsed % 60).padStart(2, "0")}</span>
    {/if}
    {#if pdf}
      <button class="ghost" on:click={openPdf}>打开 PDF</button>
    {/if}
  </div>

  <pre class="log" id="logbox">{logs.join("\n") || "等待任务…"}</pre>
</main>
