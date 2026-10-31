@echo off
REM ============================================================
REM  start_bot.bat — One-Click Innoventix Hub Voice Agent Launcher
REM  bot.py does everything itself: starts the Cloudflare tunnel,
REM  updates Telnyx automatically, pre-warms VAD/Cartesia, and
REM  starts the voice bot. This is just a double-click wrapper.
REM ============================================================

cd /d "%~dp0"
call venv\Scripts\activate.bat
python bot.py
pause
