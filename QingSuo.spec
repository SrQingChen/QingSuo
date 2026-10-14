# -*- mode: python ; coding: utf-8 -*-
"""青梭 QingSuo 打包配置（PyInstaller onedir）。

为什么用 onedir 而不是 onefile：
本包内含 onnxruntime / opencv 原生库与 ~21MB OCR 权重，总重约 250MB。
onefile 每次启动都要把整包解压到临时目录，冷启动会慢到无法接受；
onedir 一次解包、后续秒开。

打包：
    pyinstaller --clean --noconfirm QingSuo.spec

产物：
    dist/QingSuo/QingSuo.exe     ← 双击即用
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)

# 只读资源：随包分发，运行时通过 sys._MEIPASS 定位（见 app/config.py）
datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "models"), "models"),
    (str(ROOT / "NOTICE.md"), "."),
    (str(ROOT / "LICENSE"), "."),
]

# rapidocr_onnxruntime 的字典/配置/字体是数据文件，且子模块多为动态导入
datas += collect_data_files("rapidocr_onnxruntime")

hiddenimports = [
    # uvicorn 全链路均为字符串动态导入，必须显式列出
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 应用侧
    "app",
    "app.api",
    "app.config",
    "app.events",
    "app.jobs",
    "app.monitor",
    "app.ocr",
    "app.ocr.engine",
    "app.pipeline",
    "app.pipeline.clipper",
    "app.pipeline.matcher",
    "app.pipeline.sampler",
    "app.pipeline.scanner",
]
hiddenimports += collect_submodules("rapidocr_onnxruntime")

# 明确剔除：与 OCR/服务无关，只是被间接引用，留着会让包凭空胖 100MB+
excludes = [
    "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook",
    "tkinter", "PyQt5", "PySide2", "PySide6", "wx",
    "pytest", "setuptools", "pip", "wheel",
    "torch", "torchvision", "tensorflow", "paddle",
]


a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QingSuo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 会破坏 onnxruntime/opencv 的 DLL，禁用
    console=True,       # 保留控制台：ffmpeg 缺失等信息需要可见
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QingSuo",
)
