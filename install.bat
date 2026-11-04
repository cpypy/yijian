@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
echo [1/2] 安装 Python 依赖到 .runtime ...
python tools\bootstrap_deps.py
if errorlevel 1 (
  echo 依赖安装失败
  pause
  exit /b 1
)
echo.
echo [2/2] 安装 TeX 引擎 ...
call install_tectonic.bat
