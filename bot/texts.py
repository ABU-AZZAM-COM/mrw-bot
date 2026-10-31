"""
كل النصوص التي يرسلها البوت في مكان واحد لتسهيل التعديل لاحقًا.

قاعدة مهمة: النصوص تستخدم تنسيق HTML
(لأن كل الأماكن التي تُرسل منها مضبوطة على parse_mode=HTML)
لذلك أي نص قادم من المستخدم يجب تمريره عبر utils.esc() قبل وضعه هنا.
"""
from html import escape as _esc

from database import (
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_NEW,
    STATUS_REVIEW,
    STATUS_APPROVED,
    STATUS_REJECTED,
)

# ------------------------------------------------------------------
# حالات البلاغ: الرمز التعبيري لكل حالة (تُستخدم في الرسائل والقوائم)
# ------------------------------------------------------------------
STATUS_EMOJI = {
    STATUS_NEW: "🟡",
    STATUS_REVIEW: "🟠",
    STATUS_APPROVED: "🟢",
    STATUS_REJECTED: "🔴",
    STATUS_AWAITING_INFO: "🟠",
    STATUS_CLOSED_NO_RESPONSE: "⚫",
}

STATUS_BADGE = {name: f"{emoji} {name}" for name, emoji in STATUS_EMOJI.items()}


def badge(status: str) -> str:
    """إرجاع "🟠 قيد المراجعة" مثلًا لحالة مخزنة في القاعدة."""
    return STATUS_BADGE.get(status, UNKNOWN_STATUS_BADGE.format(status=status))


STATUS_EXPLANATIONS = {
    STATUS_NEW: "وصل إلى النظام وينتظر بدء المراجعة.",
    STATUS_REVIEW: "تراجعه الجهة المختصة حاليًا.",
    STATUS_APPROVED: "اعتمدته الجهة المختصة.",
    STATUS_REJECTED: "أُغلق لعدم استيفاء شروط الاستقبال؛ راجع سبب الرفض المرسل إليك.",
    STATUS_AWAITING_INFO: "ننتظر المعلومات الإضافية المطلوبة قبل استكمال مراجعة البلاغ.",
    STATUS_CLOSED_NO_RESPONSE: "أُغلق البلاغ تلقائيًا بعد انتهاء مهلة اليومين دون رد.",
}


def status_explanation(status: str) -> str:
    return STATUS_EXPLANATIONS.get(status, STATUS_EXPLANATION_FALLBACK)


UNKNOWN_STATUS_BADGE = "⚪ {status}"
STATUS_EXPLANATION_FALLBACK = "تابع هذه البطاقة للاطلاع على أي تحديث."

