# -*- encoding: utf-8 -*-
"""目录扫描：递归发现视频文件。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..config import VIDEO_EXTS

ScanCallback = Callable[[str], None]


def scan_videos(
    input_dir: str,
    recursive: bool = True,
    on_item: ScanCallback | None = None,
) -> list[dict]:
    """扫描目录下的视频文件，返回 [{path, name, size_mb}]（按名称排序）。"""
    root = Path(input_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"目录不存在或不可访问: {input_dir}")

    results: list[dict] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
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
        for p in sorted(root.iterdir()):
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
