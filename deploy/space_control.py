r"""
أداة التحكم بمساحة Hugging Face Space: إعادة تشغيل، فحص الحالة، وقراءة السجلات.

الاستخدام:
  $env:HF_TOKEN = "hf_xxx"
  $env:HF_SPACE = "اسم-المستخدم/mrw-bot"

  .\.venv\Scripts\python.exe deploy\space_control.py status
  .\.venv\Scripts\python.exe deploy\space_control.py logs
  .\.venv\Scripts\python.exe deploy\space_control.py restart
  .\.venv\Scripts\python.exe deploy\space_control.py wait
"""

from __future__ import annotations

import os
import sys
import time

from huggingface_hub import HfApi

SPACE = os.getenv("HF_SPACE", "azzam1992/mrw-bot")
TOKEN = os.getenv("HF_TOKEN", "")

api = HfApi(token=TOKEN)


def show_status() -> str:
    info = api.space_info(SPACE)
    runtime = info.runtime
    stage = runtime.stage if runtime else "غير معروف"
    print(f"المساحة: {info.id} | خاصة: {info.private}")
    print(f"الحالة: {stage} | العتاد: {runtime.hardware if runtime else '-'}")
    if runtime and runtime.stage in {"BUILD_ERROR", "RUNTIME_ERROR"}:
        print("--- آخر جزء من رسالة الخطأ ---")
        raw = getattr(runtime, "raw_info", None) or {}
        message = (
            getattr(runtime, "error_message", None)
            or raw.get("errorMessage")
            or getattr(runtime, "errorMessage", None)
            or "(لا تفاصيل)"
        )
        print(str(message)[-800:])
    print("الأسرار:", list(api.get_space_secrets(SPACE).keys()))
    print("المتغيرات:", list(api.get_space_variables(SPACE).keys()))
    return stage


def show_logs(last_lines: int = 60) -> None:
    lines = list(api.fetch_space_logs(SPACE))
    for line in lines[-last_lines:]:
        print(line, end="" if line.endswith("\n") else "\n")


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action == "status":
        show_status()
    elif action == "logs":
        show_logs()
    elif action == "restart":
        api.restart_space(SPACE)
        print("▶ أُرسل طلب إعادة التشغيل")
        for _ in range(60):
            time.sleep(10)
            stage = show_status()
            if stage in {"RUNNING", "BUILD_ERROR", "RUNTIME_ERROR"}:
                break
    elif action == "wait":
        deadline = time.time() + 900
        while time.time() < deadline:
            stage = show_status()
            if stage in {"RUNNING", "BUILD_ERROR", "RUNTIME_ERROR"}:
                print("▶ انتهت الانتظار")
                return 0
            time.sleep(15)
        print("⚠️ انتهت المهلة قبل جاهزية المساحة")
    else:
        print("أمر غير معروف. المتاح: status | logs | restart | wait")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
