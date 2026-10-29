@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" build_app.py
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 build_app.py
    ) else (
        python build_app.py
    )
)
set "BUILD_RESULT=%errorlevel%"
exit /b %BUILD_RESULT%
