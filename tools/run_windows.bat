@echo off
REM Windows launcher for the SIFAC Batch Extractor GUI.
cd /d "%~dp0"
where py >nul 2>nul && (py sifac_gui.py & goto :eof)
where python >nul 2>nul && (python sifac_gui.py & goto :eof)
echo Python 3 was not found. Install it from https://www.python.org
pause
