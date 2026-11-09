<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { convertFileSrc } from "@tauri-apps/api/core";
  import * as pdfjsLib from "pdfjs-dist";
  import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

  pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

  export let src: string;

  let host: HTMLDivElement;
  let loading = true;
  let error = "";
  let doc: any = null;
  let rendered = 0;
  let total = 0;
  let destroyed = false;

  onMount(async () => {
    try {
      const url = convertFileSrc(src);
      doc = await pdfjsLib.getDocument({ url, disableAutoFetch: true }).promise;
      total = doc.numPages;
      rendered = 0;
      loading = false;
      await tick();
      renderNext(4);
      const io = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) renderNext(4);
        },
        { rootMargin: "800px" }
      );
      io.observe(host);
      observer = io;
    } catch (e) {
      loading = false;
      error = String(e);
    }
  });

  let observer: IntersectionObserver | null = null;

  onDestroy(() => {
    destroyed = true;
    observer?.disconnect();
    doc?.destroy?.();
  });

  function tick() {
    return new Promise((r) => requestAnimationFrame(() => r(null)));
  }

  async function renderNext(count: number) {
    if (!doc || destroyed) return;
    const end = Math.min(total, rendered + count);
    for (let n = rendered + 1; n <= end; n++) {
      const page = await doc.getPage(n);
      const viewport = page.getViewport({ scale: 1.15 * window.devicePixelRatio });
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width / window.devicePixelRatio}px`;
      canvas.style.height = `${viewport.height / window.devicePixelRatio}px`;
      canvas.className = "pdfpage";
      host.appendChild(canvas);
      await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
    }
    rendered = end;
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
  :global(canvas.pdfpage) {
    background: #fff;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12);
    border-radius: 2px;
  }
</style>
