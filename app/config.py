# -*- encoding: utf-8 -*-
"""全局配置与设备探测。

设计原则（借鉴 IndexTTS webui_app/config.py）：
- 探测与配置分离：detect_* 不加载任何模型，产出只读快照，可安全缓存。
- 探测结果必须"可用但可失败"：CUDA/NVENC 不可用时给出明确回退路径，
  由上层通过事件告知用户，绝不静默。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _resolve_dirs() -> tuple[Path, Path]:
    """解析 (资源根, 数据根)。

    源码运行：两者都是项目根目录。
    PyInstaller 打包运行：只读资源（web/models）随包在 sys._MEIPASS 内，
    而 logs 这类可变数据放到 exe 同级，方便用户查看与清理。
    """
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle, Path(sys.executable).resolve().parent
    root = Path(__file__).resolve().parent.parent
    return root, root


RES_DIR, DATA_DIR = _resolve_dirs()
MODELS_DIR = RES_DIR / "models"
WEB_DIR = RES_DIR / "web"
LOGS_DIR = DATA_DIR / "logs"

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".ts", ".m4v", ".wmv", ".mpg", ".mpeg", ".rmvb"}

# OCR 档位注册表：换档 = 卸载旧模型 + 加载新模型（同一时刻只驻留一档，
# 避免 8GB 显存下多模型互踩；onnxruntime CPU 推理时主要是内存占用）。
OCR_LEVELS: dict[str, dict] = {
    "fast": {
        "name": "轻量（PP-OCRv5 mobile）",
        "det": MODELS_DIR / "ch_PP-OCRv5_det_mobile.onnx",
        "rec": MODELS_DIR / "ch_PP-OCRv5_rec_mobile.onnx",
        "note": "速度快，精度略低；字典 36775 字，生僻字覆盖完整",
    },
    "accurate": {
        "name": "高精度（PP-OCRv5 server）",
        "det": MODELS_DIR / "ch_PP-OCRv5_det_server.onnx",
        "rec": MODELS_DIR / "ch_PP-OCRv5_rec_server.onnx",
        "note": "检测+识别均为 server 版，速度约为轻量档的 1/5，字幕小字更准",
    },
}

DEFAULT_OCR_LEVEL = "fast"
# 抽帧时最长边超过该值则缩放（OCR 不需要 4K 原始分辨率，缩放可大幅提速）
OCR_MAX_WIDTH = 1920


@dataclass
class DeviceInfo:
    """只读设备快照。所有字段在探测时一次填好，之后不再变更。"""
    cpu_name: str = ""
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    ram_total_gb: float = 0.0
    gpu_name: str = ""
    gpu_vram_gb: float = 0.0
    nvenc_available: bool = False
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    notes: list[str] = field(default_factory=list)

    @property
    def has_nvidia_gpu(self) -> bool:
        return bool(self.gpu_name)

    def to_dict(self) -> dict:
        return {
            "cpu_name": self.cpu_name,
            "cpu_cores_physical": self.cpu_cores_physical,
            "cpu_cores_logical": self.cpu_cores_logical,
            "ram_total_gb": round(self.ram_total_gb, 1),
            "gpu_name": self.gpu_name,
            "gpu_vram_gb": round(self.gpu_vram_gb, 1),
            "nvenc_available": self.nvenc_available,
            "notes": self.notes,
        }


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def probe_gpu() -> tuple[str, float]:
    """nvidia-smi 探测 GPU 名称与显存（不依赖 torch，避免 heavyweight import）。"""
    code, out = _run([
        "nvidia-smi", "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if code != 0 or not out.strip():
        return "", 0.0
    first = out.strip().splitlines()[0]
    parts = [x.strip() for x in first.split(",")]
    if len(parts) < 2:
        return "", 0.0
    try:
        return parts[0], float(parts[1]) / 1024.0
    except ValueError:
        return parts[0], 0.0


def probe_nvenc() -> bool:
    """真实编码探测：用 0.2s 黑块试编 h264_nvenc，而非只看编码器列表
    （列表存在 ≠ 驱动可用；装了显卡但驱动异常时会静默失败）。"""
    code, out = _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2:r=30",
        "-c:v", "h264_nvenc", "-f", "null", "-",
    ], timeout=30)
    return code == 0


def detect_device() -> DeviceInfo:
    info = DeviceInfo()
    import psutil

    info.cpu_cores_logical = psutil.cpu_count(logical=True) or 0
    info.cpu_cores_physical = psutil.cpu_count(logical=False) or 0
    info.ram_total_gb = psutil.virtual_memory().total / (1024 ** 3)

    # CPU 型号（Windows 注册表方式最可靠）
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as k:
            info.cpu_name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
    except OSError:
        pass

    # ffmpeg / ffprobe 可用性
    for tool, attr in (("ffmpeg", "ffmpeg_path"), ("ffprobe", "ffprobe_path")):
        code, _ = _run([tool, "-version"])
        if code != 0:
            info.notes.append(f"未找到 {tool}，请确认其已加入 PATH")

    info.gpu_name, info.gpu_vram_gb = probe_gpu()
    if info.gpu_name:
        info.nvenc_available = probe_nvenc()
        if info.nvenc_available:
            info.notes.append(f"NVENC 硬编码可用（{info.gpu_name}），剪辑将使用 GPU 编码")
            info.notes.append("解码将尝试 CUDA/NVDEC 加速，失败时自动回退 CPU")
        else:
            info.notes.append(f"检测到 {info.gpu_name} 但 NVENC 试编失败，剪辑回退 CPU libx264")
    else:
        info.notes.append("未检测到 NVIDIA GPU，解码/编码均走 CPU")
    return info


def ocr_level_ready(level: str) -> tuple[bool, str]:
    """检查某档位模型文件是否就绪（load 前必须审计，借鉴 DownloadManager.audit_missing）。"""
    spec = OCR_LEVELS.get(level)
    if spec is None:
        return False, f"未知档位: {level}"
    missing = [
        str(p) for p in (spec["det"], spec["rec"]) if not p.exists()
    ]
    if missing:
        return False, "缺少模型文件: " + "; ".join(missing)
    return True, ""


# 加载器：模块级单例由 main.py 创建，通过 get_device() 访问
_device: Optional[DeviceInfo] = None


def get_device() -> DeviceInfo:
    global _device
    if _device is None:
        _device = detect_device()
    return _device
