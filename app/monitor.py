# -*- encoding: utf-8 -*-
"""性能监控：后台线程低频采样 + 内存缓存，API 读缓存零开销。

- CPU/内存：psutil（进程内，微秒级）
- GPU：nvidia-smi 子进程（~30ms/次），2 秒一次，对系统几乎无感
- 历史曲线：内存环形序列（120 点 ≈ 4 分钟），画 sparkline 用
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import psutil


class PerfMonitor:
    def __init__(self, interval: float = 2.0, history: int = 120):
        self.interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._snapshot: dict = {
            "ts": 0.0,
            "cpu_percent": 0.0,
            "ram_percent": 0.0,
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
            "gpu_util": 0.0,
            "gpu_mem_percent": 0.0,
            "gpu_mem_used_gb": 0.0,
            "gpu_mem_total_gb": 0.0,
            "gpu_temp": 0.0,
            "has_gpu": False,
        }
        self._hist_cpu: deque = deque(maxlen=history)
        self._hist_ram: deque = deque(maxlen=history)
        self._hist_gpu: deque = deque(maxlen=history)

    # ---------- 采样 ----------

    def _query_gpu(self) -> Optional[dict]:
        try:
            p = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if p.returncode != 0:
                return None
            vals = [x.strip() for x in p.stdout.strip().splitlines()[0].split(",")]
            return {
                "util": float(vals[0]),
                "mem_used_gb": float(vals[1]) / 1024.0,
                "mem_total_gb": float(vals[2]) / 1024.0,
                "temp": float(vals[3]),
            }
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return None

    def _tick(self) -> None:
        vm = psutil.virtual_memory()
        gpu = self._query_gpu()
        with self._lock:
            s = self._snapshot
            s["ts"] = time.time()
            s["cpu_percent"] = psutil.cpu_percent(interval=None)
            s["ram_percent"] = vm.percent
            s["ram_used_gb"] = vm.used / (1024 ** 3)
            s["ram_total_gb"] = vm.total / (1024 ** 3)
            if gpu:
                s["has_gpu"] = True
                s["gpu_util"] = gpu["util"]
                s["gpu_mem_used_gb"] = gpu["mem_used_gb"]
                s["gpu_mem_total_gb"] = gpu["mem_total_gb"]
                s["gpu_mem_percent"] = (
                    gpu["mem_used_gb"] / gpu["mem_total_gb"] * 100
                    if gpu["mem_total_gb"] > 0 else 0.0
                )
                s["gpu_temp"] = gpu["temp"]
            self._hist_cpu.append(s["cpu_percent"])
            self._hist_ram.append(s["ram_percent"])
            self._hist_gpu.append(s["gpu_util"] if gpu else 0.0)

    def _run(self) -> None:
        psutil.cpu_percent(interval=None)  # 首次调用初始化基线
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:
                pass  # 监控自身绝不能影响主流程

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="perf-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- 读取 ----------

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._snapshot)
            s["hist_cpu"] = list(self._hist_cpu)
            s["hist_ram"] = list(self._hist_ram)
            s["hist_gpu"] = list(self._hist_gpu)
            return s
