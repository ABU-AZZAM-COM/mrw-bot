import 'package:flutter/material.dart';

class _ActionOption {
  const _ActionOption(this.id, this.label, this.description);

  final String id;
  final String label;
  final String description;
}

class _FlowButton {
  _FlowButton({
    required this.id,
    required this.label,
    required this.action,
    required this.callback,
    required this.next,
    required this.icon,
    this.prototypeOnly = false,
  });

  final String id;
  String label;
  String action;
  final String callback;
  String next;
  final IconData icon;
  final bool prototypeOnly;

  _FlowButton copy() => _FlowButton(
    id: id,
    label: label,
    action: action,
    callback: callback,
    next: next,
    icon: icon,
    prototypeOnly: prototypeOnly,
  );
}

class _FlowStage {
  const _FlowStage({
    required this.id,
    required this.title,
    required this.description,
    required this.buttons,
    this.branchNote,
  });

  final String id;
  final String title;
  final String description;
  final List<_FlowButton> buttons;
  final String? branchNote;
}

const _actions = [
  _ActionOption(
    'start_report',
    'بدء مسار البلاغ',
    'يفتح اختيار قسم البلاغ ويبدأ التحقق من حدود الإرسال.',
  ),
  _ActionOption(
    'show_reports',
    'عرض بلاغاتي',
    'يعرض بلاغات المستخدم وحالاتها.',
  ),
  _ActionOption('show_about', 'عرض التعليمات', 'يعرض معلومات النظام.'),
  _ActionOption(
    'start_support',
    'بدء التواصل مع الإدارة',
    'يفتح مسار مراسلة الإدارة.',
  ),
  _ActionOption(
    'select_category',
    'اختيار قسم البلاغ',
    'يحفظ القسم المختار وينتقل إلى خطواته.',
  ),
  _ActionOption(
    'select_platform',
    'اختيار منصة النشر',
    'يحفظ المنصة ثم يطلب تفاصيل البلاغ.',
  ),
  _ActionOption(
    'request_description',
    'طلب وصف نصي',
    'يستقبل وصف البلاغ ضمن الحد المضبوط.',
  ),
  _ActionOption(
    'request_photo',
    'طلب مرفق صورة اختياري',
    'يقبل صورة أو يتيح للمستخدم تخطي الخطوة.',
  ),
  _ActionOption(
    'request_link',
    'طلب رابط اختياري',
    'يستقبل رابطًا أو يتيح تخطيه بحسب متطلبات القسم.',
  ),
  _ActionOption(
    'request_location',
    'طلب موقع اختياري',
    'يطلب مشاركة الموقع أو تخطي الخطوة.',
  ),
  _ActionOption(
    'request_contact',
    'طلب وسيلة تواصل',
    'يتيح مشاركة رقم التواصل أو تخطيه.',
  ),
  _ActionOption(
    'submit_report',
    'تأكيد إرسال البلاغ',
    'يحفظ البلاغ بعد مراجعة المستخدم للملخص.',
  ),
  _ActionOption('cancel_flow', 'إلغاء المسار', 'ينهي المسار ويعيد القائمة.'),
];

