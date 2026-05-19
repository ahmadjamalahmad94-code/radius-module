# ─────────────────────────────────────────────────────────────────────────────
# FreeRADIUS لـ HobeRadius — يستخدم rlm_rest + rlm_sql_sqlite + CoA outbound.
# نُحمّل من freeradius/freeradius-server الرسمي (يحوي rest+sqlite مفعّلة).
# ─────────────────────────────────────────────────────────────────────────────
FROM freeradius/freeradius-server:3.2.5

# نظِّف الـ default config ثم انسخ ملفاتنا
RUN rm -rf /etc/freeradius/mods-enabled \
        /etc/freeradius/sites-enabled \
        /etc/freeradius/policy.d \
        /etc/freeradius/clients.conf \
        /etc/freeradius/radiusd.conf 2>/dev/null || true

COPY clients.conf      /etc/freeradius/clients.conf
COPY radiusd.conf      /etc/freeradius/radiusd.conf
COPY mods-enabled/     /etc/freeradius/mods-enabled/
COPY sites-enabled/    /etc/freeradius/sites-enabled/
COPY policy.d/         /etc/freeradius/policy.d/

# عدّل الـ permissions
RUN chown -R freerad:freerad /etc/freeradius && \
    mkdir -p /var/log/freeradius/radacct && \
    chown -R freerad:freerad /var/log/freeradius

EXPOSE 1812/udp 1813/udp

# نشغّل في foreground مع debug logging قابل للتفعيل عبر env
ENV FREERADIUS_DEBUG_LEVEL=""
CMD ["sh", "-c", "exec freeradius -f -l stdout $FREERADIUS_DEBUG_LEVEL"]
