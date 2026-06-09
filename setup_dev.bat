@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== SETUP %date% %time% ==== > _setup.log
python --version >> _setup.log 2>&1
echo Installing requirements... please wait
python -m pip install --upgrade pip >> _setup.log 2>&1
python -m pip install -r requirements.txt >> _setup.log 2>&1
echo pip exited with code %errorlevel% >> _setup.log
echo ==== SETUP DONE ==== >> _setup.log
echo Done. You can close this window.
pause