List<_FlowStage> _initialStages() => [
  _FlowStage(
    id: 'home',
    title: 'القائمة الرئيسية',
    description: 'تظهر بعد /start برسالة الترحيب.',
    buttons: [
      _FlowButton(
        id: 'menu_report',
        label: '📝 إرسال بلاغ جديد',
        action: 'start_report',
        callback: 'usr:report',
        next: 'category',
        icon: Icons.edit_note_rounded,
      ),
      _FlowButton(
        id: 'menu_status',
        label: '📊 حالة بلاغي',
        action: 'show_reports',
        callback: 'usr:status',
        next: 'reports',
        icon: Icons.fact_check_outlined,
      ),
      _FlowButton(
        id: 'menu_about',
        label: 'ℹ️ التعليمات',
        action: 'show_about',
        callback: 'usr:about',
        next: 'about',
        icon: Icons.info_outline,
      ),
      _FlowButton(
        id: 'menu_contact',
        label: '📞 تواصل مع الإدارة',
        action: 'start_support',
        callback: 'usr:contact',
        next: 'support',
        icon: Icons.support_agent,
      ),
    ],
  ),
  _FlowStage(
    id: 'category',
    title: 'اختيار قسم البلاغ',
    description: 'القسم يحدد أسئلة المتابعة التي يحتاجها البلاغ.',
    buttons: [
      _FlowButton(
        id: 'category_conduct',
        label: '🚨 تجاوزات وسلوك فردي',
        action: 'select_category',
        callback: 'usr:category:conduct',
        next: 'description',
        icon: Icons.warning_amber_rounded,
      ),
      _FlowButton(
        id: 'category_administrative',
        label: '⚖️ مخالفات إدارية ومالية',
        action: 'select_category',
        callback: 'usr:category:administrative',
        next: 'description',
        icon: Icons.account_balance_outlined,
      ),
      _FlowButton(
        id: 'category_social',
        label: '📱 مخالفات النشر والتواصل الاجتماعي',
        action: 'select_category',
        callback: 'usr:category:social',
        next: 'platform',
        icon: Icons.public,
      ),
      _FlowButton(
        id: 'category_field',
        label: '📍 بلاغ ميداني عاجل / أخرى',
        action: 'select_category',
        callback: 'usr:category:field',
        next: 'description',
        icon: Icons.location_on_outlined,
      ),
      _FlowButton(
        id: 'category_cancel',
        label: '❌ إلغاء',
        action: 'cancel_flow',
        callback: 'usr:cancel',
        next: 'home',
        icon: Icons.close_rounded,
      ),
    ],
  ),
  _FlowStage(
    id: 'platform',
    title: 'اختيار المنصة',
    description: 'فرع خاص ببلاغات النشر الاجتماعي فقط.',
    branchNote: 'يُعرض بعد اختيار «مخالفات النشر والتواصل الاجتماعي».',
    buttons: [
      for (final (key, label) in const [
        ('facebook', 'فيسبوك'),
        ('instagram_tiktok', 'إنستغرام / تيك توك'),
        ('whatsapp', 'واتساب'),
        ('telegram', 'تيليجرام'),
        ('other', 'منصة أخرى'),
      ])
        _FlowButton(
          id: 'platform_$key',
          label: label,
          action: 'select_platform',
          callback: 'usr:platform:$key',
          next: 'description',
          icon: Icons.language,
        ),
      _FlowButton(
        id: 'platform_cancel',
        label: '❌ إلغاء',
        action: 'cancel_flow',
        callback: 'usr:cancel',
        next: 'home',
        icon: Icons.close_rounded,
      ),
    ],
  ),
  _FlowStage(
    id: 'description',
    title: 'وصف البلاغ',
    description: 'يكتب المستخدم التفاصيل؛ ثم تتبعها أسئلة بحسب القسم.',
    buttons: [
      _FlowButton(
        id: 'description_cancel',
        label: '❌ إلغاء',
        action: 'cancel_flow',
        callback: 'usr:cancel',
        next: 'home',
        icon: Icons.close_rounded,
      ),
    ],
  ),
  _FlowStage(
    id: 'evidence',
    title: 'الأدلة والبيانات الإضافية',
    description: 'تظهر الخطوات الاختيارية المناسبة لقسم البلاغ.',
    branchNote:
        'النشر الاجتماعي يطلب رابطًا أو صورة؛ والبلاغ الميداني قد يطلب الموقع.',
    buttons: [
      _FlowButton(
        id: 'evidence_skip_photo',
        label: '⏭️ تخطي الصورة',
        action: 'request_photo',
        callback: 'usr:skip:photo',
        next: 'contact',
        icon: Icons.skip_next_rounded,
      ),
      _FlowButton(
        id: 'evidence_skip_link',
        label: '⏭️ لا أملك رابطًا',
        action: 'request_link',
        callback: 'usr:skip:link',
        next: 'contact',
        icon: Icons.link_off_rounded,
      ),
      _FlowButton(
        id: 'evidence_skip_location',
        label: '⏭️ تخطي الموقع',
        action: 'request_location',
        callback: 'usr:skip:location',
        next: 'contact',
        icon: Icons.location_disabled_outlined,
      ),
      _FlowButton(
        id: 'evidence_cancel',
        label: '❌ إلغاء',
        action: 'cancel_flow',
        callback: 'usr:cancel',
        next: 'home',
        icon: Icons.close_rounded,
      ),
    ],
  ),
  _FlowStage(
    id: 'contact',
    title: 'معلومات التواصل',
    description: 'يمكن للمستخدم مشاركة رقم أو تخطي الخطوة.',
    buttons: [
      _FlowButton(
        id: 'contact_skip',
        label: '⏭️ تخطي بيانات التواصل',
        action: 'request_contact',
        callback: 'usr:skip:contact',
        next: 'confirmation',
        icon: Icons.skip_next_rounded,
      ),
      _FlowButton(
        id: 'contact_cancel',
        label: '❌ إلغاء',
        action: 'cancel_flow',
        callback: 'usr:cancel',
        next: 'home',
        icon: Icons.close_rounded,
      ),
    ],
  ),
  _FlowStage(
    id: 'confirmation',
    title: 'مراجعة وإرسال',
    description: 'يعرض البوت ملخص البلاغ قبل حفظه.',
    buttons: [
      _FlowButton(
        id: 'confirm_send',
        label: '✅ إرسال البلاغ',
        action: 'submit_report',
        callback: 'usr:confirm',
        next: 'done',
        icon: Icons.check_circle_outline,
      ),
      _FlowButton(
        id: 'confirm_cancel',
        label: '❌ إلغاء',
        action: 'cancel_flow',
        callback: 'usr:cancel',
        next: 'home',
        icon: Icons.close_rounded,
      ),
    ],
  ),
];

