@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher not found. Install Python 3.11 or 3.12 from python.org.
  exit /b 1
)
py -3.12 --version >nul 2>nul
if errorlevel 1 (
  py -3.11 --version >nul 2>nul
  if errorlevel 1 (
    echo Python 3.11 or 3.12 is required.
    exit /b 1
  )
  py -3.11 -m venv .venv
) else (
  py -3.12 -m venv .venv
)
if errorlevel 1 exit /b 1
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

rem Bundle a verified Playwright Chromium build; Chrome and Edge remain preferred at runtime.
set PLAYWRIGHT_BROWSERS_PATH=%CD%\.pw-browsers
python -m playwright install chromium
if errorlevel 1 exit /b 1
python -m PyInstaller --clean --noconfirm "红薯雷达.spec"
if errorlevel 1 exit /b 1

if not exist "dist\红薯雷达.exe" (
  echo Build failed: dist\红薯雷达.exe was not created.
  exit /b 1
)
if not exist "dist\红薯雷达MCP.exe" (
  echo Build failed: dist\红薯雷达MCP.exe was not created.
  exit /b 1
)
for %%F in ("dist\红薯雷达.exe" "dist\红薯雷达MCP.exe") do echo %%~fF  %%~zF bytes
echo Build finished. Copy both EXE files into the same folder before use.
exit /b 0
