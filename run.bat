@echo off
rem Runs the isolated PC fork. Optional --accounting-only skips game capture.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B maple_exp.py %*
    if errorlevel 1 pause
    goto :eof
)
for %%P in (python "py -3") do (
    %%~P -c "import cv2, mss, numpy, pytesseract, PIL" >nul 2>nul && (
        %%~P -B maple_exp.py %*
        if errorlevel 1 pause
        goto :eof
    )
)
echo Couldn't find Python with the packages this needs.
echo Install Python from python.org, then run this once:
echo     py -3 -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements-lock.txt
pause