class BotButtonMapPage extends StatefulWidget {
  const BotButtonMapPage({super.key});

  @override
  State<BotButtonMapPage> createState() => _BotButtonMapPageState();
}

class _BotButtonMapPageState extends State<BotButtonMapPage> {
  late List<_FlowStage> _stages;
  List<_FlowStage>? _savedStages;
  String _selectedStageId = 'home';
  String _selectedButtonId = 'menu_report';
  String _previewStageId = 'home';
  bool _showPhonePreview = false;
  int _customButtonCounter = 0;
  int _editorRevision = 0;

  @override
  void initState() {
    super.initState();
    _stages = _initialStages();
  }

  _FlowStage get _selectedStage =>
      _stages.firstWhere((stage) => stage.id == _selectedStageId);

  _FlowButton get _selectedButton => _selectedStage.buttons.firstWhere(
    (button) => button.id == _selectedButtonId,
    orElse: () => _selectedStage.buttons.first,
  );

  _FlowStage? _stageFor(String id) {
    for (final stage in _stages) {
      if (stage.id == id) return stage;
    }
    return null;
  }

  void _selectStage(_FlowStage stage) {
    setState(() {
      _selectedStageId = stage.id;
      _selectedButtonId = stage.buttons.first.id;
    });
  }

  void _selectButton(_FlowStage stage, _FlowButton button) {
    setState(() {
      _selectedStageId = stage.id;
      _selectedButtonId = button.id;
    });
  }

