import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class HealthPage extends StatefulWidget {
  const HealthPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<HealthPage> createState() => _HealthPageState();
}

class _HealthPageState extends State<HealthPage> {
  final Map<String, _HealthCheck> _checks = {};
  Map<String, dynamic>? _overview;
  Map<String, dynamic>? _health;
  bool _loading = true;
  DateTime? _checkedAt;

  @override
  void initState() {
    super.initState();
    _runChecks();
  }

  Future<_HealthCheck> _check(
    String label,
    Future<Object?> Function() action,
  ) async {
    final timer = Stopwatch()..start();
    try {
      await action();
      timer.stop();
      return _HealthCheck(
        label: label,
        ok: true,
        durationMs: timer.elapsedMilliseconds,
      );
    } catch (error) {
      timer.stop();
      return _HealthCheck(
        label: label,
        ok: false,
        durationMs: timer.elapsedMilliseconds,
        error: errMsg(error),
      );
    }
  }

  Future<void> _runChecks() async {
    setState(() => _loading = true);
    final results = await Future.wait([
      _check('واجهة الإدارة والاتصال بقاعدة البيانات', () async {
        final data = await widget.api.get('/api/admin/overview');
        _overview = data;
        return data;
      }),
      _check('خدمة الإحصاءات', () => widget.api.get('/api/admin/stats')),
      _check(
        'سجل التدقيق',
        () => widget.api.get('/api/admin/audit', {'limit': '1'}),
      ),
      _check('فحص الخادم والتخزين والنسخ', () async {
        _health = await widget.api.get('/api/admin/system/health');
        return _health;
      }),
    ]);
    if (!mounted) return;
    setState(() {
      for (final result in results) {
        _checks[result.label] = result;
      }
      _checkedAt = DateTime.now();
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final overview = _overview;
    final botReady = overview?['bot_ready'] == true;
    final emergency = overview?['emergency'] == true;
    final connected =
        _checks['واجهة الإدارة والاتصال بقاعدة البيانات']?.ok == true;
    final health = _health;
    final backupLastSuccess = DateTime.tryParse(
      health?['backup_last_success_at']?.toString() ?? '',
    )?.toLocal();

    return RefreshIndicator(
      onRefresh: _runChecks,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'فحوص فعلية عبر API؛ العناصر غير المتاحة لا تُعرض كأنها سليمة.',
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'إعادة الفحص',
                onPressed: _loading ? null : _runChecks,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          if (_checkedAt != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Text(
                'آخر فحص: ${TimeOfDay.fromDateTime(_checkedAt!).format(context)}',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 12,
                ),
              ),
            ),
          if (_loading && _checks.isEmpty)
            const LinearProgressIndicator(minHeight: 2),
          SectionCard(
            'حالة الاتصالات والخدمات',
            icon: Icons.monitor_heart_outlined,
            child: Column(
              children: [
                for (final check in _checks.values) _CheckTile(check: check),
                if (!_loading && _checks.isEmpty)
                  const Text('لم تكتمل الفحوص بعد'),
              ],
            ),
          ),
          const SizedBox(height: 10),
          SectionCard(
            'حالة التشغيل',
            icon: Icons.settings_suggest_outlined,
            child: Column(
              children: [
                _StateTile(
                  label: 'قاعدة البيانات',
                  value: health == null
                      ? 'غير معروف'
                      : health['database_ready'] == true
                      ? 'متاحة'
                      : 'غير متاحة',
                  ok: health?['database_ready'] == true,
                  unknown: health == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'نبض البوت (آخر 180 ثانية)',
                  value: health == null
                      ? 'غير معروف'
                      : health['bot_alive'] == true
                      ? 'حي'
                      : 'متوقف أو لا توجد نبضة حديثة',
                  ok: health?['bot_alive'] == true,
                  unknown: health == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'استقبال Telegram',
                  value: health == null
                      ? 'غير معروف'
                      : health['webhook_ready'] == true
                      ? 'Webhook مسجل'
                      : 'Polling احتياطي أو غير مسجل',
                  ok: health?['webhook_ready'] == true,
                  unknown: health == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'إعداد النسخ السحابي',
                  value: health == null
                      ? 'غير معروف'
                      : health['backup_configured'] == true
                      ? 'مهيأ'
                      : 'غير مهيأ',
                  ok: health?['backup_configured'] == true,
                  unknown: health == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'آخر نسخة احتياطية ناجحة',
                  value: backupLastSuccess == null
                      ? 'لا توجد'
                      : '${backupLastSuccess.year}-'
                            '${backupLastSuccess.month.toString().padLeft(2, '0')}-'
                            '${backupLastSuccess.day.toString().padLeft(2, '0')} '
                            '${backupLastSuccess.hour.toString().padLeft(2, '0')}:'
                            '${backupLastSuccess.minute.toString().padLeft(2, '0')}',
                  ok: backupLastSuccess != null,
                  unknown: health == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'بوت Telegram',
                  value: overview == null
                      ? 'غير معروف'
                      : botReady
                      ? 'جاهز'
                      : 'غير جاهز',
                  ok: botReady,
                  unknown: overview == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'استقبال البلاغات',
                  value: overview == null
                      ? 'غير معروف'
                      : emergency
                      ? 'متوقف طارئًا'
                      : 'يعمل',
                  ok: overview != null && !emergency,
                  unknown: overview == null,
                ),
                const Divider(height: 1),
                _StateTile(
                  label: 'رمز الإدارة',
                  value: connected ? 'مقبول لهذا الاتصال' : 'غير متحقق',
                  ok: connected,
                  unknown: !connected,
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          SectionCard(
            'حدود الفحص',
            icon: Icons.info_outline,
            child: Text(
              'تعرض هذه الصفحة ما تؤكده واجهة الإدارة فقط. حالة النسخ السحابي '
              'تُقرأ من فحص الخادم، ونبض البوت مستقل عن وصول رسائل المستخدمين.',
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurface,
                height: 1.6,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _HealthCheck {
  const _HealthCheck({
    required this.label,
    required this.ok,
    required this.durationMs,
    this.error,
  });

  final String label;
  final bool ok;
  final int durationMs;
  final String? error;
}

class _CheckTile extends StatelessWidget {
  const _CheckTile({required this.check});

  final _HealthCheck check;

  @override
  Widget build(BuildContext context) {
    final color = check.ok ? const Color(0xFF67D6A6) : const Color(0xFFE06C75);
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        check.ok ? Icons.check_circle_outline : Icons.error_outline,
        color: color,
      ),
      title: Text(check.label),
      subtitle: check.error == null
          ? Text('استجابة خلال ${check.durationMs} ms')
          : Text(
              check.error!,
              style: const TextStyle(color: Color(0xFFE06C75)),
            ),
      trailing: Text(
        check.ok ? 'سليم' : 'فشل',
        style: TextStyle(color: color, fontWeight: FontWeight.w700),
      ),
    );
  }
}

class _StateTile extends StatelessWidget {
  const _StateTile({
    required this.label,
    required this.value,
    required this.ok,
    required this.unknown,
  });

  final String label;
  final String value;
  final bool ok;
  final bool unknown;

  @override
  Widget build(BuildContext context) {
    final color = unknown
        ? Theme.of(context).colorScheme.onSurfaceVariant
        : ok
        ? const Color(0xFF67D6A6)
        : const Color(0xFFFFD166);
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(label),
      trailing: Text(
        value,
        style: TextStyle(color: color, fontWeight: FontWeight.w700),
      ),
    );
  }
}
