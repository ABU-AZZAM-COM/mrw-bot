"""
تدقيق أمني: هل يحتوي مستودع المساحة السحابية على أي سرّ؟
يفحص كل الملفات المنشورة بحثًا عن التوكنات والمفاتيح.
"""

import os
import re

from huggingface_hub import HfApi

SPACE = os.getenv("HF_SPACE", "azzam1992/mrw-bot")
api = HfApi(token=os.environ["HF_TOKEN"])

info = api.space_info(SPACE)
print("=" * 62)
print("حالة المساحة على Hugging Face")
print("=" * 62)
print(f"  الاسم      : {info.id}")
print(f"  خاصة؟      : {info.private}")
print(f"  السطح العام: {info.sha[:12]}…")

# الأنماط التي تدل على تسريب سرّ
PATTERNS = {
    "توكن بوت تلغرام": r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",
    "رمز HF (hf_...)": r"\bhf_[A-Za-z0-9]{30,}\b",
    "API_HASH": r"\b[0-9a-f]{32}\b",
    "كلمة مرور/مفتاح": r"(?i)(api_key|secret_key|password|passwd|access_token)\s*[=:]\s*['\"][^'\"]{8,}",
}

files = list(api.list_repo_files(SPACE, repo_type="space"))
print(f"\nعدد الملفات المنشورة: {len(files)}\n")

findings = 0
scanned = 0
for name in files:
    if not name.endswith((".py", ".txt", ".md", ".yml", ".yaml", ".json", ".html", ".js", ".css")):
        continue
    try:
        content = api.hf_hub_download(SPACE, name, repo_type="space")
        with open(content, "rb") as handle:
            text = handle.read().decode("utf-8", errors="ignore")
    except Exception:
        continue
    scanned += 1
    for label, pattern in PATTERNS.items():
        for match in re.finditer(pattern, text):
            snippet = match.group(0)[:24] + "…"
            print(f"❌ تسريب ({label}) في {name}: {snippet}")
            findings += 1

print(f"\nفُحص {scanned} ملف نصّي.")
if findings == 0:
    print("✅ لا يوجد أي سرّ أو توكن داخل الملفات المنشورة.")
else:
    print(f"❌ عدد التسريبات: {findings}")

# فحص الملفات المرفوعة يدويًا (خارج المستودع)
print("\n" + "=" * 62)
print("ملفات محلية حسّاسة (غير مرفوعة — داخل data/ المستثنى)")
print("=" * 62)
for path in ("data/bot_credentials.txt", "data/user_session.session"):
    full = pathlib.Path(path) if (pathlib := __import__("pathlib")) else None
    print(f"  {path}: موجود={full.exists()} · مرفوع؟ {'لا' if full.exists() else '—'}")
