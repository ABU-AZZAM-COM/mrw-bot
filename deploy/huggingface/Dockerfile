# ============================================================
# صورة Docker لتشغيل بوت بلاغات المخالفات العسكرية
# مخصّصة للمنصات المجانية مثل Hugging Face Spaces
# ============================================================
FROM python:3.12-slim

# إعدادات أساسية: طباعة فورية للسجلات + عدم حفظ ملفات pyc + عدم تخزين pip cache
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# تحديث المنطقة الزمنية وضمان تحديثات النظام
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# تثبيت المكتبات أولًا (طبقة منفصلة لإعادة البناء بسرعة عند تغيير الكود)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# نسخ الكود كاملًا إلى الصورة
COPY . .

# مجلدات العمل (تُنشأ تلقائيًا أيضًا من config.py)
RUN mkdir -p data media exports logs

# المنفذ الذي تستخدمه المنصة للفحص
EXPOSE 7860

# نقطة التشغيل
CMD ["python", "main.py"]
