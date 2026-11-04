<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { onMount, onDestroy, tick } from "svelte";
  import * as katex from "katex";

  type Segment = {
    seq: number;
    kind: string;
    status: string;
    en: string;
    zh: string;
    ph: Record<string, string>;
  };
  type Settings = { base_url: string; model: string; api_key: string };
  type Doc = {
    id: string;
    arxivId: string;
    addedAt: number;
    pdf: string;
    segments: string;
    progress: number;
    lastSeq: number;
    collection: string;
  };

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
  let nav: "work" | "library" | "discover" = "work";
  let segments: Segment[] = [];
  let mode: "both" | "en" | "zh" = "both";
  let selected = -1;

  type FeedItem = {
    id: string;
    arxiv_id: string;
    title: string;
    authors: string;
    abstract: string;
    votes?: number;
    visits?: number;
  };
  let feed: FeedItem[] = [];
  let feedLoading = false;
  let feedError = "";
  let lastTokens = 0;
  let lastSeconds = 0;
  let queueLen = 0;

  let showSettings = false;
  let savedTip = "";
  let settings: Settings = { base_url: "", model: "", api_key: "" };
  let glossaryText = "";
  let terms: string[] = [];

  let notes: Record<number, string> = {};
  let noteOpen = -1;
  let noteTimer: ReturnType<typeof setTimeout> | null = null;

  let library: Doc[] = [];
  let query = "";
  let currentDocId = "";
  let currentSegmentsPath = "";
  let readProgress = 0;
  let docEl: HTMLDivElement | null = null;
  let scrollTimer: ReturnType<typeof setTimeout> | null = null;

  const STAGE_LABEL: Record<string, string> = {
    idle: "就绪",
    fetch: "取源",
    parse: "分段",
    translate: "翻译",
    compile: "编译",
    judge: "判定",
    done: "完成",
  };

  $: visibleDocs = library
    .filter((d) => {
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return (
        d.arxivId.toLowerCase().includes(q) ||
        (d.collection || "").toLowerCase().includes(q)
      );
    })
    .sort((a, b) => b.addedAt - a.addedAt);

  function esc(s: string): string {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function markTerms(escaped: string): string {
    let out = escaped;
    for (const term of terms) {
      const safe = esc(term);
      if (safe.length < 2) continue;
      out = out.split(safe).join(`<mark class="term">${safe}</mark>`);
    }
    return out;
  }

  function renderToken(kind: string, raw: string): string {
    if (kind === "MATH") {
      const tex = raw.replace(/^\$+|\$+$/g, "");
      try {
        return `<span class="math">${katex.renderToString(tex, {
          throwOnError: false,
          displayMode: false,
        })}</span>`;
      } catch {
        return `<code>${esc(tex)}</code>`;
      }
    }
    if (kind === "CITE" || kind === "REF") {
      const key = raw.replace(/^\\[a-zA-Z]+\{|\}$/g, "");
      return `<span class="chip ${kind.toLowerCase()}" title="${esc(raw)}">${esc(
        key.slice(0, 20)
      )}</span>`;
    }
    if (kind === "URL") {
      const url = raw.replace(/^\\url\{|\}$/g, "");
      return `<a class="url" href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a>`;
    }
    return "";
  }

  function renderSegment(text: string, ph: Record<string, string>): string {
    const re = /\[\[([A-Z]+)_\d+\]\]/g;
    let out = "";
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      out += markTerms(esc(text.slice(last, m.index)));
      out += renderToken(m[1], ph[m[0]] ?? "");
      last = m.index + m[0].length;
    }
    out += markTerms(esc(text.slice(last)));
    return out;
  }

  function parseGlossary(text: string): { en: string; zh: string }[] {
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"))
      .map((line) => {
        const idx = line.indexOf(",");
        if (idx < 0) return { en: line, zh: "" };
        return { en: line.slice(0, idx).trim(), zh: line.slice(idx + 1).trim() };
      })
      .filter((it) => it.en && it.zh);
  }

  function pushLog(line: string) {
    logs = [...logs.slice(-400), line];
    const el = document.getElementById("logbox");
    if (el) el.scrollTop = el.scrollHeight;
  }

  function persistLibrary() {
    invoke("save_library", { text: JSON.stringify(library) }).catch(() => {});
  }

  function upsertDoc(patch: Partial<Doc> & { arxivId: string }) {
    const idx = library.findIndex((d) => d.arxivId === patch.arxivId);
    if (idx >= 0) {
      library = library.map((d, i) => (i === idx ? { ...d, ...patch } : d));
    } else {
      library = [
        {
          id: patch.arxivId,
          arxivId: patch.arxivId,
          addedAt: Date.now(),
          pdf: "",
          segments: "",
          progress: 0,
          lastSeq: 0,
          collection: "",
          ...patch,
        },
        ...library,
      ];
    }
    persistLibrary();
  }

  async function loadLibrary() {
    try {
      const raw = await invoke<string>("load_library");
      const items = JSON.parse(raw || "[]");
      if (Array.isArray(items)) library = items;
    } catch {
      library = [];
    }
  }

  async function loadSettings() {
    try {
      const s = await invoke<Record<string, string>>("load_settings");
      settings = { base_url: s.base_url ?? "", model: s.model ?? "", api_key: s.api_key ?? "" };
    } catch {
      /* 首次运行 */
    }
    try {
      const raw = await invoke<string>("load_glossary");
      const items = JSON.parse(raw || "[]");
      if (Array.isArray(items)) {
        glossaryText = items.map((it: any) => `${it.en},${it.zh}`).join("\n");
        terms = items.map((it: any) => it.en).filter(Boolean);
      }
    } catch {
      /* 忽略 */
    }
  }

  async function saveSettings() {
    try {
      await invoke("save_settings", { value: settings });
      const items = parseGlossary(glossaryText);
      await invoke("save_glossary", { text: JSON.stringify(items) });
      terms = items.map((it) => it.en);
      savedTip = "已保存";
      setTimeout(() => (savedTip = ""), 1500);
      showSettings = false;
    } catch (e) {
      savedTip = "保存失败：" + String(e);
    }
  }

  async function loadNotes() {
    try {
      const raw = await invoke<string>("load_notes", { doc: arxivId.trim() });
      notes = JSON.parse(raw || "{}");
    } catch {
      notes = {};
    }
  }

  function setNote(seq: number, value: string) {
    notes = { ...notes, [seq]: value };
    if (noteTimer) clearTimeout(noteTimer);
    noteTimer = setTimeout(async () => {
      try {
        await invoke("save_notes", { doc: arxivId.trim(), text: JSON.stringify(notes) });
      } catch {
        /* 忽略 */
      }
    }, 600);
  }

  async function applySegments(raw: string) {
    const data = JSON.parse(raw);
    segments = (data.chunks ?? []).map((c: any) => ({
      seq: c.seq,
      kind: c.kind || "para",
      status: c.status || "",
      en: c.en || "",
      zh: c.zh || "",
      ph: c.ph ?? {},
    }));
    view = "read";
    await loadNotes();
  }

  async function openDoc(d: Doc) {
    arxivId = d.arxivId;
    currentDocId = d.id;
    if (!d.segments) {
      nav = "work";
      view = "work";
      return;
    }
    try {
      const raw = await invoke<string>("read_segments", { path: d.segments });
      await applySegments(raw);
      await tick();
      const target = docEl?.querySelector<HTMLElement>(`[data-seq="${d.lastSeq}"]`);
      if (target) target.scrollIntoView({ block: "center" });
    } catch (e) {
      logs = [`无法打开：${String(e)}`];
      nav = "work";
      view = "work";
    }
  }

  function onDocScroll() {
    if (!docEl) return;
    const rows = docEl.querySelectorAll<HTMLElement>(".seg");
    let current = 0;
    for (const row of rows) {
      if (row.getBoundingClientRect().bottom > 120) {
        current = Number(row.dataset.seq ?? 0);
        break;
      }
    }
    const ratio = docEl.scrollTop / Math.max(1, docEl.scrollHeight - docEl.clientHeight);
    readProgress = Math.round(ratio * 100);
    if (currentDocId && !running) {
      if (scrollTimer) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        upsertDoc({
          arxivId: currentDocId,
          progress: readProgress,
          lastSeq: current,
          segments: currentSegmentsPath,
        });
      }, 800);
    }
  }

  onMount(() => {
    loadSettings();
    loadLibrary();

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
        lastTokens = p.tokens ?? 0;
        lastSeconds = p.seconds ?? 0;
        pushLog(`✓ 完成：${p.chunks ?? 0} 段`);
        if (p.segments) {
          currentSegmentsPath = p.segments;
          currentDocId = arxivId.trim();
          try {
            const raw = await invoke<string>("read_segments", { path: p.segments });
            await applySegments(raw);
            upsertDoc({
              arxivId: currentDocId,
              pdf,
              segments: p.segments,
              progress: 0,
              lastSeq: 0,
            });
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
    const un3 = listen<{ arxiv_id: string }>("job-start", (e) => {
      arxivId = e.payload.arxiv_id;
      progress = 0;
      stage = "fetch";
      logs = [];
      segments = [];
      notes = {};
      running = true;
    });
    const un4 = listen<number>("queue", (e) => (queueLen = e.payload));
    return () => {
      un1.then((f) => f());
      un2.then((f) => f());
      un3.then((f) => f());
      un4.then((f) => f());
    };
  });

  onDestroy(() => {
    if (timer) clearInterval(timer);
    if (noteTimer) clearTimeout(noteTimer);
    if (scrollTimer) clearTimeout(scrollTimer);
  });

  async function start() {
    const id = arxivId.trim();
    if (!id) return;

    if (running) {
      try {
        await invoke("start_run", { arxivId: id });
        savedTip = "已加入队列";
        setTimeout(() => (savedTip = ""), 1500);
      } catch (e) {
        pushLog(String(e));
      }
      return;
    }

    running = true;
    progress = 0;
    stage = "fetch";
    logs = [];
    pdf = "";
    segments = [];
    notes = {};
    view = "work";
    nav = "work";
    startedAt = Date.now();
    elapsed = 0;
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      elapsed = Math.floor((Date.now() - startedAt) / 1000);
    }, 1000);
    try {
      await invoke("start_run", { arxivId: id });
    } catch (e) {
      running = false;
      pushLog(String(e));
    }
  }

  async function clearQueue() {
    try {
      await invoke("clear_queue");
    } catch (e) {
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

  function setCollection(doc: Doc, value: string) {
    library = library.map((d) => (d.id === doc.id ? { ...d, collection: value } : d));
    persistLibrary();
  }

  async function loadFeed() {
    if (feedLoading) return;
    feedLoading = true;
    feedError = "";
    try {
      const raw = await invoke<string>("discover");
      const data = JSON.parse(raw || "{}");
      if (data.error) {
        feedError = String(data.error);
      } else {
        feed = (data.items ?? []).filter((it: FeedItem) => it.arxiv_id);
      }
    } catch (e) {
      feedError = String(e);
    } finally {
      feedLoading = false;
    }
  }

  async function useItem(it: FeedItem) {
    arxivId = it.arxiv_id;
    nav = "work";
    view = "work";
    await start();
  }

  function saveItem(it: FeedItem) {
    upsertDoc({ arxivId: it.arxiv_id });
    savedTip = "已收藏";
    setTimeout(() => (savedTip = ""), 1500);
  }
</script>

<header>
  <div class="brand"><span class="logo">译</span> 译见</div>
  <nav class="nav">
    <button class="nav-btn" class:on={nav === "work"} on:click={() => (nav = "work")}>工作台</button>
    <button class="nav-btn" class:on={nav === "library"} on:click={() => (nav = "library")}>
      文库 {#if library.length}<span class="badge">{library.length}</span>{/if}
    </button>
    <button
      class="nav-btn"
      class:on={nav === "discover"}
      on:click={() => {
        nav = "discover";
        loadFeed();
      }}
    >
      发现
    </button>
  </nav>
  <span class="grow"></span>
  {#if savedTip}<span class="saved">{savedTip}</span>{/if}
  <button class="icon" title="模型与术语" on:click={() => (showSettings = true)}>⚙</button>
</header>

{#if nav === "library"}
  <main class="library">
    <div class="libbar">
      <input class="search" bind:value={query} placeholder="搜索 arXiv ID 或分类…" />
      <span class="grow"></span>
      <span class="rmeta">{visibleDocs.length} 篇</span>
    </div>
    <div class="liblist">
      {#if !visibleDocs.length}
        <div class="empty">还没有论文。回工作台输入 arXiv ID 跑一篇，会自动收进这里。</div>
      {/if}
      {#each visibleDocs as d (d.id)}
        <div class="librow">
          <button class="libopen" on:click={() => openDoc(d)}>
            <span class="libtitle">{d.arxivId}</span>
            <span class="libmeta">
              {new Date(d.addedAt).toLocaleDateString()} · 进度 {Math.round(d.progress || 0)}%
            </span>
            <span class="libbar-track"><i style={`width:${d.progress || 0}%`}></i></span>
          </button>
          <input
            class="colinput"
            placeholder="分类"
            value={d.collection}
            on:change={(e) => setCollection(d, (e.target as HTMLInputElement).value)}
          />
        </div>
      {/each}
    </div>
  </main>
{:else if nav === "discover"}
  <main class="feed">
    <div class="libbar">
      <span class="feedtitle">全站热榜 · 近 7 天</span>
      <span class="grow"></span>
      <button class="ghost" on:click={loadFeed} disabled={feedLoading}>
        {feedLoading ? "加载中…" : "刷新"}
      </button>
    </div>
    <div class="feedlist">
      {#if feedError}
        <div class="empty">加载失败：{feedError}</div>
      {:else if !feed.length}
        <div class="empty">{feedLoading ? "正在拉取 alphaXiv 热榜…" : "点刷新加载"}</div>
      {/if}
      {#each feed as it (it.id)}
        <article class="card">
          <div class="cardtop">
            <span class="pill">{it.arxiv_id}</span>
            {#if it.votes != null}<span class="muted">↑ {it.votes} 票</span>{/if}
            {#if it.visits != null}<span class="muted">{it.visits} 浏览</span>{/if}
          </div>
          <h3>{it.title}</h3>
          <div class="auth">{it.authors}</div>
          <p>{it.abstract}</p>
          <div class="cardactions">
            <button class="primary sm" on:click={() => useItem(it)}>译入</button>
            <button class="ghost sm" on:click={() => saveItem(it)}>收藏</button>
          </div>
        </article>
      {/each}
    </div>
  </main>
{:else if view === "work"}
  <main>
    <div class="row">
      <input
        bind:value={arxivId}
        placeholder="arXiv ID 或链接，例如 1706.03762"
        on:keydown={(e) => e.key === "Enter" && start()}
      />
      <button class="primary" on:click={start}>{running ? "加入队列" : "开始"}</button>
      {#if running}
        <button class="ghost" on:click={cancel}>取消</button>
      {/if}
      {#if queueLen > 0}
        <button class="ghost" on:click={clearQueue}>清空队列 {queueLen}</button>
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
      {#if queueLen > 0}
        <span class="cost">队列 {queueLen}</span>
      {/if}
      {#if lastTokens}
        <span class="cost">{lastTokens.toLocaleString()} tokens · {lastSeconds}s</span>
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

    <div
      class="doc"
      class:only-en={mode === "en"}
      class:only-zh={mode === "zh"}
      bind:this={docEl}
      on:scroll={onDocScroll}
    >
      {#each segments as s (s.seq)}
        <div
          class="seg"
          class:title={s.kind === "section_title"}
          class:active={selected === s.seq}
          data-seq={s.seq}
          on:click={() => (selected = selected === s.seq ? -1 : s.seq)}
          role="button"
          tabindex="0"
          on:keydown={(e) => e.key === "Enter" && (selected = s.seq)}
        >
          <button
            class="note-btn"
            title="笔记"
            on:click|stopPropagation={() => (noteOpen = noteOpen === s.seq ? -1 : s.seq)}
          >
            ✎
          </button>
          <div class="en">{@html renderSegment(s.en, s.ph)}</div>
          <div class="zh">
            {#if s.zh}
              {@html renderSegment(s.zh, s.ph)}
            {:else}
              （未翻译）
            {/if}
            {#if notes[s.seq]}
              <div class="note-view">{notes[s.seq]}</div>
            {/if}
          </div>
        </div>
        {#if noteOpen === s.seq}
          <div class="note-editor">
            <textarea
              rows="3"
              placeholder="给这一段写点笔记…（自动保存）"
              value={notes[s.seq] ?? ""}
              on:input={(e) => setNote(s.seq, (e.target as HTMLTextAreaElement).value)}
            ></textarea>
          </div>
        {/if}
      {/each}
    </div>
  </main>
{/if}

{#if showSettings}
  <div class="overlay" on:click={() => (showSettings = false)} role="presentation">
    <div class="panel" role="dialog" aria-label="设置" on:click|stopPropagation>
      <h2>模型与术语</h2>
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
      <label>
        术语表（每行 <code>英文,中文</code>）
        <textarea
          rows="5"
          bind:value={glossaryText}
          placeholder="transformer,变换器&#10;attention,注意力"
        ></textarea>
      </label>
      <p class="tip">Key 存在本机 settings.json；术语表会写进引擎，影响后续所有翻译。</p>
      <div class="actions">
        <button on:click={() => (showSettings = false)}>取消</button>
        <button class="primary" on:click={saveSettings}>保存</button>
      </div>
    </div>
  </div>
{/if}
