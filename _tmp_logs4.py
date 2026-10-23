"""قراءة سجلات المساحة الحيّة وفحص الأخطاء."""

import os

from huggingface_hub import HfApi

api = HfApi(token=os.environ["HF_TOKEN"])
lines = list(api.fetch_space_logs(os.environ["HF_SPACE"]))
print(f"عدد الأسطر: {len(lines)}")
print("=" * 60)
for line in lines[-40:]:
    print(line[:200])

print("\n" + "=" * 60)
bad = [l for l in lines if any(k in l for k in ("Traceback", "ERROR", "CRITICAL", "Exception", "409", "Conflict", "No @spaces"))]
print(f"عدد أسطر الأخطاء في كامل السجل: {len(bad)}")
for l in bad[-12:]:
    print(" ⚠️", l[:200])