# ------------------------------------------------------------------
# نصوص المستخدمين المباشرة التي كانت مكتوبة داخل المعالجات
# ------------------------------------------------------------------
INLINE_MESSAGES = {
    "reply_usage": "الاستخدام: /reply رقم_البلاغ المعلومات الإضافية",
    "reply_private_only": "أرسل المعلومات الإضافية في المحادثة الخاصة مع البوت.",
    "followup_unavailable": "لا يوجد طلب معلومات مفتوح لهذا البلاغ.",
    "followup_length": "يجب أن تكون المعلومات بين حرفين و2000 حرف.",
    "followup_received": "تم استلام المعلومات وإيصالها إلى الإدارة.",
    "followup_saved_no_notify": "تم حفظ المعلومات، لكن تعذر إيصالها للمشرفين حاليًا.",
    "verification_image_format": (
        "أرسل الصورة من المعرض أو الكاميرا كصورة عادية في المحادثة، "
        "ولا ترسلها كملف. يمكنك الإلغاء في أي وقت."
    ),
    "verification_session_expired": "انتهت جلسة التوثيق. ابدأ طلبًا جديدًا من القائمة الرئيسية.",
    "verification_privacy_unavailable": "تعذّر التحقق من إفصاح الخصوصية الآن. لم تُعالج الصورة؛ حاول مجددًا.",
    "verification_privacy_changed": (
        "تم تحديث إفصاح الخصوصية. ابدأ طلب التوثيق من جديد وراجع الإفصاح "
        "المحدّث قبل إرسال الصور."
    ),
    "verification_sightengine_failed": (
        "تعذّر فحص صورة الوجه للتأكد من خلوها من المحتوى غير اللائق. "
        "لم تُحفظ الصورة؛ حاول مجددًا بعد قليل."
    ),
    "verification_encryption_failed": "تعذّر التشفير الآمن، لذلك لم تُحفظ الصورة. ابدأ طلبًا جديدًا لاحقًا.",
    "verification_processing_failed": "تعذّر إكمال فحص الصورة الآن، ولم تُحفظ. حاول مرة أخرى بعد قليل.",
    "verification_ticket_save_failed": "تعذّر متابعة التوثيق بسبب مشكلة مؤقتة في الحفظ. لم تُرسل الصور للمراجعة.",
    "supplement_start_required": "ابدأ الاستكمال من زر طلب التوثيق الذي وصلك.",
    "supplement_image_required": "أرسل الصورة المطلوبة كصورة في محادثة البوت.",
    "supplement_load_failed": "تعذّر تحميل طلب الاستكمال الآن. حاول مجددًا لاحقًا.",
    "supplement_missing": "لا يوجد طلب استكمال نشط. أعد فتح حالة التوثيق من القائمة الرئيسية.",
    "supplement_sightengine_failed": "تعذّر فحص صورة الوجه الآن، ولم تُحفظ. حاول مرة أخرى لاحقًا.",
    "supplement_save_failed": "تعذّر حفظ الصورة بأمان. لم تُحفظ؛ أعد المحاولة لاحقًا.",
    "supplement_wrong_image": "هذه الصورة غير مطلوبة في طلب الاستكمال الحالي.",
    "supplement_upload_failed": "تعذّر حفظ الصورة الآن. لم تُرسل للمراجعة؛ حاول مجددًا.",
    "supplement_expired": "لم يعد طلب الاستكمال نشطًا، لذلك لم تُحفظ الصورة.",
    "supplement_followup_image": "تم استلام الصورة المطلوبة. أرسل الآن فقط: ",
    "appeal_length": "اكتب اعتراضًا بين حرفين و2000 حرف.",
    "appeal_unavailable": "لم يعد هذا البلاغ قابلًا لتقديم اعتراض جديد.",
    "admin_reject_expired": "انتهت مهلة سبب الرفض؛ ابدأ الإجراء من جديد.",
    "admin_reject_required": "سبب الرفض مطلوب. أرسل سببًا غير فارغ أو ألغِ العملية.",
    "admin_reject_too_long": "سبب الرفض يتجاوز الحد المسموح (2000 حرف).",
    "admin_private_only": "أرسل الأمر في المحادثة الخاصة مع البوت.",
    "main_command_admin_only": (
        "عذرًا، الأوامر المباشرة للمشرفين فقط. استخدم أزرار البوت التفاعلية."
    ),
    "appeal_prompt": (
        "أرسل اعتراضك أو المعلومات الإضافية المتعلقة بهذا البلاغ "
        "(حتى 2000 حرف). سيصل الطلب إلى المشرف للمراجعة، ولن يُعاد فتح "
        "البلاغ تلقائيًا."
    ),
    "privacy_link_intro": (
        "يمكنك قراءة سياسة الخصوصية عبر الرابط التالي. "
        "راجعها قبل إرسال بلاغ أو بدء توثيق المراسل."
    ),
    "privacy_link_missing": (
        "لم يُضبط رابط سياسة الخصوصية بعد. لا تُرسل بيانات حساسة "
        "ضمن البلاغ قبل مراجعة الإدارة."
    ),
    "supplement_missing_status": "لا يوجد طلب استكمال نشط. راجع حالة توثيق الحساب من القائمة الرئيسية.",
    "supplement_prompt": (
        "أرسل فقط الصورة المطلوبة التالية كصورة في هذه المحادثة:\n"
        "{label}\n\n"
        "تبقى بقية صور الهوية محفوظة للمراجعة؛ لن تحتاج إلى إعادة إرسالها."
    ),
    "verification_private_only": "ابدأ التوثيق في المحادثة الخاصة مع البوت.",
    "verification_report_in_progress": "أكمل البلاغ الحالي أو ألغِه قبل بدء التوثيق.",
    "verification_not_configured": "التوثيق غير مفعّل بعد؛ إعداد التشفير الآمن غير مكتمل.",
    "verification_consent_update_failed": "تعذر تحديث الموافقة؛ أعد المحاولة لاحقًا.",
    "verification_consent_updated": (
        "تم تسجيل موافقتك على إفصاح التوثيق المحدّث. "
        "لم نجمع وثائق إضافية."
    ),
    "verification_phone_prompt": (
        "شارِك رقم هاتفك السوري عبر زر تيليجرام للانتقال إلى خطوات التوثيق. "
        "يمكنك الإلغاء في أي وقت."
    ),
    "step_expired": "انتهت هذه الخطوة. ابدأ من القائمة الرئيسية.",
    "unknown_category": "هذا القسم غير معروف.",
    "unknown_platform": "المنصة غير معروفة.",
    "invalid_draft": "لا توجد مسودة صالحة",
    "violation_type_unavailable": (
        "⚠️ نوع المخالفة المحدد لم يعد متاحًا. أعد فتح التطبيق واختر تصنيفًا من القائمة."
    ),
    "verification_image_format_invalid": "أرسل ملف صورة بصيغة JPEG أو PNG.",
    "verification_image_size_invalid": "حجم الملف غير مناسب. أرسل صورة أصغر من 10 ميغابايت.",
    "verification_image_dimensions_invalid": "دقة الصورة منخفضة. أعد الإرسال بصورة أكبر وأوضح.",
    "verification_image_blurry": "الصورة غير واضحة بما يكفي. أعد تصويرها بإضاءة جيدة وثبّت الهاتف.",
    "verification_image_unreadable": "تعذّر فتح الصورة. أعد إرسالها كصورة واضحة من المعرض.",
    "verification_face_not_found": "لم يُعثر على وجه واضح في الصورة. أعد إرسال صورة شخصية يظهر فيها وجه واحد.",
    "verification_face_multiple": "يجب أن يظهر وجه واحد فقط. أعد إرسال صورة شخصية منفردة.",
    "verification_face_unclear": "الوجه صغير أو غير واضح. أعد إرسال صورة أقرب وأوضح.",
    "verification_inspection_unavailable": (
        "تعذّر التحقق من وضوح الصورة أو الوجه الآن. لم تُحفظ الصورة؛ "
        "حاول مرة أخرى بعد قليل."
    ),
    "verification_inappropriate_content": (
        "لم تُقبل الصورة لأن فحص المحتوى رصد مادة غير مناسبة. "
        "أرسل صورة أخرى مناسبة وواضحة."
    ),
    "verification_image_check_failed": (
        "لم تجتز الصورة الفحص الأولي. أعد إرسال صورة واضحة للخطوة الحالية."
    ),
    "direct_admin_message": "📨 رسالة من الجهة المختصة:\n\n{text}",
    "account_unblocked": "✅ تم رفع الحظر عن حسابك. يمكنك استخدام البوت وتقديم البلاغات من جديد.",
    "report_rejected_notified": "تم رفض البلاغ {seq} وتم إشعار المبلّغ.",
    "report_rejected_not_notified": "تم رفض البلاغ {seq} لكن تعذر إشعار المبلّغ.",
    "verification_supplement_caption": (
        "وصلت صور الاستكمال لطلب توثيق المراسل #{request_id} "
        "(المستخدم {user_id}، {kind}). راجع الطلب في لوحة التوثيق."
    ),
    "verification_supplement_fallback": (
        "وصل استكمال طلب التوثيق #{request_id}، لكن تعذر إرفاق الصور. "
        "افتح إشعار لوحة الإدارة للوصول إلى تفاصيل الطلب المحمية."
    ),
    "backup_document_caption": (
        "نسخة احتياطية من قاعدة بيانات بلاغات المخالفات العسكرية."
    ),
    "photo_check_progress": "🔄 جارٍ فحص الصورة أمنيًا...",
    "link_check_progress": "🔎 جار فحص الرابط عبر VirusTotal...",
    "photo_check_interrupted": "⛔ أوقف النظام هذا البلاغ وفق نتيجة فحص الصورة.",
    "photo_receipt_incomplete": "⚠️ لم يكتمل استلام الصورة. أعد إرسالها.",
    "support_too_many_words": "رسالة التواصل لا تتجاوز 100 كلمة. اختصرها وأرسلها مجددًا.",
    "support_rate_limited": (
        "وصلت إلى حد رسائل التواصل. أعد المحاولة بعد انتهاء المهلة؛ "
        "هذا لا يمنعك من استخدام بقية خدمات البوت."
    ),
    "support_filtered": (
        "لم تُرسل الرسالة لأنها تحتوي على كلمة تمنعها قائمة الفلترة. "
        "أعد صياغتها أو اختر إلغاء."
    ),
    "support_settings_unavailable": (
        "تعذّر التحقق من إعدادات التواصل الآمنة. لم تُرسل رسالتك؛ "
        "أعد المحاولة لاحقًا أو استخدم البلاغات."
    ),
    "verification_disabled": (
        "ميزة التوثيق غير مفعّلة حاليًا لأن إعداد التشفير الآمن لم يكتمل."
    ),
    "verification_share_own_phone": (
        "للمتابعة شارك رقم هاتفك أنت عبر زر المشاركة، أو ألغِ العملية. "
        "لا ترسل رقم شخص آخر."
    ),
    "verification_syrian_phone_only": (
        "نقبل أرقام الهاتف السورية فقط. أعد مشاركة رقمك السوري من تيليجرام."
    ),
    "verification_storage_unavailable": (
        "تعذّر بدء التوثيق لأن خدمة الحفظ الآمن غير جاهزة. "
        "لم يُحفظ رقم الهاتف."
    ),
    "verification_image_sequence_invalid": (
        "لم يكتمل ترتيب الصور، لذلك لم يُرسل الطلب. ابدأه من جديد."
    ),
    "verification_submission_session_expired": (
        "انتهت جلسة التوثيق قبل اكتمال الإرسال. ابدأ طلبًا جديدًا."
    ),
    "verification_already_verified": (
        "حسابك موثّق بالفعل. لا حاجة لإرسال وثائقك مرة أخرى."
    ),
    "verification_already_pending": (
        "طلب التوثيق قيد المراجعة. لا ترسل صور الهوية مرة أخرى؛ "
        "سيظهر القرار في «حالة توثيق الحساب»."
    ),
    "verification_request_open": "يوجد طلب توثيق جارٍ. أعد فتحه من محادثة البوت.",
    "verification_selfie_prompt": (
        "تم استلام رقمك السوري. أرسل الآن صورة وجه واضحة من المعرض أو الكاميرا "
        "كصورة عادية في المحادثة، ولا ترسلها كملف. يجب أن يظهر وجه واحد بوضوح. "
        "ستُفحص صورة الوجه عبر Sightengine للتأكد من خلوها من المحتوى غير اللائق؛ "
        "ولا تُرسل صور الهوية إلى هذه الخدمة."
    ),
    "verification_face_passed_next_id": (
        "اجتازت صورة الوجه فحص الوضوح والوجه والمحتوى. أرسل الآن "
        "الجهة الأمامية للهوية كصورة واضحة من المعرض أو الكاميرا."
    ),
    "verification_image_passed_next_back": (
        "اجتازت الصورة السابقة الفحص الأولي. أرسل الآن الجهة الخلفية "
        "للهوية كصورة واضحة من المعرض أو الكاميرا."
    ),
    "verification_submission_complete": (
        "✅ اكتمل إرسال طلبك للمراجعة البشرية. فحص وضوح الوجه والمحتوى لا يثبت "
        "أصالة الهوية أو أن الصورة حيّة. سنبلغك عند صدور القرار."
    ),
    "verification_save_failed": (
        "تعذّر حفظ الطلب بأمان، ولم يُرسل للمراجعة. "
        "ابدأ طلبًا جديدًا لاحقًا."
    ),
    "location_description_too_long": (
        "اكتب وصفًا للمكان لا يتجاوز 120 حرفًا، أو اختر تخطي."
    ),
    "contact_must_be_own": "يرجى مشاركة رقمك أنت فقط أو اختيار تخطي.",
    "duplicate_exact": (
        "⚠️ يوجد بلاغ مطابق سابق (رقمه <code>{report_id}</code>).\n"
        "سيُسجَّل بلاغك للمراجعة فقط ولن يُنشر تلقائيًا."
    ),
    "duplicate_similar": (
        "⚠️ يوجد بلاغ مشابه لـ <code>{report_id}</code> "
        "(تشابه {similarity}%).\n"
        "سيُسجَّل بلاغك للمراجعة فقط ولن يُنشر تلقائيًا."
    ),
    "link_invalid_http": "❌ الرابط غير صالح. تأكد من أنه يبدأ بـ http أو https.",
    "link_invalid": "❌ الرابط غير صالح.",
    "link_host_missing": "❌ الرابط لا يحتوي اسم موقع.",
    "link_domain_blocked": "❌ هذا النطاق محجوب في النظام.",
    "link_private_host": "❌ الرابط يشير إلى عنوان داخلي غير مسموح.",
    "unknown_social_platform": (
        "ℹ️ لم أتمكّن من تحديد منصة التواصل من هذا الرابط.\n"
        "اذكر في الوصف أين نُشر المنشور، وسيُراجع البلاغ للتأكد."
    ),
    "description_too_short": (
        "✍️ الوصف قصير جدًا.\n"
        "اكتب وصفًا أوضح (من {min_length} حرفًا على الأقل): "
        "ما الذي حدث؟ أين؟ متى؟ وما تفاصيل المخالفة؟"
    ),
    "description_not_enough_words": (
        "✍️ الوصف لا يحتوي معلومات كافية.\n"
        "اكتب وصفًا أوضح (من {min_words} كلمات على الأقل)."
    ),
    "report_category_prompt": "اختر القسم الأقرب إلى موضوع بلاغك:",
    "privacy_report_link": (
        "\n\n<a href=\"{url}\">اطّلع على سياسة الخصوصية قبل إرسال البلاغ.</a>"
    ),
    "reporting_paused_plain": "🛑 استقبال البلاغات الجديدة متوقف مؤقتًا.\n\n",
    "link_check_complete": "✅ اكتمل فحص الرابط.",
    "photo_check_review_required": (
        "⚠️ تعذر إكمال الفحص الآلي بالكامل؛ سيُحال البلاغ للمراجعة اليدوية."
    ),
    "photo_check_passed": "✅ اكتمل الفحص الآلي الأولي للصورة.",
    "support_min_length": "اكتب رسالة من حرفين على الأقل، أو اختر إلغاء.",
    "appeal_saved_notified": (
        "تم حفظ اعتراضك وإحالته للمراجعة؛ لم يُعَد فتح البلاغ تلقائيًا."
    ),
    "appeal_saved_no_notify": (
        "تم حفظ اعتراضك، لكن تعذر إيصال إشعار للمشرفين حاليًا. "
        "سيبقى البلاغ في قائمة المراجعة."
    ),
    "chat_saved_notified": "✅ وصلت رسالتك إلى الإدارة.",
    "chat_saved_no_notify": (
        "✅ حُفظت رسالتك في سجل المحادثة، لكن تعذّر تنبيه الإدارة الآن."
    ),
    "report_photo_received": "✅ مستلمة",
    "report_photo_missing": "غير مرفقة",
    "report_link_missing": "غير مرفق",
    "app_duplicate_manual_review": (
        "⚠️ سيُحال البلاغ للمراجعة اليدوية بسبب تشابهه مع بلاغ سابق.\n\n"
    ),
    "verification_status_not_started": (
        "لم تقدّم طلب توثيق. يمكنك التقديم اختياريًا من القائمة الرئيسية."
    ),
    "verification_status_verified": (
        "✅ حسابك موثّق كمراسل. هذه الشارة لا تعني اعتماد أي بلاغ تلقائيًا."
    ),
    "verification_status_capturing": (
        "طلب التوثيق غير مكتمل بعد. الصور لا تُحفظ للمراجعة حتى تنتهي "
        "من إرسال الصور الثلاث واجتيازها الفحص المحلي."
    ),
    "verification_status_needs_info": (
        "طلب التوثيق مفتوح وينتظر الصور التالية فقط:\n"
        "{documents}\n\nملاحظة المشرف: {message}"
    ),
    "verification_status_pending": "⏳ طلب التوثيق قيد المراجعة الإدارية.",
    "verification_status_missing_documents": (
        "تعذّر العثور على صور الطلب بعد استعادة بيانات النظام. "
        "يمكنك بدء طلب جديد؛ لم تُستعد الصور من النسخ الاحتياطية."
    ),
    "verification_status_rejected": (
        "لم يُعتمد طلب التوثيق. يمكنك إعادة التقديم من القائمة الرئيسية."
    ),
    "verification_status_incomplete": (
        "لم يكتمل إرسال طلب التوثيق. ابدأ من جديد عند رغبتك."
    ),
    "verification_supplement_notification": (
        "نحتاج استكمال طلب توثيقك. أرسل الصور التالية فقط في محادثة البوت:\n"
        "{documents}\n\nملاحظة المشرف: {message}"
    ),
    "verification_supplement_button": "إرسال الصور المطلوبة",
    "unspecified_value": "غير محدد",
    "verification_checking_photo": "🔄 جارٍ فحص الصورة أمنيًا...",
    "verification_upload_complete": (
        "اكتمل إرسال الصور المطلوبة. بقي طلب التوثيق مفتوحًا للمراجعة، "
        "وسنبلغك عند صدور القرار."
    ),
    "expired_consent_start_over": "ابدأ طلبًا جديدًا من القائمة الرئيسية.",
    "admin_status_updated": "✅ تم تحديث الحالة",
    "report_closed_no_followup": (
        "أُغلق البلاغ {seq} تلقائيًا لانتهاء مهلة اليومين دون رد. "
        "يمكنك التواصل مع الإدارة إذا أردت طلب مراجعة."
    ),
}

