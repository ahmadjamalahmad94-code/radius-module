#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""رفع تغطية الترجمة إلى ~100% بإضافة الترجمات الناقصة دفعةً واحدة.

تشغيل واحد: يضيف 165 ترجمة إلى en/fr/tr/es ثم يعيد بناء الكتالوجات. لا
يحرّر .po يدويًا (نمط build_catalogs نفسه).

يعمل idempotent: لو الترجمة موجودة بالفعل لا يُكتب فوقها.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_DIR = ROOT / "tools" / "i18n_translations"

# ─────────────────────────────────────────────────────────────────────
# الترجمات: مفاتيح = msgid عربي. القيم = ترجمة لكل لغة.
# المصدر العربي يأتي من مسح كاتالوج _missing_msgids.json (165 msgid).
# ─────────────────────────────────────────────────────────────────────
TRANSLATIONS: dict[str, dict[str, str]] = {
    # نصوص قصيرة / connectors
    "،":             {"en": ",",      "fr": ",",      "tr": ",",      "es": ","},
    "، و":           {"en": ", and ", "fr": ", et ",  "tr": ", ve ",  "es": ", y "},
    "؟":             {"en": "?",      "fr": " ?",     "tr": "?",      "es": "?"},
    "و":             {"en": "and",    "fr": "et",     "tr": "ve",     "es": "y"},
    "حتى":           {"en": "until",  "fr": "jusqu'à","tr": "kadar",  "es": "hasta"},
    "نعم":           {"en": "Yes",    "fr": "Oui",    "tr": "Evet",   "es": "Sí"},
    "حسنًا":          {"en": "OK",     "fr": "OK",     "tr": "Tamam",  "es": "OK"},
    "دائم":          {"en": "Permanent", "fr": "Permanent", "tr": "Kalıcı", "es": "Permanente"},
    "دوري":          {"en": "Periodic",  "fr": "Périodique", "tr": "Periyodik", "es": "Periódico"},
    "كلاهما":        {"en": "Both",   "fr": "Les deux","tr": "Her ikisi","es": "Ambos"},
    "تركيب":         {"en": "Install","fr": "Installer","tr": "Kur",  "es": "Instalar"},
    "اختبار":        {"en": "Test",   "fr": "Test",   "tr": "Test",   "es": "Prueba"},
    "لوب":           {"en": "Loop",   "fr": "Boucle", "tr": "Döngü",  "es": "Bucle"},
    "لوب!":          {"en": "Loop!",  "fr": "Boucle !","tr": "Döngü!", "es": "¡Bucle!"},
    "لوب مكتشف":     {"en": "Loop detected", "fr": "Boucle détectée",
                       "tr": "Döngü tespit edildi", "es": "Bucle detectado"},
    "محاولة":        {"en": "attempt", "fr": "tentative", "tr": "deneme", "es": "intento"},
    "منافذ":         {"en": "Ports",  "fr": "Ports",  "tr": "Bağlantı noktaları", "es": "Puertos"},
    "ميجابت":        {"en": "Mbps",   "fr": "Mbit/s", "tr": "Mbps",   "es": "Mbps"},
    "مركّبة":        {"en": "Installed", "fr": "Installé", "tr": "Kurulu", "es": "Instalado"},
    "غير مركّبة":    {"en": "Not installed", "fr": "Non installé",
                       "tr": "Yüklü değil", "es": "No instalado"},
    "تغيّرت":        {"en": "Changed", "fr": "Modifié","tr": "Değişti","es": "Cambiado"},
    "تم النسخ":      {"en": "Copied", "fr": "Copié",  "tr": "Kopyalandı","es": "Copiado"},
    "تعذّر الفحص":   {"en": "Check failed", "fr": "Échec de la vérification",
                       "tr": "Kontrol başarısız", "es": "Comprobación fallida"},
    "تنبيهات أُرسلت":{"en": "Alerts sent", "fr": "Alertes envoyées",
                       "tr": "Uyarılar gönderildi", "es": "Alertas enviadas"},
    "انقطاعات":      {"en": "Outages","fr": "Pannes", "tr": "Kesintiler", "es": "Cortes"},
    "انقطاعات رُصدت":{"en": "Outages detected", "fr": "Pannes détectées",
                       "tr": "Tespit edilen kesintiler", "es": "Cortes detectados"},
    "جهات فريدة":    {"en": "Unique sources", "fr": "Sources uniques",
                       "tr": "Benzersiz kaynaklar", "es": "Fuentes únicas"},
    "مدراء فريدون":  {"en": "Unique admins", "fr": "Admins uniques",
                       "tr": "Benzersiz yöneticiler", "es": "Administradores únicos"},
    "تغييرات حالة":  {"en": "Status changes", "fr": "Changements d'état",
                       "tr": "Durum değişiklikleri", "es": "Cambios de estado"},
    "محاولات اليوم": {"en": "Today's attempts", "fr": "Tentatives du jour",
                       "tr": "Bugünkü denemeler", "es": "Intentos de hoy"},
    "نتيجة المحاولة":{"en": "Attempt result", "fr": "Résultat de la tentative",
                       "tr": "Deneme sonucu", "es": "Resultado del intento"},
    "سجل المحاولات": {"en": "Attempts log", "fr": "Journal des tentatives",
                       "tr": "Deneme günlüğü", "es": "Registro de intentos"},
    "سجل الفحوصات": {"en": "Checks log", "fr": "Journal des vérifications",
                      "tr": "Kontrol günlüğü", "es": "Registro de comprobaciones"},
    "فحص أخير":      {"en": "Last check", "fr": "Dernière vérification",
                       "tr": "Son kontrol", "es": "Última comprobación"},
    "فحص حي":        {"en": "Live check", "fr": "Vérification en direct",
                       "tr": "Canlı kontrol", "es": "Comprobación en vivo"},
    "فحص دوري":      {"en": "Periodic check", "fr": "Vérification périodique",
                       "tr": "Periyodik kontrol", "es": "Comprobación periódica"},
    "فحوصات آخر 24 ساعة": {"en": "Checks in last 24h",
                              "fr": "Vérifications dans les dernières 24h",
                              "tr": "Son 24 saatteki kontroller",
                              "es": "Comprobaciones en las últimas 24 h"},
    "آخر فحص (UTC)": {"en": "Last check (UTC)", "fr": "Dernière vérification (UTC)",
                       "tr": "Son kontrol (UTC)", "es": "Última comprobación (UTC)"},
    "آخر قراءة":     {"en": "Last reading", "fr": "Dernière lecture",
                       "tr": "Son okuma", "es": "Última lectura"},
    "المفحوصة":      {"en": "Checked", "fr": "Vérifiés","tr": "Kontrol edilenler","es": "Comprobados"},
    "تفاصيل دورة الفحص": {"en": "Check round details",
                            "fr": "Détails du cycle de vérification",
                            "tr": "Kontrol turu ayrıntıları",
                            "es": "Detalles del ciclo de comprobación"},
    "تفاصيل كل جهاز في هذه الدورة":
        {"en": "Details for each device in this round",
         "fr": "Détails de chaque appareil dans ce cycle",
         "tr": "Bu turdaki her cihazın ayrıntıları",
         "es": "Detalles de cada dispositivo en este ciclo"},
    "لم تُقرأ بعد": {"en": "Not read yet", "fr": "Pas encore lu",
                      "tr": "Henüz okunmadı", "es": "Aún no leído"},
    "لا فحوصات مسجّلة بعد": {"en": "No checks recorded yet",
                                 "fr": "Aucune vérification enregistrée",
                                 "tr": "Henüz kayıtlı kontrol yok",
                                 "es": "Aún no hay comprobaciones registradas"},
    "لا فحوصات مسجّلة بعد — أول فحص (يدوي أو دوري) سيظهر هنا.":
        {"en": "No checks recorded yet — the first check (manual or periodic) will appear here.",
         "fr": "Aucune vérification enregistrée — la première vérification (manuelle ou périodique) apparaîtra ici.",
         "tr": "Henüz kayıtlı kontrol yok — ilk kontrol (manuel veya periyodik) burada görünecek.",
         "es": "Aún no hay comprobaciones registradas — la primera (manual o periódica) aparecerá aquí."},
    "أول فحص (يدوي أو دوري) سيظهر هنا.":
        {"en": "The first check (manual or periodic) will appear here.",
         "fr": "La première vérification (manuelle ou périodique) apparaîtra ici.",
         "tr": "İlk kontrol (manuel veya periyodik) burada görünecek.",
         "es": "La primera comprobación (manual o periódica) aparecerá aquí."},
    "لا محاولات مطابقة": {"en": "No matching attempts",
                              "fr": "Aucune tentative correspondante",
                              "tr": "Eşleşen deneme yok",
                              "es": "Sin intentos coincidentes"},
    "لا تعليقات وصول بعد.": {"en": "No access suspensions yet.",
                                  "fr": "Aucune suspension d'accès pour le moment.",
                                  "tr": "Henüz erişim askıya alma yok.",
                                  "es": "Aún no hay suspensiones de acceso."},
    "لا حظور بعد.": {"en": "No blocks yet.",
                       "fr": "Aucun blocage pour le moment.",
                       "tr": "Henüz engelleme yok.",
                       "es": "Aún no hay bloqueos."},
    "لا منافذ بعد — اختر المنافذ أعلاه وفعّل الخدمة، أو اضغط «فحص اللوب» لقراءة الواقع من الراوتر.":
        {"en": "No ports yet — pick ports above and enable the service, or click \"Loop check\" to read live state from the router.",
         "fr": "Aucun port pour le moment — choisissez des ports ci-dessus et activez le service, ou cliquez sur « Vérifier la boucle » pour lire l'état en direct depuis le routeur.",
         "tr": "Henüz bağlantı noktası yok — yukarıdan bağlantı noktası seçin ve hizmeti etkinleştirin veya \"Döngü kontrolü\"ne tıklayarak yönlendiriciden canlı durumu okuyun.",
         "es": "Aún no hay puertos — elige puertos arriba y activa el servicio, o pulsa «Comprobar bucle» para leer el estado en vivo desde el router."},
    # حقول/أزرار/أعمدة شائعة
    "العنوان المستلَم": {"en": "Received address", "fr": "Adresse reçue",
                           "tr": "Alınan adres", "es": "Dirección recibida"},
    "IP الجديد": {"en": "New IP", "fr": "Nouvelle IP", "tr": "Yeni IP", "es": "Nueva IP"},
    "IP الحالي": {"en": "Current IP", "fr": "IP actuelle", "tr": "Mevcut IP", "es": "IP actual"},
    "تنزيل (Kbps)": {"en": "Download (Kbps)", "fr": "Téléchargement (Kbps)",
                       "tr": "İndirme (Kbps)", "es": "Descarga (Kbps)"},
    "رفع (Kbps)": {"en": "Upload (Kbps)", "fr": "Envoi (Kbps)",
                     "tr": "Yükleme (Kbps)", "es": "Subida (Kbps)"},
    "قيم بالكيلوبت/ثانية. مثال: 51200 ≈ 50 ميجا.":
        {"en": "Values in Kbps. Example: 51200 ≈ 50 Mbps.",
         "fr": "Valeurs en Kbps. Exemple : 51200 ≈ 50 Mbps.",
         "tr": "Değerler Kbps cinsindendir. Örnek: 51200 ≈ 50 Mbps.",
         "es": "Valores en Kbps. Ejemplo: 51200 ≈ 50 Mbps."},
    "عنوان IPv4 صالح فقط. سيُرسل في Framed-IP-Address.":
        {"en": "Valid IPv4 address only. Will be sent in Framed-IP-Address.",
         "fr": "Adresse IPv4 valide uniquement. Sera envoyée dans Framed-IP-Address.",
         "tr": "Yalnızca geçerli IPv4 adresi. Framed-IP-Address ile gönderilecek.",
         "es": "Solo direcciones IPv4 válidas. Se enviará en Framed-IP-Address."},
    "اتركه فارغًا للإبقاء عليه.":
        {"en": "Leave empty to keep it.",
         "fr": "Laissez vide pour le conserver.",
         "tr": "Korumak için boş bırakın.",
         "es": "Déjelo vacío para conservarlo."},
    "عنوان IP أو MAC (AA:BB:CC:DD:EE:FF)":
        {"en": "IP or MAC address (AA:BB:CC:DD:EE:FF)",
         "fr": "Adresse IP ou MAC (AA:BB:CC:DD:EE:FF)",
         "tr": "IP veya MAC adresi (AA:BB:CC:DD:EE:FF)",
         "es": "Dirección IP o MAC (AA:BB:CC:DD:EE:FF)"},
    "اسم المستخدم / المجموعة / المعرّف":
        {"en": "Username / Group / ID",
         "fr": "Nom d'utilisateur / Groupe / ID",
         "tr": "Kullanıcı adı / Grup / ID",
         "es": "Usuario / Grupo / ID"},
    "السبب (يظهر للمستخدم في رسالة المنع)":
        {"en": "Reason (shown to the user in the deny message)",
         "fr": "Motif (affiché à l'utilisateur dans le message de refus)",
         "tr": "Sebep (kullanıcıya reddetme mesajında gösterilir)",
         "es": "Motivo (se muestra al usuario en el mensaje de denegación)"},
    "مثل: صيانة مجدولة / إيقاف مؤقت للمراجعة":
        {"en": "e.g. Scheduled maintenance / temporary hold for review",
         "fr": "Ex. : maintenance planifiée / suspension temporaire pour examen",
         "tr": "Örn.: Planlı bakım / inceleme için geçici durdurma",
         "es": "Ej.: mantenimiento programado / suspensión temporal para revisión"},
    "سبب الحظر — يظهر في القائمة":
        {"en": "Block reason — shown in the list",
         "fr": "Motif du blocage — affiché dans la liste",
         "tr": "Engelleme nedeni — listede gösterilir",
         "es": "Motivo del bloqueo — visible en la lista"},
    "مدّة التعليق": {"en": "Suspension duration", "fr": "Durée de la suspension",
                       "tr": "Askıya alma süresi", "es": "Duración de la suspensión"},
    "مدّة الحظر": {"en": "Block duration", "fr": "Durée du blocage",
                     "tr": "Engelleme süresi", "es": "Duración del bloqueo"},
    "مدّة الحظر التلقائي (دقيقة)":
        {"en": "Auto-block duration (minutes)",
         "fr": "Durée du blocage automatique (minutes)",
         "tr": "Otomatik engelleme süresi (dakika)",
         "es": "Duración del bloqueo automático (minutos)"},
    "المدّة": {"en": "Duration", "fr": "Durée", "tr": "Süre", "es": "Duración"},
    "عتبة المحاولات": {"en": "Attempt threshold", "fr": "Seuil de tentatives",
                          "tr": "Deneme eşiği", "es": "Umbral de intentos"},
    "نافذة العدّ (ثانية)": {"en": "Count window (seconds)",
                              "fr": "Fenêtre de comptage (secondes)",
                              "tr": "Sayım penceresi (saniye)",
                              "es": "Ventana de conteo (segundos)"},
    "يحظر حسب": {"en": "Block by", "fr": "Bloquer par",
                   "tr": "Engelleme ölçütü", "es": "Bloquear por"},
    "تفعيل الحظر التلقائي عند تكرار محاولات الدخول الفاشلة (fail2ban)":
        {"en": "Enable automatic block on repeated failed logins (fail2ban)",
         "fr": "Activer le blocage automatique après échecs répétés (fail2ban)",
         "tr": "Tekrarlanan başarısız girişlerde otomatik engellemeyi etkinleştir (fail2ban)",
         "es": "Activar bloqueo automático tras intentos fallidos repetidos (fail2ban)"},
    "منع دخول البطاقات بعنوان MAC عشوائي (خاص)":
        {"en": "Block card logins from random (private) MAC addresses",
         "fr": "Bloquer les connexions de cartes depuis des MAC aléatoires (privées)",
         "tr": "Rastgele (özel) MAC adresleriyle kart girişini engelle",
         "es": "Bloquear inicios de sesión de tarjetas desde MAC aleatorias (privadas)"},
    "منع دخول المشتركين بعنوان MAC عشوائي (خاص)":
        {"en": "Block subscriber logins from random (private) MAC addresses",
         "fr": "Bloquer les connexions d'abonnés depuis des MAC aléatoires (privées)",
         "tr": "Rastgele (özel) MAC adresleriyle abone girişini engelle",
         "es": "Bloquear inicios de sesión de abonados desde MAC aleatorias (privadas)"},
    "ملاحظة: عند المصادقة لا يتوفّر إلا عنوان IP الراوتر (NAS) لا جهاز العميل — لذا «حظر IP» قد يحظر كل من خلف الراوتر. «MAC» أدقّ لتمييز الجهاز.":
        {"en": "Note: at authentication only the router (NAS) IP is available, not the client device — so blocking by IP may block everyone behind that router. MAC is more precise for identifying a device.",
         "fr": "Remarque : à l'authentification, seule l'IP du routeur (NAS) est disponible, pas celle du client — bloquer par IP peut donc bloquer tout le monde derrière ce routeur. Le MAC est plus précis pour identifier un appareil.",
         "tr": "Not: kimlik doğrulamada yalnızca yönlendirici (NAS) IP'si bulunur, istemci cihazı değil — bu nedenle IP ile engelleme, o yönlendiricinin arkasındaki herkesi engelleyebilir. Cihaz tanımlama için MAC daha kesindir.",
         "es": "Nota: durante la autenticación solo está disponible la IP del router (NAS), no la del cliente — por lo que bloquear por IP puede bloquear a todos los usuarios detrás de ese router. El MAC es más preciso para identificar un dispositivo."},
    "يحكم متى/هل يستطيع المشترك تسجيل الدخول (جدولة أو تعليق مؤقت). ليس حظرًا أمنيًا — عند المنع يرى المستخدم رسالة عربية مهذّبة تشرح السبب.":
        {"en": "Controls when/whether the subscriber can sign in (schedule or temporary hold). Not a security block — when denied, the user sees a polite message explaining why.",
         "fr": "Contrôle quand/si l'abonné peut se connecter (planification ou suspension temporaire). Ce n'est pas un blocage de sécurité — l'utilisateur voit un message poli expliquant pourquoi.",
         "tr": "Abonenin ne zaman/oturum açabilip açamayacağını denetler (zamanlama veya geçici askıya alma). Güvenlik engellemesi değildir — reddedildiğinde kullanıcı, nedenini açıklayan kibar bir mesaj görür.",
         "es": "Controla cuándo/si el abonado puede iniciar sesión (programación o suspensión temporal). No es un bloqueo de seguridad — cuando se deniega, el usuario ve un mensaje cortés con la razón."},
    # «تتبع حالة الأجهزة» — لوحة الفحص
    "إعدادات «تتبع حالة الأجهزة»":
        {"en": "“Device health monitor” settings",
         "fr": "Paramètres du « moniteur de santé des appareils »",
         "tr": "“Cihaz sağlık izleme” ayarları",
         "es": "Configuración de «monitor de salud de dispositivos»"},
    "إعدادات الفحص والتطبيق الحي":
        {"en": "Probe and live-apply settings",
         "fr": "Paramètres de sondage et d'application en direct",
         "tr": "Sondaj ve canlı uygulama ayarları",
         "es": "Ajustes de sondeo y aplicación en vivo"},
    "التطبيق الحي على الراوترات":
        {"en": "Live apply to routers",
         "fr": "Application en direct aux routeurs",
         "tr": "Yönlendiricilere canlı uygulama",
         "es": "Aplicación en vivo a los routers"},
    "تركيب الإعدادات على المايكروتيك / السيرفر":
        {"en": "Install settings on MikroTik / server",
         "fr": "Installer la configuration sur le MikroTik / serveur",
         "tr": "Ayarları MikroTik / sunucuya kur",
         "es": "Instalar configuración en el MikroTik / servidor"},
    "التنفيذ على الراوتر — منفذًا منفذًا":
        {"en": "Execution on the router — port by port",
         "fr": "Exécution sur le routeur — port par port",
         "tr": "Yönlendiricide yürütme — bağlantı noktası bağlantı noktasına",
         "es": "Ejecución en el router — puerto a puerto"},
    "استخدم زر «تركيب» في جدول حالة المنافذ أدناه لإصلاحها منفذًا منفذًا.":
        {"en": "Use the “Install” button in the port status table below to fix them port by port.",
         "fr": "Utilisez le bouton « Installer » dans le tableau d'état des ports ci-dessous pour les corriger port par port.",
         "tr": "Aşağıdaki bağlantı noktası durum tablosundaki “Kur” düğmesini kullanarak bunları bağlantı noktası bağlantı noktasına düzeltin.",
         "es": "Use el botón «Instalar» en la tabla de estado de puertos a continuación para corregirlos puerto por puerto."},
    "القاعدة على الراوتر": {"en": "Rule on the router",
                               "fr": "Règle sur le routeur",
                               "tr": "Yönlendirici kuralı",
                               "es": "Regla en el router"},
    "«القاعدة على الراوتر» من آخر قراءة فعلية (فحص حي أو دوري) — وهي الحقيقة، لا الحالة المحفوظة. أزرار تركيب/إزالة تنفّذ على المنفذ الواحد فورًا.":
        {"en": "“Rule on the router” comes from the latest actual reading (live or periodic probe) — that's the ground truth, not the saved state. Install/remove buttons act on a single port instantly.",
         "fr": "« Règle sur le routeur » provient de la dernière lecture réelle (sonde en direct ou périodique) — c'est la vérité, pas l'état enregistré. Les boutons Installer/Supprimer agissent instantanément sur un port.",
         "tr": "“Yönlendirici kuralı” en son gerçek okumadan (canlı veya periyodik sonda) gelir — kayıtlı durum değil, gerçek bu. Kur/Kaldır düğmeleri tek bir bağlantı noktasında anında uygulanır.",
         "es": "«Regla en el router» proviene de la última lectura real (sondeo en vivo o periódico) — esa es la verdad, no el estado guardado. Los botones Instalar/Quitar actúan sobre un único puerto al instante."},
    "قاعدة الكشف غير مركّبة فعليًا على":
        {"en": "Detection rule is not actually installed on",
         "fr": "La règle de détection n'est pas réellement installée sur",
         "tr": "Algılama kuralı şurada gerçekten kurulu değil:",
         "es": "La regla de detección no está realmente instalada en"},
    "قاعدة غير مركّبة": {"en": "Rule not installed",
                            "fr": "Règle non installée",
                            "tr": "Kural kurulu değil",
                            "es": "Regla no instalada"},
    "قواعد مفقودة":   {"en": "Missing rules", "fr": "Règles manquantes",
                         "tr": "Eksik kurallar", "es": "Reglas faltantes"},
    "حالة اللوب":     {"en": "Loop status", "fr": "État de la boucle",
                         "tr": "Döngü durumu", "es": "Estado del bucle"},
    "فحص اللوب دوريًا في الخلفية":
        {"en": "Periodic loop probe in the background",
         "fr": "Sonde de boucle périodique en arrière-plan",
         "tr": "Arka planda periyodik döngü sondası",
         "es": "Sondeo periódico de bucle en segundo plano"},
    "الفترة بين الفحوصات (دقائق)":
        {"en": "Interval between checks (minutes)",
         "fr": "Intervalle entre les vérifications (minutes)",
         "tr": "Kontroller arasındaki süre (dakika)",
         "es": "Intervalo entre comprobaciones (minutos)"},
    "الفحص الدوري التلقائي":
        {"en": "Automatic periodic check",
         "fr": "Vérification périodique automatique",
         "tr": "Otomatik periyodik kontrol",
         "es": "Comprobación periódica automática"},
    "الفحص الدوري يقرأ حالة كل المنافذ المفعّلة تلقائيًا ويسجّل النتيجة في السجل أدناه، ويفتح تنبيهًا ذكيًا فور اكتشاف لوب. أدنى فترة 5 دقائق (دقة دورة الخادم).":
        {"en": "The periodic check reads the state of every enabled port automatically, logs the result below, and opens a smart alert as soon as a loop is detected. Minimum interval is 5 minutes (server cycle granularity).",
         "fr": "La vérification périodique lit automatiquement l'état de chaque port activé, enregistre le résultat ci-dessous et ouvre une alerte intelligente dès qu'une boucle est détectée. Intervalle minimum : 5 minutes (granularité du cycle serveur).",
         "tr": "Periyodik kontrol, etkin tüm bağlantı noktalarının durumunu otomatik olarak okur, sonucu aşağıdaki günlüğe yazar ve döngü tespit edilir edilmez akıllı bir uyarı açar. Asgari aralık 5 dakikadır (sunucu döngü çözünürlüğü).",
         "es": "La comprobación periódica lee automáticamente el estado de cada puerto activado, registra el resultado a continuación y abre una alerta inteligente en cuanto detecta un bucle. Intervalo mínimo: 5 minutos (granularidad del ciclo del servidor)."},
    "يفحص النظام كل الأجهزة المُراقَبة تلقائيًا في الخلفية، يسجّل كل دورة في «سجل الفحوصات» ويُطلق التنبيهات عند الانقطاع. حدّد الفترة بين الفحوصات بالدقائق (أدنى دقة فعلية: دقيقة).":
        {"en": "The system checks every monitored device automatically in the background, logs every round in the “Checks log”, and fires alerts on outages. Set the interval between checks in minutes (minimum effective resolution: one minute).",
         "fr": "Le système vérifie automatiquement chaque appareil surveillé en arrière-plan, consigne chaque cycle dans le « Journal des vérifications » et déclenche des alertes en cas de panne. Définissez l'intervalle entre les vérifications en minutes (résolution minimale effective : une minute).",
         "tr": "Sistem, izlenen her cihazı arka planda otomatik kontrol eder, her turu “Kontrol günlüğü”ne kaydeder ve kesintilerde uyarı tetikler. Kontroller arasındaki süreyi dakika olarak ayarlayın (etkili asgari çözünürlük: bir dakika).",
         "es": "El sistema comprueba automáticamente todos los dispositivos monitoreados en segundo plano, registra cada ciclo en el «Registro de comprobaciones» y dispara alertas ante cortes. Establezca el intervalo entre comprobaciones en minutos (resolución mínima efectiva: un minuto)."},
    "عند التفعيل يكتب النظام فعليًا على المايكروتيك/السيرفر (عنوان IP/بوابة + تجاوز Hotspot + Netwatch). أبقِه مُطفأً ما لم ترِد تطبيق الخطط على الأجهزة الحقيقية — مُطفأ افتراضيًا للأمان.":
        {"en": "When enabled, the system actually writes to the MikroTik/server (IP/gateway + Hotspot bypass + Netwatch). Leave it off unless you want to apply plans to real hardware — off by default for safety.",
         "fr": "Une fois activé, le système écrit réellement sur le MikroTik/serveur (IP/passerelle + contournement Hotspot + Netwatch). Laissez-le désactivé sauf si vous voulez appliquer les plans au matériel réel — désactivé par défaut pour la sécurité.",
         "tr": "Etkinleştirildiğinde, sistem MikroTik/sunucuya gerçekten yazar (IP/ağ geçidi + Hotspot atlatması + Netwatch). Planları gerçek donanıma uygulamak istemiyorsanız kapalı bırakın — güvenlik için varsayılan olarak kapalı.",
         "es": "Cuando se activa, el sistema escribe realmente en el MikroTik/servidor (IP/puerta de enlace + omisión de Hotspot + Netwatch). Déjelo desactivado a menos que quiera aplicar planes al hardware real — desactivado por defecto por seguridad."},
    "إصدار المايكروتيك": {"en": "MikroTik version",
                            "fr": "Version MikroTik", "tr": "MikroTik sürümü",
                            "es": "Versión MikroTik"},
    "الإصدار 6 (RouterOS v6)":
        {"en": "Version 6 (RouterOS v6)",
         "fr": "Version 6 (RouterOS v6)", "tr": "Sürüm 6 (RouterOS v6)",
         "es": "Versión 6 (RouterOS v6)"},
    "الإصدار 7 (RouterOS v7)":
        {"en": "Version 7 (RouterOS v7)",
         "fr": "Version 7 (RouterOS v7)", "tr": "Sürüm 7 (RouterOS v7)",
         "es": "Versión 7 (RouterOS v7)"},
    "الإصدار 7 يستخدم WireGuard تلقائيًا (أحدث وأسرع).":
        {"en": "Version 7 uses WireGuard automatically (newer and faster).",
         "fr": "La version 7 utilise WireGuard automatiquement (plus récent et plus rapide).",
         "tr": "Sürüm 7 otomatik olarak WireGuard kullanır (daha yeni ve daha hızlı).",
         "es": "La versión 7 usa WireGuard automáticamente (más reciente y más rápido)."},
    "PPTP (أبسط، أقل أمانًا)":
        {"en": "PPTP (simpler, less secure)",
         "fr": "PPTP (plus simple, moins sécurisé)",
         "tr": "PPTP (daha basit, daha az güvenli)",
         "es": "PPTP (más simple, menos seguro)"},
    "SSTP (موصى به — يعمل خلف الجدران)":
        {"en": "SSTP (recommended — works behind firewalls)",
         "fr": "SSTP (recommandé — fonctionne derrière les pare-feu)",
         "tr": "SSTP (önerilir — güvenlik duvarlarının arkasında çalışır)",
         "es": "SSTP (recomendado — funciona detrás de cortafuegos)"},
    "اربط مايكروتيك جديدًا بضغطة واحدة. اختر إصدار جهازك واضغط الزر، ثم انسخ السكربت أو نزّله والصقه في طرفية المايكروتيك.":
        {"en": "Connect a new MikroTik in one click. Pick your device's version, press the button, then copy the script (or download it) and paste it into the MikroTik terminal.",
         "fr": "Connectez un nouveau MikroTik en un clic. Choisissez la version de votre appareil, appuyez sur le bouton, puis copiez le script (ou téléchargez-le) et collez-le dans le terminal MikroTik.",
         "tr": "Yeni bir MikroTik'i tek tıklamayla bağlayın. Cihazınızın sürümünü seçin, düğmeye basın ve ardından komut dosyasını kopyalayın (veya indirin) ve MikroTik terminaline yapıştırın.",
         "es": "Conecte un nuevo MikroTik con un clic. Elija la versión de su dispositivo, pulse el botón y luego copie el script (o descárguelo) y péguelo en la terminal del MikroTik."},
    "تنزيل ملف .rsc": {"en": "Download .rsc file", "fr": "Télécharger le fichier .rsc",
                          "tr": ".rsc dosyasını indir", "es": "Descargar archivo .rsc"},
    # CoA + live control
    "إرسال CoA":   {"en": "Send CoA", "fr": "Envoyer CoA", "tr": "CoA gönder", "es": "Enviar CoA"},
    "تطبيق IP حيّ": {"en": "Live IP apply", "fr": "Application IP en direct",
                      "tr": "Canlı IP uygulama", "es": "Aplicación de IP en vivo"},
    "تطبيق IP حيّ عبر CoA":
        {"en": "Live IP apply via CoA",
         "fr": "Application IP en direct via CoA",
         "tr": "CoA ile canlı IP uygulama",
         "es": "Aplicación de IP en vivo vía CoA"},
    "تطبيق تغيير IP حيّ على جلسة عبر CoA (بدون قطع)":
        {"en": "Apply a live IP change to a session via CoA (no disconnect)",
         "fr": "Appliquer un changement d'IP en direct à une session via CoA (sans coupure)",
         "tr": "Bir oturuma CoA ile canlı IP değişikliği uygula (bağlantı kesilmeden)",
         "es": "Aplicar un cambio de IP en vivo a una sesión vía CoA (sin desconexión)"},
    "تطبيق سرعة حيّة (CoA — بدون فصل)":
        {"en": "Live speed apply (CoA — no disconnect)",
         "fr": "Application de vitesse en direct (CoA — sans coupure)",
         "tr": "Canlı hız uygulama (CoA — bağlantı kesilmeden)",
         "es": "Aplicación de velocidad en vivo (CoA — sin desconexión)"},
    "تطبيق سرعة حيّة عبر CoA":
        {"en": "Live speed apply via CoA",
         "fr": "Application de vitesse en direct via CoA",
         "tr": "CoA ile canlı hız uygulama",
         "es": "Aplicación de velocidad en vivo vía CoA"},
    "تغيير IP المتصل (CoA — بدون فصل)":
        {"en": "Change connected IP (CoA — no disconnect)",
         "fr": "Changer l'IP connectée (CoA — sans coupure)",
         "tr": "Bağlı IP'yi değiştir (CoA — bağlantı kesilmeden)",
         "es": "Cambiar IP conectada (CoA — sin desconexión)"},
    "تغيير IP المتصل عبر CoA":
        {"en": "Change connected IP via CoA",
         "fr": "Changer l'IP connectée via CoA",
         "tr": "Bağlı IP'yi CoA ile değiştir",
         "es": "Cambiar IP conectada vía CoA"},
    "اختر جلسة من القائمة أدناه ثم انقر زرّ ↻ في عمود الإجراءات لإرسال CoA-Request مع Framed-IP-Address الجديد ـ بدون قطع الجلسة.":
        {"en": "Pick a session from the list below, then click the ↻ button in the actions column to send a CoA-Request with the new Framed-IP-Address — without dropping the session.",
         "fr": "Choisissez une session ci-dessous, puis cliquez sur le bouton ↻ dans la colonne actions pour envoyer une CoA-Request avec la nouvelle Framed-IP-Address — sans interrompre la session.",
         "tr": "Aşağıdaki listeden bir oturum seçin, ardından eylemler sütunundaki ↻ düğmesine tıklayarak yeni Framed-IP-Address ile bir CoA-Request gönderin — oturumu düşürmeden.",
         "es": "Elija una sesión de la lista a continuación y haga clic en el botón ↻ de la columna de acciones para enviar una CoA-Request con la nueva Framed-IP-Address — sin cortar la sesión."},
    "يُرسل النظام CoA-Request إلى المايكروتيك/السيرفر مع Framed-IP-Address الجديد فيُغيَّر الـ IP فورًا دون قطع الجلسة. مُثبت حيًّا على PPPoE.":
        {"en": "The system sends a CoA-Request to the MikroTik/server with the new Framed-IP-Address, so the IP changes immediately without dropping the session. Verified live on PPPoE.",
         "fr": "Le système envoie une CoA-Request au MikroTik/serveur avec la nouvelle Framed-IP-Address, l'IP change immédiatement sans interrompre la session. Vérifié en direct sur PPPoE.",
         "tr": "Sistem, MikroTik/sunucuya yeni Framed-IP-Address ile bir CoA-Request gönderir; IP, oturum düşürülmeden anında değişir. PPPoE üzerinde canlı olarak doğrulanmıştır.",
         "es": "El sistema envía una CoA-Request al MikroTik/servidor con la nueva Framed-IP-Address, por lo que la IP cambia de inmediato sin cortar la sesión. Verificado en vivo en PPPoE."},
    "يُرسل النظام Mikrotik-Rate-Limit الجديد عبر CoA فتُطبَّق السرعة على الجلسة الجارية فورًا (PPPoE وHotspot).":
        {"en": "The system sends the new Mikrotik-Rate-Limit via CoA, so the speed applies to the running session immediately (PPPoE and Hotspot).",
         "fr": "Le système envoie la nouvelle Mikrotik-Rate-Limit via CoA, et la vitesse s'applique immédiatement à la session en cours (PPPoE et Hotspot).",
         "tr": "Sistem yeni Mikrotik-Rate-Limit'i CoA ile gönderir; hız, çalışan oturuma anında uygulanır (PPPoE ve Hotspot).",
         "es": "El sistema envía el nuevo Mikrotik-Rate-Limit vía CoA, por lo que la velocidad se aplica de inmediato a la sesión en curso (PPPoE y Hotspot)."},
    "جلسات Hotspot غير مدعومة لهذه العملية وسيُعرض ذلك بوضوح في نافذة التأكيد.":
        {"en": "Hotspot sessions are not supported for this operation; that is shown clearly in the confirmation dialog.",
         "fr": "Les sessions Hotspot ne sont pas prises en charge pour cette opération ; cela est clairement indiqué dans la boîte de dialogue de confirmation.",
         "tr": "Bu işlem için Hotspot oturumları desteklenmez; bu durum onay penceresinde açıkça gösterilir.",
         "es": "Las sesiones Hotspot no son compatibles con esta operación; eso se muestra claramente en el cuadro de confirmación."},
    "هذه جلسة Hotspot — مايكروتيك لا يقبل تغيير IP عبر CoA. سيُرفض الطلب وسيُعرض «unsupported». استخدم «تغيير IP» من راوتر الهوتسبوت مباشرة بدلًا من ذلك.":
        {"en": "This is a Hotspot session — MikroTik will not accept an IP change via CoA. The request will be rejected and shown as “unsupported”. Use “Change IP” directly on the Hotspot router instead.",
         "fr": "Ceci est une session Hotspot — MikroTik n'accepte pas le changement d'IP via CoA. La demande sera rejetée et affichée comme « unsupported ». Utilisez plutôt « Changer l'IP » directement sur le routeur Hotspot.",
         "tr": "Bu bir Hotspot oturumudur — MikroTik, CoA ile IP değişikliğini kabul etmez. İstek reddedilir ve “desteklenmiyor” olarak gösterilir. Bunun yerine Hotspot yönlendiricisinde doğrudan “IP'yi değiştir”i kullanın.",
         "es": "Esta es una sesión Hotspot — MikroTik no aceptará un cambio de IP vía CoA. La solicitud se rechazará y se mostrará como «unsupported». Use «Cambiar IP» directamente en el router Hotspot en su lugar."},
    "خدمة مدفوعة — تغيير IP حيّ عبر CoA (RFC 5176) دون قطع":
        {"en": "Paid service — live IP change via CoA (RFC 5176) without disconnect",
         "fr": "Service payant — changement d'IP en direct via CoA (RFC 5176) sans coupure",
         "tr": "Ücretli hizmet — CoA (RFC 5176) ile bağlantı kesilmeden canlı IP değişikliği",
         "es": "Servicio de pago — cambio de IP en vivo vía CoA (RFC 5176) sin desconexión"},
    "طلب تفعيل الخدمة بمواصفاتها":
        {"en": "Request to enable this service",
         "fr": "Demander l'activation de ce service",
         "tr": "Bu hizmetin etkinleştirilmesini iste",
         "es": "Solicitar la activación de este servicio"},
    "طلب تفعيل/زيادة السقف من المالك (طلب مركزي)":
        {"en": "Request activation / cap increase from the owner (central request)",
         "fr": "Demander activation / augmentation de plafond au propriétaire (demande centrale)",
         "tr": "Sahibinden etkinleştirme / üst sınır artışı iste (merkezi istek)",
         "es": "Solicitar activación / aumento de tope al propietario (solicitud central)"},
    "طلب سقف":  {"en": "Cap request", "fr": "Demande de plafond",
                   "tr": "Üst sınır isteği", "es": "Solicitud de tope"},
    # Telegram bot
    "تفعيل إرسال الإشعارات إلى تلجرام":
        {"en": "Enable sending notifications to Telegram",
         "fr": "Activer l'envoi des notifications à Telegram",
         "tr": "Telegram'a bildirim gönderimini etkinleştir",
         "es": "Activar envío de notificaciones a Telegram"},
    "الصق توكن البوت ومعرّف المحادثة/القناة، فعّل الإشعارات، ثم اضغط «اختبار الاتصال».":
        {"en": "Paste the bot token and the chat/channel ID, enable notifications, then press “Test connection”.",
         "fr": "Collez le jeton du bot et l'ID du chat/canal, activez les notifications, puis cliquez sur « Tester la connexion ».",
         "tr": "Bot belirtecini ve sohbet/kanal kimliğini yapıştırın, bildirimleri etkinleştirin ve ardından “Bağlantıyı test et”e basın.",
         "es": "Pegue el token del bot y el ID de chat/canal, active las notificaciones y pulse «Probar conexión»."},
    "توكن البوت (Bot Token)":
        {"en": "Bot Token", "fr": "Jeton du bot (Bot Token)",
         "tr": "Bot belirteci", "es": "Token del bot"},
    "معرّف المحادثة/القناة (Chat ID)":
        {"en": "Chat/Channel ID", "fr": "ID du chat/canal",
         "tr": "Sohbet/kanal kimliği", "es": "ID de chat/canal"},
    "معرّف الموضوع (اختياري)":
        {"en": "Topic ID (optional)", "fr": "ID du sujet (facultatif)",
         "tr": "Konu kimliği (isteğe bağlı)", "es": "ID del tema (opcional)"},
    "التوكن الحالي محفوظ مشفّرًا":
        {"en": "Current token is stored encrypted",
         "fr": "Le jeton actuel est stocké chiffré",
         "tr": "Mevcut belirteç şifrelenmiş olarak saklanır",
         "es": "El token actual está cifrado en almacenamiento"},
    "هذا المفتاح هو سرّ الربط الوحيد. اتركه فارغًا إن كان محفوظًا. لا نعرضه كاملًا في الصفحة.":
        {"en": "This key is the only link secret. Leave empty if already saved. We never show it in full on the page.",
         "fr": "Cette clé est le seul secret de liaison. Laissez vide si elle est déjà enregistrée. Nous ne l'affichons jamais en entier sur la page.",
         "tr": "Bu anahtar, tek bağlantı sırrıdır. Zaten kayıtlıysa boş bırakın. Sayfada hiçbir zaman tam olarak göstermeyiz.",
         "es": "Esta clave es el único secreto de enlace. Déjelo vacío si ya está guardada. Nunca la mostramos completa en la página."},
    "المفتاح يظهر مختصرًا فقط. كل عمليات المزامنة تتم عبر اتصال آمن — لا يمكن استخدام اتصال غير آمن.":
        {"en": "The key is shown abbreviated only. All sync operations use a secure connection — insecure connections are not allowed.",
         "fr": "La clé n'est affichée que de manière abrégée. Toutes les opérations de synchronisation passent par une connexion sécurisée — les connexions non sécurisées ne sont pas autorisées.",
         "tr": "Anahtar yalnızca kısaltılmış olarak gösterilir. Tüm eşitleme işlemleri güvenli bir bağlantı üzerinden yapılır — güvenli olmayan bağlantılara izin verilmez.",
         "es": "La clave se muestra abreviada. Todas las sincronizaciones usan una conexión segura — no se permiten conexiones inseguras."},
    "تحديث الصفحة لرؤية الحالة الجديدة":
        {"en": "Refresh the page to see the new state",
         "fr": "Actualisez la page pour voir le nouvel état",
         "tr": "Yeni durumu görmek için sayfayı yenileyin",
         "es": "Actualice la página para ver el nuevo estado"},
    "تم — تحديث الصفحة": {"en": "Done — refreshing the page",
                            "fr": "Terminé — actualisation de la page",
                            "tr": "Tamam — sayfa yenileniyor",
                            "es": "Hecho — actualizando la página"},
    "التفعيل المحفوظ":  {"en": "Saved activation", "fr": "Activation enregistrée",
                          "tr": "Kayıtlı etkinleştirme", "es": "Activación guardada"},
    "التفعيل المحفوظ لا يطابق الراوتر:":
        {"en": "Saved activation does not match the router:",
         "fr": "L'activation enregistrée ne correspond pas au routeur :",
         "tr": "Kayıtlı etkinleştirme yönlendiriciyle uyuşmuyor:",
         "es": "La activación guardada no coincide con el router:"},
    # Sidebar + reports
    "البوابة/الويب":   {"en": "Portal / Web", "fr": "Portail / Web",
                         "tr": "Portal / Web", "es": "Portal / web"},
    "المتصفح / الجهاز": {"en": "Browser / Device", "fr": "Navigateur / appareil",
                          "tr": "Tarayıcı / cihaz", "es": "Navegador / dispositivo"},
    "الجهاز (المتصفح / عنوان MAC)":
        {"en": "Device (browser / MAC address)",
         "fr": "Appareil (navigateur / adresse MAC)",
         "tr": "Cihaz (tarayıcı / MAC adresi)",
         "es": "Dispositivo (navegador / dirección MAC)"},
    "الشبكة (عنوان / جهاز)":
        {"en": "Network (address / device)",
         "fr": "Réseau (adresse / appareil)",
         "tr": "Ağ (adres / cihaz)",
         "es": "Red (dirección / dispositivo)"},
    "حالات دخول البطاقات":   {"en": "Card login states",
                                  "fr": "États de connexion des cartes",
                                  "tr": "Kart oturum açma durumları",
                                  "es": "Estados de inicio de sesión de tarjetas"},
    "حالات بوابة المشتركين": {"en": "Subscriber portal states",
                                  "fr": "États du portail des abonnés",
                                  "tr": "Abone portalı durumları",
                                  "es": "Estados del portal de abonados"},
    "حالات بوابة متجر البطاقات": {"en": "Card store portal states",
                                          "fr": "États du portail de la boutique de cartes",
                                          "tr": "Kart mağazası portalı durumları",
                                          "es": "Estados del portal de la tienda de tarjetas"},
    "سجل محاولات تسجيل الدخول":
        {"en": "Login attempt log", "fr": "Journal des tentatives de connexion",
         "tr": "Oturum açma deneme günlüğü", "es": "Registro de intentos de inicio de sesión"},
    "سجل محاولات دخول المدراء":
        {"en": "Admin login attempt log",
         "fr": "Journal des tentatives de connexion d'administrateur",
         "tr": "Yönetici oturum açma deneme günlüğü",
         "es": "Registro de intentos de inicio de sesión de administradores"},
    "لوحة الإدارة":     {"en": "Admin panel", "fr": "Panneau d'administration",
                          "tr": "Yönetim paneli", "es": "Panel de administración"},
    "بحث (اسم المدير / عنوان الشبكة)…":
        {"en": "Search (admin name / network address)…",
         "fr": "Rechercher (nom d'admin / adresse réseau)…",
         "tr": "Ara (yönetici adı / ağ adresi)…",
         "es": "Buscar (nombre de admin / dirección de red)…"},
    "بحث (المستخدم / عنوان الشبكة / جهاز الشبكة)…":
        {"en": "Search (user / network address / network device)…",
         "fr": "Rechercher (utilisateur / adresse réseau / appareil réseau)…",
         "tr": "Ara (kullanıcı / ağ adresi / ağ cihazı)…",
         "es": "Buscar (usuario / dirección de red / dispositivo de red)…"},
    "غيّر البحث أو الفلاتر — أو لا توجد محاولات دخول مدراء مسجَّلة في هذه الفترة بعد.":
        {"en": "Change the search or filters — or no admin login attempts are recorded for this period yet.",
         "fr": "Modifiez la recherche ou les filtres — ou aucune tentative de connexion d'administrateur n'est encore enregistrée pour cette période.",
         "tr": "Aramayı veya filtreleri değiştirin — veya bu döneme ait yönetici oturum açma denemesi henüz kayıtlı değil.",
         "es": "Cambie la búsqueda o los filtros — o aún no hay intentos de inicio de sesión de administradores registrados para este período."},
    "غيّر البحث أو الفلاتر — أو لا توجد محاولات مسجَّلة في هذه الفترة بعد.":
        {"en": "Change the search or filters — or no attempts are recorded for this period yet.",
         "fr": "Modifiez la recherche ou les filtres — ou aucune tentative n'est encore enregistrée pour cette période.",
         "tr": "Aramayı veya filtreleri değiştirin — veya bu döneme ait deneme henüz kayıtlı değil.",
         "es": "Cambie la búsqueda o los filtros — o aún no hay intentos registrados para este período."},
    "كل محاولة دخول إلى النظام — نجاحًا أو فشلًا — مع الوقت، المستخدم، الشبكة والجهاز، وسبب الفشل عند وجوده. الترتيب من الأحدث للأقدم. للقراءة فقط.":
        {"en": "Every login attempt — success or failure — with time, user, network and device, and the failure reason when present. Newest first. Read-only.",
         "fr": "Chaque tentative de connexion — réussie ou échouée — avec l'heure, l'utilisateur, le réseau et l'appareil, et le motif d'échec le cas échéant. Plus récent en premier. Lecture seule.",
         "tr": "Her oturum açma denemesi — başarı veya başarısızlık — zaman, kullanıcı, ağ ve cihaz, varsa başarısızlık nedeniyle birlikte. En yeniden eskiye. Salt okunur.",
         "es": "Cada intento de inicio de sesión — éxito o fallo — con hora, usuario, red y dispositivo, y motivo del fallo si lo hay. Más reciente primero. Solo lectura."},
    "كل محاولة دخول مدير إلى لوحة الإدارة — نجاحًا أو فشلًا — مع الوقت، اسم المدير، عنوان الشبكة، المتصفح، وسبب الفشل عند وجوده. الترتيب من الأحدث للأقدم. للقراءة فقط.":
        {"en": "Every admin login attempt — success or failure — with time, admin name, network address, browser, and failure reason if any. Newest first. Read-only.",
         "fr": "Chaque tentative de connexion d'administrateur — réussie ou échouée — avec l'heure, le nom de l'admin, l'adresse réseau, le navigateur et le motif d'échec le cas échéant. Plus récent en premier. Lecture seule.",
         "tr": "Her yönetici oturum açma denemesi — başarı veya başarısızlık — zaman, yönetici adı, ağ adresi, tarayıcı ve varsa başarısızlık nedeni ile birlikte. En yeniden eskiye. Salt okunur.",
         "es": "Cada intento de inicio de sesión de administrador — éxito o fallo — con hora, nombre del admin, dirección de red, navegador y motivo del fallo si lo hay. Más reciente primero. Solo lectura."},
    "كل صف هنا = محاولة تسجيل دخول واحدة. البحث يطابق المستخدم وعنوان الشبكة وجهاز الشبكة، فلتر النتيجة يفرز نجاحًا أو فشلًا، نطاق التاريخ يعمل على وقت المحاولة. مصدر البيانات نفس مصدر «حالات تسجيل الدخول» و«فشل دخول الشبكة».":
        {"en": "Each row here = one login attempt. Search matches user, network address and device; the result filter sorts success vs. failure; the date range applies to the attempt time. Data source is the same as “Login states” and “Network login failures”.",
         "fr": "Chaque ligne ici = une tentative de connexion. La recherche cible l'utilisateur, l'adresse réseau et l'appareil ; le filtre de résultat trie succès vs échec ; la plage de dates s'applique à l'heure de la tentative. Source identique à « États de connexion » et « Échecs de connexion réseau ».",
         "tr": "Buradaki her satır = bir oturum açma denemesi. Arama; kullanıcı, ağ adresi ve cihaz ile eşleşir; sonuç filtresi başarı/başarısızlığı ayırır; tarih aralığı deneme zamanına uygulanır. Veri kaynağı, “Oturum açma durumları” ve “Ağ oturum açma hataları” ile aynıdır.",
         "es": "Cada fila aquí = un intento de inicio de sesión. La búsqueda coincide con usuario, dirección de red y dispositivo; el filtro de resultado ordena éxito vs fallo; el rango de fechas se aplica a la hora del intento. La fuente es la misma que «Estados de inicio de sesión» y «Fallos de inicio de red»."},
    "كل صف هنا = محاولة دخول مدير واحدة. البحث يطابق اسم المدير وعنوان الشبكة، فلتر النتيجة يفرز نجاحًا أو فشلًا، نطاق التاريخ يعمل على وقت المحاولة. مصدر البيانات نفس مصدر «سجل محاولات تسجيل الدخول» مع تثبيت الفاعل على «مدير».":
        {"en": "Each row here = one admin login attempt. Search matches admin name and network address; the result filter sorts success vs. failure; the date range applies to the attempt time. Data source matches the “Login attempt log” pinned to admin actor.",
         "fr": "Chaque ligne ici = une tentative de connexion d'administrateur. La recherche cible le nom de l'admin et l'adresse réseau ; le filtre de résultat trie succès vs échec ; la plage de dates s'applique à l'heure de la tentative. Source identique au « Journal des tentatives de connexion » avec l'acteur fixé sur « admin ».",
         "tr": "Buradaki her satır = bir yönetici oturum açma denemesi. Arama; yönetici adı ve ağ adresi ile eşleşir; sonuç filtresi başarı/başarısızlığı ayırır; tarih aralığı deneme zamanına uygulanır. Veri kaynağı, aktör “yönetici” olarak sabitlenmiş “Oturum açma denemesi günlüğü” ile aynıdır.",
         "es": "Cada fila aquí = un intento de inicio de sesión de administrador. La búsqueda coincide con nombre del admin y dirección de red; el filtro de resultado ordena éxito vs fallo; el rango de fechas se aplica a la hora del intento. La fuente coincide con el «Registro de intentos de inicio de sesión» con el actor fijado en «admin»."},
    # Data connection
    "اتصال بيانات": {"en": "Data connection", "fr": "Connexion de données",
                       "tr": "Veri bağlantısı", "es": "Conexión de datos"},
    "الميزة قيد التهيئة من قِبل المشغّل (لم يُضبط نطاق الاتصال بعد). يمكنك المحاولة لاحقًا.":
        {"en": "The feature is being set up by the operator (the connection range is not configured yet). Please try again later.",
         "fr": "La fonctionnalité est en cours de configuration par l'opérateur (la plage de connexion n'est pas encore définie). Veuillez réessayer plus tard.",
         "tr": "Özellik operatör tarafından kuruluyor (bağlantı aralığı henüz yapılandırılmadı). Daha sonra tekrar deneyin.",
         "es": "La función está siendo configurada por el operador (aún no se ha configurado el rango de conexión). Inténtelo más tarde."},
    "السرعة ثابتة 5 ميجابت — بلا حد للبيانات وبلا قطع.":
        {"en": "Speed is fixed at 5 Mbps — no data cap and no cutoff.",
         "fr": "La vitesse est fixée à 5 Mbit/s — sans plafond de données ni coupure.",
         "tr": "Hız 5 Mbps olarak sabittir — veri sınırı veya kesinti yoktur.",
         "es": "La velocidad es fija a 5 Mbps — sin tope de datos ni corte."},
    "استيراد المشتركين من الراوتر":
        {"en": "Import subscribers from the router",
         "fr": "Importer les abonnés depuis le routeur",
         "tr": "Aboneleri yönlendiriciden içe aktar",
         "es": "Importar abonados desde el router"},
    # Misc context strings
    " محاولة":       {"en": " attempt(s)", "fr": " tentative(s)",
                       "tr": " deneme", "es": " intento(s)"},
    " محاولة مطابقة":{"en": " matching attempt(s)",
                       "fr": " tentative(s) correspondante(s)",
                       "tr": " eşleşen deneme",
                       "es": " intento(s) coincidente(s)"},
    " من ":          {"en": " of ", "fr": " sur ", "tr": " / ", "es": " de "},
    "(1–5000) و":    {"en": "(1–5000) and ", "fr": "(1–5000) et ",
                       "tr": "(1–5000) ve ", "es": "(1–5000) y "},
    "(1–8) و":       {"en": "(1–8) and ", "fr": "(1–8) et ",
                       "tr": "(1–8) ve ", "es": "(1–8) y "},
    ") و":           {"en": ") and ", "fr": ") et ", "tr": ") ve ", "es": ") y "},
    "12د":           {"en": "12 min", "fr": "12 min", "tr": "12 dk", "es": "12 min"},
    "41د":           {"en": "41 min", "fr": "41 min", "tr": "41 dk", "es": "41 min"},
    "٥٠٠":           {"en": "500", "fr": "500", "tr": "500", "es": "500"},
    "عرض التفاصيل": {"en": "Show details", "fr": "Afficher les détails",
                       "tr": "Ayrıntıları göster", "es": "Mostrar detalles"},
    "قيمة الفاتورة بـ": {"en": "Invoice value in",
                             "fr": "Valeur de la facture en",
                             "tr": "Fatura tutarı (",
                             "es": "Valor de la factura en"},
    "— يُحتسب على رصيد المشترك.":
        {"en": "— charged to the subscriber's balance.",
         "fr": "— prélevé sur le solde de l'abonné.",
         "tr": "— abonenin bakiyesinden düşülür.",
         "es": "— se carga al saldo del abonado."},
    "من إجمالي ": {"en": "out of ", "fr": "sur ", "tr": "/ ", "es": "de "},
}


def main() -> None:
    print(f"» تحميل/تحديث {len(TRANSLATIONS)} مدخل لكل لغة …")
    for locale in ("en", "fr", "tr", "es"):
        path = JSON_DIR / f"{locale}.json"
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        added = 0
        for k, v in TRANSLATIONS.items():
            if k in data and data[k]:
                continue
            data[k] = v.get(locale, "")
            if data[k]:
                added += 1
        # write sorted for stable diffs
        ordered = {k: data[k] for k in sorted(data)}
        with path.open("w", encoding="utf-8") as fh:
            json.dump(ordered, fh, ensure_ascii=False, indent=2)
        print(f"  {locale}: أضيف {added} / إجمالي {len(ordered)}")
    print("» إعادة بناء الكتالوجات …")
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "i18n_build_catalogs.py")],
                          capture_output=True, text=True, cwd=str(ROOT))
    print(res.stdout[-1500:])
    if res.returncode != 0:
        print("STDERR:", res.stderr[-500:])
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()
