# -*- encoding: utf-8 -*-
"""剪辑导出：ffmpeg 精确剪切（NVENC 硬编，回退 libx264）+ 合并拼接。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .sampler import _creation_flags


def _run(cmd: list[str], timeout: int = 1800) -> None:
    p = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, creationflags=_creation_flags(),
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 导出失败: {(p.stderr or '')[-800:]}")


class Clipper:
    def __init__(self, use_nvenc: bool):
        self.encoder_args = (
            ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23"]
            if use_nvenc
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
        )

    def clip(
        self,
        src: str,
        start: float,
        end: float,
        dst: str,
        fast_copy: bool = False,
    ) -> None:
        """导出 [start, end] 片段。

        fast_copy=False（默认，精确）：-ss 前置 + 重编码，帧精确。
        fast_copy=True（快速）：流复制，秒级完成但只能落在关键帧上。
        """
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        dur = max(0.05, end - start)
        if fast_copy:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
                "-c", "copy", "-avoid_negative_ts", "make_zero",
                dst,
            ]
        else:
            # -ss 放 -i 前是快速定位，重编码模式下输出帧精确对齐目标时间点
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
                *self.encoder_args,
                "-c:a", "aac", "-b:a", "192k",
                dst,
            ]
        _run(cmd)

    def concat(self, files: list[str], dst: str) -> None:
        """合并片段（同编码参数的 ts/mp4 可安全流拼接）。"""
        if not files:
            return
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        list_file = Path(dst).with_suffix(".concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            # concat 清单里的特殊字符需要单引号转义
            for fp in files:
                f.write("file '" + fp.replace("'", "'\\''") + "'\n")
        try:
            _run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c", "copy", dst,
            ])
        finally:
            try:
                list_file.unlink()
            except OSError:
                pass
