# تشغيل radius-module مستقلًا
$env:FLASK_APP = "wsgi:app"
$env:FLASK_DEBUG = "1"
$env:RADIUS_MODE = "manual"
python -m flask run --host 127.0.0.1 --port 5050
