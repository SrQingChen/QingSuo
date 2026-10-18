# -*- encoding: utf-8 -*-
"""目录扫描：递归发现视频文件。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from ..config import VIDEO_EXTS

ScanCallback = Callable[[str], None]

_NUM_SPLIT = re.compile(r"(\d+)")


def natural_key(name: str) -> list[tuple[int, int, str]]:
    """数字感知排序键：`v_2.mp4` 排在 `v_10.mp4` 之前（字典序会反过来，
    导致多视频任务的成片不是时间轴顺序）。

    每段统一 (类型, 数值, 原文) 三元组：数字段 (0, int, 原文)、文本段
    (1, 0, casefold)。形状必须一致——混用 (int,str) 与 (str,) 会在
    "01.mp4 与 trailer.mp4 同目录"这类常见形态下抛 TypeError。
    数字段排在文本段前，与 ASCII 序一致；`007` 与 `7` 由 (int, 原文)
    二元组保证稳定次序；casefold 消除 `B.mp4` 排在 `a.mp4` 之前的怪序。"""
    key: list[tuple[int, int, str]] = []
    for part in _NUM_SPLIT.split(name):
        if part.isascii() and part.isdigit():
            key.append((0, int(part), part))
        elif part:
            key.append((1, 0, part.casefold()))
    return key


def scan_videos(
    input_dir: str,
    recursive: bool = True,
    on_item: ScanCallback | None = None,
) -> list[dict]:
    """扫描目录下的视频文件，返回 [{path, name, size_mb}]。

    按自然顺序排序（数字感知）。递归时 os.walk 先列父目录文件、再进子目录
    （每个目录内部各自自然排序），不是跨目录全局排序。"""
    root = Path(input_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"目录不存在或不可访问: {input_dir}")

    results: list[dict] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort(key=natural_key)
            for fn in sorted(filenames, key=natural_key):
                if os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                    p = Path(dirpath) / fn
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    item = {
                        "path": str(p),
                        "name": fn,
                        "size_mb": round(size / (1024 * 1024), 1),
                    }
                    results.append(item)
                    if on_item:
                        on_item(item)
    else:
        for p in sorted(root.iterdir(), key=lambda q: natural_key(q.name)):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                item = {
                    "path": str(p),
                    "name": p.name,
                    "size_mb": round(p.stat().st_size / (1024 * 1024), 1),
                }
                results.append(item)
                if on_item:
                    on_item(item)
    return results
