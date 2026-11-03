REPORT_CATEGORIES = {
    "conduct": "🚨 تجاوزات وسلوك فردي",
    "administrative": "⚖️ مخالفات إدارية ومالية",
    "social": "📱 مخالفات النشر والتواصل الاجتماعي",
    "field": "📍 بلاغ ميداني عاجل / أخرى",
}

LEGACY_VIOLATION_TYPES = (
    "تجاوز السرعة",
    "مخالفة إشارة",
    "إعاقة الطريق",
    "مخالفة زي",
    "مواد ممنوعة",
    "سلاح أو عنيف",
    "تصوير في مكان ممنوع",
    "أخرى",
)

DEFAULT_VTYPE_CATEGORIES = {
    "conduct": (
        "إساءة معاملة",
        "تجاوز صلاحيات",
        "تهديد أو اعتداء",
        "ابتزاز أو طلب رشوة",
        "مواد ممنوعة",
        "سلاح أو عنيف",
    ),
    "administrative": (
        "مخالفة قرار أو تعليمات",
        "فساد أو استغلال موارد",
        "تزوير أو تلاعب",
        "إهمال أو تقصير",
        "تجاوز السرعة",
        "مخالفة إشارة",
        "مخالفة زي",
    ),
    "social": (
        "حساب أو انتحال صفة",
        "نشر صور أو وثائق",
        "بث مباشر",
        "نشر معلومات أو محتوى محظور",
        "تصوير في مكان ممنوع",
    ),
    "field": (
        "تحركات أو تجمعات",
        "نشاط ميداني مشبوه",
        "حادثة عاجلة",
        "موقع أو إحداثيات",
        "إعاقة الطريق",
        "أخرى",
    ),
}

DEFAULT_VIOLATION_TYPES = tuple(
    dict.fromkeys(
        (*LEGACY_VIOLATION_TYPES, *(
            item
            for category in REPORT_CATEGORIES
            for item in DEFAULT_VTYPE_CATEGORIES[category]
        ))
    )
)


def default_vtype_groups(items: list[str]) -> dict[str, list[str]]:
    groups = {
        category: [
            item
            for item in items
            if item in DEFAULT_VTYPE_CATEGORIES[category]
        ]
        for category in REPORT_CATEGORIES
    }
    assigned = {item for values in groups.values() for item in values}
    groups["field"].extend(item for item in items if item not in assigned)
    return groups


def normalize_vtype_groups(
    raw: object, items: list[str]
) -> dict[str, list[str]] | None:
    if not isinstance(raw, dict):
        return None

    item_set = set(items)
    groups: dict[str, list[str]] = {}
    for category in REPORT_CATEGORIES:
        values = raw.get(category)
        if not isinstance(values, list):
            return None
        clean: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                return None
            item = value.strip()
            if item and item in item_set and item not in seen:
                clean.append(item)
                seen.add(item)
        groups[category] = clean

    if any(key not in REPORT_CATEGORIES for key in raw):
        return None
    return groups
