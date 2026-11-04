@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%CD%\.runtime"
set "TEXLATE_DATA_DIR=%CD%\data\texlate"
set "TEXLATE_CACHE_DIR=%CD%\data\texlate-cache"
set "PYTHONIOENCODING=utf-8"
if not exist "%PYTHONPATH%" (
  echo 依赖没装，先运行 install.bat
  pause
  exit /b 1
)
python -m app.main
pause
