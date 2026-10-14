# -*- encoding: utf-8 -*-
"""文字匹配与片段合成。

帧级结果 → 命中帧序列 → 合并（间隔 < merge_gap 视为同一段）→
前后扩余量 margin → 与相邻段重叠则再合并 → 裁剪到 [0, duration]。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class FrameResult:
    t: float
    texts: list[str]          # 该帧全部识别文本
    matched: list[str]        # 命中的关键词


@dataclass
class Segment:
    start: float
    end: float
    keywords: list[str] = field(default_factory=list)
    sample_texts: list[str] = field(default_factory=list)


# 形近字容错表：细宋体/低分辨率下 OCR 常混淆的字组（实测暝/瞑 混淆率
# 约 5%），匹配时同组字视为等价。按需扩充；组内第一个字为规范代表。
_CONFUSABLE_SETS = [
    "暝瞑",      # 日/目 偏旁（本工具核心关键词场景实测）
    "曰日", "未末", "士土", "己已巳", "王玉",
    "治冶", "设没", "干于千", "令今", "太犬",
    "历厉", "导至", "厮斯", "沾粘",
]
_CONFUSABLE_MAP = {
    ch: group[0] for group in _CONFUSABLE_SETS for ch in group
}


def apply_fuzzy(s: str) -> str:
    return "".join(_CONFUSABLE_MAP.get(ch, ch) for ch in s)


def _normalize(s: str, ignore_case: bool, fuzzy_chars: bool = False) -> str:
    """OCR 可能在中文之间插入空格、把英文大小写混淆：统一去空白 + 可选小写。
    fuzzy_chars=True 时进一步做形近字归一（暝/瞑 等视为同字）。"""
    s = re.sub(r"\s+", "", s)
    if fuzzy_chars:
        s = apply_fuzzy(s)
    return s.lower() if ignore_case else s


def build_matcher(keywords: list[str], use_regex: bool, ignore_case: bool,
                  fuzzy_chars: bool = False):
    """返回 f(texts) -> 命中关键词列表。"""
    if use_regex:
        patterns = [re.compile(k, re.IGNORECASE if ignore_case else 0) for k in keywords]

        def match(texts: list[str]) -> list[str]:
            joined = " ".join(texts)
            hits = [p.pattern for p in patterns if p.search(joined)]
            return hits
        return match

    norm_kw = {_normalize(k, ignore_case, fuzzy_chars): k for k in keywords}

    def match(texts: list[str]) -> list[str]:
        joined = _normalize(" ".join(texts), ignore_case, fuzzy_chars)
        hits = []
        for nk, orig in norm_kw.items():
            if nk and nk in joined:
                hits.append(orig)
        return hits
    return match


def find_segments(
    frame_results: list[FrameResult],
    merge_gap: float,
    margin: float,
    duration: float,
) -> list[Segment]:
    """从帧级结果合成最终片段。

    merge_gap: 命中帧之间的最大空档（小于它的两段并为一段）
    margin:    片段前后保留余量（秒）
    """
    hits = [fr for fr in frame_results if fr.matched]
    if not hits:
        return []

    # 第一轮：按空档合并
    raw: list[Segment] = []
    cur: Segment | None = None
    for fr in hits:
        if cur is None or fr.t - cur.end > merge_gap:
            if cur:
                raw.append(cur)
            cur = Segment(start=fr.t, end=fr.t)
        cur.end = max(cur.end, fr.t)
        cur.keywords = sorted(set(cur.keywords) | set(fr.matched))
        cur.sample_texts.append(" / ".join(fr.texts))
    if cur:
        raw.append(cur)

    # 采样间隔的一半：帧 t 命中意味着文字在 [t-Δ, t+Δ] 内存在，
    # 段边界应向外扩半个采样周期再叠加用户余量，避免切掉首尾帧
    if frame_results:
        step = 0.5
        for i in range(1, len(frame_results)):
            d = frame_results[i].t - frame_results[i - 1].t
            if d > 0:
                step = min(step, d)
                break
    else:
        step = 0.5

    # 第二轮：扩余量
    segs = [
        Segment(
            start=max(0.0, s.start - margin - step / 2),
            end=s.end + margin + step / 2,
            keywords=s.keywords,
            sample_texts=s.sample_texts[:3],
        )
        for s in raw
    ]

    # 第三轮：扩余量后重叠的段再合并（保留全部关键词）
    merged: list[Segment] = []
    for s in segs:
        if merged and s.start <= merged[-1].end:
            m = merged[-1]
            m.end = max(m.end, s.end)
            m.keywords = sorted(set(m.keywords) | set(s.keywords))
            m.sample_texts = (m.sample_texts + s.sample_texts)[:3]
        else:
            merged.append(s)

    # 裁剪到时长范围内
    if duration > 0:
        for s in merged:
            s.end = min(s.end, duration)
        merged = [s for s in merged if s.end - s.start > 0.05]
    return merged
