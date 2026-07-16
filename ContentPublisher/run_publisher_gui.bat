@echo off
setlocal
cd /d "%~dp0.."
python ContentPublisher\launcher.py
if errorlevel 1 pause
