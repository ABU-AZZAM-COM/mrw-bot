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
  Map<String, dynamic>? _integrations;
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
      _check('سجل المستودع والفحوص الآلية', () async {
        _integrations = await widget.api.get('/api/admin/system/integrations');
        return _integrations;
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
    final integrations = _integrations;
    final github = integrations?['github'] as Map<String, dynamic>?;
    final services = (integrations?['services'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();

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
          const SizedBox(height: 10),
          SectionCard(
            'الخدمات المرتبطة بالمشروع',
            icon: Icons.hub_outlined,
            child: Column(
              children: [
                const Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: Text(
                    'زمن الاستجابة هنا مقاس من متصفحك إلى الخدمة، وليس قياسًا لسرعة الإنترنت العامة.',
                  ),
                ),
                const SizedBox(height: 8),
                for (final service in services)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      _statusIcon(service['status']?.toString() ?? 'unknown'),
                      color: _statusColor(
                        context,
                        service['status']?.toString() ?? 'unknown',
                      ),
                    ),
                    title: Text(service['name']?.toString() ?? 'خدمة'),
                    subtitle: Text(service['detail']?.toString() ?? ''),
                    trailing: Text(
                      _statusLabel(service['status']?.toString() ?? 'unknown'),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          SectionCard(
            'سجل المستودع والدمج',
            icon: Icons.account_tree_outlined,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  github == null
                      ? 'تعذّر تحميل نشاط المستودع.'
                      : 'المستودع: ${github['repository'] ?? 'غير معروف'} · '
                            'آخر تحقق: ${_formatTime(github['checked_at'])}',
                ),
                if (github?['error'] != null)
                  const Padding(
                    padding: EdgeInsets.only(top: 8),
                    child: Text(
                      'تعذّر التحقق من GitHub؛ لا يعني ذلك وحده وجود عطل في الخدمة.',
                    ),
                  ),
                const SizedBox(height: 8),
                const Text(
                  'آخر الفحوص الآلية',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
                for (final run
                    in (github?['recent_runs'] as List? ?? const [])
                        .whereType<Map<String, dynamic>>())
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      _statusIcon(run['status']?.toString() ?? 'unknown'),
                      color: _statusColor(
                        context,
                        run['status']?.toString() ?? 'unknown',
                      ),
                    ),
                    title: Text(
                      run['name']?.toString() ?? 'فحص آلي',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(
                      '${run['event'] ?? 'حدث غير معروف'} · '
                      '${_formatTime(run['updated_at'])}',
                    ),
                    trailing: Text(
                      run['conclusion']?.toString().isNotEmpty == true
                          ? run['conclusion'].toString()
                          : _statusLabel(run['status']?.toString() ?? ''),
                    ),
                  ),
                if ((github?['recent_runs'] as List? ?? const []).isEmpty)
                  const Text('لا تتوفر فحوص آلية حديثة.'),
                const Divider(),
                const Text(
                  'آخر الدمجات',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
                for (final merge
                    in (github?['recent_merges'] as List? ?? const [])
                        .whereType<Map<String, dynamic>>())
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.merge_type_outlined),
                    title: Text(
                      '#${merge['number']} · ${merge['title'] ?? 'دمج تغييرات'}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(_formatTime(merge['merged_at'])),
                  ),
                if ((github?['recent_merges'] as List? ?? const []).isEmpty)
                  const Text('لا تتوفر عمليات دمج حديثة.'),
              ],
            ),
          ),
        ],
      ),
    );
  }

  IconData _statusIcon(String status) => switch (status) {
    'healthy' => Icons.check_circle_outline,
    'running' => Icons.autorenew,
    'degraded' => Icons.error_outline,
    _ => Icons.help_outline,
  };

  Color _statusColor(BuildContext context, String status) => switch (status) {
    'healthy' => const Color(0xFF67D6A6),
    'running' => Theme.of(context).colorScheme.primary,
    'degraded' => const Color(0xFFFFD166),
    _ => Theme.of(context).colorScheme.onSurfaceVariant,
  };

  String _statusLabel(String status) => switch (status) {
    'healthy' => 'سليم',
    'running' => 'قيد التشغيل',
    'degraded' => 'متدهور',
    _ => 'تعذّر التحقق',
  };

  String _formatTime(Object? value) {
    final parsed = DateTime.tryParse(value?.toString() ?? '')?.toLocal();
    if (parsed == null) return 'وقت غير متاح';
    final date =
        '${parsed.year}-${parsed.month.toString().padLeft(2, '0')}-'
        '${parsed.day.toString().padLeft(2, '0')}';
    final time =
        '${parsed.hour.toString().padLeft(2, '0')}:'
        '${parsed.minute.toString().padLeft(2, '0')}';
    return '$date $time';
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
