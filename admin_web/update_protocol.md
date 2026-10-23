# آلية التحديث المضمونة — admin_web/ فقط (من أجل عدم فقدان أي شيء)
# القاعدة: desktop_panel/ مُمنوع التعديل، والنشر عبر /admin/ فقط.

STEPS = [
    "1) git checkout main && git pull; git branch update-$(date +%Y%m%d)",
    "2) عدّل فقط admin_web/lib/*.dart (BlockedPage / main.dart / pages.dart)",
    "3) flutter build web --release --base-href=/admin/  → EXIT=0",
    "4) cp -r admin_web/dist admin_web/dist-backup-YYYYMMDD",
    "5) git add admin_web/lib/ admin_web/dist/; git commit -m '...' (بند واحد)",
    "6) export HF_TOKEN=... HF_SPACE=azzam1992/mrw-bot; python deploy/deploy_to_hf.py (...)",
    "7) تحقق: python verify_deploy.py (يرسل لـ /api/admin/vtypes — لا /health)",
    "8) تدوير HF_TOKEN + إلغاء bot token عبر BotFather فورًا",
    "9) لا تستخدم tail / \ في المسار؛ استخدم python مباشرًا أو > file",
]

CHECKLIST = {
    "build_exit_0": True,
    "dist_copied": True,
    "single_commit": True,
    "no_desktop_panel_edit": True,
    "no_hardcoded_tokens": True,
    "verify_vtypes_not_health": True,
    "rotate_after_session": True,
}

# الملفات المحمية من الضياع
GITIGNORE_REQUIRED = [
    "desktop_panel/panel_config.json",
    ".env",
    "build/",
    ".venv/",
]
