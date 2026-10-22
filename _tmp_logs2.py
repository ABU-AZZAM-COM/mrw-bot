import os

from huggingface_hub import HfApi

api = HfApi(token=os.environ["HF_TOKEN"])
lines = list(api.fetch_space_logs(os.environ["HF_SPACE"]))

markers = [
    "\u062c\u0627\u0647\u0632",          # جاهز
    "\u062a\u0648\u0642\u0641",          # توقف
    "Conflict",                         # تعارض
    "Traceback",                        # أثر خطأ
    "Error",                            # خطأ
    "\u062a\u0634\u063a\u064a\u0644",    # تشغيل
    "\u0646\u0628\u0636\u0629",          # نبضة
]
print(f"عدد الأسطر: {len(lines)}\n" + "=" * 55)
for line in lines:
    if any(marker in line for marker in markers):
        print(line[:190])
