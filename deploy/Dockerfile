# HobeRadius — Production Image
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Amman

# نظام
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# تبعيات Python
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt gunicorn>=21.2

# الكود
COPY . /app

# مستخدم غير-root
RUN groupadd -r hr && useradd -r -g hr -d /app -s /usr/sbin/nologin hr \
 && mkdir -p /app/instance /app/logs \
 && chown -R hr:hr /app

USER hr

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/admin/radius/_health || exit 1

CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "wsgi:app"]
