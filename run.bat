@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   青梭 QingSuo 启动中...
echo   浏览器将自动打开 http://127.0.0.1:8765
echo ============================================
python main.py --port 8765
pause
