# ==================================================================
# رابط سريع مع لوحة سطح المكتب — يُستورد في نهاية panel.py
# ==================================================================
# لم يتم تعديل panel.py مباشرة لتجنب كسر البنية — بدلاً من ذلك نُعدّل
# السلوك عبر استبدال الدالة عند الاستيراد (آمن وسريع).

import desktop_panel.panel as _pmod  # لا يُستعمل مباشرة — فقط للتأكد من وجوده

# عند استيراد هذا الملف من panel.py (أو تشغيله مباشرة)، نربطه تلقائيًا:
try:
    import desktop_panel.panel as panel_mod

    # ربط التحدث المباشر
    original_toggle = None  # سيُعيّن من الزر نفسه عند الاستيراد

    # ربط التنبيه الذكي على apply_overview
    if hasattr(panel_mod.MainWindow, "apply_overview"):
        _orig_ov = panel_mod.MainWindow.apply_overview
        def _patched_overview(self, data):
            return apply_overview_smart(self, data)
        panel_mod.MainWindow.apply_overview = _patched_overview
except Exception as exc:
    import logging
    logging.getLogger("addon").warning("لم يُربط الإضافة: %s", exc)
