# -*- encoding: utf-8 -*-
"""下载 PP-OCRv5 ONNX 模型到 models/。

背景：PP-OCRv5 的 ONNX 权重不在 PaddleOCR 的 GitHub 仓库里直接托管，
本工具从 ModelScope 的 RapidAI/RapidOCR 仓库拉取，四个文件的体积已逐一核对
（见下表 SIZE_BYTES），下载后按体积校验，避免半截文件被当成"就绪"。

用法：
    python scripts/fetch_models.py                # 补齐缺失的（两档全下）
    python scripts/fetch_models.py --level fast   # 只拉轻量档
    python scripts/fetch_models.py --level accurate
    python scripts/fetch_models.py --force        # 忽略本地文件，重新下载
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "RapidAI/RapidOCR"
BRANCH = "master"
BASE = f"https://modelscope.cn/models/{REPO}/resolve/{BRANCH}"

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"

# (档位, 本地文件名, 仓库内路径, 期望字节数)
MODELS: list[tuple[str, str, str, int]] = [
    ("fast", "ch_PP-OCRv5_det_mobile.onnx",
     "onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx", 4_819_576),
    ("fast", "ch_PP-OCRv5_rec_mobile.onnx",
     "onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx", 16_631_306),
    ("accurate", "ch_PP-OCRv5_det_server.onnx",
     "onnx/PP-OCRv5/det/ch_PP-OCRv5_det_server.onnx", 88_118_768),
    ("accurate", "ch_PP-OCRv5_rec_server.onnx",
     "onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_server.onnx", 84_577_022),
]


def _progress(done: int, total: int) -> None:
    if total <= 0:
        sys.stdout.write(f"\r    {done / 1048576:.1f} MB")
    else:
        pct = done / total * 100
        bar = "#" * int(pct / 4) + "-" * (25 - int(pct / 4))
        sys.stdout.write(f"\r    [{bar}] {pct:5.1f}%  {done / 1048576:.1f} MB")
    sys.stdout.flush()


def download(name: str, url: str, dst: Path, expect: int, force: bool) -> bool:
    if dst.exists() and not force:
        actual = dst.stat().st_size
        if actual == expect:
            print(f"  [跳过] {name} 已就绪（{actual / 1048576:.1f} MB）")
            return True
        print(f"  [重下] {name} 体积不符（本地 {actual} / 期望 {expect}），重新下载")

    tmp = dst.with_suffix(dst.suffix + ".part")
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [下载] {name}\n         {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "qingsuo-fetch-models"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    _progress(done, total)
        sys.stdout.write("\n")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        sys.stdout.write("\n")
        print(f"  [失败] {name}: {e}")
        tmp.unlink(missing_ok=True)
        return False

    got = tmp.stat().st_size
    if got != expect:
        print(f"  [失败] {name}: 体积校验不通过（下载 {got} / 期望 {expect}）")
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dst)
    print(f"  [完成] {name} → {dst}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 PP-OCRv5 ONNX 模型")
    ap.add_argument("--level", choices=["fast", "accurate", "all"], default="all",
                    help="fast=轻量档，accurate=高精度档，all=两档（默认）")
    ap.add_argument("--force", action="store_true", help="重新下载已存在的文件")
    args = ap.parse_args()

    wanted = {"fast", "accurate"} if args.level == "all" else {args.level}
    todo = [m for m in MODELS if m[0] in wanted]

    print(f"目标目录: {MODELS_DIR}")
    print(f"来源: ModelScope {REPO} ({BASE})\n")

    failures = []
    for level, name, path, expect in todo:
        url = f"{BASE}/{path}"
        if not download(name, url, MODELS_DIR / name, expect, args.force):
            failures.append(name)

    print()
    if failures:
        print(f"有 {len(failures)} 个文件未就绪：{', '.join(failures)}")
        print("可稍后重跑本脚本；已完成的文件会被跳过。")
        return 1
    print("全部就绪，可以启动：python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
