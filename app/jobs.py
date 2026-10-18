# -*- encoding: utf-8 -*-
"""任务运行器：后台线程执行"扫描→抽帧→OCR→匹配→剪辑"全流程。

调度约定（对应参考项目的 runner↔engine 双向互斥）：
- 引擎侧：OCREngine.load/unload 在 busy>0 时拒绝（引擎反查任务）。
- 任务侧：派发新任务时若引擎忙于换档则等待锁（任务反查引擎）。
- 同一时刻默认只跑一个任务（OCR 会吃满 CPU，多任务并行只会互相拖慢，
  且 onnxruntime 线程池已经吃满所有核）。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import LOGS_DIR, OCR_LEVELS, get_device
from .events import EventLog
from .ocr.engine import OCREngine
from .pipeline import matcher as M
from .pipeline.clipper import Clipper
from .pipeline.sampler import FrameSampler, is_cancelled, probe
from .pipeline.scanner import scan_videos

# 相似帧跳过：64x64 灰度签名。字幕静止时整段视频只做一次 OCR，
# 这是"几乎无开销"的最大单项优化（典型字幕场景可省 80%+ 推理量）。
#
# 相似判定的实测教训（2026-10 静止画面漏检事故）：
# 细笔画字幕经 64x64 降采样平均后，均值差分仅 ~0.35（远低于阈值 4.0），
# 单看均值会把"字幕刚出现"的帧误判为静止 → 整个关键词时段被跳过。
# 因此双重判定：整帧均值（抗噪）+ 底部 1/3（字幕高发区）的"显著变化
# 像素占比"（对小幅局部变化敏感：实测字幕出现时 1.86%，静止时 0%）。
# 另加强制兜底：距上次真实 OCR 超过 force_ocr_every 秒必定重识别。
_HASH_W, _HASH_H = 64, 64
_SIMILAR_MEAN_THRESHOLD = 4.0    # 整帧平均每像素差
_CHANGED_PIXEL_LEVEL = 25        # 单像素视为"显著变化"的差值
_CHANGED_FRACTION_THRESHOLD = 0.01  # 底部区域显著变化像素占比超过 1% 即重识别


def _frame_signature(img: np.ndarray) -> np.ndarray:
    small = cv2.resize(img, (_HASH_W, _HASH_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def _frames_similar(a: np.ndarray, b: np.ndarray) -> bool:
    if a.shape != b.shape:
        return False
    if float(np.abs(a - b).mean()) >= _SIMILAR_MEAN_THRESHOLD:
        return False
    bottom = a.shape[0] * 2 // 3
    d_bottom = np.abs(a[bottom:] - b[bottom:])
    frac = float((d_bottom > _CHANGED_PIXEL_LEVEL).mean())
    return frac < _CHANGED_FRACTION_THRESHOLD


@dataclass
class JobParams:
    input_dir: str
    output_dir: str
    keywords: list[str]
    ocr_level: str = "fast"
    sample_fps: float = 2.0
    margin: float = 0.5
    merge_gap: float = 1.0
    use_regex: bool = False
    ignore_case: bool = True
    fuzzy_chars: bool = True
    fast_copy: bool = False
    merged_output: bool = True      # 成片模式：按时间轴拼成单个视频并清理散片段
    skip_similar: bool = True
    recursive: bool = True
    # 相似跳过的强制兜底间隔：距上次真实 OCR 超过该秒数必定重识别，
    # 保证任何关键词出现 ≤2s 内必被采样到（可调小提高保障）。
    force_ocr_every: float = 2.0
    # 调试模式：逐帧记录 OCR 结果到 logs/debug/{job_id}/{视频名}.jsonl
    debug: bool = False


@dataclass
class FileProgress:
    path: str
    name: str
    status: str = "pending"      # pending/processing/done/skipped/failed/cancelled
    duration: float = 0.0
    scanned: float = 0.0
    segments: list[dict] = field(default_factory=list)
    clip_paths: list[str] = field(default_factory=list)
    error: str = ""
    last_text: str = ""          # 最近一帧 OCR 文本（前端实时预览）
    # 实时计数器（处理过程中即可见，段数 segments 要等剪辑完成才有）
    hit_frames: int = 0          # 命中关键词的采样帧数
    ocr_frames: int = 0          # 实际执行 OCR 推理的帧数
    skip_frames: int = 0         # 被相似跳过的帧数

    def to_dict(self) -> dict:
        return {
            "path": self.path, "name": self.name, "status": self.status,
            "duration": round(self.duration, 1), "scanned": round(self.scanned, 1),
            "segments": self.segments, "clip_paths": self.clip_paths,
            "error": self.error, "last_text": self.last_text,
            "hit_frames": self.hit_frames, "ocr_frames": self.ocr_frames,
            "skip_frames": self.skip_frames,
        }


@dataclass
class Job:
    params: JobParams
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"       # queued/running/done/failed/cancelled
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    files: list[FileProgress] = field(default_factory=list)
    merged_file: str = ""
    clips_cleaned: bool = False     # 成片模式下散片段是否已自动清理
    error: str = ""

    def to_dict(self) -> dict:
        total_dur = sum(f.duration for f in self.files)
        total_scan = sum(
            f.scanned for f in self.files if f.status in ("done", "processing"))
        return {
            "id": self.id,
            "status": self.status,
            "params": self.params.__dict__,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "progress": round(total_scan / total_dur, 3) if total_dur > 0 else 0.0,
            "files": [f.to_dict() for f in self.files],
            "merged_file": self.merged_file,
            "clips_cleaned": self.clips_cleaned,
            "error": self.error,
            "total_segments": sum(len(f.segments) for f in self.files),
            # 实时命中（扫描过程中持续增长；segments 要等剪辑完成才统计）
            "live_hits": sum(f.hit_frames for f in self.files),
        }


class JobRunner:
    def __init__(self, engine: OCREngine, event_log: EventLog):
        self.engine = engine
        self.log = event_log
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: Optional[Job] = None
        self._cancel_flags: dict[str, bool] = {}
        self._worker: Optional[threading.Thread] = None
        self._wakeup = threading.Event()

    # ---------- 对外接口 ----------

    def submit(self, params: JobParams) -> str:
        if not params.keywords:
            raise ValueError("关键词不能为空")
        if not Path(params.input_dir).is_dir():
            raise ValueError(f"输入目录不存在: {params.input_dir}")
        Path(params.output_dir).mkdir(parents=True, exist_ok=True)
        if params.ocr_level not in OCR_LEVELS:
            raise ValueError(f"未知 OCR 档位: {params.ocr_level}")

        job = Job(params=params)
        with self._lock:
            self._jobs[job.id] = job
        self.log.add("jobs", f"任务已提交：关键词 {params.keywords} @ {params.ocr_level}")
        self._ensure_worker()
        self._wakeup.set()
        return job.id

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        with self._lock:
            self._cancel_flags[job_id] = True
        if job.status in ("queued", "running"):
            self.log.add("jobs", f"任务 {job_id[:8]} 请求取消", "warn")
        return True

    def has_active_jobs(self) -> bool:
        with self._lock:
            if self._active is not None:
                return True
            return any(j.status == "queued" for j in self._jobs.values())

    # ---------- 工作线程 ----------

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run_loop, daemon=True, name="job-runner")
        self._worker.start()

    def _run_loop(self) -> None:
        while True:
            job = self._next_queued()
            if job is None:
                self._wakeup.wait(timeout=1.0)
                self._wakeup.clear()
                continue
            self._execute(job)

    def _next_queued(self) -> Optional[Job]:
        with self._lock:
            for j in self._jobs.values():
                if j.status == "queued":
                    j.status = "running"
                    self._active = j
                    return j
        return None

    def _cancelled(self, job: Job) -> bool:
        with self._lock:
            return self._cancel_flags.get(job.id, False)

    def _execute(self, job: Job) -> None:
        p = job.params
        try:
            self.log.add("jobs", f"任务 {job.id[:8]} 开始：扫描 {p.input_dir}")
            videos = scan_videos(p.input_dir, recursive=p.recursive)
            job.files = [
                FileProgress(path=v["path"], name=v["name"]) for v in videos
            ]
            if not videos:
                job.status = "done"
                self.log.add("jobs", "目录中未发现视频文件", "warn")
                return
            self.log.add("jobs", f"发现 {len(videos)} 个视频，开始处理")

            # 引擎就绪（懒加载 + 任务与换档互斥）
            self.engine.ensure_loaded(p.ocr_level)

            dev = get_device()
            sampler = FrameSampler(hwaccel=dev.has_nvidia_gpu)
            clipper = Clipper(use_nvenc=dev.nvenc_available)
            match = M.build_matcher(p.keywords, p.use_regex, p.ignore_case, p.fuzzy_chars)

            # 同一次出现的相邻命中帧天然相距一个采样间隔；merge_gap 小于它
            # 只会把连续命中切成碎片（如 2s 采样 + 默认 1s gap = 每个命中帧
            # 一段，全靠扩余量后的重叠回并补救），这里直接放宽到位。
            interval = 1.0 / p.sample_fps if p.sample_fps > 0 else 0.5
            merge_gap = p.merge_gap
            if interval > merge_gap:
                merge_gap = interval
                self.log.add(
                    "jobs",
                    f"采样间隔 {interval:.2f}s 大于合并间隔 {p.merge_gap:.2f}s，"
                    f"已自动放宽合并间隔为 {interval:.2f}s", "info")

            # 递归扫描时不同子目录可能同名：重名文件的片段前缀加文件序号
            # 消歧，并确保不与任何真实 stem 或其他消歧前缀撞车（撞名会让
            # 两个源的片段互相覆盖且无告警）
            stems = [Path(v["path"]).stem for v in videos]
            stem_counts = Counter(stems)
            used: set[str] = set(stems)
            prefixes: list[str] = []
            for idx, stem in enumerate(stems):
                if stem_counts[stem] > 1:
                    cand = f"{stem}_{idx:02d}"
                    while cand in used:
                        cand += "_"
                    used.add(cand)
                    prefixes.append(cand)
                else:
                    prefixes.append(stem)

            # (文件序号, 段起始, 段时长, 片段路径)。合并前显式按时间轴排序，
            # 让成片顺序与处理顺序彻底解耦——即使未来引入并行处理也不受影响。
            all_clips: list[tuple[int, float, float, str]] = []
            for file_index, fp in enumerate(job.files):
                if self._cancelled(job):
                    raise _JobCancelled()
                fp.status = "processing"
                clips = self._process_one(
                    job, fp, file_index, prefixes[file_index], merge_gap,
                    interval, sampler, match, clipper, all_clips)
                fp.status = "done"
                fp.last_text = ""
                self.log.add(
                    "jobs",
                    f"{fp.name}: {len(clips)} 个片段",
                    "success" if clips else "info",
                )

            if p.merged_output and all_clips:
                if self._cancelled(job):
                    raise _JobCancelled()
                ordered = sorted(all_clips, key=lambda c: (c[0], c[1]))
                paths = [c[3] for c in ordered]
                total_dur = sum(c[2] for c in ordered)
                merged = str(Path(p.output_dir) / _merged_name(job))
                self.log.add(
                    "jobs",
                    f"成片模式：按时间轴合并 {len(paths)} 个片段"
                    f"（共 {total_dur:.1f}s）…")
                path_used = clipper.concat_smart(paths, merged, total_dur)
                job.merged_file = merged
                self.log.add(
                    "jobs",
                    f"合并完成（{'流复制拼接' if path_used == 'copy' else '参数统一后重编码拼接'}）"
                    f" → {merged}", "success")
                self._cleanup_clips(job, paths)

            job.status = "done"
            job.finished_at = time.time()
            self.log.add(
                "jobs",
                f"任务 {job.id[:8]} 完成：共 {sum(len(f.segments) for f in job.files)} 段",
                "success",
            )
        except _JobCancelled:
            job.status = "cancelled"
            job.finished_at = time.time()
            for f in job.files:
                if f.status in ("pending", "processing"):
                    f.status = "cancelled"
            self.log.add("jobs", f"任务 {job.id[:8]} 已取消", "warn")
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.finished_at = time.time()
            self.log.add("jobs", f"任务 {job.id[:8]} 失败：{e}", "error")
        finally:
            with self._lock:
                self._active = None
                self._cancel_flags.pop(job.id, None)

    def _process_one(
        self, job, fp: FileProgress, file_index: int, prefix: str,
        merge_gap: float, interval: float, sampler, match, clipper,
        all_clips: list[tuple[int, float, float, str]],
    ) -> list[str]:
        p = job.params
        # 读取时长（进度分母）
        try:
            fp.duration = probe(fp.path).duration
        except Exception:
            fp.duration = 0.0

        self.log.add(
            "jobs",
            f"开始处理 {fp.name}（时长 {fp.duration:.1f}s · 采样 {p.sample_fps}fps · "
            f"档位 {p.ocr_level} · 关键词 {p.keywords}）")

        # 调试模式：逐帧记录（时间/是否跳过/推理毫秒/识别文本/命中关键词）
        debug_fh = None
        if p.debug:
            ddir = LOGS_DIR / "debug" / job.id
            ddir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[\\/:*?"<>|]', "_", fp.name)
            debug_fh = open(ddir / f"{safe}.jsonl", "a", encoding="utf-8")
            self.log.add("debug", f"{fp.name}: 逐帧日志 → {debug_fh.name}", "info")

        frame_results: list[M.FrameResult] = []
        prev_sig: np.ndarray | None = None
        prev_texts: list[str] = []
        last_ocr_t: float | None = None   # 上次真实 OCR 的视频时间（强制兜底用）
        last_report = -10.0
        ocr_seconds = 0.0

        try:
            for t, frame in sampler.sample(
                    fp.path, p.sample_fps,
                    check_cancel=lambda: self._cancelled(job)):
                infer_ms = 0.0
                skipped = False
                if p.skip_similar:
                    sig = _frame_signature(frame)
                    # 距上次 OCR 超过兜底间隔，或画面有实质变化 → 重识别
                    force = (last_ocr_t is None
                             or t - last_ocr_t >= p.force_ocr_every)
                    if (not force and prev_sig is not None
                            and _frames_similar(sig, prev_sig)):
                        skipped = True
                        texts = prev_texts
                        fp.skip_frames += 1
                    else:
                        t0 = time.perf_counter()
                        results = self.engine.recognize(frame)
                        infer_ms = (time.perf_counter() - t0) * 1000
                        ocr_seconds += infer_ms / 1000.0
                        texts = [r[0] for r in results]
                        prev_sig, prev_texts = sig, texts
                        last_ocr_t = t
                        fp.ocr_frames += 1
                else:
                    t0 = time.perf_counter()
                    results = self.engine.recognize(frame)
                    infer_ms = (time.perf_counter() - t0) * 1000
                    ocr_seconds += infer_ms / 1000.0
                    texts = [r[0] for r in results]
                    fp.ocr_frames += 1

                hits = match(texts)
                if hits:
                    fp.hit_frames += 1
                frame_results.append(M.FrameResult(t=t, texts=texts, matched=hits))

                if debug_fh is not None:
                    debug_fh.write(json.dumps({
                        "t": round(t, 3), "skip": skipped,
                        "ms": round(infer_ms, 1) if infer_ms else None,
                        "texts": texts, "matched": hits,
                    }, ensure_ascii=False) + "\n")

                fp.scanned = t
                if t - last_report > 1.5:
                    last_report = t
                    fp.last_text = " | ".join(texts)[:120]
        finally:
            if debug_fh is not None:
                debug_fh.close()

        if self._cancelled(job):
            raise _JobCancelled()

        # 采样阶段汇总（无论是否命中都记录，便于排查"看到了字却没命中"）
        hit_ts = [round(fr.t, 2) for fr in frame_results if fr.matched]
        self.log.add(
            "jobs",
            f"{fp.name}: 采样 {len(frame_results)} 帧 · OCR {fp.ocr_frames} 次"
            f"（相似跳过 {fp.skip_frames} · 推理共 {ocr_seconds:.1f}s）· "
            f"命中 {fp.hit_frames} 帧 → {len(frame_results) and ''}{len(hit_ts)} 个命中点",
            "info")
        if p.debug and hit_ts:
            preview = hit_ts[:60] + (["…"] if len(hit_ts) > 60 else [])
            self.log.add("debug", f"{fp.name}: 命中帧时间点 {preview}")

        segments = M.find_segments(
            frame_results,
            merge_gap=merge_gap,
            margin=p.margin,
            duration=fp.duration,
            default_step=interval,
        )
        for seg in segments:
            self.log.add(
                "jobs",
                f"  ↳ 段 {seg.start:.1f}s–{seg.end:.1f}s · 关键词 {seg.keywords}"
                f" · 首个命中文本「{(seg.sample_texts[0] or '')[:30]}」")
        if not segments and hit_ts:
            # 有命中帧却没生成段 —— 异常状态，明确报警而非静默
            self.log.add(
                "jobs",
                f"{fp.name}: 警告：{fp.hit_frames} 个命中帧未生成任何片段"
                f"（merge_gap={merge_gap}），请检查参数",
                "warn")

        stem = prefix
        out_dir = Path(p.output_dir)
        warned_silent = False
        for i, seg in enumerate(segments, 1):
            if self._cancelled(job):
                raise _JobCancelled()
            clip_name = f"{stem}_{i:02d}_{seg.start:.1f}s-{seg.end:.1f}s.mp4"
            dst = str(out_dir / clip_name)
            mode = clipper.clip(
                fp.path, seg.start, seg.end, dst,
                fast_copy=p.fast_copy, ensure_audio=p.merged_output)
            if mode == "reenc_silent" and p.fast_copy and not warned_silent:
                warned_silent = True
                self.log.add(
                    "jobs",
                    f"{fp.name}: 源无音轨，成片模式下该文件的片段忽略快速模式，"
                    f"重编码并补静音轨", "info")
            fp.clip_paths.append(dst)
            all_clips.append((file_index, seg.start, seg.end - seg.start, dst))
            fp.segments.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "duration": round(seg.end - seg.start, 2),
                "keywords": seg.keywords,
                "texts": seg.sample_texts,
                "clip": clip_name,
            })
        return fp.clip_paths


    def _cleanup_clips(self, job: Job, paths: list[str]) -> None:
        """成片校验通过后清理散片段。

        两道护栏防误删：片段必须位于本任务输出目录内，且修改时间不早于
        任务开始时间（覆盖"输出目录=输入目录、且恰有源文件名撞上片段命名"
        的边角情况——clip 的 -y 覆盖会更新 mtime，旧文件不会被碰到）。
        任何删除失败只告警，绝不让清理影响已有成果。
        """
        out_resolved = Path(job.params.output_dir).resolve()
        removed = skipped = failed = 0
        for clip in paths:
            q = Path(clip)
            try:
                if not q.is_file() or q.parent.resolve() != out_resolved:
                    skipped += 1
                    continue
                # 5s 容差：FAT/exFAT 的 2 秒 mtime 粒度与小幅时钟偏差
                # 不应导致刚写出的片段被误保留
                if q.stat().st_mtime < job.created_at - 5:
                    skipped += 1
                    continue
                q.unlink()
                removed += 1
            except OSError:
                failed += 1
        # 只有全部清理干净才置位——UI 的"已自动清理"提示必须与事实一致
        job.clips_cleaned = (removed == len(paths) and not failed and not skipped)
        # clip_paths 只保留仍存在于磁盘的文件：清空 + clips_cleaned=True
        # 才能和"本来就没剪出片段"区分开
        for fp in job.files:
            fp.clip_paths = [c for c in fp.clip_paths if Path(c).exists()]
        if removed:
            self.log.add("jobs", f"已清理 {removed} 个散片段", "info")
        if skipped:
            self.log.add(
                "jobs",
                f"{skipped} 个片段未通过删除护栏（不在输出目录内或早于任务开始），已保留",
                "warn")
        if failed:
            self.log.add("jobs", f"{failed} 个片段删除失败，已保留", "warn")


def _merged_name(job: Job) -> str:
    """成片文件名：成片_{关键词}_{时间戳}_{任务号}.mp4。

    关键词净化 Windows 非法字符与控制字符（正则模式可能是 `\\d+年` 这类）
    并截断；时间戳到秒 + 任务号前缀防同秒多任务同名互覆。"""
    kw = re.sub(r'[\x00-\x1f\\/:*?"<>|\s]+', "",
                "-".join(job.params.keywords))[:20]
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"成片_{kw or '未命名'}_{ts}_{job.id[:4]}.mp4"


class _JobCancelled(Exception):
    pass
