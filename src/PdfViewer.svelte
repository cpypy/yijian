<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { convertFileSrc } from "@tauri-apps/api/core";
  import { createEventDispatcher } from "svelte";
  import * as pdfjsLib from "pdfjs-dist";
  import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

  pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

  export let src: string;
  export let zoom = 1;
  export let marks: { seq: number; page: number; x: number; y: number; w: number; h: number }[] = [];
  export let activeSeq = -1;

  const dispatch = createEventDispatcher<{ hover: number; leave: void }>();

  let host: HTMLDivElement;
  let loading = true;
  let error = "";
  let doc: any = null;
  let rendered = 0;
  let total = 0;
  let destroyed = false;
  let observer: IntersectionObserver | null = null;
  const pageBoxes: HTMLDivElement[] = [];

  // 渲染倍率：超过 1 的 dpr 之外再乘 1.6，避免高分屏发虚
  const SUPERSAMPLE = 1.6;

  onMount(() => {
    load();
  });

  onDestroy(() => {
    destroyed = true;
    observer?.disconnect();
  });

  let currentZoom = zoom;
  $: if (doc && zoom !== currentZoom) {
    currentZoom = zoom;
    reload();
  }

  let currentMarks = marks;
  $: if (marks !== currentMarks) {
    currentMarks = marks;
    // 只更新高亮层，不重渲染
    requestAnimationFrame(updateMarks);
  }

  let currentActive = activeSeq;
  $: if (activeSeq !== currentActive) {
    currentActive = activeSeq;
    updateMarks();
  }

  async function load() {
    destroyed = false;
    try {
      const url = convertFileSrc(src);
      doc = await pdfjsLib.getDocument({ url, disableAutoFetch: true }).promise;
      total = doc.numPages;
      loading = false;
      await nextFrame();
      await renderNext(3);
      armScroll();
    } catch (e) {
      loading = false;
      error = String(e);
    }
  }

  async function reload() {
    if (!doc) return;
    host.innerHTML = "";
    pageBoxes.length = 0;
    rendered = 0;
    await nextFrame();
    await renderNext(3);
    armScroll();
  }

  /** 滚到底部附近就继续渲染下一页批次 */
  function armScroll() {
    host.onscroll = () => {
      if (destroyed || rendered >= total) return;
      const nearBottom = host.scrollTop + host.clientHeight >= host.scrollHeight - 900;
      if (nearBottom) void renderNext(3);
    };
    // 内容不足一屏时直接把后续批次补齐
    if (host.scrollHeight <= host.clientHeight + 200 && rendered < total) {
      void renderNext(3);
    }
  }

  function nextFrame() {
    return new Promise((r) => requestAnimationFrame(() => r(null)));
  }

  async function renderNext(count: number) {
    if (!doc || destroyed) return;
    const end = Math.min(total, rendered + count);
    for (let n = rendered + 1; n <= end; n++) {
      const page = await doc.getPage(n);
      const dpr = Math.max(window.devicePixelRatio || 1, 1);
      const scale = zoom * dpr * SUPERSAMPLE;
      const viewport = page.getViewport({ scale });
      const cssWidth = viewport.width / (dpr * SUPERSAMPLE);
      const cssHeight = viewport.height / (dpr * SUPERSAMPLE);

      const box = document.createElement("div");
      box.className = "pagebox";
      box.dataset.page = String(n);
      box.style.width = `${cssWidth}px`;
      box.style.height = `${cssHeight}px`;

      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${cssWidth}px`;
      canvas.style.height = `${cssHeight}px`;
      box.appendChild(canvas);

      const layer = document.createElement("div");
      layer.className = "marklayer";
      box.appendChild(layer);

      host.appendChild(box);
      pageBoxes[n] = box;
      await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
      drawMarksForPage(n);
    }
    rendered = end;
  }

  function marksFor(page: number) {
    return currentMarks.filter((m) => m.page === page);
  }

  function drawMarksForPage(page: number) {
    const box = pageBoxes[page];
    if (!box) return;
    const layer = box.querySelector(".marklayer") as HTMLDivElement;
    layer.innerHTML = "";
    const cssWidth = parseFloat(box.style.width);
    // PDF 坐标是点（pt），渲染时按 zoom 放大；这里用相对比例定位
    const pageWidthPt = cssWidth / zoom;
    for (const m of marksFor(page)) {
      const el = document.createElement("div");
      el.className = "mark";
      el.dataset.seq = String(m.seq);
      el.style.left = `${(m.x / pageWidthPt) * 100}%`;
      el.style.top = `${(m.y / pageWidthPt) * 100}%`;
      el.style.width = `${(m.w / pageWidthPt) * 100}%`;
      el.style.height = `${(m.h / pageWidthPt) * 100}%`;
      el.addEventListener("mouseenter", () => dispatch("hover", m.seq));
      el.addEventListener("mouseleave", () => dispatch("leave"));
      layer.appendChild(el);
    }
    applyActive();
  }

  function updateMarks() {
    for (let p = 1; p <= total; p++) drawMarksForPage(p);
  }

  function applyActive() {
    if (!host) return;
    host.querySelectorAll<HTMLElement>(".mark").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.seq) === currentActive);
    });
  }

  export function scrollToSeq(seq: number) {
    const el = host?.querySelector<HTMLElement>(`.mark[data-seq="${seq}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }
</script>

<div class="pdfview" bind:this={host}>
  {#if loading}
    <div class="pdfstate">正在渲染 PDF…</div>
  {:else if error}
    <div class="pdfstate">打不开 PDF：{error}</div>
  {/if}
</div>

<style>
  .pdfview {
    height: 100%;
    overflow: auto;
    background: #e9e5dc;
    padding: 16px 0 40px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  .pdfstate {
    margin: 40px auto;
    font-size: 13px;
    color: #7d776d;
  }
  :global(.pagebox) {
    position: relative;
    background: #fff;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12);
    border-radius: 2px;
  }
  :global(.pagebox canvas) {
    display: block;
    border-radius: 2px;
  }
  :global(.marklayer) {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  :global(.mark) {
    position: absolute;
    background: rgba(47, 111, 94, 0.12);
    border-radius: 2px;
    pointer-events: auto;
    cursor: pointer;
    transition: background 0.12s;
  }
  :global(.mark:hover),
  :global(.mark.active) {
    background: rgba(47, 111, 94, 0.38);
  }
</style>
