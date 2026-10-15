@echo off
cd /d "%~dp0"
"_internal\runtime\python.exe" app\server.py --workspace Projects\MyFilm
pause
