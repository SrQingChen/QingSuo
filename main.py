# -*- encoding: utf-8 -*-
"""青梭 QingSuo 启动入口：python main.py [--port 8000]"""
from __future__ import annotations

import argparse
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="青梭 QingSuo — 基于 OCR 的智能素材剪辑器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"青梭 QingSuo 启动中: {url}")

    if not args.no_browser:
        threading_browser(url)

    uvicorn.run("app.api:app", host=args.host, port=args.port, log_level="warning")


def threading_browser(url: str) -> None:
    import threading
    import time
    def _open():
        time.sleep(1.5)  # 等 uvicorn 起来
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


if __name__ == "__main__":
    main()
