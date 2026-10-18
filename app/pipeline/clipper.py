# -*- encoding: utf-8 -*-
"""剪辑导出：ffmpeg 精确剪切（NVENC 硬编，回退 libx264）+ 智能合并拼接。

合并策略（concat_smart）：
- 各片段流参数完全一致 → concat demuxer 流拼接（快、无损）；
- 不一致（混合分辨率 / 编码 / 音轨参数）→ concat filter 在滤镜图内
  统一 scale/pad/fps/aresample 后重编码，段数多时自动分批规避命令行超长；
- 无论走哪条路径，删除散片段前都用 ffprobe 校验成片时长（≥98% 片段总长）
  与音视频流存在性——ffmpeg 流拼接在参数不匹配时可能"成功"产出坏文件。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .sampler import _creation_flags

# concat filter 单批最大段数：Windows 命令行约 32k 字符上限，
# 每段两路滤镜链约 200+ 字符，50 段一批留足余量
_FILTER_BATCH = 50


def _run(cmd: list[str], timeout: int = 1800) -> None:
    p = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, creationflags=_creation_flags(),
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 导出失败: {(p.stderr or '')[-800:]}")


def _parse_rational(s: str) -> float:
    """解析 ffprobe 的 `num/den` 有理数（avg_frame_rate / sample_aspect_ratio）。"""
    if not s or s in ("N/A", "0/0", "0:1"):
        return 0.0
    for sep in ("/", ":"):
        if sep in s:
            num, _, den = s.partition(sep)
            try:
                d = float(den)
                return float(num) / d if d else 0.0
            except ValueError:
                return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


@dataclass
class MediaProfile:
    """ffprobe 探测的流参数快照（用于合并前的兼容性判断）。"""
    vcodec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    pix_fmt: str = ""
    sar: float = 1.0          # 采样宽高比（0/未知按 1:1 处理）
    acodec: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration: float = 0.0

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec)

    def compat_key(self) -> tuple:
        """流拼接（-c copy concat）安全所需的参数元组；fps 取整容忍
        30000/1001 与 29.97 这类有理数表示差异。"""
        return (
            self.vcodec, self.width, self.height, round(self.fps, 3),
            self.pix_fmt, round(self.sar, 3),
            self.acodec, self.sample_rate, self.channels,
        )


class Clipper:
    def __init__(self, use_nvenc: bool):
        self.encoder_args = (
            ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23"]
            if use_nvenc
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
        )
        self._probe_cache: dict[str, MediaProfile] = {}

    # ---------- 探测 ----------

    def probe_media(self, path: str) -> MediaProfile:
        """ffprobe 读取首个视频流 + 首个音频流参数（按路径缓存）。"""
        key = str(path)
        if key in self._probe_cache:
            return self._probe_cache[key]
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate,"
            "pix_fmt,sample_aspect_ratio,sample_rate,channels"
            ":format=duration",
            "-of", "json", path,
        ]
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, creationflags=_creation_flags(),
        )
        if p.returncode != 0:
            raise RuntimeError(f"ffprobe 失败: {(p.stderr or '')[:500]}")
        data = json.loads(p.stdout)
        prof = MediaProfile()
        for st in data.get("streams", []):
            if st.get("codec_type") == "video" and not prof.vcodec:
                prof.vcodec = st.get("codec_name") or ""
                prof.width = int(st.get("width") or 0)
                prof.height = int(st.get("height") or 0)
                prof.fps = _parse_rational(st.get("avg_frame_rate", ""))
                prof.pix_fmt = st.get("pix_fmt") or ""
                sar = _parse_rational(st.get("sample_aspect_ratio", ""))
                prof.sar = sar if sar > 0 else 1.0
            elif st.get("codec_type") == "audio" and not prof.acodec:
                prof.acodec = st.get("codec_name") or ""
                prof.sample_rate = int(st.get("sample_rate") or 0)
                prof.channels = int(st.get("channels") or 0)
        try:
            prof.duration = float(
                data.get("format", {}).get("duration", 0) or 0)
        except (ValueError, TypeError):
            prof.duration = 0.0
        self._probe_cache[key] = prof
        return prof

    # ---------- 剪辑 ----------

    def clip(
        self,
        src: str,
        start: float,
        end: float,
        dst: str,
        fast_copy: bool = False,
        ensure_audio: bool = False,
    ) -> str:
        """导出 [start, end] 片段，返回实际使用的导出模式。

        fast_copy=False（默认，精确）：-ss 前置 + 重编码，帧精确。
        fast_copy=True（快速）：流复制，秒级完成但只能落在关键帧上。
        ensure_audio=True（成片模式）：源无音轨时强制走重编码并补静音轨
        （流复制无法混入 lavfi 静音源），保证所有片段流布局一致、可拼接。
        """
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        dur = max(0.05, end - start)
        has_audio = self.probe_media(src).has_audio
        pad_silent = ensure_audio and not has_audio
        use_fast = fast_copy and not pad_silent

        if use_fast:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
                "-c", "copy", "-avoid_negative_ts", "make_zero",
                dst,
            ]
            mode = "copy"
        else:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", src,
            ]
            if pad_silent:
                # 显式 -map 必不可少：不 map 时 ffmpeg 的默认流选择可能挑
                # 声道数更多的 anullsrc，把真实音频替换成静音。参数固定
                # 48k/stereo，使各片段补出的静音轨互相一致。
                cmd += [
                    "-f", "lavfi", "-t", f"{dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-map", "0:v:0", "-map", "1:a:0",
                ]
            cmd += [
                "-t", f"{dur:.3f}",
                *self.encoder_args,
                "-c:a", "aac", "-b:a", "192k",
                dst,
            ]
            mode = "reenc_silent" if pad_silent else "reenc"
        _run(cmd)
        return mode

    # ---------- 合并 ----------

    def concat(self, files: list[str], dst: str, timeout: int = 1800) -> None:
        """concat demuxer 流拼接（仅限流参数一致的片段）。"""
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
            ], timeout=timeout)
        finally:
            try:
                list_file.unlink()
            except OSError:
                pass

    def concat_smart(
        self, files: list[str], dst: str, expected_duration: float,
    ) -> str:
        """按时间轴顺序合并片段，返回实际路径（"copy" / "filter"）。

        expected_duration：各片段时长之和，用于合并后的成片校验；
        校验不过会抛异常，调用方据此保留散片段不清理。
        """
        if not files:
            return ""
        profiles = [self.probe_media(f) for f in files]
        missing_audio = [f for f, p in zip(files, profiles) if not p.has_audio]
        if missing_audio:
            raise RuntimeError(
                f"内部错误：{len(missing_audio)} 个片段缺失音轨，无法合并："
                f"{Path(missing_audio[0]).name}")

        # 重编码合并可能明显慢于流复制，按总时长放宽超时
        timeout = max(1800, int(expected_duration * 6) + 300)

        baseline = profiles[0]
        try:
            if all(p.compat_key() == baseline.compat_key() for p in profiles):
                self.concat(files, dst, timeout=timeout)
                path_used = "copy"
            else:
                self._concat_filter(files, dst, baseline, timeout=timeout)
                path_used = "filter"
            self._validate_merged(dst, expected_duration)
        except Exception:
            # 合并/校验失败不留半成品成片——散片段仍完整保留在输出目录
            try:
                Path(dst).unlink()
            except OSError:
                pass
            raise
        return path_used

    def _concat_filter(
        self, files: list[str], dst: str, baseline: MediaProfile,
        timeout: int = 1800,
    ) -> None:
        """concat filter 重编码合并：混合分辨率/SAR/帧率/音频参数时的兜底。

        画面统一到基准片段（首个片段）的宽高与帧率：等比缩放 + 黑边补齐
        （避免不同宽高比来源被拉伸），音频统一重采样。段数超过单批上限时
        分批合并，再把各批（编码参数天然一致）流拼接成最终成片。
        """
        w = baseline.width - baseline.width % 2
        h = baseline.height - baseline.height % 2
        if w <= 0 or h <= 0:
            raise RuntimeError(f"无法确定合并基准分辨率: {baseline.width}x{baseline.height}")
        fps = round(baseline.fps, 3)
        rate = baseline.sample_rate or 48000

        batches = [files[i:i + _FILTER_BATCH]
                   for i in range(0, len(files), _FILTER_BATCH)]
        parts: list[str] = []
        attempted: list[str] = []
        try:
            for bi, batch in enumerate(batches):
                single = len(batches) == 1
                out = dst if single else str(
                    Path(dst).with_name(f"{Path(dst).stem}.part{bi:03d}.mp4"))
                attempted.append(out)
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                for f in batch:
                    cmd += ["-i", f]

                chains: list[str] = []
                labels: list[str] = []
                for i in range(len(batch)):
                    fps_filter = f",fps={fps}" if fps > 0 else ""
                    chains.append(
                        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease"
                        f":force_divisible_by=2,"
                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
                        f"{fps_filter},format=yuv420p[v{i}]")
                    chains.append(
                        f"[{i}:a]aresample={rate},"
                        f"aformat=sample_rates={rate}:channel_layouts=stereo[a{i}]")
                    labels += [f"[v{i}]", f"[a{i}]"]
                chains.append(
                    "".join(labels) + f"concat=n={len(batch)}:v=1:a=1[vout][aout]")

                cmd += [
                    "-filter_complex", ";".join(chains),
                    "-map", "[vout]", "-map", "[aout]",
                    *self.encoder_args,
                    "-c:a", "aac", "-b:a", "192k",
                    out,
                ]
                _run(cmd, timeout=timeout)
                if not single:
                    parts.append(out)
            if parts:
                # 各批流拼接也纳入 try：失败时连同 .part 中间件一并清掉
                self.concat(parts, dst, timeout=timeout)
        except Exception:
            # 中途失败时清掉已写出的批次文件（含写了一半的当前批次）
            for part in attempted:
                try:
                    Path(part).unlink()
                except OSError:
                    pass
            raise

        for part in parts:
            try:
                Path(part).unlink()
            except OSError:
                pass

    def _validate_merged(self, dst: str, expected_duration: float) -> None:
        """删除散片段前的最后防线：成片必须有时长合理的音视频流。"""
        prof = self.probe_media(dst)
        if not prof.vcodec:
            raise RuntimeError("合并校验失败：成片没有视频流")
        if not prof.acodec:
            raise RuntimeError("合并校验失败：成片没有音频流")
        if expected_duration > 0 and prof.duration > 0:
            if prof.duration < 0.98 * expected_duration:
                raise RuntimeError(
                    f"合并校验失败：成片时长 {prof.duration:.1f}s "
                    f"远小于片段总长 {expected_duration:.1f}s，疑似拼接损坏")
