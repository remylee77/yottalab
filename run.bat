@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo [YOTTA LAB] Starting server at http://127.0.0.1:8000
echo [YOTTA LAB] Close this window to stop the server.
echo.
python -m uvicorn main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
    echo.
    echo [ERROR] Server failed. Run: pip install -r requirements.txt
)
pause
