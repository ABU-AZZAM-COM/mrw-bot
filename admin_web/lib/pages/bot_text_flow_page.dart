import 'package:flutter/material.dart';

import '../api.dart';

enum _ContentFilter { all, buttons, messages }

class BotTextManagementPage extends StatefulWidget {
  const BotTextManagementPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotTextManagementPage> createState() => _BotTextManagementPageState();
}

class _BotTextManagementPageState extends State<BotTextManagementPage> {
  final _searchController = TextEditingController();
  final _contentController = TextEditingController();
  List<Map<String, dynamic>> _texts = [];
  String _flowId = 'main';
  String? _selectedKey;
  bool _loading = true;
  bool _saving = false;
  String? _error;
  _ContentFilter _contentFilter = _ContentFilter.all;
  String _mobilePanel = 'preview';

  Map<String, dynamic>? get _selected {
    for (final item in _texts) {
      if (item['key'] == _selectedKey) return item;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _searchController.addListener(_refresh);
    _load();
  }

  @override
  void dispose() {
    _searchController
      ..removeListener(_refresh)
      ..dispose();
    _contentController.dispose();
    super.dispose();
  }

  void _refresh() {
    if (!mounted) return;
    final query = _searchController.text.trim().toLowerCase();
    if (query.isNotEmpty && _itemsFor(_flowId).isEmpty) {
      for (final flowId in _flowOrder) {
        if (_itemsFor(flowId).isNotEmpty) {
          _flowId = flowId;
          break;
        }
      }
    }
    setState(() {});
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final response = await widget.api.get('/admin_api/texts');
      if (!mounted) return;
      if (response['ok'] != true) {
        setState(
          () => _error = response['error']?.toString() ?? 'تعذر تحميل النصوص',
        );
        return;
      }
      final texts =
          (response['texts'] as List? ?? const [])
              .whereType<Map>()
              .map((item) => Map<String, dynamic>.from(item))
              .toList()
            ..sort((a, b) => _displayName(a).compareTo(_displayName(b)));
      setState(() {
        _texts = texts;
        if (_selectedKey != null &&
            texts.any((item) => item['key'] == _selectedKey)) {
          _contentController.text = _selected?['content']?.toString() ?? '';
        } else {
          _selectedKey = null;
          _contentController.clear();
        }
      });
    } catch (_) {
      if (mounted) setState(() => _error = 'تعذر الاتصال بالخادم');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    final item = _selected;
    if (item == null) return;
    setState(() => _saving = true);
    try {
      final response = await widget.api.post('/admin_api/texts/update', {
        'key': item['key'],
        'content': _contentController.text,
        'label': item['label'],
        'category': item['category'] ?? 'user_facing',
      });
      if (!mounted) return;
      if (response['ok'] == true && response['applied'] == true) {
        await _load();
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تم الحفظ والتطبيق على البوت مباشرة')),
        );
      } else {
        final saved = response['saved'] == true;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              saved
                  ? 'حُفظ النص لكن لم يُطبّق على البوت؛ أعد المحاولة بعد التحقق من حالة البوت.'
                  : 'لم يُحفظ أو يُطبّق النص: ${response['error'] ?? 'خطأ غير معروف'}',
            ),
          ),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر حفظ النص أو تطبيقه على البوت')),
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  static const _flowOrder = [
    'main',
    'report_categories',
    'report_steps',
    'followup',
    'verification',
    'admin',
    'other',
  ];

  static const _flowTitles = {
    'main': 'القائمة الرئيسية',
    'report_categories': 'اختيار نوع البلاغ',
    'report_steps': 'خطوات إرسال البلاغ',
    'followup': 'المتابعة والتواصل',
    'verification': 'توثيق المراسل',
    'admin': 'أزرار ورسائل الإدارة',
    'other': 'بقية رسائل البوت',
  };

  static const _flowIcons = {
    'main': Icons.home_outlined,
    'report_categories': Icons.category_outlined,
    'report_steps': Icons.edit_note_outlined,
    'followup': Icons.mark_chat_read_outlined,
    'verification': Icons.verified_user_outlined,
    'admin': Icons.admin_panel_settings_outlined,
    'other': Icons.more_horiz_rounded,
  };

  static const _flowDescriptions = {
    'main': 'الأزرار الفعلية للقائمة ورسائل البداية والخدمات التي تفتحها.',
    'report_categories': 'أزرار الأقسام والمنصات مع النص الذي يظهر في كل خطوة.',
    'report_steps': 'رسائل إدخال البلاغ وأزرار التخطي والرجوع والإلغاء.',
    'followup': 'أزرار المعلومات الإضافية والاعتراض ورسائل المتابعة.',
    'verification': 'زر التوثيق، الموافقة، وخطوات ورسائل التوثيق.',
    'admin': 'أزرار وإشعارات لوحة المشرف داخل تيليجرام.',
    'other': 'النصوص الأخرى، مصنفة حسب استخدامها بدل مفاتيح الشيفرة.',
  };

  String _flowFor(String key) {
    if (key == 'welcome_message' ||
        key == 'bot.WELCOME_HINT' ||
        key == 'bot.ABOUT_SYSTEM' ||
        key == 'bot.HELP' ||
        key == 'bot.PRIVACY_POLICY' ||
        key == 'bot.ASK_SUPPORT' ||
        key == 'bot.keyboards.APP_BUTTON_TEXT' ||
        key.startsWith('bot.keyboards.USER_MENU_ACTIONS.')) {
      return 'main';
    }
    if (key.startsWith('bot.keyboards.REPORT_CATEGORIES.') ||
        key.startsWith('bot.keyboards.SOCIAL_PLATFORM_LABELS.') ||
        key.startsWith('bot.CATEGORY_INTROS.') ||
        key == 'bot.PLATFORM_PROMPT' ||
        key == 'bot.INLINE_MESSAGES.report_category_prompt') {
      return 'report_categories';
    }
    if (key == 'verify_consent' ||
        key.startsWith('bot.VERIFICATION_') ||
        key.startsWith('bot.INLINE_MESSAGES.verification_') ||
        key.startsWith('bot.keyboards.VERIFICATION_') ||
        key == 'bot.KEYBOARD_LABELS.consent_continue') {
      return 'verification';
    }
    if (key.startsWith('bot.ASK_') ||
        (key.startsWith('bot.KEYBOARD_LABELS.') &&
            key != 'bot.KEYBOARD_LABELS.consent_continue') ||
        key.startsWith('bot.control.') ||
        key.startsWith('bot.INLINE_MESSAGES.photo_') ||
        key.startsWith('bot.INLINE_MESSAGES.link_') ||
        key.startsWith('bot.INLINE_MESSAGES.location_') ||
        key == 'bot.SUMMARY' ||
        key == 'bot.DESC_TOO_LONG' ||
        key == 'bot.ASK_DESC' ||
        key == 'bot.ASK_PHOTO' ||
        key == 'bot.ASK_LINK' ||
        key == 'bot.ASK_LOCATION' ||
        key == 'bot.ASK_CONTACT') {
      return 'report_steps';
    }
    if (key.contains('followup') ||
        key.contains('appeal') ||
        key == 'bot.MY_REPORTS_HEADER' ||
        key == 'bot.MY_REPORT_OPEN' ||
        key == 'bot.NO_REPORTS_YET') {
      return 'followup';
    }
    if (key.startsWith('bot.ADMIN_KEYBOARD_LABELS.') ||
        key.startsWith('bot.keyboards.STATUS_LABELS.') ||
        key.startsWith('bot.keyboards.FILTER_LABELS.') ||
        key.startsWith('bot.STATUS_') ||
        key.startsWith('bot.ADMIN_') ||
        key.startsWith('bot.INLINE_MESSAGES.admin_')) {
      return 'admin';
    }
    return 'other';
  }

  bool _isButton(Map<String, dynamic> item) =>
      item['kind'] == 'button' ||
      (item['key']?.toString().startsWith('bot.KEYBOARD_LABELS.') ?? false);

  bool _isDirty(Map<String, dynamic> item) =>
      _contentController.text != (item['content']?.toString() ?? '');

  String _previewContent(Map<String, dynamic> item) =>
      item['key'] == _selectedKey
      ? _contentController.text
      : item['content']?.toString() ?? '';

  String _sourceFor(Map<String, dynamic> item) {
    final key = item['key']?.toString() ?? '';
    if (key == 'welcome_message') {
      return 'مصدر الترحيب الفعلي';
    }
    if (key.startsWith('bot.keyboards.USER_MENU_ACTIONS.')) {
      return 'تخطيط القائمة النشط';
    }
    if (key.startsWith('bot.control.')) {
      return 'لوحة الرد النشطة';
    }
    return 'نصوص البوت المحمّلة';
  }

  String _displayName(Map<String, dynamic> item) {
    final key = item['key']?.toString() ?? '';
    final label = item['label']?.toString() ?? '';
    if (label.isNotEmpty && !label.startsWith('نص البوت —')) return label;
    const names = {
      'report': 'إرسال بلاغ جديد',
      'status': 'متابعة البلاغات',
      'about': 'دليل الاستخدام',
      'contact': 'التواصل مع الإدارة',
      'privacy': 'سياسة الخصوصية',
      'verify': 'التسجيل كمراسل موثوق',
      'verify-status': 'حالة التوثيق',
      'report_category_prompt': 'طلب اختيار القسم',
      'report_type_prefix': 'زر نوع المخالفة',
      'consent_continue': 'متابعة والموافقة',
      'cancel': 'إلغاء',
      'skip_photo': 'تخطي الصورة',
      'skip_link': 'تخطي الرابط',
      'skip_location': 'تخطي الموقع',
      'skip_contact': 'تخطي بيانات التواصل',
      'followup_length': 'طلب المعلومات الإضافية',
      'appeal_prompt': 'طلب الاعتراض',
      'welcome_message': 'رسالة الترحيب',
      'APP_BUTTON_TEXT': 'فتح تطبيق البلاغ',
      'PLATFORM_PROMPT': 'طلب اختيار المنصة',
      'ASK_DESC': 'طلب وصف الواقعة',
      'ASK_PHOTO': 'طلب الصورة',
      'ASK_LINK': 'طلب رابط المنشور',
      'ASK_LOCATION': 'طلب الموقع',
      'ASK_CONTACT': 'طلب بيانات التواصل',
      'ASK_FOLLOWUP': 'طلب المعلومات الإضافية',
      'SUMMARY': 'مراجعة البلاغ قبل الإرسال',
      'SAVED': 'رسالة تأكيد استلام البلاغ',
      'conduct': 'تجاوزات وسلوك فردي',
      'administrative': 'مخالفات إدارية ومالية',
      'social': 'مخالفات النشر والتواصل الاجتماعي',
      'field': 'بلاغ ميداني عاجل / أخرى',
      'facebook': 'فيسبوك',
      'instagram_tiktok': 'إنستغرام / تيك توك',
      'whatsapp': 'واتساب',
      'telegram': 'تيليجرام',
    };
    final last = key.split('.').last;
    return names[last] ?? last.replaceAll('_', ' ');
  }

  List<Map<String, dynamic>> _itemsFor(String flowId) {
    final query = _searchController.text.trim().toLowerCase();
    return _texts.where((item) {
      final key = item['key']?.toString() ?? '';
      if (_flowFor(key) != flowId) return false;
      if (query.isEmpty) return true;
      return '${_displayName(item)} $key ${item['content']}'
          .toLowerCase()
          .contains(query);
    }).toList();
  }

  List<Map<String, dynamic>> _visibleItemsFor(String flowId) {
    final items = _itemsFor(flowId);
    return switch (_contentFilter) {
      _ContentFilter.all => items,
      _ContentFilter.buttons => items.where(_isButton).toList(),
      _ContentFilter.messages =>
        items.where((item) => !_isButton(item)).toList(),
    };
  }

  List<Map<String, dynamic>> _relatedTexts(Map<String, dynamic> button) {
    final key = button['key']?.toString() ?? '';
    final action = key.startsWith('bot.keyboards.USER_MENU_ACTIONS.')
        ? key.split('.').last
        : '';
    final explicitKeys = switch (action) {
      'report' => ['bot.INLINE_MESSAGES.report_category_prompt'],
      'status' => ['bot.NO_REPORTS_YET', 'bot.MY_REPORTS_HEADER'],
      'about' => ['bot.ABOUT_SYSTEM'],
      'contact' => ['bot.ASK_SUPPORT'],
      'privacy' => [
        'bot.PRIVACY_POLICY',
        'bot.INLINE_MESSAGES.privacy_link_intro',
      ],
      'verify' => ['verify_consent', 'bot.KEYBOARD_LABELS.consent_continue'],
      'verify-status' => [
        'bot.INLINE_MESSAGES.verification_status_not_started',
        'bot.INLINE_MESSAGES.verification_status_pending',
      ],
      _ => const <String>[],
    };
    final prefix = key.startsWith('bot.keyboards.REPORT_CATEGORIES.')
        ? 'bot.CATEGORY_INTROS.${key.split('.').last}'
        : key.startsWith('bot.keyboards.SOCIAL_PLATFORM_LABELS.')
        ? 'bot.PLATFORM_PROMPT'
        : '';
    final relatedKeys = {...explicitKeys};
    if (prefix.isNotEmpty) relatedKeys.add(prefix);
    return _texts
        .where(
          (item) =>
              relatedKeys.contains(item['key']) && item['key'] != button['key'],
        )
        .toList();
  }

  Future<bool> _confirmDiscard() async {
    final current = _selected;
    if (current == null || !_isDirty(current)) return true;
    final discard = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('تعديل غير محفوظ'),
        content: const Text('هل تريد تجاهل التعديل والانتقال إلى عنصر آخر؟'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('البقاء هنا'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('تجاهل التعديل'),
          ),
        ],
      ),
    );
    return mounted && discard == true;
  }

  Future<void> _select(Map<String, dynamic> item) async {
    if (item['key'] == _selectedKey || !await _confirmDiscard()) return;
    setState(() {
      _selectedKey = item['key']?.toString();
      _contentController.text = item['content']?.toString() ?? '';
      _mobilePanel = 'editor';
    });
  }

  Future<void> _selectFlow(String flowId) async {
    if (flowId == _flowId || !await _confirmDiscard()) return;
    setState(() {
      _flowId = flowId;
      _selectedKey = null;
      _contentController.clear();
      _mobilePanel = 'preview';
    });
  }

  Widget _flowRail() => SizedBox(
    width: 220,
    child: ListView(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 14),
      children: [
        Padding(
          padding: const EdgeInsetsDirectional.only(start: 10, bottom: 8),
          child: Text(
            'مراحل البوت',
            style: Theme.of(context).textTheme.labelLarge?.copyWith(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
        ),
        for (final id in _flowOrder)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: ListTile(
              selected: id == _flowId,
              selectedTileColor: Theme.of(
                context,
              ).colorScheme.secondaryContainer.withValues(alpha: .42),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
              ),
              leading: Icon(_flowIcons[id] ?? Icons.route_outlined),
              title: Text(
                _flowTitles[id] ?? id,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              subtitle: Text(
                _flowDescriptions[id] ?? '',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              trailing: Text(
                '${_visibleItemsFor(id).length}',
                style: Theme.of(context).textTheme.labelMedium,
              ),
              onTap: () => _selectFlow(id),
            ),
          ),
      ],
    ),
  );

  Widget _mobileFlowPicker() => DropdownButtonFormField<String>(
    key: ValueKey(_flowId),
    initialValue: _flowId,
    isExpanded: true,
    decoration: const InputDecoration(
      labelText: 'مرحلة البوت',
      prefixIcon: Icon(Icons.alt_route_rounded),
      border: OutlineInputBorder(),
    ),
    items: [
      for (final id in _flowOrder)
        DropdownMenuItem(
          value: id,
          child: Text('${_flowTitles[id]} (${_visibleItemsFor(id).length})'),
        ),
    ],
    onChanged: (id) {
      if (id != null) _selectFlow(id);
    },
  );

  Widget _flowChips() => SingleChildScrollView(
    scrollDirection: Axis.horizontal,
    child: Row(
      children: [
        for (final id in _flowOrder)
          Padding(
            padding: const EdgeInsetsDirectional.only(end: 8),
            child: ChoiceChip(
              label: Text(
                '${_flowTitles[id]} (${_visibleItemsFor(id).length})',
              ),
              selected: id == _flowId,
              onSelected: (_) => _selectFlow(id),
            ),
          ),
      ],
    ),
  );

  Widget _filterPicker() => SegmentedButton<_ContentFilter>(
    showSelectedIcon: false,
    segments: const [
      ButtonSegment(
        value: _ContentFilter.all,
        label: Text('الكل'),
        icon: Icon(Icons.view_agenda_outlined),
      ),
      ButtonSegment(
        value: _ContentFilter.buttons,
        label: Text('الأزرار'),
        icon: Icon(Icons.smart_button_outlined),
      ),
      ButtonSegment(
        value: _ContentFilter.messages,
        label: Text('الرسائل'),
        icon: Icon(Icons.chat_bubble_outline),
      ),
    ],
    selected: {_contentFilter},
    onSelectionChanged: (value) => setState(() => _contentFilter = value.first),
  );

  Widget _chatBubble(Map<String, dynamic> item) {
    final selected = item['key'] == _selectedKey;
    final colors = Theme.of(context).colorScheme;
    return Align(
      alignment: AlignmentDirectional.centerStart,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: Material(
          color: selected
              ? colors.secondaryContainer
              : colors.surfaceContainerHigh,
          borderRadius: const BorderRadiusDirectional.only(
            topStart: Radius.circular(5),
            topEnd: Radius.circular(17),
            bottomStart: Radius.circular(17),
            bottomEnd: Radius.circular(17),
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(17),
            onTap: () => _select(item),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _displayName(item),
                    style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: colors.primary,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 5),
                  Text(_previewContent(item)),
                  if (selected)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text(
                        _isDirty(item) ? 'تعديلات غير محفوظة' : 'محدد للتحرير',
                        style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: colors.onSurfaceVariant,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _keyboardButton(Map<String, dynamic> item) {
    final selected = item['key'] == _selectedKey;
    return Padding(
      padding: const EdgeInsetsDirectional.only(end: 8, bottom: 8),
      child: OutlinedButton(
        style: OutlinedButton.styleFrom(
          backgroundColor: selected
              ? Theme.of(context).colorScheme.secondaryContainer
              : Theme.of(context).colorScheme.surface,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        ),
        onPressed: () => _select(item),
        child: Text(_previewContent(item), textAlign: TextAlign.center),
      ),
    );
  }

  Widget _chatPreview() {
    final items = _visibleItemsFor(_flowId);
    final buttons = items.where(_isButton).toList();
    final messages = items.where((item) => !_isButton(item)).toList();
    final selected = _selected;
    final related = selected != null && _isButton(selected)
        ? _relatedTexts(selected)
        : const <Map<String, dynamic>>[];
    final colors = Theme.of(context).colorScheme;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
          child: Row(
            children: [
              CircleAvatar(
                radius: 19,
                backgroundColor: colors.primaryContainer,
                child: Icon(Icons.smart_toy_outlined, color: colors.primary),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _flowTitles[_flowId] ?? 'معاينة البوت',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    Text(
                      '${items.length} عنصر · معاينة قابلة للتحرير',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: colors.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: items.isEmpty
              ? Center(
                  child: Text(
                    'لا توجد عناصر تطابق هذا الاختيار',
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                )
              : Container(
                  color: colors.surfaceContainerLowest,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      Container(
                        padding: const EdgeInsets.all(11),
                        decoration: BoxDecoration(
                          color: colors.tertiaryContainer.withValues(
                            alpha: .38,
                          ),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(
                              Icons.info_outline,
                              size: 18,
                              color: colors.onTertiaryContainer,
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                'معاينة مجمّعة حسب المرحلة؛ قد تختلف الرسائل الفعلية بحسب إجابات المستخدم.',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ),
                          ],
                        ),
                      ),
                      if (messages.isNotEmpty) ...[
                        const _SectionHeading(title: 'رسائل البوت في المرحلة'),
                        for (final item in messages)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 10),
                            child: _chatBubble(item),
                          ),
                      ],
                      if (buttons.isNotEmpty) ...[
                        const _SectionHeading(title: 'الأزرار المتاحة'),
                        Wrap(children: buttons.map(_keyboardButton).toList()),
                      ],
                      if (related.isNotEmpty) ...[
                        const _SectionHeading(
                          title: 'رسائل مرتبطة بهذا الاختيار',
                        ),
                        for (final item in related)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 10),
                            child: _chatBubble(item),
                          ),
                      ],
                    ],
                  ),
                ),
        ),
      ],
    );
  }

  Widget _editor({bool compact = false}) {
    final item = _selected;
    if (item == null) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.touch_app_outlined, size: 42),
            SizedBox(height: 10),
            Text('اختر زرًا أو رسالة من التدفق لتعديلها'),
          ],
        ),
      );
    }
    final isButton = _isButton(item);
    final editor = Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(
                  isButton
                      ? Icons.smart_button_outlined
                      : Icons.message_outlined,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '${isButton ? 'تعديل الزر' : 'تعديل الرسالة'}: ${_displayName(item)}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Row(
                children: [
                  const Icon(Icons.source_outlined, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'المصدر النشط: ${_sourceFor(item)}',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                  if (_isDirty(item))
                    const Tooltip(
                      message: 'تعديلات غير محفوظة',
                      child: Icon(Icons.edit_outlined, size: 17),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            if (isButton)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: ActionChip(
                  label: Text(_contentController.text),
                  onPressed: null,
                ),
              )
            else
              Container(
                constraints: const BoxConstraints(maxHeight: 140),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: SingleChildScrollView(
                  child: Text(_contentController.text),
                ),
              ),
            const SizedBox(height: 12),
            if (compact)
              SizedBox(
                height: 180,
                child: TextField(
                  controller: _contentController,
                  onChanged: (_) => setState(() {}),
                  minLines: 4,
                  maxLines: null,
                  textAlignVertical: TextAlignVertical.top,
                  decoration: InputDecoration(
                    border: const OutlineInputBorder(),
                    labelText: isButton ? 'نص الزر' : 'محتوى الرسالة',
                    hintText: isButton
                        ? 'اكتب التسمية التي ستظهر على الزر'
                        : 'اكتب الرسالة التي سيرسلها البوت',
                    alignLabelWithHint: true,
                  ),
                ),
              )
            else
              Expanded(
                child: TextField(
                  controller: _contentController,
                  onChanged: (_) => setState(() {}),
                  expands: true,
                  minLines: null,
                  maxLines: null,
                  textAlignVertical: TextAlignVertical.top,
                  decoration: InputDecoration(
                    border: const OutlineInputBorder(),
                    labelText: isButton ? 'نص الزر' : 'محتوى الرسالة',
                    hintText: isButton
                        ? 'اكتب التسمية التي ستظهر على الزر'
                        : 'اكتب الرسالة التي سيرسلها البوت',
                    alignLabelWithHint: true,
                  ),
                ),
              ),
            const SizedBox(height: 12),
            Text(
              'سيظهر التغيير في الرسائل والأزرار الجديدة التي ينشئها البوت. '
              'لن تتغير الرسائل القديمة داخل محادثات تيليجرام.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 10),
            FilledButton.icon(
              onPressed: _saving || !_isDirty(item) ? null : _save,
              icon: _saving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.save_outlined),
              label: Text(
                _saving
                    ? 'جارٍ الحفظ والتطبيق...'
                    : _isDirty(item)
                    ? 'حفظ وتطبيق مباشرة'
                    : 'لا توجد تغييرات للحفظ',
              ),
            ),
          ],
        ),
      ),
    );
    return compact ? SingleChildScrollView(child: editor) : editor;
  }

  @override
  Widget build(BuildContext context) {
    final query = _searchController.text.trim().toLowerCase();
    return Scaffold(
      appBar: AppBar(
        title: const Text('أزرار ورسائل البوت'),
        actions: [
          IconButton(
            tooltip: 'تحديث الحالة من البوت',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _loading && _texts.isEmpty
          ? const Center(child: CircularProgressIndicator())
          : _error != null && _texts.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_error!),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: _load,
                    child: const Text('إعادة المحاولة'),
                  ),
                ],
              ),
            )
          : Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final isCompact = constraints.maxWidth < 760;
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (isCompact)
                            _mobileFlowPicker()
                          else if (constraints.maxWidth < 1120)
                            _flowChips(),
                          const SizedBox(height: 10),
                          Row(
                            children: [
                              Expanded(
                                child: TextField(
                                  controller: _searchController,
                                  decoration: InputDecoration(
                                    prefixIcon: const Icon(Icons.search),
                                    hintText:
                                        'ابحث باسم الزر أو الرسالة أو محتواها...',
                                    suffixIcon: query.isEmpty
                                        ? null
                                        : IconButton(
                                            onPressed: _searchController.clear,
                                            icon: const Icon(Icons.close),
                                          ),
                                    border: const OutlineInputBorder(),
                                    isDense: true,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 10),
                              if (!isCompact)
                                ConstrainedBox(
                                  constraints: const BoxConstraints(
                                    maxWidth: 430,
                                  ),
                                  child: _filterPicker(),
                                ),
                            ],
                          ),
                          if (isCompact) ...[
                            const SizedBox(height: 8),
                            _filterPicker(),
                            const SizedBox(height: 8),
                            SegmentedButton<String>(
                              showSelectedIcon: false,
                              segments: const [
                                ButtonSegment(
                                  value: 'preview',
                                  label: Text('معاينة المحادثة'),
                                  icon: Icon(Icons.forum_outlined),
                                ),
                                ButtonSegment(
                                  value: 'editor',
                                  label: Text('محرر العنصر'),
                                  icon: Icon(Icons.edit_outlined),
                                ),
                              ],
                              selected: {_mobilePanel},
                              onSelectionChanged: (value) =>
                                  setState(() => _mobilePanel = value.first),
                            ),
                          ],
                        ],
                      );
                    },
                  ),
                ),
                if (_error != null)
                  MaterialBanner(
                    content: Text(_error!),
                    actions: [
                      TextButton(
                        onPressed: _load,
                        child: const Text('إعادة المحاولة'),
                      ),
                    ],
                  ),
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final isCompact =
                          constraints.maxWidth < 760 ||
                          constraints.maxHeight < 560;
                      if (constraints.maxWidth < 760) {
                        return _mobilePanel == 'preview'
                            ? _chatPreview()
                            : _editor(compact: isCompact);
                      }
                      if (constraints.maxWidth < 1120) {
                        return Row(
                          children: [
                            Expanded(flex: 5, child: _chatPreview()),
                            const VerticalDivider(width: 1),
                            Expanded(
                              flex: 4,
                              child: _editor(compact: isCompact),
                            ),
                          ],
                        );
                      }
                      return Row(
                        children: [
                          _flowRail(),
                          const VerticalDivider(width: 1),
                          SizedBox(width: 410, child: _chatPreview()),
                          const VerticalDivider(width: 1),
                          Expanded(child: _editor(compact: isCompact)),
                        ],
                      );
                    },
                  ),
                ),
              ],
            ),
    );
  }
}

class _SectionHeading extends StatelessWidget {
  const _SectionHeading({required this.title});

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(12, 16, 12, 4),
    child: Text(
      title,
      style: Theme.of(context).textTheme.titleSmall?.copyWith(
        color: Theme.of(context).colorScheme.primary,
        fontWeight: FontWeight.bold,
      ),
    ),
  );
}
