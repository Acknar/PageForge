@echo off
REM ============================================================================
REM  One-shot build for PageForge (Windows edition).
REM  Run this ON WINDOWS from the repo root:   build\build.bat
REM  Produces:
REM    dist\PageForge\PageForge.exe          (the app, one-folder build)
REM    Output\PageForge-Setup-1.7.1.exe      (the installer, if Inno Setup found)
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo.
echo === [1/4] Creating virtual environment (.venv) ===
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: could not create a virtual environment. Is Python installed and on PATH?
    exit /b 1
)
call .venv\Scripts\activate

echo.
echo === [2/4] Installing dependencies ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if %errorlevel% neq 0 (
    echo ERROR: dependency install failed.
    exit /b 1
)

echo.
echo === [3/4] Building the app with PyInstaller ===
pyinstaller build\pageforge.spec --noconfirm
if not exist "dist\PageForge\PageForge.exe" (
    echo ERROR: PyInstaller did not produce dist\PageForge\PageForge.exe
    exit /b 1
)
echo Built: dist\PageForge\PageForge.exe

echo.
echo === [4/4] Building the installer with Inno Setup (optional) ===
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%P set "ISCC=%%~P"
if defined ISCC (
    "!ISCC!" build\installer.iss
    echo Installer written to the Output\ folder.
) else (
    echo Inno Setup not found - skipping installer.
    echo Install it from https://jrsoftware.org/isdl.php then run:
    echo     iscc build\installer.iss
    echo The app itself is ready to run at dist\PageForge\PageForge.exe
)

echo.
echo === Done ===
endlocal