  void _addPrototypeButton() {
    final id = 'prototype_${_customButtonCounter++}';
    setState(() {
      _selectedStage.buttons.add(
        _FlowButton(
          id: id,
          label: '✨ زر تجريبي جديد',
          action: 'show_about',
          callback: 'غير موصول — نموذج فقط',
          next: 'about',
          icon: Icons.add_circle_outline,
          prototypeOnly: true,
        ),
      );
      _selectedButtonId = id;
    });
  }

  List<_FlowStage> _copyStages(List<_FlowStage> source) => [
    for (final stage in source)
      _FlowStage(
        id: stage.id,
        title: stage.title,
        description: stage.description,
        branchNote: stage.branchNote,
        buttons: [for (final button in stage.buttons) button.copy()],
      ),
  ];

  void _saveDraft() {
    setState(() => _savedStages = _copyStages(_stages));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'حُفظت نسخة المسودة داخل هذه الجلسة فقط — لم يتغير البوت.',
        ),
      ),
    );
  }

  void _restoreDraft() {
    final saved = _savedStages;
    if (saved == null) return;
    setState(() {
      _stages = _copyStages(saved);
      _selectedStageId = 'home';
      _selectedButtonId = _stages.first.buttons.first.id;
      _editorRevision++;
    });
  }

  void _resetPrototype() {
    setState(() {
      _stages = _initialStages();
      _savedStages = null;
      _selectedStageId = 'home';
      _selectedButtonId = 'menu_report';
      _previewStageId = 'home';
      _customButtonCounter = 0;
      _editorRevision++;
    });
  }

  void _editLabel(String value) {
    setState(() => _selectedButton.label = value);
  }

  void _editAction(String? value) {
    if (value == null) return;
    setState(() => _selectedButton.action = value);
  }

  void _editNext(String? value) {
    if (value == null) return;
    setState(() => _selectedButton.next = value);
  }

  void _openPhonePreview() {
    setState(() {
      _showPhonePreview = true;
      _previewStageId = 'home';
    });
  }

  void _advancePreview(_FlowButton button) {
    final next = _stageFor(button.next);
    if (next == null) return;
    setState(() => _previewStageId = next.id);
  }

  Widget _statusBanner(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: colors.tertiaryContainer.withValues(alpha: .55),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: colors.tertiary.withValues(alpha: .25)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.science_outlined, color: colors.tertiary),
          const SizedBox(width: 10),
          const Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'مختبر تصميم — لا يغيّر البوت',
                  style: TextStyle(fontWeight: FontWeight.w800),
                ),
                SizedBox(height: 3),
                Text(
                  'الخريطة مستندة إلى الأزرار والمسارات الموجودة حاليًا. '
                  'التعديلات والأزرار الجديدة تجريبية داخل هذه الجلسة فقط.',
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _toolbar(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        FilledButton.icon(
          onPressed: _openPhonePreview,
          icon: const Icon(Icons.smartphone_outlined),
          label: const Text('معاينة الهاتف'),
        ),
        OutlinedButton.icon(
          onPressed: _saveDraft,
          icon: const Icon(Icons.save_outlined),
          label: const Text('حفظ مسودة تجريبية'),
        ),
        OutlinedButton.icon(
          onPressed: _savedStages == null ? null : _restoreDraft,
          icon: const Icon(Icons.restore_rounded),
          label: const Text('استعادة المسودة'),
        ),
        IconButton(
          tooltip: 'إعادة النموذج إلى الأزرار الحالية',
          onPressed: _resetPrototype,
          icon: const Icon(Icons.restart_alt),
        ),
        Chip(
          avatar: Icon(Icons.visibility_outlined, color: colors.primary),
          label: const Text('عرض ومحاكاة فقط'),
        ),
      ],
    );
  }

  Widget _stageCard(BuildContext context, _FlowStage stage, int index) {
    final colors = Theme.of(context).colorScheme;
    final selectedStage = stage.id == _selectedStageId;
    return Column(
      children: [
        if (index > 0) ...[
          Icon(
            stage.id == 'platform'
                ? Icons.alt_route_rounded
                : Icons.south_rounded,
            color: colors.primary,
          ),
          if (stage.branchNote != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                stage.branchNote!,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ),
        ],
        Card(
          clipBehavior: Clip.antiAlias,
          color: selectedStage
              ? colors.primaryContainer.withValues(alpha: .28)
              : null,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(18),
            side: BorderSide(
              color: selectedStage ? colors.primary : colors.outlineVariant,
              width: selectedStage ? 1.5 : 1,
            ),
          ),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    CircleAvatar(
                      radius: 16,
                      backgroundColor: colors.primaryContainer,
                      child: Text('${index + 1}'),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        stage.title,
                        style: Theme.of(context).textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                    Text(
                      '${stage.buttons.length} أزرار',
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  stage.description,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 10),
                for (final button in stage.buttons)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () => _selectButton(stage, button),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 11,
                          vertical: 9,
                        ),
                        decoration: BoxDecoration(
                          color: button.id == _selectedButtonId
                              ? colors.primary.withValues(alpha: .1)
                              : colors.surface,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: button.id == _selectedButtonId
                                ? colors.primary
                                : colors.outlineVariant,
                          ),
                        ),
                        child: Row(
                          children: [
                            Icon(button.icon, size: 18),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                button.label,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            if (button.prototypeOnly)
                              const Padding(
                                padding: EdgeInsetsDirectional.only(start: 6),
                                child: Icon(Icons.science_outlined, size: 16),
                              ),
                            const Icon(Icons.chevron_left, size: 18),
                          ],
                        ),
                      ),
                    ),
                  ),
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton.icon(
                    onPressed: () => _selectStage(stage),
                    icon: const Icon(Icons.tune, size: 17),
                    label: const Text('خصائص المرحلة'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _flowMap(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Row(
        children: [
          const Icon(Icons.account_tree_outlined),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'تدفق المستخدم الحالي',
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
            ),
          ),
          Text('${_stages.length} مراحل'),
        ],
      ),
      const SizedBox(height: 12),
      for (var i = 0; i < _stages.length; i++)
        _stageCard(context, _stages[i], i),
      const SizedBox(height: 8),
      OutlinedButton.icon(
        onPressed: _addPrototypeButton,
        icon: const Icon(Icons.add),
        label: const Text('إضافة زر تجريبي إلى المرحلة المحددة'),
      ),
    ],
  );

  Widget _inspector(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final button = _selectedButton;
    final currentAction = _actions.any((item) => item.id == button.action)
        ? button.action
        : _actions.first.id;
    final stageOptions = {
      for (final stage in _stages) stage.id: stage.title,
      'done': 'إنهاء المسار',
      'reports': 'شاشة بلاغاتي',
      'about': 'شاشة التعليمات',
      'support': 'محادثة الإدارة',
    };
    final currentNext = stageOptions.containsKey(button.next)
        ? button.next
        : stageOptions.keys.first;

    return Card(
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(color: colors.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.tune_rounded),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'خصائص الزر',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
                if (button.prototypeOnly) const Chip(label: Text('تجريبي')),
              ],
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: ValueKey('button-label-${button.id}-$_editorRevision'),
              initialValue: button.label,
              maxLength: 64,
              decoration: const InputDecoration(
                labelText: 'النص الظاهر على الزر',
                helperText: 'غيّر النص لترى أثره في معاينة الهاتف.',
                border: OutlineInputBorder(),
              ),
              onChanged: _editLabel,
            ),
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              key: ValueKey('button-action-${button.id}-$_editorRevision'),
              isExpanded: true,
              initialValue: currentAction,
              decoration: const InputDecoration(
                labelText: 'الإجراء الآمن',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final action in _actions)
                  DropdownMenuItem(
                    value: action.id,
                    child: Text(action.label, overflow: TextOverflow.ellipsis),
                  ),
              ],
              onChanged: _editAction,
            ),
            const SizedBox(height: 6),
            Text(
              _actions
                  .firstWhere((action) => action.id == currentAction)
                  .description,
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: ValueKey('button-next-${button.id}-$_editorRevision'),
              isExpanded: true,
              initialValue: currentNext,
              decoration: const InputDecoration(
                labelText: 'الانتقال التالي في الخريطة',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final entry in stageOptions.entries)
                  DropdownMenuItem(
                    value: entry.key,
                    child: Text(entry.value, overflow: TextOverflow.ellipsis),
                  ),
              ],
              onChanged: _editNext,
            ),
            const SizedBox(height: 12),
            InputDecorator(
              decoration: const InputDecoration(
                labelText: 'معرّف الاستدعاء الحالي',
                border: OutlineInputBorder(),
              ),
              child: SelectableText(button.callback),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: colors.secondaryContainer.withValues(alpha: .45),
                borderRadius: BorderRadius.circular(12),
              ),
              child: const Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.lock_outline, size: 19),
                  SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'لا تُنفذ هذه المسودة أي كود ولا ترسل تغييرات إلى البوت. '
                      'الأفعال الجديدة تحتاج تعريفًا وربطًا خادميًا واختبارات قبل النشر.',
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _phonePreview(BuildContext context) {
    final stage = _stageFor(_previewStageId) ?? _stages.first;
    final colors = Theme.of(context).colorScheme;
    return Card(
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(26),
        side: BorderSide(color: colors.outlineVariant, width: 2),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                IconButton(
                  tooltip: 'رجوع في المعاينة',
                  onPressed: () {
                    final index = _stages.indexWhere(
                      (item) => item.id == stage.id,
                    );
                    setState(
                      () => _previewStageId =
                          _stages[(index - 1).clamp(0, _stages.length - 1)].id,
                    );
                  },
                  icon: const Icon(Icons.arrow_back),
                ),
                CircleAvatar(
                  radius: 17,
                  backgroundColor: colors.primaryContainer,
                  child: const Icon(Icons.smart_toy_outlined, size: 19),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'بوت البلاغات',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                IconButton(
                  tooltip: 'إغلاق المعاينة',
                  onPressed: () => setState(() => _showPhonePreview = false),
                  icon: const Icon(Icons.close),
                ),
              ],
            ),
            const Divider(),
            Container(
              constraints: const BoxConstraints(minHeight: 140),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: colors.surfaceContainerLow,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    stage.title,
                    style: const TextStyle(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 8),
                  Text(stage.description),
                  if (stage.id == 'home') ...[
                    const SizedBox(height: 10),
                    const Text('أهلًا بك في منظومة استقبال البلاغات.'),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 10),
            for (final button in stage.buttons)
              Padding(
                padding: const EdgeInsets.only(bottom: 7),
                child: OutlinedButton(
                  key: ValueKey('preview-button-${button.id}'),
                  onPressed: () => _advancePreview(button),
                  style: OutlinedButton.styleFrom(
                    alignment: AlignmentDirectional.center,
                    padding: const EdgeInsets.symmetric(vertical: 11),
                  ),
                  child: Text(button.label, textAlign: TextAlign.center),
                ),
              ),
            const SizedBox(height: 4),
            Text(
              'محاكاة محلية — لا يتم إرسال أي تفاعل إلى Telegram.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.labelSmall,
            ),
          ],
        ),
      ),
    );
  }

  Widget _workbench(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    if (_showPhonePreview) return _phonePreview(context);
    if (width >= 1180) {
      return Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(flex: 6, child: _flowMap(context)),
          const SizedBox(width: 16),
          Expanded(flex: 4, child: _inspector(context)),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _flowMap(context),
        const SizedBox(height: 14),
        _inspector(context),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      _statusBanner(context),
      const SizedBox(height: 14),
      _toolbar(context),
      const SizedBox(height: 14),
      _workbench(context),
    ],
  );
}
