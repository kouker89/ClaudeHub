@echo off
title QQ Bridge - Claude
cd /d "%~dp0"
echo QQ Bridge starting...
pip install -q websockets httpx >nul 2>&1
python qq-bridge.py
pause
