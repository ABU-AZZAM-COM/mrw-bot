import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'shared_dialogs.dart';

class OperationsPage extends StatefulWidget {
  const OperationsPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<OperationsPage> createState() => _OperationsPageState();
}

class _OperationsPageState extends State<OperationsPage> {
  final _maxDesc = TextEditingController(text: '1500');
  final _maxPhoto = TextEditingController(text: '10');
  final _dailyLimit = TextEditingController(text: '10');
  final _minuteLimit = TextEditingController(text: '2');
  final _hourLimit = TextEditingController(text: '5');
  final _globalHourLimit = TextEditingController(text: '60');
  final _globalDayLimit = TextEditingController(text: '300');
  final _freezeAfterStrikes = TextEditingController(text: '1');
  final _freezeFirst = TextEditingController(text: '60');
  final _freezeSecond = TextEditingController(text: '1440');
  final _freezeRepeat = TextEditingController(text: '8640');
  final _freezePauseMinutes = TextEditingController(text: '60');
  final _userLimitsPauseMinutes = TextEditingController(text: '60');
  final Map<String, TextEditingController> _blockMessages = {
    'frozen': TextEditingController(
      text:
          '⛔ تم إيقاف الإرسال مؤقتًا بسبب تكرار تجاوز الحدود.\n'
          'الوقت المتبقي: {minutes} دقيقة.\n'
          'إذا كنت ترى أن هذا خطأ، تواصل مع الإدارة.',
    ),
    'minute_limit': TextEditingController(
      text:
          '⏱️ تم بلوغ حد المحاولات في الدقيقة.\n'
          'الحد المسموح: {limit} محاولة/دقيقة. حاول لاحقًا.',
    ),
    'hour_limit': TextEditingController(
      text:
          '⏳ تم بلوغ حد المحاولات في الساعة.\n'
          'الحد المسموح: {limit} محاولة/ساعة. حاول لاحقًا.',
    ),
    'daily_limit': TextEditingController(
      text:
          '📅 تم بلوغ الحد اليومي ({limit} محاولة).\n'
          'يمكنك المحاولة غدًا أو التواصل مع الإدارة.',
    ),
    'emergency': TextEditingController(
      text:
          '🛑 استقبال البلاغات متوقف مؤقتًا.\n'
          'يرجى المحاولة لاحقًا أو التواصل مع الإدارة.',
    ),
    'manual_block': TextEditingController(
      text:
          '🔒 تم تقييد حسابك: {kind}.\n'
          'السبب: {reason}\n'
          'للاستفسار، تواصل مع الإدارة.',
    ),
  };
  Map<String, dynamic>? _ov;
  Map<String, dynamic>? _settings;
  String? _err;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _maxDesc.dispose();
    _maxPhoto.dispose();
    _dailyLimit.dispose();
    _minuteLimit.dispose();
    _hourLimit.dispose();
    _globalHourLimit.dispose();
    _globalDayLimit.dispose();
    _freezeAfterStrikes.dispose();
    _freezeFirst.dispose();
    _freezeSecond.dispose();
    _freezeRepeat.dispose();
    _freezePauseMinutes.dispose();
    _userLimitsPauseMinutes.dispose();
    for (final controller in _blockMessages.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final ov = await widget.api.get('/api/admin/overview');
      Map<String, dynamic>? settings;
      try {
        final response = await widget.api.get('/api/admin/settings');
        settings = (response['items'] as Map?)?.cast<String, dynamic>();
      } on ApiException catch (error) {
        if (error.status != 404) rethrow;
        // يسمح بالاتصال بخوادم API الأقدم التي لا تدعم الإعدادات بعد.
      }
      if (!mounted) return;
      setState(() {
        _ov = ov;
        _settings = settings;
        if (settings != null) {
          _maxDesc.text = '${settings['max_description_length'] ?? 1500}';
          _maxPhoto.text = '${settings['max_photo_mb'] ?? 10}';
          _dailyLimit.text = '${settings['max_reports_per_day'] ?? 10}';
          _minuteLimit.text = '${settings['max_reports_per_minute'] ?? 2}';
          _hourLimit.text = '${settings['max_reports_per_hour'] ?? 5}';
          _globalHourLimit.text =
              '${settings['max_reports_per_hour_global'] ?? 60}';
          _globalDayLimit.text =
              '${settings['max_reports_per_day_global'] ?? 300}';
          _freezeAfterStrikes.text = '${settings['freeze_after_strikes'] ?? 1}';
          _freezeFirst.text = '${settings['freeze_minutes_first'] ?? 60}';
          _freezeSecond.text = '${settings['freeze_minutes_second'] ?? 1440}';
          _freezeRepeat.text = '${settings['freeze_minutes_repeat'] ?? 8640}';
          final messages = settings['block_messages'];
          if (messages is Map) {
            for (final entry in _blockMessages.entries) {
              final message = messages[entry.key];
              if (message is String) entry.value.text = message;
            }
          }
        }
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = errMsg(e));
    }
  }

