# -*- encoding: utf-8 -*-
"""REST API 路由。"""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    DEFAULT_OCR_LEVEL,
    OCR_LEVELS,
    WEB_DIR,
    get_device,
    ocr_level_ready,
)
from .events import EventLog
from .jobs import JobParams, JobRunner
from .monitor import PerfMonitor
from .ocr.engine import OCREngine
from .pipeline.scanner import scan_videos


class CreateJobRequest(BaseModel):
    input_dir: str
    output_dir: str
    keywords: list[str] = Field(min_length=1)
    ocr_level: str = DEFAULT_OCR_LEVEL
    sample_fps: float = Field(default=2.0, ge=0.2, le=10.0)
    margin: float = Field(default=0.5, ge=0.0, le=30.0)
    merge_gap: float = Field(default=1.0, ge=0.0, le=60.0)
    use_regex: bool = False
    ignore_case: bool = True
    fuzzy_chars: bool = True
    fast_copy: bool = False
    merged_output: bool = False
    skip_similar: bool = True
    recursive: bool = True
    debug: bool = False


class LoadRequest(BaseModel):
    level: str


def create_app() -> FastAPI:
    app = FastAPI(title="青梭 QingSuo", docs_url="/api/docs")

    from .config import LOGS_DIR
    LOGS_DIR.mkdir(exist_ok=True)
    event_log = EventLog(log_file=LOGS_DIR / "events.log")

    device = get_device()
    for note in device.notes:
        event_log.add("config", note, "info")

    engine = OCREngine(event_log)
    runner = JobRunner(engine, event_log)
    monitor = PerfMonitor()
    monitor.start()

    # ---------- 设备 / 引擎 ----------

    @app.get("/api/device")
    def api_device():
        return device.to_dict()

    @app.get("/api/engine")
    def api_engine():
        return engine.snapshot()

    @app.post("/api/engine/load")
    def api_engine_load(req: LoadRequest):
        if runner.has_active_jobs():
            raise HTTPException(400, "有任务正在运行，请等待完成后再换档")
        if req.level not in OCR_LEVELS:
            raise HTTPException(400, f"未知档位: {req.level}")
        ok, msg = ocr_level_ready(req.level)
        if not ok:
            raise HTTPException(400, msg)
        try:
            engine.load(req.level)
        except Exception as e:
            raise HTTPException(500, str(e))
        return engine.snapshot()

    @app.post("/api/engine/unload")
    def api_engine_unload():
        if runner.has_active_jobs():
            raise HTTPException(400, "有任务正在运行，禁止卸载")
        try:
            engine.unload("用户手动卸载")
        except Exception as e:
            raise HTTPException(500, str(e))
        return engine.snapshot()

    # ---------- 任务 ----------

    @app.post("/api/jobs")
    def api_create_job(req: CreateJobRequest):
        try:
            params = JobParams(**req.dict())
            job_id = runner.submit(params)
        except ValueError as e:
            raise HTTPException(400, str(e))
        # 后台预加载（不阻塞响应；加载失败任务侧会报错）
        threading.Thread(
            target=lambda: _safe_preload(engine, req.ocr_level),
            daemon=True, name="engine-preload",
        ).start()
        return {"job_id": job_id}

    @app.get("/api/jobs")
    def api_list_jobs():
        return {"jobs": runner.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def api_get_job(job_id: str):
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def api_cancel_job(job_id: str):
        if not runner.cancel(job_id):
            raise HTTPException(404, "任务不存在")
        return {"ok": True}

    # ---------- 扫描 / 监控 / 事件 ----------

    @app.get("/api/scan")
    def api_scan(input_dir: str, recursive: bool = True):
        try:
            return {"files": scan_videos(input_dir, recursive=recursive)}
        except NotADirectoryError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/monitor")
    def api_monitor():
        return monitor.snapshot()

    @app.get("/api/events")
    def api_events(limit: int = 100):
        return {"events": event_log.list(limit)}

    @app.get("/api/jobs/{job_id}/debuglog")
    def api_debug_log(job_id: str, file: str, tail: int = 200):
        """读取逐帧调试日志（jsonl 尾部）。file 为视频文件名。"""
        import re as _re
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        if not job.params.debug:
            raise HTTPException(400, "该任务未开启调试模式")
        safe = _re.sub(r'[\\/:*?"<>|]', "_", file)
        path = LOGS_DIR / "debug" / job_id / f"{safe}.jsonl"
        if not path.is_file():
            raise HTTPException(404, "日志不存在")
        lines = path.read_text(encoding="utf-8").splitlines()
        return {"lines": lines[-tail:]}

    # ---------- 前端 ----------

    web_dir = WEB_DIR
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    else:  # web 目录缺失时仍提供 API
        @app.get("/")
        def index_fallback():
            return {"status": "ok", "api": "/api/docs"}

    return app


def _safe_preload(engine: OCREngine, level: str) -> None:
    try:
        engine.ensure_loaded(level)
    except Exception:
        pass  # 错误已通过事件流上报


app = create_app()
