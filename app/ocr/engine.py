# -*- encoding: utf-8 -*-
"""OCR 引擎：生命周期管理（借鉴 IndexTTS webui_app/services/engine.py 的 TTSEngine）。

核心约定：
- 单实例 + RLock：load/unload/换档互斥；推理本身不加锁
  （onnxruntime session 的 Run 是线程安全的，锁住会白白浪费多视频并行）。
- 同一时刻只驻留一档模型：换档 = 先卸载再加载，绝不共存
  （参考项目实测教训：8GB 显存下多模型共存必 OOM / 互踩）。
- busy 计数保护：有推理在飞时拒绝换档/卸载（对应 runner↔engine 双向互斥）。
- 所有状态变化走事件流，绝不静默回退。
"""
from __future__ import annotations

import gc
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from ..config import (
    DEFAULT_OCR_LEVEL,
    OCR_LEVELS,
    ocr_level_ready,
)
from ..events import EventLog


@dataclass
class EngineStats:
    """显式统计字段，UI 直接消费（借鉴 EngineStats dataclass）。"""
    level: Optional[str] = None
    loaded: bool = False
    load_seconds: float = 0.0
    infer_count: int = 0
    total_infer_seconds: float = 0.0
    last_infer_ms: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        avg = (
            self.total_infer_seconds / self.infer_count * 1000
            if self.infer_count else 0.0
        )
        return {
            "level": self.level,
            "level_name": OCR_LEVELS[self.level]["name"] if self.level else "",
            "loaded": self.loaded,
            "load_seconds": round(self.load_seconds, 2),
            "infer_count": self.infer_count,
            "avg_infer_ms": round(avg, 1),
            "last_infer_ms": round(self.last_infer_ms, 1),
            "error": self.error,
        }


EventHandler = Callable[[str, str, str], None]  # (event, message, level)


class OCREngine:
    def __init__(self, event_log: EventLog, threads: int = 0):
        self._lock = threading.RLock()
        self._ocr = None  # RapidOCR 实例
        self._busy = 0    # 在飞推理计数（用于换档互斥）
        self.stats = EngineStats()
        self._event_handler: Optional[EventHandler] = None
        self.threads = threads or (os.cpu_count() or 4)
        self.log = event_log

    # ---------- 事件 ----------

    def set_event_handler(self, handler: EventHandler) -> None:
        self._event_handler = handler

    def _emit(self, event: str, message: str, level: str = "info") -> None:
        self.log.add("engine", message, level)
        if self._event_handler:
            try:
                self._event_handler(event, message, level)
            except Exception:
                pass

    # ---------- 生命周期 ----------

    def load(self, level: str) -> None:
        """加载指定档位。已加载同档则跳过；已加载其他档则先卸载。"""
        with self._lock:
            if self._busy > 0:
                raise RuntimeError("有任务正在使用当前模型，禁止换档/卸载")
            if self.stats.loaded and self.stats.level == level:
                return
            if self.stats.loaded:
                self._unload_locked(reason="换档")

            ok, msg = ocr_level_ready(level)
            if not ok:
                self.stats.error = msg
                self._emit("error", msg, "error")
                raise RuntimeError(msg)

            spec = OCR_LEVELS[level]
            self._emit("loading", f"正在加载 OCR 模型：{spec['name']} …")
            t0 = time.time()
            try:
                # 延迟导入：进程启动时不触碰 onnxruntime，加快冷启动
                from rapidocr_onnxruntime import RapidOCR

                intra = max(1, self.threads)
                self._ocr = RapidOCR(
                    det_model_path=str(spec["det"]),
                    rec_model_path=str(spec["rec"]),
                    intra_op_num_threads=intra,
                )
                # 预热一次（首次推理含图优化，避免首个视频的帧被慢启动拖爆）
                warm = np.zeros((64, 256, 3), dtype=np.uint8)
                self._ocr(warm)
            except Exception as e:
                self._ocr = None
                self.stats.error = str(e)
                self._emit("error", f"模型加载失败：{e}", "error")
                raise

            self.stats.level = level
            self.stats.loaded = True
            self.stats.load_seconds = time.time() - t0
            self.stats.error = ""
            self._emit(
                "loaded",
                f"{spec['name']} 加载完成（{self.stats.load_seconds:.1f}s，"
                f"CPU 线程 {intra}）",
                "success",
            )

    def unload(self, reason: str = "手动卸载") -> None:
        with self._lock:
            if self._busy > 0:
                raise RuntimeError("有任务正在使用当前模型，禁止换档/卸载")
            self._unload_locked(reason)

    def _unload_locked(self, reason: str) -> None:
        """卸载三板斧（借鉴 TTSEngine）：置 None → gc → 释放。"""
        if not self.stats.loaded and self._ocr is None:
            return
        level = self.stats.level
        self._ocr = None
        self.stats.loaded = False
        self.stats.level = None
        gc.collect()
        self._emit("unloaded", f"已卸载 OCR 模型（{level}），原因：{reason}")

    def ensure_loaded(self, level: str) -> None:
        """任务侧入口：保证指定档位就绪（懒加载）。"""
        if self.stats.loaded and self.stats.level == level:
            return
        self.load(level)

    # ---------- 推理 ----------

    def recognize(self, img: np.ndarray) -> list[tuple[str, float]]:
        """识别一帧，返回 [(text, score), ...]。onnxruntime Run 线程安全，
        这里不加引擎锁 —— 锁只在 load/unload 门口。"""
        if not self.stats.loaded or self._ocr is None:
            raise RuntimeError("OCR 引擎未加载")
        with self._busy_guard(self):
            t0 = time.time()
            try:
                result, _ = self._ocr(img)
            except Exception as e:
                self.stats.error = str(e)
                self._emit("error", f"推理失败：{e}", "error")
                raise
            dt = time.time() - t0
            self.stats.infer_count += 1
            self.stats.total_infer_seconds += dt
            self.stats.last_infer_ms = dt * 1000
            self.stats.error = ""
            if result is None:
                return []
            return [(r[1], float(r[2])) for r in result if r[1]]

    class _busy_guard:
        """busy 计数器：进入 +1 / 退出 -1，异常安全。"""

        def __init__(self, engine: "OCREngine"):
            self.engine = engine

        def __enter__(self):
            with self.engine._lock:
                self.engine._busy += 1
            return self

        def __exit__(self, *exc):
            with self.engine._lock:
                self.engine._busy -= 1
            return False

    # ---------- 状态 ----------

    def snapshot(self) -> dict:
        return {
            "levels": {
                k: {"name": v["name"], "note": v["note"]}
                for k, v in OCR_LEVELS.items()
            },
            "default_level": DEFAULT_OCR_LEVEL,
            "stats": self.stats.to_dict(),
        }
