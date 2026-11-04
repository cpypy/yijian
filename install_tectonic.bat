@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%CD%\.runtime"
set "TEXLATE_DATA_DIR=%CD%\data\texlate"
set "TEXLATE_CACHE_DIR=%CD%\data\texlate-cache"
set "TECTONIC_CACHE_DIR=%CD%\data\tectonic-cache"
set "PYTHONIOENCODING=utf-8"
python -m texlate tools install-tectonic
pause
