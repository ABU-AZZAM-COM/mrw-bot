import 'package:flutter/material.dart';

import '../api.dart';

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

  Future<void> _select(Map<String, dynamic> item) async {
    if (item['key'] == _selectedKey) return;
    final current = _selected;
    if (current != null &&
        _contentController.text != (current['content']?.toString() ?? '')) {
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
      if (!mounted || discard != true) return;
    }
    setState(() {
      _selectedKey = item['key']?.toString();
      _contentController.text = item['content']?.toString() ?? '';
    });
  }

  Widget _flowPicker() => SingleChildScrollView(
    scrollDirection: Axis.horizontal,
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    child: Row(
      children: [
        for (final id in _flowOrder)
          Padding(
            padding: const EdgeInsetsDirectional.only(end: 8),
            child: ChoiceChip(
              label: Text('${_flowTitles[id]} (${_itemsFor(id).length})'),
              selected: id == _flowId,
              onSelected: (_) => setState(() => _flowId = id),
            ),
          ),
      ],
    ),
  );

  Widget _preview(Map<String, dynamic> item, {bool compact = false}) {
    final isButton = _isButton(item);
    final content = item['content']?.toString() ?? '';
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => _select(item),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                isButton
                    ? Icons.smart_button_outlined
                    : Icons.chat_bubble_outline,
                color: item['key'] == _selectedKey
                    ? Theme.of(context).colorScheme.primary
                    : null,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _displayName(item),
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: 8),
                    if (isButton)
                      Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: ActionChip(
                          label: Text(content),
                          onPressed: () => _select(item),
                        ),
                      )
                    else
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: Theme.of(
                            context,
                          ).colorScheme.surfaceContainerHighest,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Text(
                          content,
                          maxLines: compact ? 2 : 4,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                ),
              ),
              if (item['key'] == _selectedKey)
                const Icon(Icons.edit_outlined, size: 18),
            ],
          ),
        ),
      ),
    );
  }

  Widget _flowList() {
    final items = _itemsFor(_flowId);
    final buttons = items.where(_isButton).toList();
    final messages = items.where((item) => !_isButton(item)).toList();
    final selected = _selected;
    final related = selected != null && _isButton(selected)
        ? _relatedTexts(selected)
        : const <Map<String, dynamic>>[];
    if (items.isEmpty) {
      return const Center(child: Text('لا توجد عناصر في هذا التدفق'));
    }
    return ListView(
      padding: const EdgeInsets.only(bottom: 12),
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
          child: Text(
            _flowDescriptions[_flowId] ?? '',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
        if (buttons.isNotEmpty) ...[
          const _SectionHeading(title: 'الأزرار كما تظهر في البوت'),
          ...buttons.map((item) => _preview(item, compact: true)),
        ],
        if (related.isNotEmpty) ...[
          const _SectionHeading(title: 'النصوص المرتبطة بالزر المحدد'),
          ...related.map((item) => _preview(item, compact: true)),
        ],
        if (messages.isNotEmpty) ...[
          const _SectionHeading(title: 'رسائل هذا التدفق'),
          ...messages.map((item) => _preview(item)),
        ],
      ],
    );
  }

  Widget _editor() {
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
    return Card(
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
            if (isButton)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: ActionChip(
                  label: Text(_contentController.text),
                  onPressed: () {},
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
              onPressed: _saving ? null : _save,
              icon: _saving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.save_outlined),
              label: Text(
                _saving ? 'جارٍ الحفظ والتطبيق...' : 'حفظ وتطبيق مباشرة',
              ),
            ),
          ],
        ),
      ),
    );
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
                _flowPicker(),
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
                  child: TextField(
                    controller: _searchController,
                    decoration: InputDecoration(
                      prefixIcon: const Icon(Icons.search),
                      hintText: 'ابحث باسم الزر أو الرسالة أو محتواها...',
                      suffixIcon: query.isEmpty
                          ? null
                          : IconButton(
                              onPressed: _searchController.clear,
                              icon: const Icon(Icons.close),
                            ),
                      border: const OutlineInputBorder(),
                    ),
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
                      if (constraints.maxWidth < 760) {
                        return Column(
                          children: [
                            Expanded(flex: 5, child: _flowList()),
                            const Divider(height: 1),
                            Expanded(flex: 4, child: _editor()),
                          ],
                        );
                      }
                      return Row(
                        children: [
                          SizedBox(width: 460, child: _flowList()),
                          const VerticalDivider(width: 1),
                          Expanded(child: _editor()),
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
