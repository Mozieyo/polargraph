@echo off
rem PolarGraph Studio - double-click to launch (Windows).
rem Needs Python 3.11+; pyserial is only required for plotting (pip install pyserial).
cd /d "%~dp0"
set PYTHONPATH=src
python -m polargraph serve --open
if errorlevel 1 pause
