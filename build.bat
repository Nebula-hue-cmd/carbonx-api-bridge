@echo off
REM Build the portable single-file Windows .exe (no Python needed at runtime).
REM Requirements: Python 3.10+ on this machine. Everything else is done here.
cd /d "%~dp0"

echo [build] creating build venv (.venv-build)...
python -m venv .venv-build || goto :fail
call .venv-build\Scripts\activate.bat

echo [build] installing PyInstaller...
python -m pip install --upgrade pip
pip install pyinstaller || goto :fail

echo [build] building carbonx-bridge.exe...
pyinstaller --noconfirm --clean --onefile --name carbonx-bridge --add-data "scripts\lumen_game_context.luau;carbonx_bridge" --add-data "carbonx_bridge\panel.html;carbonx_bridge" entry.py || goto :fail

echo [build] shipping an editable copy of the Lumen scripts next to the exe...
if not exist "dist\scripts" mkdir "dist\scripts"
copy /y "scripts\lumen_game_context.luau" "dist\scripts\lumen_game_context.luau" >nul

echo.
echo [build] done: dist\carbonx-bridge.exe
echo [build] On first run the exe creates config.json NEXT TO ITSELF, so keep
echo         the whole folder if you want to hand it to someone else.
pause
exit /b 0

:fail
echo [build] FAILED - see the message above.
pause
exit /b 1