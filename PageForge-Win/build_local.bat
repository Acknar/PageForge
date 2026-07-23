@echo off
REM ============================================================================
REM  PageForge - LOCAL one-shot build (Windows, embedded-Python). No GitHub.
REM  Run from inside the PageForge-Win folder (double-click, or from a cmd window).
REM
REM  Produces:
REM    dist\PageForge\                      a self-contained app
REM    Output\PageForge-Setup-1.7.2.exe     (only if Inno Setup 6 is installed)
REM
REM  Needs: Windows 10/11 x64 + internet. Python is downloaded automatically.
REM  If this window ever closes too fast to read, open Command Prompt, cd to this
REM  folder, and run  build_local.bat  from there.
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo === [1/4] Downloading a relocatable standalone Python (3.12) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_python.ps1"
if not exist "python\python.exe" ( echo. & echo ERROR: Python download/extract failed. & goto :fail )

echo.
echo === [2/4] Installing base dependencies into the bundled Python ===
python\python.exe -m pip install --upgrade pip
python\python.exe -m pip install -r requirements.txt
if errorlevel 1 ( echo. & echo ERROR: dependency install failed. & goto :fail )

echo.
echo === [3/4] Assembling dist\PageForge (python\ + app\) ===
if exist dist rmdir /s /q dist
mkdir dist\PageForge\app
copy /y pageforge.py dist\PageForge\app\ >nul
xcopy /e /i /y tools dist\PageForge\app\tools >nul
xcopy /e /i /y icons dist\PageForge\app\icons >nul
move python dist\PageForge\python >nul
if not exist "dist\PageForge\python\pythonw.exe" ( echo. & echo ERROR: assemble failed. & goto :fail )

echo.
echo === [4/4] Building the installer (if Inno Setup 6 is installed) ===
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC (
  "!ISCC!" /O"Output" build\installer.iss
  echo Installer written to the Output\ folder.
) else (
  echo Inno Setup 6 not found - skipping the installer step.
  echo Get it at https://jrsoftware.org/isdl.php then run:  iscc /O"Output" build\installer.iss
)

echo.
echo ============================================================================
echo  DONE.
echo  Run the app now without installing:
echo     dist\PageForge\python\pythonw.exe dist\PageForge\app\pageforge.py
echo  Or install from  Output\PageForge-Setup-1.7.2.exe  (if it was built).
echo ============================================================================
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo ============================================================================
echo  BUILD FAILED - read the messages above for the cause.
echo ============================================================================
echo.
pause
endlocal
exit /b 1
