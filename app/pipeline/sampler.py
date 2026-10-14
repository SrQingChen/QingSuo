# -*- encoding: utf-8 -*-
"""帧采样器：ffmpeg 子进程抽帧 → rawvideo 管道 → numpy 帧。

- 解码：优先 `-hwaccel cuda`（NVDEC）。ffmpeg 在硬解初始化失败时会自动
  回退软解（非致命），因此可以放心常开；GPU 失败不会让任务挂掉。
- 缩放：超过 OCR_MAX_WIDTH 的帧在解码管线内缩放（GPU/CPU 缩放由 ffmpeg
  内部选择），OCR 不需要 4K 原图，这一步能省数倍带宽与计算。
- 取消：check_cancel 回调在每帧边界检查，及时 kill 子进程。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import numpy as np

from ..config import OCR_MAX_WIDTH

_CANCEL_FLAG = Callable[[], bool]
_NOOP = lambda: False  # noqa: E731


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def probe(path: str) -> VideoInfo:
    """ffprobe 读取宽高/时长/帧率。时长未知时返回 0（进度条降级为计数）。"""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate:format=duration",
        "-of", "json", path,
    ]
    p = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, creationflags=_creation_flags(),
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {p.stderr[:500]}")
    data = json.loads(p.stdout)
    stream = data["streams"][0]
    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except (ValueError, TypeError):
        pass

    def _parse_rate(s: str) -> float:
        m = re.match(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", s or "")
        if not m:
            return 0.0
        num, den = float(m.group(1)), float(m.group(2))
        return num / den if den else 0.0

    return VideoInfo(
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        duration=duration,
        fps=_parse_rate(stream.get("avg_frame_rate", "")),
    )


class FrameSampler:
    def __init__(self, hwaccel: bool = True):
        self.hwaccel = hwaccel

    def sample(
        self,
        path: str,
        sample_fps: float,
        check_cancel: _CANCEL_FLAG = _NOOP,
    ) -> Iterator[tuple[float, np.ndarray]]:
        """按 sample_fps 抽帧，产出 (时间戳秒, BGR ndarray)。

        时间戳 = 帧序号 / sample_fps（fps 滤镜保证恒定速率输出，
        首帧 t=0，对字幕定位精度 = 1/sample_fps，足够使用）。
        """
        info = probe(path)
        if info.width <= 0 or info.height <= 0:
            raise RuntimeError(f"无法读取视频尺寸: {path}")

        # 计算缩放目标（偶数对齐，ffmpeg 要求）
        tw = min(info.width, OCR_MAX_WIDTH)
        tw -= tw % 2
        th = round(info.height * tw / info.width)
        th -= th % 2
        if th <= 0:
            th = 2

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.hwaccel:
            cmd += ["-hwaccel", "cuda"]
        cmd += [
            "-i", path,
            "-vf", f"fps={sample_fps},scale={tw}:{th}",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        frame_bytes = tw * th * 3
        frame_idx = 0
        try:
            assert proc.stdout is not None
            while True:
                if check_cancel():
                    raise _Cancelled()
                buf = proc.stdout.read(frame_bytes)
                if not buf:
                    break
                if len(buf) < frame_bytes:
                    # 流尾残帧：继续读直到凑齐或 EOF
                    while len(buf) < frame_bytes:
                        more = proc.stdout.read(frame_bytes - len(buf))
                        if not more:
                            break
                        buf += more
                    if len(buf) < frame_bytes:
                        break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(th, tw, 3)
                yield frame_idx / sample_fps, frame
                frame_idx += 1
        finally:
            proc.kill()
            proc.wait(timeout=10)
            for s in (proc.stdout, proc.stderr):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass


class _Cancelled(Exception):
    """内部取消信号（不与外部异常混用）。"""


def is_cancelled(exc: BaseException) -> bool:
    return isinstance(exc, _Cancelled)


CancelledError = _Cancelled
