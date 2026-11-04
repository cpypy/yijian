#!/usr/bin/env python3
"""译见 · 全站热榜算法离线验证原型

目的：用真实数据跑一遍「多源信号 → 百分位归一化 → 加权 → 时间衰减」，
回答三个问题：
  1. 各源数据拿得到吗、字段稳定吗
  2. 算出来的 Top 20 和 alphaXiv 官方 Hot 榜像不像（Spearman）
  3. 权重稍微动一动，榜单会不会翻车（敏感性）

用法：
    python tools/hot_probe.py
产物：
    data/probe/report.md     人读的报告
    data/probe/raw/*.json    原始快照，便于复现
"""

from __future__ import annotations

import json
import math
import re
import time
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import os

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "probe"
RAW = OUT / "raw"

_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("YIJIAN_PROXY")
PROXIES = {"http": _PROXY, "https": _PROXY} if _PROXY else None
UA = "Yijian/0.1 (discovery probe; local research use)"

SESSION = requests.Session()
SESSION.proxies = PROXIES
SESSION.headers["User-Agent"] = UA

ARXIV_CATS = ["cs.CL", "cs.LG", "cs.CV", "cs.AI"]
ARXIV_PER_CAT = 100

# 设计权重（评论、社媒提及、引用增速本次拿不到，按可得信号重分配）
WEIGHTS = {
    "ax_votes": 0.35,    # alphaXiv 点赞
    "ax_visits": 0.20,   # alphaXiv 近 7 天浏览
    "hf_upvotes": 0.30,  # HF 每日榜投票
    "gh_stars": 0.15,    # GitHub 星标
}

AX_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
NOW = datetime.now(timezone.utc)

CS_RE = re.compile(r"^cs\.[A-Z]{2}$")
TOPIC_CAT = {
    "interpretability": "cs.LG", "ai-for-science": "cs.AI", "agents": "cs.AI",
    "llm-reasoning": "cs.CL", "optimization": "cs.LG", "weight-editing": "cs.LG",
    "test-time-inference": "cs.LG", "transformers": "cs.LG", "reasoning": "cs.AI",
    "multi-agent-learning": "cs.AI", "tool-use": "cs.AI", "agentic-frameworks": "cs.AI",
    "human-ai-interaction": "cs.HC", "ml-systems": "cs.LG", "training-orchestration": "cs.LG",
    "statistical-learning": "cs.LG", "nlp": "cs.CL", "computer-vision": "cs.CV",
}


def norm_cat(raw: str) -> str:
    if not raw:
        return ""
    if CS_RE.match(raw):
        return raw
    return TOPIC_CAT.get(raw.lower(), "")


# ---------------------------------------------------------------- 抓取
def fetch_arxiv(cat: str, n: int = ARXIV_PER_CAT) -> list[dict]:
    r = SESSION.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": f"cat:{cat}",
            "start": 0,
            "max_results": n,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        timeout=40,
    )
    r.raise_for_status()
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", AX_NS):
        aid = (e.findtext("a:id", "", AX_NS) or "").rsplit("/abs/", 1)[-1]
        cat_el = e.find("arxiv:primary_category", AX_NS)
        out.append(
            {
                "arxiv_id": re.sub(r"v\d+$", "", aid),
                "title": " ".join((e.findtext("a:title", "", AX_NS) or "").split()),
                "abstract": " ".join((e.findtext("a:summary", "", AX_NS) or "").split()),
                "published": e.findtext("a:published", "", AX_NS),
                "category": cat_el.get("term") if cat_el is not None else cat,
            }
        )
    return out


def fetch_alphaxiv() -> list[dict]:
    for size in (100, 50, 25):
        r = SESSION.get(
            "https://api.alphaxiv.org/papers/v3/feed",
            params={"pageNum": 0, "pageSize": size, "sort": "Hot", "interval": "7 Days"},
            timeout=40,
        )
        if r.status_code == 200:
            break
        print(f"  alphaxiv pageSize={size} -> {r.status_code}")
    r.raise_for_status()
    papers = r.json().get("papers", [])
    out = []
    for p in papers:
        m = p.get("metrics") or {}
        topics = p.get("topics") or []
        cats = [t for t in topics if isinstance(t, str) and re.fullmatch(r"cs\.[A-Z]{2}", t)]
        out.append(
            {
                "ax_id": p.get("universal_paper_id") or p.get("id"),
                "title": " ".join((p.get("title") or "").split()),
                "abstract": " ".join((p.get("abstract") or "").split()),
                "published": p.get("publication_date") or p.get("first_publication_date") or "",
                "metrics": m,
                "github_stars": p.get("github_stars"),
                "cat": cats[0] if cats else (topics[0] if topics else ""),
                "ax_rank": len(out),  # alphaXiv 自己的 Hot 排序位置
            }
        )
    return out


