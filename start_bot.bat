@echo off
title RinaraDev Automation Server - 24/7 Service
cd /d "%~dp0"
echo ==========================================================
echo   RinaraDev Automation Bot Server
echo   Running in 24/7 background mode
echo ==========================================================
:loop
py web_app/run_web.py
echo [!] Server terhenti atau restart. Memulai kembali dalam 5 detik...
timeout /t 5 >nul
goto loop