VERIFICATION_DOCUMENT_LABELS = {
    "selfie": "صورة الوجه",
    "id_front": "الجهة الأمامية للهوية",
    "id_back": "الجهة الخلفية للهوية",
}


class _MissingTextPlaceholder(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_inline_message(key: str, **values: object) -> str:
    return INLINE_MESSAGES[key].format_map(_MissingTextPlaceholder(values))


# ------------------------------------------------------------------
# دليل الخصوصية المعروض داخل البوت عند عدم إعداد رابط خارجي
# ------------------------------------------------------------------
PRIVACY_POLICY = (
    "🔒 <b>سياسة الخصوصية</b>\n"
    "تُحفظ البيانات اللازمة لمعالجة البلاغات وطلبات التوثيق في قاعدة بيانات "
    "مخصصة. يقتصر الاطلاع على المشرفين المخولين، وتُسجل عمليات الوصول إلى "
    "الوثائق. لا تُرسل بياناتك إلى خدمات خارجية إلا عند توضيح ذلك في خطوة "
    "الإرسال أو بموجب المتطلبات النظامية. مشاركة رقم الهاتف والموقع اختيارية."
)

KEYBOARD_LABELS = {
    "report_start": "📝 تقديم بلاغ جديد",
    "consent_continue": "متابعة والموافقة",
    "cancel": "❌ إلغاء",
    "cancel_plain": "إلغاء",
    "privacy_policy": "سياسة الخصوصية",
    "privacy_read": "قراءة سياسة الخصوصية",
    "return_menu": "العودة إلى القائمة",
    "main_menu": "🏠 القائمة الرئيسية",
    "back_to_category": "⬅️ رجوع للقسم",
    "skip": "⏭️ تخطي",
    "location": "📍 مشاركة الموقع",
    "back": "⬅️ رجوع",
    "confirm_report": "✅ إرسال البلاغ",
    "review_report": "⬅️ مراجعة البيانات",
    "home": "🏠 القائمة الرئيسية",
    "back_menu": "↩️ الرجوع للقائمة",
    "return_to_reports": "⬅️ عودة إلى بلاغاتي",
    "send_followup": "📩 إرسال المعلومات المطلوبة",
    "send_appeal": "✉️ اعتراض أو معلومات إضافية",
    "submit_appeal": "إرسال اعتراض أو معلومات إضافية",
    "skip_photo": "⏭️ تخطي وإكمال البلاغ",
    "skip_link": "⏭️ لا أملك رابطًا",
    "skip_location": "⏭️ تخطي الموقع",
    "skip_contact": "⏭️ تخطي بيانات التواصل",
    "report_type_prefix": "📌 {type}",
    "last_report_type": "🔁 نفس تصنيفي السابق: {type}",
}

ADMIN_KEYBOARD_LABELS = {
    "previous": "◀️ السابق",
    "next": "التالي ▶️",
    "all_reports": "🗂 كل البلاغات",
    "export_excel": "📥 تصدير Excel",
    "export_text": "📄 تصدير نصي",
    "stats": "📊 إحصائيات",
    "home": "🏠 القائمة الرئيسية",
    "reject_reason": "❌ رفض بسبب",
    "back_to_list": "⬅️ رجوع للقائمة",
    "main": "🏠 الرئيسية",
    "approve_now": "✅ اعتماد فوري",
    "send_review": "🟠 إحالة للمراجعة",
    "details": "ℹ️ التفاصيل",
    "cancel_rejection": "🚫 إلغاء الرفض",
    "new_reports": "🆕 البلاغات الجديدة ({count})",
    "page": "📄 {current}/{total}",
}

# قوالب الإشعارات التي تتضمن بيانات متغيرة.
FOLLOWUP_ADMIN_NOTIF = (
    "📩 معلومات إضافية للبلاغ {seq}\n"
    "المبلّغ: {name} ({user_id})\n\n{message}"
)
APPEAL_ADMIN_NOTIF = (
    "✉️ طلب إعادة مراجعة للبلاغ {seq}\n"
    "المبلّغ: {name} ({user_id})\n\n{message}\n\n"
    "لم يُعَد فتح البلاغ تلقائيًا؛ راجعه من لوحة الإدارة."
)

# ------------------------------------------------------------------
# تحميل دليل النصوص من قاعدة البيانات
# ------------------------------------------------------------------
_TEXT_ALIASES = {
    "welcome_message": ("WELCOME",),
    "verify_consent": ("VERIFICATION_CONSENT_DEFAULT",),
    "help_text": ("HELP",),
    "privacy_policy": ("PRIVACY_POLICY",),
    "about_system": ("ABOUT_SYSTEM",),
    "status_update": ("STATUS_UPDATE_NOTIF",),
}
_TEXT_EXCLUDED = {"VERIFICATION_CONSENT_DB_KEY"}
_TEXT_FALLBACKS: dict[str, tuple[str, str]] = {}
_TEXT_RUNTIME: dict[str, str] = {}


def _catalog_defaults() -> dict[str, tuple[str, str]]:
    defaults: dict[str, tuple[str, str]] = {}
    alias_names = {
        name
        for (name,) in _TEXT_ALIASES.values()
    }

    def add_mapping(
        name: str, values: dict, prefix: str = "", *, module: str = "texts"
    ) -> None:
        for subkey, content in values.items():
            path = f"{prefix}.{subkey}" if prefix else str(subkey)
            if isinstance(content, str) and content:
                key = f"bot.{module}.{name}.{path}" if module != "texts" else f"bot.{name}.{path}"
                defaults[key] = (
                    f"نص البوت — {name} / {path}",
                    content,
                )
            elif isinstance(content, dict):
                add_mapping(name, content, path, module=module)

    for name, value in tuple(globals().items()):
        if (
            not name.isupper()
            or name.startswith("_")
            or name in _TEXT_EXCLUDED
            or name in alias_names
        ):
            continue
        if isinstance(value, str) and value:
            defaults[f"bot.{name}"] = (f"نص البوت — {name}", value)
        elif isinstance(value, dict):
            add_mapping(name, value)
    from bot import control, keyboards

    for module_name, module, names in (
        (
            "control",
            control,
            (
                "CANCEL_LABEL",
                "CONFIRM_LABEL",
                "APP_LABEL",
                "SKIP_LABEL",
                "BACK_LABEL",
                "LOCATION_LABEL",
                "SHARE_CONTACT_LABEL",
                "INPUT_PLACEHOLDER",
                "CHOOSE_BUTTON_PLACEHOLDER",
                "APP_INPUT_PLACEHOLDER",
                "VERIFICATION_CONTACT_PLACEHOLDER",
            ),
        ),
        (
            "keyboards",
            keyboards,
            (
                "APP_BUTTON_TEXT",
                "USER_MENU_ACTIONS",
                "REPORT_CATEGORIES",
                "SOCIAL_PLATFORM_LABELS",
                "FILTER_LABELS",
                "STATUS_LABELS",
            ),
        ),
    ):
        for name in names:
            value = getattr(module, name)
            if isinstance(value, str) and value:
                defaults[f"bot.{module_name}.{name}"] = (
                    f"نص البوت — {name}",
                    value,
                )
            elif isinstance(value, dict):
                add_mapping(name, value, module=module_name)
    for key, (name,) in _TEXT_ALIASES.items():
        defaults[key] = (name, globals()[name])
    return defaults


def _apply_catalog_value(key: str, content: str) -> bool:
    target = key.removeprefix("bot.")
    for alias, (name,) in _TEXT_ALIASES.items():
        if key == alias:
            target = name
            break
    parts = target.split(".")
    if parts[0] in {"control", "keyboards"}:
        from bot import control, keyboards

        module = control if parts[0] == "control" else keyboards
        if len(parts) < 2:
            return False
        name, *path = parts[1:]
        value = getattr(module, name, None)
        if not path:
            if not isinstance(value, str):
                return False
            old_value = value
            setattr(module, name, content)
            _refresh_external_keyboard_text(parts[0], name, old_value, content)
            return True
        if not isinstance(value, dict):
            return False
        for subkey in path[:-1]:
            value = value.get(subkey)
            if not isinstance(value, dict):
                return False
        if path[-1] not in value:
            return False
        old_value = value[path[-1]]
        value[path[-1]] = content
        _refresh_external_keyboard_text(
            parts[0],
            ".".join(parts[1:]),
            old_value if isinstance(old_value, str) else None,
            content,
        )
        return True
    if len(parts) == 1:
        if parts[0] not in globals() or not parts[0].isupper():
            return False
        globals()[parts[0]] = content
        return True
    name, *path = parts
    value = globals().get(name)
    if not name.isupper() or not isinstance(value, dict):
        return False
    for subkey in path[:-1]:
        value = value.get(subkey)
        if not isinstance(value, dict):
            return False
    if not path or path[-1] not in value:
        return False
    value[path[-1]] = content
    return True


def _refresh_external_keyboard_text(
    module_name: str, name: str, old_value: str | None, content: str
) -> None:
    from bot import control, keyboards

    if module_name == "control":
        semantic = {
            "CANCEL_LABEL": "cancel",
            "CONFIRM_LABEL": "confirm",
            "APP_LABEL": "web_app",
            "SKIP_LABEL": "skip",
            "BACK_LABEL": "back",
            "LOCATION_LABEL": "location",
            "SHARE_CONTACT_LABEL": "contact",
        }.get(name)
        if semantic:
            current = control.current_reply_keyboards()
            changed = False
            for config in current.values():
                for row in config.get("rows", []):
                    for button in row:
                        if (
                            button.get("type") == semantic
                            and old_value is not None
                            and button.get("text") == old_value
                        ):
                            button["text"] = content
                            changed = True
            if changed:
                control.set_reply_keyboards(current)
            mirror_name = {
                "CANCEL_LABEL": "CANCEL_TEXT",
                "CONFIRM_LABEL": "CONFIRM_TEXT",
            }.get(name)
            if mirror_name:
                setattr(keyboards, mirror_name, content)
        if name.endswith("_PLACEHOLDER") and old_value is not None:
            current = control.current_reply_keyboards()
            changed = False
            for config in current.values():
                if config.get("placeholder") == old_value:
                    config["placeholder"] = content
                    changed = True
            if changed:
                control.set_reply_keyboards(current)
    elif module_name == "keyboards" and name.startswith("USER_MENU_ACTIONS."):
        action = name.split(".", 1)[1]
        current = keyboards.current_user_menu_layout()
        changed = False
        for row in current:
            for button in row:
                if (
                    button.get("action") == action
                    and old_value is not None
                    and button.get("label") == old_value
                ):
                    button["label"] = content
                    changed = True
        if changed:
            keyboards.set_user_menu_layout(current)


async def load_database_texts(db, *, seed_defaults: bool = True) -> int:
    if seed_defaults:
        defaults = _catalog_defaults()
        if not _TEXT_FALLBACKS:
            _TEXT_FALLBACKS.update(defaults)
        await db.ensure_bot_text_defaults([
            (key, label, content, "user_facing")
            for key, (label, content) in defaults.items()
        ])
    rows = await db.get_all_bot_texts()
    loaded = 0
    for row in rows:
        key = str(row.get("key") or "")
        content = str(row.get("content") or "")
        if not content:
            fallback = _TEXT_FALLBACKS.get(key)
            content = fallback[1] if fallback else ""
        if content and _apply_catalog_value(key, content):
            _TEXT_RUNTIME[key] = content
            loaded += 1
    return loaded


def apply_database_text(key: str, content: str) -> bool:
    if not content:
        return False
    if not _apply_catalog_value(key, content):
        return False
    _TEXT_RUNTIME[key] = content
    return True


async def get_text(key: str, fallback: str = "") -> str:
    return _TEXT_RUNTIME.get(key, fallback)

# نص الموافقة للتوثيق - يقرأ من DB مع fallback لل النص الكامل
VERIFICATION_CONSENT_DB_KEY = "verify_consent"
VERIFICATION_CONSENT_DEFAULT = (
    "🪪 التسجيل كمراسل موثوق\n\n"
    "الغرض: توثيق حساب المراسل لدى الشرطة العسكرية فرع الحسكة. "
    "يتطلب الطلب مشاركة رقم سوري عبر زر تيليجرام، وصورة وجه، "
    "وصورتي الهوية الأمامية والخلفية.\n\n"
    "يُتحقق محليًا من وضوح الصور، ومن وجود وجه واحد واضح في صورة الوجه. "
    "تُرسل صورة الوجه فقط إلى Sightengine لفحص المحتوى غير اللائق؛ "
    "ولا تُرسل صور الهوية إلى هذه الخدمة. لا يثبت الفحص أصالة الهوية "
    "أو أن الصورة حيّة. إذا اعتُمد الطلب، يقتصر الاطلاع والتنزيل على المشرفين "
    "الذين مُنحوا صلاحية الوثائق؛ وتُسجل عمليات الاطلاع والتنزيل. "
    "لا تُحذف الصور تلقائيًا بعد القرار.\n\n"
    "بالضغط على «متابعة والموافقة» تؤكد اطلاعك وموافقتك على هذه الشروط."
)


# بطاقة البلاغ كما تُنشر في القناة الرسمية الموحّدة
ARCHIVE_REPORT = (
    "\U0001F6E1\uFE0F <b>بلاغ {seq}</b>\n"
    "━━━━━━━━━━━━━━━\n"
    "<b>البلاغ:</b> {name}\n"
    "<b>المعرف:</b> {username}\n"
    "<b>وقت الاستلام:</b> {time}\n"
    "<b>المنشور:</b> {anchor}\n"
    "\n<b>الوصف:</b>\n{desc}\n"
    "━━━━━━━━━━━━━━━\n"
    "أرشف رسمي — نظام بلاغات المخالفات العسكرية"
)

# رسالة أمر /chatid (يُستخدم لمعرفة معرّف القناة الرسمية أثناء الإعداد)
CHAT_ID = (
    "\U0001F4CD <b>معرّفات المحادثة</b>\n"
    "━━━━━━━━━━━━━━━\n"
    "<b>معرّف المحادثة:</b> <code>{chat_id}</code>\n"
    "<b>اسم المحادثة:</b> {title}\n"
    "<b>النوع:</b> {chat_type}\n"
    "<b>معرّفك الرقمي:</b> <code>{user_id}</code>"
)

# ملاحظة تُرسل للمشرف في محادثته الخاصة عند تنفيذ /chatid داخل قناة
CHAT_ID_ADMIN_NOTE = (
    "\n\n📌 <b>للإعداد:</b> انسخ <code>{chat_id}</code> وضعه في المتغير "
    "<code>ARCHIVE_CHAT_ID</code> داخل المنصة السحابية، ثم أعد تشغيل البوت."
)

# ==================================================================
# نصوص طبقة الحماية
# ==================================================================
# رفض الاستقبال خارج المحادثات الخاصة
PRIVATE_ONLY = (
    "🔒 <b>هذا البوت يعمل في المحادثات الخاصة فقط.</b>\n\n"
    "افتح محادثة مباشرة مع البوت من @{bot} ثم ابدأ من جديد.\n"
    "المحادثات الجماعية مغلقة لحماية السجل الرسمي من البلاغات الجماعية."
)

# حالة الإيقاف الطارئ
EMERGENCY_ON = (
    "🛑 <b>الاستقبال متوقف مؤقتًا.</b>\n\n"
    "تم تفعيل وضع الأمان بسبب نشاط غير معتاد. أُبلغ المشرف وسيعود الاستقبال "
    "تلقائيًا. لا تكرر المحاولة الآن."
)

BLOCK_MESSAGES = {
    "frozen": (
        "⛔ تم إيقاف الإرسال مؤقتًا بسبب تكرار تجاوز الحدود.\n"
        "الوقت المتبقي: {minutes} دقيقة.\n"
        "إذا كنت ترى أن هذا خطأ، تواصل مع الإدارة."
    ),
    "minute_limit": (
        "⏱️ تم بلوغ حد المحاولات في الدقيقة.\n"
        "الحد المسموح: {limit} محاولة/دقيقة. حاول لاحقًا."
    ),
    "hour_limit": (
        "⏳ تم بلوغ حد المحاولات في الساعة.\n"
        "الحد المسموح: {limit} محاولة/ساعة. حاول لاحقًا."
    ),
    "daily_limit": (
        "📅 تم بلوغ الحد اليومي ({limit} محاولة).\n"
        "يمكنك المحاولة غدًا أو التواصل مع الإدارة."
    ),
    "emergency": (
        "🛑 استقبال البلاغات متوقف مؤقتًا.\n"
        "يرجى المحاولة لاحقًا أو التواصل مع الإدارة."
    ),
    "manual_block": (
        "🔒 تم تقييد حسابك: {kind}.\n"
        "السبب: {reason}\n"
        "للاستفسار، تواصل مع الإدارة."
    ),
}

# ==================================================================
# نصوص التطبيق (Mini App)
# ==================================================================
# استلام بيانات التطبيق — ثم طلب الصورة
APP_DATA_OK = (
    "✅ <b>وصلت بياناتك من التطبيق</b>\n\n"
    "<b>نوع المخالفة:</b> {vtype}\n"
    "<b>الموقع:</b> {place}\n\n"
    "{review}سيُسجَّل بلاغك في النظام ويظهر للمشرف."
)

APP_SEND_PHOTO = (
    "📷 <b>أرسل الآن الصورة التوثيقية</b> (من الكاميرا أو المعرض).\n\n"
    "ℹ️ تلغرام يحفظ الصورة في خوادمه، فلا تضيع حتى لو أُعيد تشغيل الخادم."
)

# بيانات التطبيق غير صالحة
APP_BAD_DATA = (
    "⚠️ <b>تعذّر قراءة بيانات التطبيق.</b>\n"
    "أعد فتح التطبيق وأعد الإرسال، أو استخدم الأزرار التفاعلية في القائمة."
)

# رسالة الترحيب بالأمان (زر فتح التطبيق)
APP_INTRO = (
    "🧩 <b>تطبيق إرسال البلاغات</b>\n\n"
    "اختر النوع والموقع والصورة والوصف في ثلاث خطوات، وسيصل البلاغ مباشرة "
    "إلى سجل الجهة.\n\n"
    "👇 اضغط زر <b>«🚀 فتح تطبيق البلاغ»</b> في أسفل الشاشة:"
)

# التطبيق غير مفعّل بعد
APP_NOT_READY = (
    "ℹ️ <b>التطبيق غير مفعّل بعد.</b>\n\n"
    "يمكنك إرسال البلاغ بالطريقة المعتادة من القائمة التفاعلية."
)

# ==================================================================
# نصوص أوامر المشرف
# ==================================================================
# رفض الأوامر لغير المشرفين
ADMIN_ONLY = "🔒 <b>هذا الأمر للمشرفين فقط.</b>"

# ------------------------------------------------------------------
# هوية البوت في تلغرام (تظهر عند فتح المحادثة وزر «ماذا يفعل البوت؟»)
# ------------------------------------------------------------------
BOT_DESCRIPTION = (
    "إدارة الشرطة العسكرية — قسم المعلومات\n"
    "\n"
    "منظومة استقبال البلاغات والشكاوى\n"
    "\n"
    "تستقبل البلاغات عن التجاوزات الفردية والمخالفات الإدارية والمالية "
    "ومخالفات النشر والحوادث الميدانية.\n"
    "\n"
    "يمكن إرفاق صورة أو رابط أو موقع بحسب نوع البلاغ، ومشاركة رقم التواصل اختيارية.\n"
    "\n"
    "يظهر رقم تتبع للبلاغ، وتُعرض التفاصيل للمشرفين المعتمدين.\n"
    "\n"
    "اضغط «Start» ثم اختر الخدمة المطلوبة."
)

BOT_SHORT_DESCRIPTION = (
    "استقبال ومتابعة بلاغات الشرطة العسكرية بسرية."
)

# تفعيل الإيقاف الطارئ
PANIC_ON = (
    "🛑 <b>تم تفعيل الإيقاف الطارئ.</b>\n\n"
    "لن يُقبل أي بلاغ جديد حتى يصدر أمر <code>/resume</code>.\n"
    "تم تسجيل العملية في سجل التدقيق."
)

# تعطيل الإيقاف الطارئ
PANIC_OFF = (
    "✅ <b>عاد استقبال البلاغات.</b>\n\n"
    "تم تسجيل العملية في سجل التدقيق."
)

# سجل التدقيق فارغ
AUDIT_EMPTY = "🧾 سجل التدقيق فارغ — لا توجد عمليات مسجّلة بعد."

# النسخ الاحتياطي إلى المستودع السحابي (أمر المشرف /backup)
BACKUP_DONE = (
    "✅ <b>تم رفع نسخة احتياطية من قاعدة البيانات</b> إلى المستودع السحابي بنجاح.\n\n"
    "النسخ الأخرى: تلقائية بعد كل بلاغ، وأخرى دورية عند الإقلاع."
)
BACKUP_FAIL = "⚠️ تعذّر رفع النسخة الاحتياطية إلى المستودع السحابي — راجع سجل التشغيل."
BACKUP_OFF = (
    "ℹ️ النسخ السحابي غير مُهيأ — سرّ <code>HF_TOKEN</code> غير موجود "
    "في إعدادات المساحة."
)


# ==================================================================
# رسائل المستخدم العادي
# ==================================================================
WELCOME = (
    "🛡️ <b>منظومة البلاغات والشكاوى</b>\n"
    "إدارة الشرطة العسكرية\n"
    "────────────────────\n"
    "أهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته من هذه المحادثة.\n"
    "ستراجع بياناتك قبل الإرسال، ثم يصلك رقم تتبع للمتابعة.\n\n"
    "مشاركة رقم الهاتف والموقع اختيارية. أرسل فقط المعلومات والأدلة ذات الصلة.\n\n"
    "اختر الخدمة التي تحتاجها:"
)

BOT_PROFILE_DEFAULT_WELCOME = (
    "🛡️ منظومة البلاغات والشكاوى\n"
    "إدارة الشرطة العسكرية\n"
    "────────────────────\n"
    "أهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته من هذه المحادثة.\n"
    "ستراجع بياناتك قبل الإرسال، ثم يصلك رقم تتبع للمتابعة.\n\n"
    "مشاركة رقم الهاتف والموقع اختيارية. أرسل فقط المعلومات والأدلة ذات الصلة.\n\n"
    "اختر الخدمة التي تحتاجها:"
)

VERIFICATION_CONSENT_DEFAULT = (
    "🪪 التسجيل كمراسل موثوق\n\n"
    "الغرض: توثيق حساب المراسل لدى الشرطة العسكرية فرع الحسكة. "
    "يتطلب الطلب مشاركة رقم سوري عبر زر تيليجرام، وصورة وجه، "
    "وصورتي الهوية الأمامية والخلفية.\n\n"
    "يُتحقق محليًا من وضوح الصور، ومن وجود وجه واحد واضح في صورة الوجه. "
    "تُرسل صورة الوجه فقط إلى Sightengine لفحص المحتوى غير اللائق؛ "
    "ولا تُرسل صور الهوية إلى هذه الخدمة. لا يثبت الفحص أصالة الهوية "
    "أو أن الصورة حيّة. إذا اعتُمد الطلب، "
    "يُحفظ الملف والصور دون حذف تلقائي، ويمكن تنزيلها ضمن ملف PDF. "
    "يقتصر الاطلاع والتنزيل على المشرفين الذين مُنحوا صلاحية الوثائق؛ "
    "وتُسجل عمليات الاطلاع والتنزيل. لا تُحذف الصور تلقائيًا بعد القرار. "
    "يمكن لمشرف مُنح صلاحية حذف مستقلة حذف الصور ونسخها بسبب مسجل، "
    "مع الإبقاء على سجل الطلب.\n\n"
    "بالضغط على «متابعة والموافقة» تؤكد اطلاعك وموافقتك على هذه الشروط."
)

REPORTING_PAUSED = (
    "🛑 <b>استقبال البلاغات الجديدة متوقف مؤقتًا.</b>\n"
    "يمكنك مع ذلك متابعة بلاغاتك أو مراسلة الإدارة من القائمة."
)

REPORT_PAUSED_ALERT = (
    "الاستقبال متوقف مؤقتًا؛ لم يُحفظ البلاغ. ستبقى مسودتك متاحة للمحاولة "
    "مجدّدًا عند استئناف الاستقبال."
)

# نص إشعار بتحديد منصة النشر
PLATFORM_DETECTED = "🔎 تم التعرّف على منصة النشر: <b>{platform}</b>"

# تذكير بأن الرابط إلزامي ضمن نطاق النظام
LINK_REQUIRED = (
    "🔗 <b>مطلوب: رابط المنشور الأصلي.</b>\n\n"
    "هذا النظام يستقبل المخالفات <b>المنشورة على مواقع التواصل الاجتماعي</b>، "
    "والرابط هو أساس التحقق. أرسل رابط المنشور من فضلك."
)

# سطر يظهر في الترحيب عند توفّر التطبيق (لزيادة معرفة المستخدمين به)
WELCOME_APP_HINT = (
    "💡 <b>أسرع طريقة؟</b> اضغط زر <b>«🚀 فتح تطبيق البلاغ»</b> في القائمة — "
    "يفتح التطبيق بخطوات بسيطة مع تحديد الموقع تلقائيًا."
)

ASK_PHOTO = (
    "📎 إذا رغبت، أرسل صورة توثيقية للمخالفة الآن.\n"
    "تُفحص الصور آليًا وقد تُحال للمراجعة اليدوية عند تعذر الفحص. "
    "لا ترسل مواد لا صلة لها بالبلاغ.\n"
    "يمكنك تخطي المرفق إذا لم يتوفر."
)

PHOTO_REMINDER = (
    "⚠️ مطلوب منك <b>صورة توثيقية</b> للمخالفة.\n"
    "📸 أرسل الصورة الآن (كصورة أو كملف صورة)، أو اضغط (❌ إلغاء) للانسحاب."
)

PHOTO_TOO_BIG = (
    "⛔ حجم الملف أكبر من الحد المسموح ({mb} ميجابايت).\n"
    "أرسل صورة أصغر حجمًا أو صورة مضغوطة (كصورة تلغرام العادية)."
)

PHOTO_FAILED = (
    "⚠️ تعذر حفظ الصورة بسبب خطأ تقني، يُرجى إعادة الإرسال أو اختيار زر الإلغاء."
)

ASK_LINK = (
    "🔗 أرسل رابط المنشور الأصلي إن توفر (يبدأ بـ http أو https).\n"
    "بإرسال الرابط، توافق على مشاركته مع VirusTotal لفحصه الأمني.\n"
    "يمكنك تخطي الرابط إذا كان الدليل المرفق لقطة شاشة أو لم يتوفر رابط."
)

LINK_INVALID = (
    "⚠️ الرابط غير صحيح — تأكد أنه يبدأ بـ http:// أو https:// "
    "وأنه منسوخ كاملًا بلا مسافات.\n"
    "مثال: <code>https://t.me/channel/1234</code>\n"
    "أعد إرسال الرابط، أو اضغط (❌ إلغاء)."
)

VIRUSTOTAL_REVIEW = (
    "⚠️ تعذر تأكيد سلامة الرابط آليًا أو ظهرت مؤشرات تستلزم التحقق؛ "
    "سيُحال البلاغ للمراجعة اليدوية."
)

ASK_DESC = (
    "📝 اكتب تفاصيل الواقعة بوضوح، مثل ما حدث والزمان والمكان "
    "والأشخاص أو الجهة ذات الصلة. "
    "حد أقصى {maxlen} حرفًا."
)

DESC_TOO_LONG = (
    "⚠️ الوصف أطول من الحد المسموح ({length} حرفًا، والحد {max}).\n"
    "اختصر النص وأرسله من جديد، أو اضغط (❌ إلغاء)."
)

SUMMARY = (
    "📋 <b>مراجعة سريعة قبل الحفظ</b>\n"
    "────────────────────\n"
    "🗂 القسم: {category}\n"
    "📱 المنصة: {platform}\n"
    "🖼 المرفق: {photo}\n"
    "🔗 رابط المنشور: {link}\n"
    "📍 الموقع: {place}\n"
    "☎️ رقم التواصل: {contact}\n"
    "📝 الوصف:\n"
    "<b>{desc}</b>\n"
    "{review_note}"
    "────────────────────\n"
    "✅ اضغط (✅ إرسال البلاغ) للحفظ النهائي، أو ❌ إلغاء."
)

ASK_LOCATION = (
    "📍 أرسل الموقع الجغرافي من زر المشاركة أسفل الشاشة، أو اكتب وصف المكان.\n"
    "الموقع اختياري؛ لا تشارك موقعك إلا إذا كان ذلك مناسبًا للبلاغ."
)

ASK_CONTACT = (
    "☎️ هل ترغب بمشاركة رقم هاتف للتواصل بشأن البلاغ؟ "
    "استخدم زر مشاركة جهة الاتصال أو تخطَّ هذه الخطوة.\n"
    "مشاركة الرقم اختيارية."
)

ASK_FOLLOWUP = (
    "📩 أرسل المعلومات الإضافية المطلوبة لهذا البلاغ في رسالة واحدة "
    "(من حرفين إلى 2000 حرف)، أو اختر إلغاء."
)

SHARE_CONTACT = "📞 مشاركة رقم هاتفي"

CATEGORY_INTROS = {
    "conduct": "🚨 <b>تجاوزات وسلوك فردي</b>\nمثل سوء المعاملة أو إساءة السلطة أو تجاوزات الحواجز.",
    "administrative": "⚖️ <b>مخالفات إدارية ومالية</b>\nمثل الرشوة أو التلاعب بالمستحقات أو استغلال الموارد.",
    "social": "📱 <b>مخالفات النشر والتواصل الاجتماعي</b>\nيشمل ذلك نشر صور أو مقاطع بالزي العسكري، وتسريب المعلومات والقرارات، والإساءة عبر الإنترنت.",
    "field": "📍 <b>بلاغ ميداني عاجل / أخرى</b>\nمثل حادث قائم أو مشاجرة أو ملاحظة ميدانية عامة.",
}

PLATFORM_PROMPT = "اختر المنصة التي نُشر عليها المحتوى:"

DUPLICATE_REVIEW_NOTE = (
    "⚠️ سيُحال البلاغ إلى المراجعة اليدوية بسبب مصدر غير معروف أو تشابه مع بلاغ سابق.\n"
)

SAVED = (
    "✅ <b>تم حفظ بلاغك بنجاح</b>\n\n"
    "🆔 رقم البلاغ: <b>{seq}</b>\n"
    "🗓 وقت التسجيل: {time}\n"
    "📌 الحالة: {status}\n\n"
    "سيتم مراجعة البلاغ من قبل الجهة المختصة.\n"
    "احتفظ برقم التتبع لمتابعة الحالة. شكرًا لمساهمتك."
)

CANCELLED = (
    "🚫 <b>تم إلغاء العملية</b>، ولم يُحفظ أي مما أرسلته.\n"
    "لإرسال بلاغ جديد أرسل /start"
)

LIMIT_REACHED = (
    "⛔ وصلت إلى الحد اليومي المسموح ({limit} بلاغ في اليوم).\n"
    "يُرجى المحاولة غدًا، أو التواصل مع الإدارة مباشرة إن كان الأمر عاجلاً."
)

WELCOME_HINT = (
    "👋 أهلًا بك في منظومة البلاغات.\n"
    "اضغط /start لفتح القائمة، أو اختر أحد الأزرار الظاهرة."
)

FALLBACK_HINT = (
    "🤔 لم أفهم هذه الرسالة ضمن الشاشة الحالية.\n"
    "استخدم الأزرار الظاهرة، أو اضغط /start لفتح القائمة الرئيسية."
)

GENERAL_ERROR = "⚠️ حدث خطأ تقني غير متوقع، يُرجى إعادة المحاولة أو إبلاغ الإدارة."

# ==================================================================
# رسائل المشرف
# ==================================================================
ADMIN_DENIED = "⛔ <b>وصول مرفوض</b> — هذه اللوحة مخصصة للمشرفين فقط."

ADMIN_MENU = (
    "🛡 <b>لوحة إدارة البلاغات</b>\n"
    "────────────────────\n"
    "🟡 جديد: {new} | 🟠 قيد المراجعة: {review}\n"
    "🟢 معتمد: {approved} | 🔴 مرفوض: {rejected}\n"
    "📦 الإجمالي: {total}\n\n"
    "اختر الإجراء المطلوب من الأزرار 👇"
)

ADMIN_LIST_HEADER = (
    "🗂 <b>قائمة البلاغات</b> — تصفية: {label}\n"
    "📦 عدد السجلات المطابقة: {total}\n"
    "────────────────────\n"
    "{items}\n"
    "👆 اضغط رقم أي بلاغ لعرضه وتغيير حالته."
)

ADMIN_LIST_EMPTY = "🗂 لا توجد بلاغات تحت تصفية ({label}) حاليًا."

ADMIN_STATS = (
    "📊 <b>إحصائيات البلاغات</b>\n"
    "────────────────────\n"
    "🟡 جديد: {new}\n"
    "🟠 قيد المراجعة: {review}\n"
    "🟢 معتمد: {approved}\n"
    "🔴 مرفوض: {rejected}\n"
    "────────────────────\n"
    "📦 الإجمالي: {total}\n"
    "🕒 آخر 24 ساعة: {last24}"
)

ADMIN_VIEW = (
    "🛡 <b>عرض بلاغ #{seq}</b>\n"
    "────────────────────\n"
    "📌 الحالة: {status}\n"
    "👤 المرسل: {name}\n"
    "🔗 المعرف: @{username} | 🆔 {uid}\n"
    "🕒 وقت الإرسال: {time}\n"
    "🗂 القسم: {category}\n"
    "📱 المنصة: {platform}\n"
    "📍 الموقع: {place}\n"
    "☎️ التواصل: {contact}\n"
    "🔗 الرابط: {anchor}\n"
    "📝 الوصف:\n"
    "{desc}\n"
    "{review_line}"
    "────────────────────\n"
    "💡 غيّر حالة البلاغ من الأزرار بالأسفل."
)

REVIEW_LINE = "🔎 راجعه: {admin} | وقت المراجعة: {time}\n"

ADMIN_NOTIFY = (
    "🚨 <b>بلاغ جديد رقم {seq}</b>\n"
    "────────────────────\n"
    "👤 من: {name} (@{username} | {uid})\n"
    "📌 النوع: {vtype}\n"
    "🗂 القسم: {category}\n"
    "📱 المنصة: {platform}\n"
    "📍 الموقع: {place}\n"
    "☎️ التواصل: {contact}\n"
    "🕒 {time}\n"
    "🔗 الرابط: {anchor}\n"
    "📝 الوصف: {desc}\n\n"
    "⚡ قرّر فورًا من الأزرار بالأسفل:"
)

EXPORT_DONE = "✅ تم تجهيز ملف التصدير: <b>{name}</b>\n📦 عدد السجلات: {count}"
EXPORT_EMPTY = "📃 لا توجد بلاغات محفوظة بعد لإجراء تصدير لها."
REPORT_NOT_FOUND = "❌ البلاغ غير موجود أو تم حذفه."
RULE_NOT_KNOWN = "حالة غير معروفة!"

# تنسيق عنوان التقرير المصدّر نصّيًا
TEXT_REPORT_TITLE = "سجل بلاغات المخالفات العسكرية"

# ==================================================================
# إشعار المبلّغ بتغيّر حالة بلاغه (من لوحة المشرف)
# ==================================================================
STATUS_UPDATE_NOTIF = (
    "🔔 <b>تحديث بلاغك رقم {seq}</b>\n"
    "────────────────────\n"
    "📌 الحالة الجديدة: {status}\n"
    "🕒 وقت التحديث: {time}\n"
    "{note}"
)

STATUS_UPDATE_NOTE = "\n📝 ملاحظة الجهة: {note}\n"
STATUS_UPDATE_NO_NOTE = ""

STATUS_FINAL_LINES = {
    "new": "",
    "review": "\n🔎 بلاغك قيد المراجعة من قبل الجهة المختصة.\n",
    "approved": "\n✅ تم اعتماد البلاغ وسيُتخذ فيه الإجراء بحسب صلاحيات الجهة.\n",
    "rejected": "\n❇️ البلاغ غير مطابق لشروط الاستقبال، وجزاك الله خيرًا على اهتمامك.\n",
}

# سبب الرفض المُلحق بإشعار المبلّغ (من لوحة الإدارة)
REJECT_REASON_NOTIF = "\n📌 <b>سبب الرفض:</b>\n{reason}\n"

# رد الجهة المختصة على المتبلّغ (من لوحة الإدارة)
ADMIN_REPLY_TO_REPORTER = (
    "📩 <b>رد الجهة المختصة على بلاغك رقم {seq}</b>\n"
    "────────────────────\n"
    "{text}"
)

# إشعار المستخدم بضبط المحاسبة (من لوحة الإدارة)
PANEL_BLOCK_NOTIF = (
    "⛔ <b>تم إيقاف نشاط حسابك في منظومة استقبال البلاغات.</b>\n"
    "⏱️ الحالة: {kind}\n\n"
    "📌 السبب: {reason}\n"
    "إن كان هذا خطأً، يرجى التواصل مع المشرف."
)

# تعميم عام من لوحة الإدارة إلى كل من بلغ يوماً
BROADCAST_NOTIF = (
    "📢 <b>تعميم من الجهة المختصة</b>\n"
    "────────────────────\n"
    "{text}"
)

# ==================================================================
# نصوص القائمة الرئيسية الاحترافية (أزرار مضمنة)
# ==================================================================
MENU_HINT = (
    "👇 اختر الإجراء المطلوب من الأزرار أدناه — لا تحتاج لكتابة أي أمر."
)

NO_REPORTS_YET = (
    "📭 لا توجد بلاغات مسجلة باسمك بعد.\n"
    "اضغط «إرسال بلاغ جديد» لبدء بلاغ أول."
)

MY_REPORT = (
    "📋 <b>آخر بلاغ لك</b>\n"
    "────────────────────\n"
    "🆔 رقم البلاغ: <b>{seq}</b>\n"
    "🗓 وقت التسجيل: {time}\n"
    "📌 الحالة: {status}\n"
    "ℹ️ {status_detail}\n"
    "📝 الوصف: {desc}\n\n"
    "سيتم مراجعة البلاغ من قبل الجهة المختصة."
)

# شاشة «بلاغاتي»: سطر لكل بلاغ (بطاقات مختصرة بأرقامها وحالاتها)
MY_REPORTS_HEADER = (
    "📋 <b>بلاغاتك المسجلة</b> — {count} بلاغ\n"
    "────────────────────\n"
    "{lines}"
    "👆 اضغط أي بلاغ لعرض تفاصيله وحالته."
)

MY_REPORTS_MORE = "\nويوجد بلاغات أقدم — لمس الإدارة إن احتجت رؤيتها.\n"

# بطاقة تفصيل بلاغ واحد في شاشة «بلاغاتي»
MY_REPORT_OPEN = (
    "📋 <b>تفاصيل بلاغك</b>\n"
    "────────────────────\n"
    "🆔 رقم البلاغ: <b>{seq}</b>\n"
    "🗓 وقت التسجيل: {time}\n"
    "📌 الحالة: {status}\n"
    "ℹ️ {status_detail}\n"
    "📝 الوصف: {desc}\n\n"
    "سيتم إبلاغك فورًا عند أي تحديث في البلاغ."
)

# دليل الاستخدام السريع (/help)
HELP = (
    "📖 <b>الدليل السريع</b>\n"
    "────────────────────\n"
    "📝 <b>إرسال بلاغ:</b> اضغط /start ثم اختر القسم المناسب، وأدخل التفاصيل "
    "وأرفق الدليل أو الموقع ورقم التواصل اختياريًا عند الحاجة.\n\n"
    "📊 <b>متابعة بلاغاتك:</b> استخدم زر «حالة بلاغي» لعرض بطاقة لكل بلاغ "
    "وحالته وزمنه.\n\n"
    "📩 <b>معلومات إضافية:</b> افتح «حالة بلاغي»، ثم بطاقة البلاغ التي طلبت "
    "الإدارة معلومات إضافية عنها واضغط زر الإرسال.\n\n"
    "🚫 <b>الإلغاء:</b> استخدم زر (❌ إلغاء) الظاهر في خطوات البلاغ.\n\n"
    "🔔 <b>التحديثات:</b> تُرسل إليك فورًا عند أي تغيير في حالة بلاغك.\n\n"
    "🔗 <b>الأدلة:</b> أرفق ما يتوفر من روابط أو صور ذات صلة، ويمكن تخطي "
    "المرفقات الاختيارية.\n\n"
    "🔒 <b>السرية:</b> كل بلاغ يُعرض على المشرف المختص فقط.\n\n"
    "للبدء الآن اضغط زر «📝 إرسال بلاغ جديد» أو أرسل /start."
)

# ==================================================================
# نصوص إجراءات المشرف من تلغرام نفسه (أزرار إشعار البلاغ الجديد)
# ==================================================================
# طلب سبب الرفض (إلزامي قبل تنفيذ الرفض)
ADMIN_REJECT_ASK = (
    "✍️ <b>سبب رفض البلاغ {seq}</b> — إلزامي.\n\n"
    "اكتب السبب في رسالة قادمة (جملة واضحة تكفي)، وسيلتحق برسالة إشعار المبلّغ.\n"
    "للتراجع اضغط «🚫 إلغاء الرفض» بالأسفل."
)

# سبب مرفوض (قصير/طويل) — يُعاد الطلب
ADMIN_REJECT_BAD_LEN = (
    "⚠️ السبب غير مقبول: من حرفين إلى 300 حرف.\n"
    "أعد كتابته في رسالة جديدة."
)

ADMIN_REJECT_DONE = (
    "✅ <b>تم اعتماد الرفض للبلاغ {seq}</b>\n"
    "🔴 الحالة: مرفوض — وأُبلغ المبلّغ بالسبب.\n"
    "وردت العملية في سجل التدقيق."
)

ADMIN_REJECT_CANCELLED = "🚫 أُلغيت عملية الرفض — لم يتغيّر شيء."

# طلب إجراء على بلاغ حالته على وضعها (حماية من النقر المزدوج)
ADMIN_STATUS_ALREADY = "ℹ️ الحالة على وضعها بالفعل ({status}) — لا يوجد ما يغيّره."

# زر/إجراء غير معروف (لوحة عتيقة أو استدعاء قديم)
ADMIN_UNKNOWN_ACTION = (
    "⚠️ هذا الزر عتيق أو غير معروف — أرسل /admin للحصول على لوحة حديثة."
)

UNKNOWN_BUTTON = "زر غير صالح — أعد فتح القائمة بـ /start"

ABOUT_SYSTEM = (
    "ℹ️ <b>التعليمات واشتراطات تقديم البلاغ</b>\n"
    "────────────────────\n"
    "اختر القسم الأقرب للواقعة، واكتب وصفًا دقيقًا يتضمن ما حدث ومتى وأين "
    "والأشخاص أو الجهة ذات الصلة.\n\n"
    "📎 أرفق صورة ذات صلة إن توفرت. في بلاغات النشر اختر المنصة وأرسل الرابط "
    "أو تخطّه إذا كان الدليل لقطة شاشة. في البلاغ الميداني يمكنك مشاركة الموقع "
    "أو كتابة وصف المكان.\n\n"
    "☎️ مشاركة رقم الهاتف اختيارية. لا ترسل بيانات أو أدلة لا تتعلق بالبلاغ.\n\n"
    "🔒 راجع البيانات قبل الإرسال؛ سيظهر لك رقم تتبع ويمكنك متابعة الحالة من "
    "زر «حالة بلاغي». الصور قد تُفحص آليًا، والنتائج الاحتمالية قد تحتاج مراجعة بشرية."
)

CONTACT_ADMIN = (
    "📞 <b>تواصل مع الإدارة</b>\n"
    "────────────────────\n"
    "اكتب رسالتك هنا، وستصل إلى المشرفين عبر النظام.\n"
    "لا ترسل كلمات مرور أو معلومات لا تتعلق باستفسارك."
)

ASK_SUPPORT = (
    "📩 <b>رسالة إلى الإدارة</b>\n"
    "اكتب استفسارك أو رسالتك في رد واحد (من حرفين إلى 1000 حرف).\n"
    "ستُحفظ في سجل المحادثة حتى يتمكن المشرفون من متابعتها.\n"
    "لا ترسل كلمات مرور أو معلومات لا تتعلق باستفسارك."
)

SUPPORT_RECEIVED = (
    "✅ وصلت رسالتك إلى الإدارة وحُفظت للمتابعة.\n"
    "يمكنك إرسال رد في هذه المحادثة إذا احتجت إلى إضافة معلومة."
)

SUPPORT_SAVED_NO_NOTIFY = (
    "⚠️ حُفظت رسالتك في سجل المحادثة، لكن تعذّر تنبيه المشرفين الآن. "
    "سيحتاجون إلى مراجعة صندوق الرسائل في لوحة الإدارة."
)

SUPPORT_UNAVAILABLE = (
    "⚠️ خدمة التواصل غير متاحة حاليًا، ولم تُرسل رسالتك. "
    "يرجى المحاولة لاحقًا."
)

SUPPORT_TOO_LONG = "⚠️ الرسالة أطول من 1000 حرف. اختصرها وأرسلها مجددًا."

SUPPORT_DURING_REPORT = (
    "أكمل البلاغ الجاري أو ألغِه أولًا، ثم افتح التواصل مع الإدارة؛ "
    "حتى لا تفقد مسودتك."
)

ASK_TYPE = (
    "📌 <b>اختر نوع المخالفة</b> من الأزرار أدناه (اختياري):"
)

TYPE_SELECTED = (
    "✅ تم تحديد نوع المخالفة: <b>{vtype}</b>\n\n"
    "يمكنك الآن متابعة إرسال التفاصيل عبر التطبيق أو المحادثة."
)


def username_display(username: str | None) -> str:
    """تنسيق معرف المستخدم بشكل آمن داخل HTML."""
    if username:
        return _esc(username, quote=True)
    return "بدون معرف"
