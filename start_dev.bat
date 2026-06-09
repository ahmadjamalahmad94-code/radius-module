@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== %date% %time% ==== > _dev_server.log
where python >> _dev_server.log 2>&1
python --version >> _dev_server.log 2>&1
set FLASK_APP=wsgi:app
set FLASK_DEBUG=1
set RADIUS_MODE=manual
echo Starting HobeRadius dev server at http://127.0.0.1:5050 ...
python -m flask run --host 127.0.0.1 --port 5050 >> _dev_server.log 2>&1
echo flask exited with code %errorlevel% >> _dev_server.log
pause
