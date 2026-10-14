# -*- encoding: utf-8 -*-
"""事件日志：环形缓冲 + 文件双写（借鉴 IndexTTS webui_app/context.py 的 EventLog）。

内存 deque 供 API 即时读取，文件副本保证进程退出后仍可排障。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional


class EventLog:
    def __init__(self, maxlen: int = 500, log_file: Optional[Path] = None):
        self._items: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._log_file = log_file
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)

    def add(self, source: str, message: str, level: str = "info") -> dict:
        item = {
            "ts": time.time(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": source,
            "level": level,  # info / warn / error / success
            "message": message,
        }
        with self._lock:
            self._items.append(item)
        if self._log_file is not None:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{item['time']}] [{source}] [{level}] {message}\n")
            except OSError:
                pass
        return item

    def list(self, limit: int = 100) -> list[dict]:
        with self._lock:
            items = list(self._items)
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
