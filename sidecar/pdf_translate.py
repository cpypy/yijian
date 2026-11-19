"""上传 PDF 的翻译通道：抽文本 → 调模型 → 回写到原版式 PDF。

不依赖 babeldoc（AGPL、需单独安装），只用 PyMuPDF + 现有 BYOK 配置。
产物放在 <job>/ 下：en.pdf（原件副本）、zh.pdf（中文覆盖版）、segments.json。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _collect_blocks(doc) -> list[dict]:
    blocks: list[dict] = []
    for page_index in range(doc.page_count):
        page = doc[page_index]
        for raw in page.get_text("blocks"):
            if len(raw) < 7 or raw[6] != 0:
                continue
            text = _clean(str(raw[4]))
            # 太短的多半是页码、公式编号，跳过
            if len(re.sub(r"[^A-Za-z]", "", text)) < 12:
                continue
            blocks.append(
                {
                    "page": page_index,
                    "rect": [
                        round(float(raw[0]), 2),
                        round(float(raw[1]), 2),
                        round(float(raw[2]), 2),
                        round(float(raw[3]), 2),
                    ],
                    "en": text,
                }
            )
    return blocks


def _make_translator(base: str, key: str, model: str):
    from texlate.xlat._dialects import ChatOptions
    from texlate.xlat.client import ChatClient

    client = ChatClient(base_url=base, api_key=key)

    async def one(text: str) -> str:
        resp = await client.chat(
            model,
            [
                {
                    "role": "user",
                    "content": (
                        "把下面这段论文正文翻译成简体中文，保留公式符号与专有名词，"
                        "只输出译文，不要解释：\n" + text
                    ),
                }
            ],
            options=ChatOptions(temperature=0.3, max_tokens=8000),
        )
        return (resp.content or "").strip()

    return client, one


async def _translate_all(texts: list[str], base: str, key: str, model: str, on_progress):
    if __import__("os").environ.get("TEXLATE_TRANSLATOR") == "mock":
        out = [f"【译文】{t[:24]}" for t in texts]
        on_progress(len(texts), len(texts))
        return out

    client, one = _make_translator(base, key, model)
    results: list[str] = [""] * len(texts)
    batch = 6
    done = 0
    try:
        for start in range(0, len(texts), batch):
            chunk = list(enumerate(texts[start : start + batch], start=start))
            gathered = await asyncio.gather(*(one(t) for _i, t in chunk), return_exceptions=True)
            for (index, _text), value in zip(chunk, gathered):
                results[index] = "" if isinstance(value, Exception) else value
            done += len(chunk)
            on_progress(done, len(texts))
    finally:
        await client.aclose()
    return results


FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _pick_font() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _draw(doc, blocks: list[dict], zh_texts: list[str]) -> None:
    font_file = _pick_font()
    font_kwargs = (
        {"fontname": "F0", "fontfile": font_file} if font_file else {"fontname": "china-s"}
    )
    for block, zh in zip(blocks, zh_texts):
        if not zh:
            continue
        page = doc[block["page"]]
        rect = block["rect"]
        area = page_rect = None
        import pymupdf

        area = pymupdf.Rect(*rect)
        if area.width < 12 or area.height < 8:
            continue
        # 盖掉原文
        page.draw_rect(area, color=None, fill=(1, 1, 1), overlay=True)
        size = min(10.5, max(6.0, area.height / max(1, len(zh) // 20 + 1)))
        while size >= 5:
            remaining = page.insert_textbox(
                area,
                zh,
                fontsize=size,
                color=(0, 0, 0),
                align=0,
                overlay=True,
                **font_kwargs,
            )
            if remaining >= 0:
                break
            size -= 0.5
        _ = page_rect


def run_pdf(path: str, out_dir: str, emit) -> int:
    import pymupdf

    src = Path(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    base = __import__("os").environ.get("TEXLATE_BASE_URL") or "https://api.deepseek.com"
    key = __import__("os").environ.get("TEXLATE_API_KEY") or ""
    model = __import__("os").environ.get("TEXLATE_MODEL") or "deepseek-flash"
    mock = __import__("os").environ.get("TEXLATE_TRANSLATOR") == "mock"
    if not key and not mock:
        emit(event="error", message="未配置 API Key")
        return 1

    started = time.time()
    emit(event="stage", stage="parse", progress=5)
    try:
        doc = pymupdf.open(src)
    except Exception as exc:  # noqa: BLE001
        emit(event="error", message=f"打不开 PDF：{exc}")
        return 1

    blocks = _collect_blocks(doc)
    total = len(blocks)
    emit(event="log", line=f"共提取 {total} 段可翻译文本（{doc.page_count} 页）")
    if not total:
        emit(event="error", message="这个 PDF 里没有可提取的文字（可能是扫描件）")
        return 1

    emit(event="stage", stage="translate", progress=10)

    def on_progress(done: int, count: int) -> None:
        pct = 10 + int(done / max(1, count) * 70)
        emit(event="stage", stage="translate", progress=pct)
        emit(event="log", line=f"翻译 {done}/{count}")

    zh_texts = asyncio.run(
        _translate_all([b["en"] for b in blocks], base, key, model, on_progress)
    )

    emit(event="stage", stage="compile", progress=85)
    _draw(doc, blocks, zh_texts)

    zh_pdf = out / "zh.pdf"
    en_pdf = out / "en.pdf"
    doc.save(zh_pdf)
    doc.close()
    import shutil

    shutil.copy2(src, en_pdf)

    chunks = []
    align = {}
    for index, block in enumerate(blocks):
        x, y, x2, y2 = block["rect"]
        rect = {
            "page": block["page"] + 1,
            "x": x,
            "y": y,
            "w": round(x2 - x, 2),
            "h": round(y2 - y, 2),
        }
        chunks.append(
            {
                "seq": index,
                "kind": "para",
                "en": block["en"],
                "zh": zh_texts[index],
                "ph": {},
            }
        )
        align[str(index)] = {"en": rect, "zh": rect}

    segments = out / "segments.json"
    segments.write_text(
        json.dumps({"chunks": chunks, "total": len(chunks), "align": align}, ensure_ascii=False),
        encoding="utf-8",
    )

    emit(event="stage", stage="done", progress=100)
    emit(
        event="done",
        status="done",
        pdf=str(zh_pdf),
        en_pdf=str(en_pdf),
        segments=str(segments),
        chunks=len(chunks),
        tokens=0,
        seconds=round(time.time() - started, 1),
    )
    return 0
