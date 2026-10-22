# ============================================================
#  صورة Docker لتشغيل بوت بلاغات المخالفات العسكرية
#  تعمل على أي منصة تستضيف حاويات (Zeabur / Hugging Face Spaces / خادم خاص)
#
#  المنصة تضبط متغير PORT تلقائيًا — يسمعه خادم الصحة داخل البوت.
# ============================================================
FROM python:3.12-slim

# إعدادات أساسية: طباعة فورية للسجلات + عدم حفظ ملفات pyc + عدم تخزين pip cache
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# المنطقة الزمنية وشهادات الجذر (للاتصال الآمن بواجهة تلغرام)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# تثبيت المكتبات أولًا (طبقة منفصلة لإعادة البناء بسرعة عند تغيير الكود فقط)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# نسخ الكود كاملًا إلى الصورة
COPY . .

# مجلدات العمل (تُنشأ تلقائيًا أيضًا من config.py)
RUN mkdir -p data media exports logs

# المنفذ: يقرأه البوت من متغير البيئة PORT الذي تضبطه المنصة
EXPOSE 8080

# نقطة التشغيل
CMD ["python", "main.py"]