  Future<void> _saveSettings() async {
    final desc = int.tryParse(_maxDesc.text.trim());
    final photo = int.tryParse(_maxPhoto.text.trim());
    final daily = int.tryParse(_dailyLimit.text.trim());
    final minute = int.tryParse(_minuteLimit.text.trim());
    final hour = int.tryParse(_hourLimit.text.trim());
    final globalHour = int.tryParse(_globalHourLimit.text.trim());
    final globalDay = int.tryParse(_globalDayLimit.text.trim());
    final freezeAfter = int.tryParse(_freezeAfterStrikes.text.trim());
    final freezeFirst = int.tryParse(_freezeFirst.text.trim());
    final freezeSecond = int.tryParse(_freezeSecond.text.trim());
    final freezeRepeat = int.tryParse(_freezeRepeat.text.trim());
    if (desc == null ||
        photo == null ||
        daily == null ||
        minute == null ||
        hour == null ||
        globalHour == null ||
        globalDay == null ||
        freezeAfter == null ||
        freezeFirst == null ||
        freezeSecond == null ||
        freezeRepeat == null) {
      toast(context, 'أدخل أرقامًا صحيحة في جميع الحقول', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      final result = await widget.api.put('/api/admin/settings', {
        'max_description_length': desc,
        'max_photo_mb': photo,
        'max_reports_per_day': daily,
        'max_reports_per_minute': minute,
        'max_reports_per_hour': hour,
        'max_reports_per_hour_global': globalHour,
        'max_reports_per_day_global': globalDay,
        'freeze_after_strikes': freezeAfter,
        'freeze_minutes_first': freezeFirst,
        'freeze_minutes_second': freezeSecond,
        'freeze_minutes_repeat': freezeRepeat,
        'block_messages': {
          for (final entry in _blockMessages.entries)
            entry.key: entry.value.text,
        },
      });
      if (!mounted) return;
      setState(
        () => _settings = (result['items'] as Map).cast<String, dynamic>(),
      );
      toast(context, 'حُفظت حدود التشغيل وتُطبّق فورًا ✅');
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  DateTime? get _autoFreezeUntil =>
      DateTime.tryParse('${_settings?['auto_freeze_paused_until'] ?? ''}');

  bool get _autoFreezePaused {
    final until = _autoFreezeUntil;
    return until != null && until.isAfter(DateTime.now());
  }

  DateTime? get _userLimitsUntil =>
      DateTime.tryParse('${_settings?['user_limits_paused_until'] ?? ''}');

  bool get _userLimitsPaused {
    final until = _userLimitsUntil;
    return until != null && until.isAfter(DateTime.now());
  }

  Future<void> _setAutoFreezePause(bool on) async {
    if (_busy) return;
    if (on == _autoFreezePaused) {
      toast(
        context,
        on ? 'التجميد التلقائي موقوف بالفعل' : 'التجميد التلقائي يعمل',
      );
      return;
    }
    String? reason;
    int? duration;
    if (on) {
      duration = int.tryParse(_freezePauseMinutes.text.trim());
      if (duration == null || duration < 5 || duration > 1440) {
        toast(
          context,
          'مدة الإيقاف يجب أن تكون بين 5 و1440 دقيقة',
          error: true,
        );
        return;
      }
      reason = await requiredTextDialog(
        context,
        title: 'إيقاف التجميد التلقائي مؤقتًا',
        hint: 'اكتب السبب (ستبقى حدود المحاولات فعّالة)…',
        danger: true,
      );
      if (reason == null) return;
    } else {
      final ok = await confirmDialog(
        context,
        'استئناف التجميد التلقائي',
        'ستعود مدد التجميد بعد تجاوز الحدود.',
        okLabel: 'استئناف',
      );
      if (!ok) return;
    }
    setState(() => _busy = true);
    try {
      await widget.api.post('/api/admin/system/auto-freeze', {
        'on': on,
        'duration_minutes': duration,
        'reason': reason,
      });
      if (!mounted) return;
      toast(
        context,
        on
            ? 'أُوقف التجميد التلقائي مؤقتًا؛ حدود المحاولات ما زالت فعّالة'
            : 'استؤنف التجميد التلقائي',
      );
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _setUserLimitsPause(bool on) async {
    if (_busy) return;
    if (on == _userLimitsPaused) {
      toast(
        context,
        on ? 'حدود المستخدم موقوفة بالفعل' : 'حدود المستخدم فعّالة',
      );
      return;
    }
    String? reason;
    int? duration;
    if (on) {
      duration = int.tryParse(_userLimitsPauseMinutes.text.trim());
      if (duration == null || duration < 5 || duration > 1440) {
        toast(
          context,
          'مدة الإيقاف يجب أن تكون بين 5 و1440 دقيقة',
          error: true,
        );
        return;
      }
      reason = await requiredTextDialog(
        context,
        title: 'إيقاف حدود المستخدم مؤقتًا',
        hint: 'اكتب السبب (حد الإغراق العام والطوارئ يبقيان فعّالين)…',
        danger: true,
      );
      if (reason == null) return;
    } else {
      final ok = await confirmDialog(
        context,
        'استئناف حدود المستخدم',
        'ستعود حدود الدقيقة والساعة واليوم فورًا.',
        okLabel: 'استئناف',
      );
      if (!ok) return;
    }
    setState(() => _busy = true);
    try {
      await widget.api.post('/api/admin/system/user-limits', {
        'on': on,
        'duration_minutes': duration,
        'reason': reason,
      });
      if (!mounted) return;
      toast(
        context,
        on
            ? 'أُوقفت حدود المستخدم مؤقتًا؛ حماية الإغراق العام مستمرة'
            : 'استؤنفت حدود المستخدم',
      );
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _setEmergency(bool on) async {
    if (_busy) return;
    final ctx = context;
    final bool emergencyNow = _ov?['emergency'] == true;
    if (on == emergencyNow) {
      toast(ctx, on ? 'الإيقاف الطارئ مفعّل بالفعل' : 'الاستقبال شغّال بالفعل');
      return;
    }
    String? reason;
    if (on) {
      // سبب الإيقاف إلزامي — يُسجل في سجل التدقيق ويظهر للنظام
      reason = await requiredTextDialog(
        ctx,
        title: 'إيقاف طارئ — السبب إلزامي',
        hint: 'اكتب سبب إيقاف الاستقبال (مثال: صيانة / حالة طارئة)…',
        danger: true,
      );
      if (reason == null) return;
    } else {
      final ok = await confirmDialog(
        ctx,
        'استئناف الاستقبال',
        'إعادة تفعيل استقبال البلاغات من تلغرام؟',
        okLabel: 'استئناف',
      );
      if (!ok) return;
    }
    setState(() => _busy = true);
    try {
      await widget.api.post('/api/admin/system/emergency', {
        'on': on,
        'reason': reason,
      });
      if (!mounted) return;
      toast(
        context,
        on
            ? '🛑 الإيقاف الطارئ مفعّل — تُمنع البلاغات الجديدة'
            : '🟢 استؤنفت منظومة الاستقبال',
      );
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _backupNow() async {
    setState(() => _busy = true);
    try {
      final d = await widget.api.post('/api/admin/backup/now', {});
      if (!mounted) return;
      toast(
        context,
        d['uploaded'] == true
            ? 'رُفعت النسخة إلى المستودع السحابي ✅'
            : 'تمت المحاولة — مستودع النسخ غير مُهيأ على الخادم',
      );
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ov = _ov;
    if (_err != null && ov == null) {
      return ErrorView(_err!, _load);
    }
    if (ov == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final bool emergency = ov['emergency'] == true;
    final bool botReady = ov['bot_ready'] == true;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            StateBadge(
              botReady ? '🤖 البوت متصل ويعمل' : '🤖 البوت غير متصل حالياً',
              color: botReady
                  ? const Color(0xFF3FA66B)
                  : const Color(0xFFE06C75),
            ),
            const SizedBox(width: 8),
            IconButton(
              tooltip: 'تحديث',
              onPressed: _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: 10),
        SectionCard(
          'الإيقاف الطارئ — السيطرة الكامل',
          icon: Icons.emergency,
          trailing: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : null,
          child: SwitchListTile(
            activeThumbColor: const Color(0xFFE06C75),
            contentPadding: EdgeInsets.zero,
            title: Text(
              emergency
                  ? 'الاستقبال متوقف — البلاغات الجديدة تُرفض'
                  : 'الاستقبال تعمل',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            subtitle: const Text(
              'يمنع فورًا بدء البلاغات الجديدة من تلغرام — لا يقطع المحادثات الجارية',
            ),
            value: emergency,
            onChanged: _busy ? null : (v) => _setEmergency(v == true),
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'إدارة الحظر وحدود المحاولات',
          icon: Icons.person_off_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SwitchListTile(
                activeThumbColor: const Color(0xFFE0A44C),
                contentPadding: EdgeInsets.zero,
                title: Text(
                  _userLimitsPaused
                      ? 'حدود المحاولات الفردية موقوفة مؤقتًا'
                      : 'حدود المحاولات الفردية فعّالة',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                subtitle: Text(
                  _userLimitsPaused
                      ? 'حتى ${_userLimitsUntil?.toLocal().toString().substring(0, 16) ?? ''}. حد الإغراق العام والإيقاف الطارئ باقيان فعّالين؛ الحظر اليدوي أو الساري لا يُرفع.'
                      : 'يمكن إيقاف حدود الدقيقة والساعة واليوم مؤقتًا، مع بقاء حماية النظام العامة والطوارئ.',
                ),
                value: _userLimitsPaused,
                onChanged: _busy || _settings == null
                    ? null
                    : _setUserLimitsPause,
              ),
              if (!_userLimitsPaused) ...[
                const SizedBox(height: 6),
                _numberField(
                  _userLimitsPauseMinutes,
                  'مدة إيقاف حدود المستخدم (5–1440 دقيقة)',
                  300,
                ),
              ],
              const Divider(height: 24),
              SwitchListTile(
                activeThumbColor: const Color(0xFFE0A44C),
                contentPadding: EdgeInsets.zero,
                title: Text(
                  _autoFreezePaused
                      ? 'التجميد التلقائي موقوف مؤقتًا'
                      : 'التجميد التلقائي يعمل',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                subtitle: Text(
                  _autoFreezePaused
                      ? 'حتى ${_autoFreezeUntil?.toLocal().toString().substring(0, 16) ?? ''}. ستظل حدود المحاولات سارية، ولن يُرفع الحظر عن المحجوبين حاليًا.'
                      : 'يمكن إيقاف التجميد مؤقتًا؛ رفض الطلبات المتجاوزة للحدود يبقى فعالًا.',
                ),
                value: _autoFreezePaused,
                onChanged: _busy || _settings == null
                    ? null
                    : _setAutoFreezePause,
              ),
              if (!_autoFreezePaused) ...[
                const SizedBox(height: 6),
                _numberField(
                  _freezePauseMinutes,
                  'مدة إيقاف التجميد (5–1440 دقيقة)',
                  260,
                ),
              ],
              if (_settings == null)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text(
                    'إدارة التجميد المؤقت غير مدعومة في إصدار API المتصل.',
                    style: TextStyle(color: Colors.amber),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'حدود استقبال البلاغات',
          icon: Icons.tune_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'تُحفظ في قاعدة البيانات وتُطبّق على البوت فورًا، وتبقى بعد إعادة التشغيل.',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 12,
                ),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 12,
                runSpacing: 10,
                children: [
                  _numberField(_maxDesc, 'طول الوصف (200–4000)', 210),
                  _numberField(_maxPhoto, 'حجم الصورة MB (1–20)', 190),
                  _numberField(_dailyLimit, 'الحد اليومي (1–100)', 190),
                  _numberField(_minuteLimit, 'الحد في الدقيقة (1–10)', 210),
                  _numberField(_hourLimit, 'الحد في الساعة (1–100)', 210),
                  _numberField(
                    _globalHourLimit,
                    'الحد العام/ساعة (10–10000)',
                    240,
                  ),
                  _numberField(
                    _globalDayLimit,
                    'الحد العام/يوم (100–100000)',
                    250,
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(
                'مدة التجميد التصاعدية عند تكرار تجاوز الحد — بالدقائق',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurface,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 12,
                runSpacing: 10,
                children: [
                  _numberField(
                    _freezeAfterStrikes,
                    'التجميد بعد كم تجاوز؟ (1–10)',
                    220,
                  ),
                  _numberField(_freezeFirst, 'المخالفة الأولى (1–10080)', 210),
                  _numberField(_freezeSecond, 'الثانية (1–43200)', 190),
                  _numberField(_freezeRepeat, 'الثالثة فأكثر (1–43200)', 220),
                ],
              ),
              const SizedBox(height: 12),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.tonalIcon(
                  onPressed: _busy || _settings == null ? null : _saveSettings,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('حفظ جميع إعدادات الحماية'),
                ),
              ),
              if (_settings == null)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text(
                    'هذه الخدمة لا تدعم إعدادات التشغيل الديناميكية.',
                    style: TextStyle(color: Colors.amber),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'رسائل الحدود والحظر للمستخدم',
          icon: Icons.sms_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'رسائل نصية عادية آمنة؛ المتغيرات المدعومة: {limit} و{minutes} و{kind} و{reason}. تُحفظ مع الحدود وتُطبّق فورًا.',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 12,
                ),
              ),
              const SizedBox(height: 12),
              _messageField(
                _blockMessages['frozen']!,
                'رسالة التجميد التلقائي — {minutes}',
              ),
              _messageField(
                _blockMessages['minute_limit']!,
                'رسالة حد الدقيقة — {limit}',
              ),
              _messageField(
                _blockMessages['hour_limit']!,
                'رسالة حد الساعة — {limit}',
              ),
              _messageField(
                _blockMessages['daily_limit']!,
                'رسالة الحد اليومي — {limit}',
              ),
              _messageField(
                _blockMessages['emergency']!,
                'رسالة الإيقاف العام',
              ),
              _messageField(
                _blockMessages['manual_block']!,
                'رسالة الحظر اليدوي — {kind} و{reason}',
              ),
              const SizedBox(height: 8),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.tonalIcon(
                  onPressed: _busy || _settings == null ? null : _saveSettings,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('حفظ جميع إعدادات الحماية'),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'النسخ الاحتياطي والتصدير',
          icon: Icons.cloud_download_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FilledButton.tonalIcon(
                onPressed: () => widget.api.download('/api/admin/export/xlsx'),
                icon: const Icon(Icons.table_chart),
                label: const Text('تصدير اكسل (xlsx) — كل البلاغات'),
              ),
              const SizedBox(height: 6),
              OutlinedButton.icon(
                onPressed: () => widget.api.download('/api/admin/export/txt'),
                icon: const Icon(Icons.description_outlined),
                label: const Text('تصدير نصي (txt) — كل البلاغات'),
              ),
              const Divider(height: 22),
              ListTile(
                leading: const Icon(Icons.storage_outlined),
                title: const Text('تنزيل نسخة قاعدة البيانات'),
                subtitle: const Text(
                  'ملف SQLite كامل — يُسجَّل في سجل التدقيق',
                  style: TextStyle(fontSize: 12),
                ),
                trailing: const Icon(Icons.download),
                onTap: () => widget.api.download('/api/admin/backup/download'),
              ),
              ListTile(
                leading: const Icon(Icons.backup_outlined),
                title: const Text('رفع نسخة فورية إلى مستودع الحفظ'),
                subtitle: const Text(
                  'يُرفع لقطة متسقة إلى مستودع النسخ السحابي الخاص',
                  style: TextStyle(fontSize: 12),
                ),
                trailing: const Icon(Icons.cloud_upload_outlined),
                onTap: _busy ? null : _backupNow,
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _numberField(
    TextEditingController controller,
    String label,
    double width,
  ) => SizedBox(
    width: width,
    child: TextField(
      controller: controller,
      keyboardType: TextInputType.number,
      decoration: InputDecoration(labelText: label, isDense: true),
    ),
  );

  Widget _messageField(TextEditingController controller, String label) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: TextField(
          controller: controller,
          minLines: 2,
          maxLines: 4,
          maxLength: 500,
          decoration: InputDecoration(
            labelText: label,
            alignLabelWithHint: true,
          ),
        ),
      );
}

// ==================================================================
// 5) الرسائل: تعميم لكل من بلغ + قوالب الردود الجاهزة
// ==================================================================
