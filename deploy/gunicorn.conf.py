"""gunicorn config — Phase-1: single worker (workers الخلفية تحتاج process واحد)."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))   # 1 لأن worker الـ webhook/sync inline
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
keepalive = 5
graceful_timeout = 30

# logging
accesslog = "-"     # stdout (يُمسك بـ docker logs)
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)ss'

# security
forwarded_allow_ips = "*"   # خلف nginx
proxy_protocol = False
limit_request_line = 8190
limit_request_fields = 64
