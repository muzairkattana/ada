@echo off
REM Set console window title
title ADAICON - A.D.A. Assistant
REM Run the A.D.A. assistant with one double-click / command

REM Change to the folder where this script is located
cd /d "%~dp0"

REM Check that the virtual environment exists
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at venv\Scripts\python.exe
    echo Create it first by running:  python -m venv venv
    echo Then install dependencies inside it and try again.
    pause
    exit /b 1
)

REM Activate the virtual environment
call "venv\Scripts\activate.bat"

REM Choose the mode here: camera, screen, or none
set MODE=camera

REM Run the main A.D.A. application
python ada.py --mode %MODE%

REM Keep window open so you can see any errors
pause