def fetch_hf() -> list[dict]:
    r = SESSION.get("https://huggingface.co/api/daily_papers", params={"limit": 100}, timeout=40)
    r.raise_for_status()
    out = []
    for item in r.json():
        p = item.get("paper") or {}
        out.append(
            {
                "arxiv_id": p.get("id"),
                "title": " ".join((p.get("title") or "").split()),
                "abstract": " ".join((p.get("summary") or "").split()),
                "published": p.get("publishedAt") or "",
                "upvotes": p.get("upvotes"),
                "github_stars": p.get("githubStars"),
                "ai_summary": (p.get("ai_summary") or "")[:300],
            }
        )
    return out


# ---------------------------------------------------------------- 工具
def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def pct_rank(values: list[float]) -> list[float]:
    """同源内百分位（0..1），并列取平均名次；缺失值不参与。"""
    s = sorted(values)
    n = len(s)
    if n <= 1:
        return [1.0] * n
    out = []
    for v in values:
        lo = bisect_left(s, v)
        hi = bisect_right(s, v)
        out.append((lo + hi) / 2 / n)
    return out


def norm_score(values: list[float]) -> list[float]:
    """百分位 + 对数幅度各占一半。

    纯百分位会把「249 票 vs 12 票」压成 0.95 vs 0.90，量级信息全丢；
    纯对数又会被极端值带偏。各取一半，既保序又保差距。
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    pcts = pct_rank(values)
    logs = [math.log1p(max(0.0, v)) for v in values]
    ordered = sorted(logs)
    hi = ordered[max(0, math.ceil(0.95 * n) - 1)] or 1.0
    return [0.5 * p + 0.5 * min(1.0, v / hi) for p, v in zip(pcts, logs)]


def spearman(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 3:
        return float("nan")
    ra, rb = pct_rank(a), pct_rank(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else float("nan")


# ---------------------------------------------------------------- 评分
def build_candidates(arxiv: list[dict], ax: list[dict], hf: list[dict]) -> dict[str, dict]:
    cands: dict[str, dict] = {}
    title_key: dict[str, str] = {}

    def touch(key: str) -> dict:
        c = cands.get(key)
        if c is None:
            c = cands[key] = {"key": key, "signals": {}, "sources": set(), "category": ""}
        return c

    for a in arxiv:
        nt = norm_title(a["title"])
        key = a["arxiv_id"]
        title_key.setdefault(nt, key)
        c = touch(key)
        c.update(title=a["title"], abstract=a["abstract"],
                 published=a["published"], category=norm_cat(a["category"]))
        c["sources"].add("arxiv")

    for p in ax:
        nt = norm_title(p["title"])
        key = title_key.get(nt) or f"ax:{p['ax_id']}"
        title_key.setdefault(nt, key)
        c = touch(key)
        if not c.get("title"):
            c.update(title=p["title"], abstract=p.get("abstract", ""),
                     published=p.get("published", ""))
        c["category"] = c.get("category") or norm_cat(p.get("cat", ""))
        c["sources"].add("alphaxiv")
        c["ax_rank"] = p["ax_rank"]
        m = p["metrics"]
        if (v := m.get("total_votes")) is not None:
            c["signals"]["ax_votes"] = float(v)
        if (v := (m.get("visits_count") or {}).get("last_7_days")) is not None:
            c["signals"]["ax_visits"] = float(v)
        if isinstance(p.get("github_stars"), (int, float)):
            c["signals"]["gh_stars"] = float(p["github_stars"])

    for h in hf:
        nt = norm_title(h["title"])
        key = title_key.get(nt) or h.get("arxiv_id") or f"hf:{nt[:24]}"
        title_key.setdefault(nt, key)
        c = touch(key)
        if not c.get("title"):
            c.update(title=h["title"], abstract=h.get("abstract", ""),
                     published=h.get("published", ""))
        c["sources"].add("hf")
        if h.get("upvotes") is not None:
            c["signals"]["hf_upvotes"] = float(h["upvotes"])
        if isinstance(h.get("github_stars"), (int, float)):
            c["signals"].setdefault("gh_stars", float(h["github_stars"]))

    return cands


def score_all(cands: dict[str, dict], weights: dict[str, float]) -> None:
    keys = [k for k, c in cands.items() if c["signals"]]
    for sig in weights:
        present = [cands[k]["signals"][sig] for k in keys if sig in cands[k]["signals"]]
        if not present:
            continue
        mapping = dict(zip([k for k in keys if sig in cands[k]["signals"]], norm_score(present)))
        for k, v in mapping.items():
            cands[k].setdefault("pct", {})[sig] = v

    for k in keys:
        c = cands[k]
        pct = c.get("pct", {})
        # 按「已存在信号」的权重归一，否则缺信号的条目被当成 0 分惩罚，
        # 多源条目会因为加得多而天然占优。
        present_w = sum(w for sig, w in weights.items() if sig in pct)
        raw = sum(w * pct.get(sig, 0.0) for sig, w in weights.items())
        raw = raw / present_w if present_w else 0.0
        dt = parse_dt(c.get("published", ""))
        age = max(0.0, (NOW - dt).total_seconds() / 86400) if dt else 30.0
        c["hot_raw"] = raw
        c["hot"] = raw * math.exp(-age / 14)
        c["n_signals"] = sum(1 for sig in weights if sig in c["signals"])
        c["age_days"] = age


def top_n(cands: dict[str, dict], n: int = 20, min_sources: int = 2, max_per_cat: int = 4):
    pool = [c for c in cands.values() if c.get("hot") and c.get("n_signals", 0) >= min_sources]
    pool.sort(key=lambda c: c["hot"], reverse=True)
    picked, per_cat = [], {}
    for c in pool:
        cat = c.get("category") or "?"
        if per_cat.get(cat, 0) >= max_per_cat:
            continue
        picked.append(c)
        per_cat[cat] = per_cat.get(cat, 0) + 1
        if len(picked) >= n:
            break
    return picked


# ---------------------------------------------------------------- 报告
def fmt_num(v) -> str:
    if v is None:
        return "—"
    return f"{int(v):,}" if v >= 100 else f"{v:g}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    print("拉取 arXiv …")
    arxiv = []
    for i, cat in enumerate(ARXIV_CATS):
        if i:
            time.sleep(3)  # arXiv 礼貌间隔
        batch = fetch_arxiv(cat)
        print(f"  {cat}: {len(batch)}")
        arxiv.extend(batch)

    print("拉取 alphaXiv …")
    ax = fetch_alphaxiv()
    print(f"  feed: {len(ax)}")

    print("拉取 Hugging Face …")
    hf = fetch_hf()
    print(f"  daily_papers: {len(hf)}")

    for name, data in [("arxiv", arxiv), ("alphaxiv", ax), ("hf", hf)]:
        (RAW / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")

    cands = build_candidates(arxiv, ax, hf)
    score_all(cands, WEIGHTS)
    top = top_n(cands)
    ax_sorted = sorted(
        [c for c in cands.values() if "ax_rank" in c], key=lambda c: c["ax_rank"]
    )

    # 与 alphaXiv 官方 Hot 的一致性
    both = [c for c in cands.values() if "ax_rank" in c and c.get("hot")]
    rho = spearman([c["hot"] for c in both], [-c["ax_rank"] for c in both])
    ax_top20 = {c["key"] for c in sorted(both, key=lambda c: c["ax_rank"])[:20]}
    overlap = len({c["key"] for c in top} & ax_top20)

    # 敏感性：权重各 ±20% 后 Top20 重合
    sens = {}
    for sig in WEIGHTS:
        w = dict(WEIGHTS)
        w[sig] *= 1.2
        score_all(cands, w)
        sens[f"{sig} +20%"] = len({c["key"] for c in top_n(cands)} & {c["key"] for c in top})
        score_all(cands, WEIGHTS)

    lines = []
    lines.append("# 全站热榜算法验证报告\n")
    lines.append(f"- 运行时间：{NOW.astimezone().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 候选池：{len(cands)} 条（arXiv {len(arxiv)} / alphaXiv {len(ax)} / HF {len(hf)}，按标题与 arXiv ID 合并）")
    lines.append(f"- 有效打分：{sum(1 for c in cands.values() if c.get('hot'))} 条")
    lines.append(f"- 权重：{WEIGHTS}")
    lines.append("")
    lines.append("## Top 20（双源以上 + 单分类上限 4）\n")
    lines.append("| # | 标题 | 分类 | 热度 | 源数 | alphaXiv 票 | 7 日浏览 | HF 票 | 星 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(top, 1):
        s = c["signals"]
        lines.append(
            f"| {i} | {c.get('title','')[:64]} | {c.get('category','—')} | {c['hot']:.3f} | "
            f"{c['n_signals']} | {fmt_num(s.get('ax_votes'))} | {fmt_num(s.get('ax_visits'))} | "
            f"{fmt_num(s.get('hf_upvotes'))} | {fmt_num(s.get('gh_stars'))} |"
        )
    lines.append("")
    lines.append("## alphaXiv 原生 Hot Top 20（直接可用的基准）\n")
    lines.append("| # | 标题 | 分类 | 热度 | 票 | 7 日浏览 | 星 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(ax_sorted[:20], 1):
        s = c["signals"]
        lines.append(
            f"| {i} | {c.get('title','')[:64]} | {c.get('category','—')} | {c.get('hot',0):.3f} | "
            f"{fmt_num(s.get('ax_votes'))} | {fmt_num(s.get('ax_visits'))} | {fmt_num(s.get('gh_stars'))} |"
        )
    lines.append("")
    lines.append("## 与 alphaXiv 官方 Hot 的一致性\n")
    lines.append(f"- 共同样本：{len(both)} 条")
    lines.append(f"- Spearman ρ = **{rho:.3f}**（1 表示排序完全一致）")
    lines.append(f"- Top20 重合：**{overlap}/20**")
    lines.append("")
    lines.append("## 权重敏感性（各 +20% 后 Top20 与原榜重合数）\n")
    for k, v in sens.items():
        lines.append(f"- {k}：{v}/20")
    lines.append("")
    multi = sum(1 for c in cands.values() if c.get("n_signals", 0) >= 2)
    cat_dist: dict[str, int] = {}
    for c in top:
        cat = c.get("category", "?")
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
    worst = min(sens.items(), key=lambda kv: kv[1]) if sens else ("-", 0)
    if rho >= 0.7:
        verdict = "与 alphaXiv 官方 Hot 高度一致，说明这套聚合没有引入系统性偏差。"
    elif rho >= 0.4:
        verdict = "中等一致：主流趋势抓到了，但排序有实质差异，差异条目要逐条看。"
    else:
        verdict = "分歧较大：要么权重有偏，要么自建榜提供了不同信息，必须人工判读 Top 20。"

    lines.append("## 观察\n")
    lines.append(f"- 双源及以上候选 {multi}/{len(cands)}（{multi / max(1, len(cands)):.0%}）；单源条目被护栏 2 挡在 Top 20 外。")
    lines.append(f"- Top 20 分类分布：{cat_dist}，单分类最多 {max(cat_dist.values()) if cat_dist else 0} 条。")
    lines.append(f"- 与官方榜：ρ={rho:.3f}，{verdict}")
    lines.append(f"- 最敏感权重：{worst[0]}（Top20 重合 {worst[1]}/20），重合越低说明该信号越能左右榜单。")

    (OUT / "report.md").write_text("\n".join(lines), "utf-8")

    print("\n" + "=" * 78)
    print(f"候选 {len(cands)} 条 | Spearman ρ={rho:.3f} | Top20 与 alphaXiv 重合 {overlap}/20")
    print("=" * 78)
    for i, c in enumerate(top, 1):
        s = c["signals"]
        print(f"{i:2d}. [{c.get('category','?'):6s}] hot={c['hot']:.3f} "
              f"src={c['n_signals']} votes={fmt_num(s.get('ax_votes')):>4s} "
              f"vis={fmt_num(s.get('ax_visits')):>7s} hf={fmt_num(s.get('hf_upvotes')):>3s}  "
              f"{c.get('title','')[:58]}")
    print("\n敏感性:", sens)
    print(f"\n报告已写入 {OUT / 'report.md'}")


if __name__ == "__main__":
    main()
