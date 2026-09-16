@echo off
REM Builds the distributable app into dist\BattleVoiceMod\ (PyInstaller onedir).
REM
REM The build uses Python 3.11 on purpose: the project's default interpreter
REM (3.14) is too new for reliable freezing, and 3.11 is what the build
REM environment was created with. Recreate it with:
REM
REM   py -3.11 -m venv .venv-build
REM   .venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
REM
setlocal
cd /d "%~dp0"

if not exist ".venv-build\Scripts\python.exe" (
    echo.
    echo [!] Build environment not found.
    echo     Create it first:
    echo       py -3.11 -m venv .venv-build
    echo       .venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
    echo.
    exit /b 1
)

echo.
echo === Icon ===
".venv-build\Scripts\python.exe" tools\make_icon.py
if errorlevel 1 (
    echo [!] Could not generate the icon.
    exit /b 1
)

echo.
echo === Building ===
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean BattleVoiceMod.spec
if errorlevel 1 (
    echo.
    echo [!] Build failed.
    exit /b 1
)

echo.
echo === Verifying the bundle ===
"dist\BattleVoiceMod\BattleVoiceMod.exe" --selftest
if errorlevel 1 (
    echo.
    echo [!] Self-test failed - see %TEMP%\battle_voicemod_selftest.txt
    exit /b 1
)

echo.
echo Done: dist\BattleVoiceMod\BattleVoiceMod.exe
echo The whole dist\BattleVoiceMod folder is the distributable.

REM --- Optional: package it as a Setup.exe with Inno Setup ----------------
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do if exist %%P set "ISCC=%%P"

echo.
if not defined ISCC (
    echo [i] Inno Setup 6 not found - skipping the installer.
    echo     Get it from https://jrsoftware.org/isdl.php to also produce Setup.exe.
    exit /b 0
)

echo === Installer ===
%ISCC% installer\BattleVoiceMod.iss
if errorlevel 1 (
    echo.
    echo [!] Installer build failed.
    exit /b 1
)

echo.
echo Done: dist\installer\BattleVoiceMod-Setup-1.0.0.exe
