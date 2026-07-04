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

# الكود — نسخ صريح للمسارات المطلوبة وقت التشغيل فقط (لا `COPY . .`).
# كان `COPY .` يبتلع كل ما تراكم في مجلد الريبو على السيرفر (قاعدة
# instance/ الحيّة، أرشيفات النسخ، دمبات الترحيل…) فتضخّمت الصورة إلى
# ~10.7GB. الـ.dockerignore الجديد allowlist يمنعها من سياق البناء أصلًا،
# وهذا النسخ الصريح طبقة أمان ثانية: حتى لو دخل شيء غريب السياق، لا
# يصل إلى الصورة.
COPY wsgi.py babel.cfg /app/
COPY app/          /app/app/
COPY translations/ /app/translations/
COPY tools/        /app/tools/
COPY deploy/       /app/deploy/

# مستخدم غير-root.
# N4: hr يحصل على supplementary group بـ gid=101 (نفس gid المستخدم
# `freerad` داخل container الـ FreeRADIUS). الـ DB ملف مشترك (SQLite
# WAL يحتاج write access على db/db-wal/db-shm). لما FreeRADIUS يكتب
# على radacct، الـ wal/shm files تصير 101:101 — وبدون عضوية hr في
# هذي المجموعة، أي docker exec hoberadius python يفشل بـ
# "attempt to write a readonly database" (issue #13 في الـ postmortem).
RUN groupadd -r -g 101 hr-freerad-shared \
 && groupadd -r hr \
 && useradd  -r -g hr -G hr-freerad-shared -d /app -s /usr/sbin/nologin hr \
 && mkdir -p /app/instance /app/logs \
 && chown -R hr:hr /app

# N4 entrypoint: قبل تشغيل gunicorn، نظّف ownership أي ملفات DB قديمة
# على bind-mount عشان hr يقدر يكتب عليها كلها. best-effort — لو ما في
# صلاحية (مثلاً bind-mount read-only)، نتجاهل والإقلاع يكمل.
COPY deploy/entrypoint.sh /usr/local/bin/hr-entrypoint.sh
RUN chmod 0755 /usr/local/bin/hr-entrypoint.sh

USER hr

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/admin/radius/_health || exit 1

ENTRYPOINT ["/usr/local/bin/hr-entrypoint.sh"]
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "wsgi:app"]
