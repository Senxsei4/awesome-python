@echo off
REM ===========================================================================
REM  Oracle AI: Project Citadel - Windows build script
REM  Produces a single clickable dist\OracleAI.exe with the branded icon.
REM  Run this on a Windows machine (PyInstaller cannot cross-compile a .exe).
REM ===========================================================================
setlocal

echo [1/4] Upgrading pip...
python -m pip install --upgrade pip || goto :error

echo [2/4] Installing runtime dependencies...
pip install -r requirements.txt || goto :error

echo [3/4] Installing build tooling...
pip install pyinstaller pillow || goto :error

echo [4/4] Building OracleAI.exe...
pyinstaller --noconfirm --clean oracle_citadel.spec || goto :error

echo.
echo ============================================================
echo  Build complete:  dist\OracleAI.exe
echo  (Optional) build the installer with Inno Setup: installer.iss
echo ============================================================
goto :eof

:error
echo.
echo BUILD FAILED. See the output above.
exit /b 1
