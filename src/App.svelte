<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { onMount, onDestroy, tick } from "svelte";
  import * as katex from "katex";
  import PdfViewer from "./PdfViewer.svelte";

  type Segment = {
    seq: number;
    kind: string;
    status: string;
    en: string;
    zh: string;
    ph: Record<string, string>;
  };
  type Settings = { base_url: string; model: string; api_key: string; batch_chars?: number };
  type Doc = {
    id: string;
    arxivId: string;
    addedAt: number;
    pdf: string;
    enPdf?: string;
    segments: string;
    progress: number;
    lastSeq: number;
    title?: string;
    authors?: string;
    collection?: string;
    collectionId?: string;
    tags?: string[];
  };
  type Collection = { id: string; name: string; parentId: string | null };

  let arxivId = "1706.03762";
  let conflict: { task_id: string; status: string; progress: number } | null = null;
  let running = false;
  let progress = 0;
  let stage = "idle";
  let logs: string[] = [];
  let pdf = "";
  let enPdf = "";
  let align: Record<string, { en?: any; zh?: any }> = {};
  let zoom = 1;
  let hoverSeq = -1;
  let enViewer: PdfViewer | undefined;
  let zhViewer: PdfViewer | undefined;
  let feedZh: Record<string, { title: string; abstract: string; loading?: boolean }> = {};

  function marksFor(a: typeof align, side: "en" | "zh") {
    const out: { seq: number; page: number; x: number; y: number; w: number; h: number }[] = [];
    for (const [seq, pair] of Object.entries(a ?? {})) {
      const hit = (pair as any)?.[side];
      if (hit) out.push({ seq: Number(seq), ...hit });
    }
    return out;
  }

  $: enMarks = marksFor(align, "en");
  $: zhMarks = marksFor(align, "zh");

  function onHover(seq: number, side: "en" | "zh") {
    hoverSeq = seq;
    if (side === "en") zhViewer?.scrollToSeq(seq);
    else enViewer?.scrollToSeq(seq);
  }

  function changeZoom(delta: number) {
    zoom = Math.min(3, Math.max(0.5, Math.round((zoom + delta) * 10) / 10));
  }

  function onWheelZoom(e: WheelEvent) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    changeZoom(e.deltaY > 0 ? -0.1 : 0.1);
  }
  let startedAt = 0;
  let elapsed = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

  let view: "work" | "read" = "work";
  let nav: "work" | "library" | "discover" = "work";
  let segments: Segment[] = [];
  let mode: "both" | "en" | "zh" = "both";
  let selected = -1;
  let readerQuery = "";
  let openedFromLibrary = false;

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
  let settings: Settings = {
    base_url: "https://api.deepseek.com",
    model: "deepseek-flash",
    api_key: "",
    batch_chars: 6000,
  };
  let glossaryText = "";
  let terms: string[] = [];

  let notes: Record<number, string> = {};
  let noteOpen = -1;
  let noteTimer: ReturnType<typeof setTimeout> | null = null;

  type RefInfo = { authors: string; title: string; venue: string };
  let refs: Record<string, RefInfo> = {};
  let activeRef = "";
  let lastMeta: { title?: string; authors?: string } = {};

  let library: Doc[] = [];
  let collections: Collection[] = [];
  let activeCollection = "";
  let activeTag = "";
  let newCollectionName = "";
  let query = "";
  let textHits: { arxiv_id: string; seq: number; kind: string; snippet: string }[] = [];
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
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

  function buildTree(items: Collection[]): { id: string; name: string; depth: number }[] {
    const out: { id: string; name: string; depth: number }[] = [];
    const byParent = new Map<string, Collection[]>();
    for (const c of items) {
      const key = c.parentId ?? "";
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key)!.push(c);
    }
    const walk = (parent: string, depth: number) => {
      const kids = (byParent.get(parent) ?? []).slice().sort((a, b) => a.name.localeCompare(b.name));
      for (const c of kids) {
        out.push({ id: c.id, name: c.name, depth });
        walk(c.id, depth + 1);
      }
    };
    walk("", 0);
    return out;
  }

  function descendantsOf(cols: Collection[], id: string): Set<string> {
    const set = new Set<string>([id]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const c of cols) {
        if (c.parentId && set.has(c.parentId) && !set.has(c.id)) {
          set.add(c.id);
          changed = true;
        }
      }
    }
    return set;
  }

  function filterDocs(docs: Doc[], cols: Collection[], active: string, q: string, tag: string): Doc[] {
    const query = q.trim().toLowerCase();
    const scope = active && active !== "none" ? descendantsOf(cols, active) : null;
    return docs
      .filter((d) => {
        if (tag && !(d.tags ?? []).includes(tag)) return false;
        if (active === "none") {
          if (d.collectionId) return false;
        } else if (scope) {
          if (!scope.has(d.collectionId ?? "")) return false;
        }
        if (!query) return true;
        const hay = `${d.arxivId} ${d.title ?? ""} ${d.authors ?? ""}`.toLowerCase();
        return hay.includes(query);
      })
      .sort((a, b) => b.addedAt - a.addedAt);
  }

  function allTags(docs: Doc[]): { name: string; count: number }[] {
    const map = new Map<string, number>();
    for (const d of docs) {
      for (const t of d.tags ?? []) {
        if (t) map.set(t, (map.get(t) ?? 0) + 1);
      }
    }
    return [...map.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
  }

  $: tree = buildTree(collections);
  $: configured = Boolean(
    settings.base_url.trim() && settings.model.trim() && settings.api_key.trim()
  );
  $: tagList = allTags(library);
  $: visibleDocs = filterDocs(library, collections, activeCollection, query, activeTag);

  $: shownSegments = (() => {
    const q = readerQuery.trim().toLowerCase();
    if (!q) return segments;
    return segments.filter(
      (s) => s.en.toLowerCase().includes(q) || s.zh.toLowerCase().includes(q)
    );
  })();

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
    if (kind === "CITE") {
      const keys = raw.replace(/^\\[a-zA-Z]+\{|\}$/g, "");
      const first = keys.split(",")[0].trim();
      return `<span class="chip cite" data-cite="${esc(first)}" title="${esc(raw)}">${esc(
        first.slice(0, 20)
      )}</span>`;
    }
    if (kind === "REF") {
      const key = raw.replace(/^\\[a-zA-Z]+\{|\}$/g, "");
      return `<span class="chip ref" title="${esc(raw)}">${esc(key.slice(0, 20))}</span>`;
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

  let deletedIds = new Set<string>();

  async function persistLibrary() {
    // 与磁盘上的副本合并：避免启动时 load 未完成就把已有条目覆盖掉
    try {
      const raw = await invoke<string>("load_library");
      const disk = JSON.parse(raw || "[]");
      if (Array.isArray(disk)) {
        const map = new Map<string, Doc>();
        for (const d of disk as Doc[]) {
          if (!deletedIds.has(d.id)) map.set(d.id, d);
        }
        for (const d of library) map.set(d.id, d);
        library = [...map.values()].sort((a, b) => b.addedAt - a.addedAt);
      }
    } catch {
      /* 读不到就按内存为准 */
    }
    invoke("save_library", { text: JSON.stringify(library) }).catch(() => {});
  }

  function deleteDoc(doc: Doc) {
    deletedIds.add(doc.id);
    library = library.filter((d) => d.id !== doc.id);
    persistLibrary();
    savedTip = "已删除";
    setTimeout(() => (savedTip = ""), 1200);
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
          collectionId: "",
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
      settings = {
        base_url: s.base_url || "https://api.deepseek.com",
        model: s.model || "deepseek-flash",
        api_key: s.api_key ?? "",
        batch_chars: Number(s.batch_chars) || 6000,
      };
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
    refs = data.refs ?? {};
    lastMeta = data.meta ?? {};
    activeRef = "";
    segments = (data.chunks ?? []).map((c: any) => ({
      seq: c.seq,
      kind: c.kind || "para",
      status: c.status || "",
      en: c.en || "",
      zh: c.zh || "",
      ph: c.ph ?? {},
    }));
    align = data.align ?? {};
    view = "read";
    await loadNotes();
  }

  async function openDoc(d: Doc) {
    arxivId = d.arxivId;
    currentDocId = d.id;
    openedFromLibrary = true;
    readerQuery = "";
    pdf = d.pdf || "";
    enPdf = d.enPdf || "";
    if (d.segments) {
      try {
        const raw = await invoke<string>("read_segments", { path: d.segments });
        await applySegments(raw);
      } catch {
        /* PDF 仍可打开 */
      }
    }
    if (pdf || enPdf) {
      view = "read";
      return;
    }
    if (!d.segments) {
      nav = "work";
      view = "work";
      return;
    }
    nav = "work";
    view = "work";
  }

  function onDocClick(e: MouseEvent) {
    const target = e.target as HTMLElement | null;
    const chip = target?.closest?.(".chip[data-cite]") as HTMLElement | null;
    if (chip) {
      activeRef = chip.dataset.cite || "";
      e.stopPropagation();
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
    loadLibrary().then(loadCollections);

    const un1 = listen<Record<string, unknown>>("engine-event", async (e) => {
      const p = e.payload as any;
      if (p.event === "stage") {
        stage = p.stage;
        progress = p.progress;
      } else if (p.event === "log") {
        pushLog(p.line);
      } else if (p.event === "conflict") {
        running = false;
        conflict = { task_id: p.task_id, status: p.status, progress: p.progress ?? 0 };
        pushLog(`发现未完成任务 ${p.task_id}（${p.status}），等待选择`);
      } else if (p.event === "done") {
        running = false;
        progress = 100;
        stage = "done";
        pdf = p.pdf || "";
        enPdf = p.en_pdf || "";
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
              enPdf,
              segments: p.segments,
              progress: 0,
              lastSeq: 0,
              title: lastMeta.title,
              authors: lastMeta.authors,
            });
          } catch (err) {
            pushLog("读取段落失败：" + String(err));
          }
        }
      } else if (p.event === "error") {
        running = false;
        pushLog("ERROR: " + p.message);
      } else if (p.event) {
        pushLog(`未知事件 ${p.event}：${JSON.stringify(p).slice(0, 200)}`);
      }
    });
    const un2 = listen("engine-exit", () => (running = false));
    const un5 = listen<string>("engine-log", (e) => pushLog(e.payload));
    const un3 = listen<{ arxiv_id: string }>("job-start", (e) => {
      arxivId = e.payload.arxiv_id;
      progress = 0;
      stage = "fetch";
      logs = [];
      segments = [];
      notes = {};
      running = true;
    });
    const un4 = listen<number>("queue", (e) => {
      queueLen = e.payload;
      if (showQueue) loadQueue();
    });
    return () => {
      un1.then((f) => f());
      un2.then((f) => f());
      un3.then((f) => f());
      un4.then((f) => f());
      un5.then((f) => f());
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

    if (!configured) {
      savedTip = "请先在设置里填 Base URL、模型和 API Key";
      setTimeout(() => (savedTip = ""), 2500);
      showSettings = true;
      return;
    }

    if (running) {
      try {
        await invoke("start_run", { arxivId: id, mode: "auto" });
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
    openedFromLibrary = false;
    readerQuery = "";
    view = "work";
    nav = "work";
    startedAt = Date.now();
    elapsed = 0;
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      elapsed = Math.floor((Date.now() - startedAt) / 1000);
    }, 1000);
    try {
      await invoke("start_run", { arxivId: id, mode: "auto" });
    } catch (e) {
      running = false;
      pushLog(String(e));
    }
  }

  async function resolveConflict(mode: "resume" | "fresh") {
    const current = conflict;
    conflict = null;
    if (!current) return;
    running = true;
    stage = "fetch";
    progress = 0;
    logs = [];
    try {
      await invoke("start_run", { arxivId: arxivId.trim(), mode });
    } catch (e) {
      running = false;
      pushLog(String(e));
    }
  }

  async function clearQueue() {
    try {
      await invoke("clear_queue");
      queueItems = [];
    } catch (e) {
      pushLog(String(e));
    }
  }

  let queueItems: string[] = [];
  let showQueue = false;

  async function loadQueue() {
    try {
      queueItems = await invoke<string[]>("queue_list");
    } catch {
      queueItems = [];
    }
  }

  async function toggleQueue() {
    showQueue = !showQueue;
    if (showQueue) await loadQueue();
  }

  async function removeQueue(index: number) {
    try {
      await invoke("queue_remove", { index });
      await loadQueue();
    } catch (e) {
      pushLog(String(e));
    }
  }

  async function moveQueue(index: number, delta: number) {
    try {
      await invoke("queue_move", { index, delta });
      await loadQueue();
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

  function saveCollections() {
    invoke("save_collections", { text: JSON.stringify(collections) }).catch(() => {});
  }

  async function loadCollections() {
    try {
      const raw = await invoke<string>("load_collections");
      const items = JSON.parse(raw || "[]");
      if (Array.isArray(items)) collections = items;
    } catch {
      collections = [];
    }
    migrateLegacyCollections();
  }

  /** 老数据里 documents 存的是分类名字符串，这里迁移成集合 ID */
  function migrateLegacyCollections() {
    let changedDocs = false;
    let changedCols = false;
    for (const d of library) {
      const legacy = (d.collection ?? "").trim();
      if (legacy && !d.collectionId) {
        let target = collections.find((c) => !c.parentId && c.name === legacy);
        if (!target) {
          target = { id: crypto.randomUUID(), name: legacy, parentId: null };
          collections = [...collections, target];
          changedCols = true;
        }
        d.collectionId = target.id;
        changedDocs = true;
      }
    }
    if (changedDocs) persistLibrary();
    if (changedCols) saveCollections();
  }

  function addCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    const parentId = activeCollection && activeCollection !== "none" ? activeCollection : null;
    collections = [...collections, { id: crypto.randomUUID(), name, parentId }];
    newCollectionName = "";
    saveCollections();
  }

  function deleteCollection(c: Collection) {
    const parent = c.parentId;
    collections = collections
      .filter((x) => x.id !== c.id)
      .map((x) => (x.parentId === c.id ? { ...x, parentId: parent } : x));
    library = library.map((d) =>
      d.collectionId === c.id ? { ...d, collectionId: parent ?? "" } : d
    );
    if (activeCollection === c.id) activeCollection = "";
    persistLibrary();
    saveCollections();
  }

  function moveDoc(doc: Doc, collectionId: string) {
    library = library.map((d) => (d.id === doc.id ? { ...d, collectionId } : d));
    persistLibrary();
  }

  function onQueryInput() {
    if (searchTimer) clearTimeout(searchTimer);
    const q = query.trim();
    if (q.length < 2) {
      textHits = [];
      return;
    }
    searchTimer = setTimeout(async () => {
      const docs = library.filter((d) => d.segments).map((d) => [d.arxivId, d.segments]);
      try {
        textHits = await invoke("search_text", { docs, query: q });
      } catch {
        textHits = [];
      }
    }, 450);
  }

  async function openDocAt(d: Doc, seq: number) {
    await openDoc(d);
    await tick();
    const target = docEl?.querySelector<HTMLElement>(`[data-seq="${seq}"]`);
    if (target) target.scrollIntoView({ block: "center" });
    selected = seq;
  }

  function setTags(doc: Doc, raw: string) {
    const tags = raw
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean)
      .slice(0, 8);
    library = library.map((d) => (d.id === doc.id ? { ...d, tags } : d));
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

  async function translateItem(it: FeedItem) {
    if (!configured) {
      savedTip = "请先在设置里填 API Key";
      setTimeout(() => (savedTip = ""), 2000);
      showSettings = true;
      return;
    }
    feedZh = { ...feedZh, [it.id]: { title: "", abstract: "", loading: true } };
    try {
      const raw = await invoke<string>("translate_texts", {
        texts: [it.title, it.abstract],
      });
      const data = JSON.parse(raw || "{}");
      if (data.error) throw new Error(data.error);
      const list = data.texts ?? [];
      feedZh = {
        ...feedZh,
        [it.id]: { title: list[0] ?? "", abstract: list[1] ?? "", loading: false },
      };
    } catch (e) {
      feedZh = { ...feedZh, [it.id]: { title: "", abstract: String(e), loading: false } };
    }
  }
</script>

<div class="shell">
  <aside class="rail">
    <div class="brand">
      <span class="logo">译</span>
      <div class="bname">译见<small>Yijian</small></div>
    </div>
    <nav class="nav">
      <button class:on={nav === "work"} on:click={() => (nav = "work")}>工作台</button>
      <button class:on={nav === "library"} on:click={() => (nav = "library")}>
        文库{#if library.length}<em>{library.length}</em>{/if}
      </button>
      <button
        class:on={nav === "discover"}
        on:click={() => {
          nav = "discover";
          loadFeed();
        }}
      >
        发现
      </button>
    </nav>
    <span class="grown"></span>
    <div class="engine">
      <b>{settings.model || "未配置模型"}</b>
      {running ? "翻译中…" : "就绪"}
    </div>
    <button class="railset" on:click={() => (showSettings = true)}>⚙ 设置</button>
  </aside>

  <div class="main">
    <div class="topbar">
      <h1>
        {nav === "library"
          ? "文库"
          : nav === "discover"
            ? "发现"
            : view === "read"
              ? "阅读器"
              : "工作台"}
      </h1>
      <span class="sub">
        {#if nav === "library"}
          {library.length} 篇 · {collections.length} 个集合
        {:else if nav === "discover"}
          alphaXiv 热榜 · 近 7 天
        {:else if view === "read"}
          {arxivId}
        {:else}
          输入 arXiv ID，生成中英对照
        {/if}
      </span>
      <span class="grow"></span>
      {#if savedTip}<span class="saved">{savedTip}</span>{/if}
      {#if lastTokens}
        <span class="cost">{lastTokens.toLocaleString()} tokens</span>
      {/if}
    </div>

{#if nav === "library"}
  <main class="library">
    <aside class="libside">
      <button class="sideitem" class:on={activeCollection === ""} on:click={() => (activeCollection = "")}>
        全部 <span class="n">{library.length}</span>
      </button>
      <button
        class="sideitem"
        class:on={activeCollection === "none"}
        on:click={() => (activeCollection = "none")}
      >
        未分类
      </button>
      {#each tree as node (node.id)}
        <div class="siderow" style={`padding-left:${node.depth * 12}px`}>
          <button
            class="sideitem"
            class:on={activeCollection === node.id}
            on:click={() => (activeCollection = node.id)}
          >
            {node.name}
          </button>
          <button
            class="del"
            title="删除集合"
            on:click|stopPropagation={() => {
              const c = collections.find((x) => x.id === node.id);
              if (c) deleteCollection(c);
            }}
          >
            ×
          </button>
        </div>
      {/each}
      <div class="addrow">
        <input
          bind:value={newCollectionName}
          placeholder="新集合"
          on:keydown={(e) => e.key === "Enter" && addCollection()}
        />
        <button on:click={addCollection}>+</button>
      </div>
    </aside>

    <section class="libmain">
      <div class="libbar">
        <input
          class="search"
          bind:value={query}
          on:input={onQueryInput}
          placeholder="搜索标题 / 作者 / 全文…"
        />
        {#if tagList.length}
          <div class="tagbar">
            {#each tagList as t (t.name)}
              <button
                class="tagchip"
                class:on={activeTag === t.name}
                on:click={() => (activeTag = activeTag === t.name ? "" : t.name)}
              >
                #{t.name} <span class="n">{t.count}</span>
              </button>
            {/each}
          </div>
        {/if}
        <span class="grow"></span>
        <span class="rmeta">{visibleDocs.length} 篇</span>
      </div>
      {#if textHits.length}
        <div class="hits">
          <div class="hitshead">全文命中 {textHits.length} 段</div>
          {#each textHits as h (h.arxiv_id + "-" + h.seq)}
            <button
              class="hit"
              on:click={() => {
                const d = library.find((x) => x.arxivId === h.arxiv_id);
                if (d) openDocAt(d, h.seq);
              }}
            >
              <span class="hitid">{h.arxiv_id} · 第 {h.seq} 段</span>
              <span class="hitsnip">…{h.snippet}…</span>
            </button>
          {/each}
        </div>
      {/if}

      <div class="liblist">
        {#if !visibleDocs.length}
          <div class="empty">还没有论文。回工作台输入 arXiv ID 跑一篇，会自动收进这里。</div>
        {/if}
        {#each visibleDocs as d (d.id)}
          <div class="librow">
            <button class="libopen" on:click={() => openDoc(d)}>
              <span class="libtitle">{d.title || d.arxivId}</span>
              <span class="libmeta">
                {d.arxivId}{d.authors ? ` · ${d.authors}` : ""} · 进度 {Math.round(d.progress || 0)}%
              </span>
              <span class="libbar-track"><i style={`width:${d.progress || 0}%`}></i></span>
            </button>
            <select
              class="colselect"
              value={d.collectionId ?? ""}
              on:change={(e) => moveDoc(d, (e.target as HTMLSelectElement).value)}
            >
              <option value="">未分类</option>
              {#each collections as c (c.id)}
                <option value={c.id}>{c.name}</option>
              {/each}
            </select>
            <input
              class="taginput"
              placeholder="标签"
              value={(d.tags ?? []).join(", ")}
              on:change={(e) => setTags(d, (e.target as HTMLInputElement).value)}
            />
            <button class="mini danger" title="从文库删除" on:click={() => deleteDoc(d)}>删除</button>
          </div>
        {/each}
      </div>
    </section>
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
          {#if feedZh[it.id]}
            <div class="zhblock">
              <div class="zhtitle">{feedZh[it.id].title}</div>
              <div class="zhabstract">{feedZh[it.id].abstract}</div>
            </div>
          {/if}
          <div class="cardactions">
            <button class="primary sm" on:click={() => useItem(it)}>译入</button>
            <button class="ghost sm" on:click={() => saveItem(it)}>收藏</button>
            <button
              class="ghost sm"
              disabled={feedZh[it.id]?.loading}
              on:click={() => translateItem(it)}
            >
              {feedZh[it.id]?.loading ? "翻译中…" : "译成中文"}
            </button>
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
        <button class="ghost" on:click={toggleQueue}>队列 {queueLen}</button>
      {/if}
    </div>

    {#if showQueue}
      <div class="queuebox">
        {#if !queueItems.length}
          <div class="qempty">队列是空的</div>
        {:else}
          {#each queueItems as qid, i (qid + "-" + i)}
            <div class="qrow">
              <span class="qidx">{i + 1}</span>
              <span class="qid">{qid}</span>
              <button class="mini" title="上移" on:click={() => moveQueue(i, -1)}>↑</button>
              <button class="mini" title="下移" on:click={() => moveQueue(i, 1)}>↓</button>
              <button class="mini" on:click={() => removeQueue(i)}>移除</button>
            </div>
          {/each}
          <button class="ghost sm" on:click={clearQueue}>清空队列</button>
        {/if}
      </div>
    {/if}

    {#if !configured}
      <div class="banner">
        还没配置模型：点右上角 ⚙ 填 API Key（Base URL 与模型默认 DeepSeek）。
        <button class="link" on:click={() => (showSettings = true)}>去设置</button>
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
      <button
        class="ghost"
        on:click={() => {
          view = "work";
          if (openedFromLibrary) nav = "library";
        }}
      >
        ← 返回
      </button>
      <span class="rid">{arxivId}</span>
      <span class="rmeta">{mode === "both" ? "中英对照" : mode === "en" ? "原文" : "译文"}</span>
      <span class="grow"></span>
      <button class="ghost" title="缩小" on:click={() => changeZoom(-0.1)}>－</button>
      <span class="rmeta">{Math.round(zoom * 100)}%</span>
      <button class="ghost" title="放大" on:click={() => changeZoom(0.1)}>＋</button>
      <button class="seg-btn" class:on={mode === "en"} on:click={() => (mode = "en")}>原文</button>
      <button class="seg-btn" class:on={mode === "both"} on:click={() => (mode = "both")}>
        对照
      </button>
      <button class="seg-btn" class:on={mode === "zh"} on:click={() => (mode = "zh")}>译文</button>
      {#if pdf}
        <button class="ghost" on:click={openPdf}>系统打开</button>
      {/if}
    </div>

    <div class="pdfarea" class:dual={mode === "both"} on:wheel|nonpassive={onWheelZoom}>
      {#if mode === "both"}
        {#if enPdf}
          <div class="pane">
            <div class="panehead">原文</div>
            <PdfViewer
              bind:this={enViewer}
              src={enPdf}
              {zoom}
              marks={enMarks}
              activeSeq={hoverSeq}
              on:hover={(e) => onHover(e.detail, "en")}
            />
          </div>
        {:else}
          <div class="pane"><div class="pdfstate">没有原文 PDF</div></div>
        {/if}
        {#if pdf}
          <div class="pane">
            <div class="panehead">译文</div>
            <PdfViewer
              bind:this={zhViewer}
              src={pdf}
              {zoom}
              marks={zhMarks}
              activeSeq={hoverSeq}
              on:hover={(e) => onHover(e.detail, "zh")}
            />
          </div>
        {:else}
          <div class="pane"><div class="pdfstate">没有译文 PDF</div></div>
        {/if}
      {:else if mode === "en"}
        {#if enPdf}
          <PdfViewer src={enPdf} {zoom} marks={enMarks} activeSeq={hoverSeq} />
        {:else}
          <div class="pdfstate">没有原文 PDF</div>
        {/if}
      {:else if pdf}
        <PdfViewer src={pdf} {zoom} marks={zhMarks} activeSeq={hoverSeq} />
      {:else}
        <div class="pdfstate">没有译文 PDF</div>
      {/if}
    </div>
  </main>
{/if}

{#if activeRef}
  <div class="refpop">
    <div class="refhead">
      <span class="refkey">{activeRef}</span>
      <button class="refclose" on:click={() => (activeRef = "")}>×</button>
    </div>
    {#if refs[activeRef]}
      <div class="reftitle">{refs[activeRef].title || "（无标题）"}</div>
      <div class="refmeta">{refs[activeRef].authors}</div>
      <div class="refmeta">{refs[activeRef].venue}</div>
    {:else}
      <div class="refmeta">本地文献表里没有这条引用</div>
    {/if}
  </div>
{/if}
  </div>
</div>

{#if conflict}
  <div class="overlay" role="presentation">
    <div class="panel" role="dialog" aria-label="已有翻译任务">
      <h2>这篇论文已有翻译任务</h2>
      <p class="tip">
        任务 <code>{conflict.task_id}</code>，状态 <b>{conflict.status}</b>，进度
        {Math.round(conflict.progress || 0)}%。
      </p>
      <p class="tip">继续翻译会保留已翻好的段落；重新翻译会删掉旧任务从头开始。</p>
      <div class="actions">
        <button on:click={() => (conflict = null)}>取消</button>
        <button on:click={() => resolveConflict("fresh")}>重新翻译</button>
        <button class="primary" on:click={() => resolveConflict("resume")}>继续翻译</button>
      </div>
    </div>
  </div>
{/if}

{#if showSettings}
  <div class="overlay" on:click={() => (showSettings = false)} role="presentation">
    <div class="panel" role="dialog" aria-label="设置" on:click|stopPropagation>
      <h2>模型与术语</h2>
      <label>
        Base URL
        <input bind:value={settings.base_url} placeholder="https://api.deepseek.com" />
      </label>
      <label>
        模型
        <input bind:value={settings.model} list="model-presets" placeholder="deepseek-flash" />
        <datalist id="model-presets">
          <option value="deepseek-flash"></option>
          <option value="deepseek-v4-pro"></option>
        </datalist>
      </label>
      <label>
        API Key
        <input type="password" bind:value={settings.api_key} placeholder="sk-..." />
      </label>
      <label>
        单批字数（越小越稳，默认 6000）
        <input type="number" min="500" max="12000" step="500" bind:value={settings.batch_chars} />
      </label>
      <label>
        术语表（每行 <code>英文,中文</code>）
        <textarea
          rows="5"
          bind:value={glossaryText}
          placeholder="transformer,变换器&#10;attention,注意力"
        ></textarea>
      </label>
      <p class="tip">
        三项都必填。Base URL 与模型默认 DeepSeek（<code>https://api.deepseek.com</code> /
        <code>deepseek-flash</code>）；Key 存在本机 settings.json；术语表会写进引擎，影响后续所有翻译。
      </p>
      <div class="actions">
        <button on:click={() => (showSettings = false)}>取消</button>
        <button class="primary" on:click={saveSettings}>保存</button>
      </div>
    </div>
  </div>
{/if}
