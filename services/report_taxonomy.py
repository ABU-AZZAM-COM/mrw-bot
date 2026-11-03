REPORT_CATEGORIES = {
    "conduct": "🚨 تجاوزات وسلوك فردي",
    "administrative": "⚖️ مخالفات إدارية ومالية",
    "social": "📱 مخالفات النشر والتواصل الاجتماعي",
    "field": "📍 بلاغ ميداني عاجل / أخرى",
}


def default_vtype_groups(items: list[str]) -> dict[str, list[str]]:
    return {category: list(items) for category in REPORT_CATEGORIES}


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
