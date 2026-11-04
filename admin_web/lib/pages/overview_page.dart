import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../design_tokens.dart';
import '../widgets.dart';

const Map<String, (Color, IconData)> kStatusStyles = {
  'جديد': (Color(0xFFFFC857), Icons.fiber_new_outlined),
  'قيد المراجعة': (Color(0xFF79B8FF), Icons.manage_search_outlined),
  'معتمد': (Color(0xFF56D6A0), Icons.verified_outlined),
  'مرفوض': (Color(0xFFFF7C83), Icons.cancel_outlined),
};
const List<String> kStatusOrder = ['جديد', 'قيد المراجعة', 'معتمد', 'مرفوض'];

/// تنسيق طابع زمني ISO للعرض: تاريخ ووقت مقروءان، معزولان باتجاه LTR.
///
/// العزل مطلوب لأن الواجهة عربية RTL: بدون `U+202A … U+202C` تنقلب أجزاء
/// التاريخ بصريًا (يظهر `04|08,10-11-2026` بدل `2026-11-04 08:10`).
String fmt(String? iso, {int chars = 16}) {
  if (iso == null || iso.isEmpty) return '-';
  final parsed = DateTime.tryParse(iso);
  if (parsed != null) {
    String two(int value) => value.toString().padLeft(2, '0');
    final text =
        '${parsed.year}-${two(parsed.month)}-${two(parsed.day)} '
        '${two(parsed.hour)}:${two(parsed.minute)}';
    return '\u202A$text\u202C';
  }
  final raw = iso.length > chars ? iso.substring(0, chars) : iso;
  return '\u202A$raw\u202C';
}

class OverviewPage extends StatefulWidget {
  const OverviewPage({super.key, required this.api, this.onNavigate});

  final AdminApi api;
  final ValueChanged<int>? onNavigate;

  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage> {
  Map<String, dynamic>? _overview;
  Map<String, dynamic>? _stats;
  List<Map<String, dynamic>> _recentReports = [];
  List<Map<String, dynamic>> _urgentReports = [];
  List<Map<String, dynamic>> _overdueReports = [];
  List<Map<String, dynamic>> _unassignedReports = [];
  String? _error;
  final List<String> _partialErrors = [];
  DateTime? _updatedAt;
  bool _refreshing = false;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) _load(silent: true);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<(Map<String, dynamic>?, Object?)> _optionalGet(
    String path, [
    Map<String, String>? query,
  ]) async {
    try {
      return (await widget.api.get(path, query), null);
    } catch (error) {
      return (null, error);
    }
  }

  List<Map<String, dynamic>> _items(Map<String, dynamic>? payload) {
    final value = payload?['items'];
    if (value is! List) return [];
    return value
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
  }

  Future<void> _load({bool silent = false}) async {
    if (_refreshing) return;
    _refreshing = true;
    if (mounted) {
      setState(() {
        if (!silent) _error = null;
      });
    }

    try {
      final overview = await widget.api.get('/api/admin/overview');
      final results = await Future.wait([
        _optionalGet('/api/admin/stats'),
        _optionalGet('/api/admin/reports', {'size': '5'}),
        _optionalGet('/api/admin/reports', {'priority': 'urgent', 'size': '3'}),
        _optionalGet('/api/admin/reports', {'overdue': 'true', 'size': '3'}),
        _optionalGet('/api/admin/reports', {'unassigned': 'true', 'size': '3'}),
      ]);
      if (!mounted) return;

      final errors = <String>[
        for (var index = 0; index < results.length; index++)
          if (results[index].$2 != null)
            '${_sectionNames[index]}: ${errMsg(results[index].$2!)}',
      ];
      setState(() {
        _overview = overview;
        _stats = results[0].$1;
        _recentReports = _items(results[1].$1);
        _urgentReports = _items(results[2].$1);
        _overdueReports = _items(results[3].$1);
        _unassignedReports = _items(results[4].$1);
        _partialErrors
          ..clear()
          ..addAll(errors);
        _error = null;
        _updatedAt = DateTime.now();
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = errMsg(error);
      });
    } finally {
      _refreshing = false;
      if (mounted) setState(() {});
    }
  }

  static const _sectionNames = [
    'التحليلات',
    'أحدث البلاغات',
    'العاجلة',
    'المتأخرة',
    'غير المسندة',
  ];

  @override
  Widget build(BuildContext context) {
    if (_error != null && _overview == null) {
      return ErrorView(_error!, () => _load());
    }
    if (_overview == null) {
      return const Center(child: CircularProgressIndicator());
    }

    final overview = _overview!;
    final counts = _map(overview['counts']);
    final workflow = _map(overview['workflow']);
    final users = _map(overview['users']);
    final emergency = overview['emergency'] == true;
    final botReady = overview['bot_ready'] == true;

    return RefreshIndicator(
      onRefresh: () => _load(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AdminTokens.pageInset,
          18,
          AdminTokens.pageInset,
          28,
        ),
        children: [
          _DashboardHero(
            botReady: botReady,
            emergency: emergency,
            updatedAt: _updatedAt,
            refreshing: _refreshing,
            onRefresh: () => _load(),
            onOperations: () => widget.onNavigate?.call(3),
          ),
          if (_partialErrors.isNotEmpty || _error != null) ...[
            const SizedBox(height: 12),
            _PartialErrorBanner(
              errors: [
                ..._partialErrors,
                if (_error != null) 'تعذر تحديث البيانات: $_error',
              ],
            ),
          ],
          const SizedBox(height: AdminTokens.sectionGap),
          LayoutBuilder(
            builder: (context, constraints) {
              final columns = constraints.maxWidth >= 1120
                  ? 4
                  : constraints.maxWidth >= 700
                  ? 2
                  : 1;
              final gap = 12.0;
              final cardWidth =
                  (constraints.maxWidth - gap * (columns - 1)) / columns;
              return Wrap(
                spacing: gap,
                runSpacing: gap,
                children: [
                  _MetricCard(
                    width: cardWidth,
                    title: 'إجمالي البلاغات',
                    value: _value(counts['total']),
                    subtitle: '${_value(counts['last24'])} خلال آخر 24 ساعة',
                    icon: Icons.inventory_2_outlined,
                    color: const Color(0xFF79B8FF),
                    onTap: () => widget.onNavigate?.call(1),
                  ),
                  _MetricCard(
                    width: cardWidth,
                    title: 'تحتاج مراجعة',
                    value: _value(counts['جديد']),
                    subtitle: '${_value(counts['قيد المراجعة'])} قيد المعالجة',
                    icon: Icons.pending_actions_outlined,
                    color: const Color(0xFFFFC857),
                    onTap: () => widget.onNavigate?.call(1),
                  ),
                  _MetricCard(
                    width: cardWidth,
                    title: 'بلاغات عاجلة',
                    value: _value(workflow['urgent']),
                    subtitle: 'أولوية قصوى',
                    icon: Icons.bolt_rounded,
                    color: const Color(0xFFFF7C83),
                    onTap: () => widget.onNavigate?.call(1),
                  ),
                  _MetricCard(
                    width: cardWidth,
                    title: 'مستخدمو البوت',
                    value: _value(users['total']),
                    subtitle: '${_value(users['blocked'])} مقيّد نشط',
                    icon: Icons.groups_2_outlined,
                    color: const Color(0xFF56D6A0),
                    onTap: () => widget.onNavigate?.call(2),
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 16),
          LayoutBuilder(
            builder: (context, constraints) {
              final wide = constraints.maxWidth >= 900;
              final chart = _Panel(
                title: 'حركة البلاغات',
                subtitle: 'اتجاه البلاغات اليومية خلال آخر سبعة أيام',
                icon: Icons.show_chart_rounded,
                trailing: _SmallAction(
                  label: 'التحليلات',
                  onTap: () => widget.onNavigate?.call(10),
                ),
                child: _stats == null
                    ? const _InlineEmpty(
                        'بيانات الرسم غير متاحة حاليًا',
                        icon: Icons.query_stats_outlined,
                      )
                    : WeekChart(items: _rows(_stats?['daily7'])),
              );
              final distribution = _Panel(
                title: 'توزيع الحالات',
                subtitle: 'نظرة فورية على مسار معالجة البلاغات',
                icon: Icons.donut_small_outlined,
                child: _StatusDistribution(
                  counts: counts,
                  onTap: () => widget.onNavigate?.call(1),
                ),
              );
              if (!wide) {
                return Column(
                  children: [chart, const SizedBox(height: 14), distribution],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(flex: 3, child: chart),
                  const SizedBox(width: 14),
                  Expanded(flex: 2, child: distribution),
                ],
              );
            },
          ),
          const SizedBox(height: 14),
          _Panel(
            title: 'تحتاج انتباهك',
            subtitle: 'أولويات المتابعة التي قد تتطلب إجراءً',
            icon: Icons.notifications_active_outlined,
            trailing: _SmallAction(
              label: 'فتح البلاغات',
              onTap: () => widget.onNavigate?.call(1),
            ),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final columns = constraints.maxWidth >= 820 ? 3 : 1;
                final gap = 10.0;
                final width =
                    (constraints.maxWidth - gap * (columns - 1)) / columns;
                return Wrap(
                  spacing: gap,
                  runSpacing: gap,
                  children: [
                    _AttentionCard(
                      width: width,
                      title: 'متابعة متأخرة',
                      count: _value(workflow['overdue']),
                      hint: 'تجاوزت موعد المتابعة',
                      icon: Icons.event_busy_outlined,
                      color: const Color(0xFFFF9C66),
                      items: _overdueReports,
                      onTap: () => widget.onNavigate?.call(1),
                    ),
                    _AttentionCard(
                      width: width,
                      title: 'غير مسندة',
                      count: _value(workflow['unassigned']),
                      hint: 'بانتظار تعيين مسؤول',
                      icon: Icons.person_search_outlined,
                      color: const Color(0xFF79B8FF),
                      items: _unassignedReports,
                      onTap: () => widget.onNavigate?.call(1),
                    ),
                    _AttentionCard(
                      width: width,
                      title: 'أولوية عاجلة',
                      count: _value(workflow['urgent']),
                      hint: 'تستحق مراجعة سريعة',
                      icon: Icons.priority_high_rounded,
                      color: const Color(0xFFFF7C83),
                      items: _urgentReports,
                      onTap: () => widget.onNavigate?.call(1),
                    ),
                  ],
                );
              },
            ),
          ),
          const SizedBox(height: 14),
          _RecentReportsPanel(
            items: _recentReports,
            onOpen: () => widget.onNavigate?.call(1),
          ),
          const SizedBox(height: 12),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: Text(
              'المستخدمون: ${_value(users['total'])}  •  '
              'المحظورون النشطون: ${_value(users['blocked'])}  •  '
              'المعتمد: ${_value(counts['معتمد'])}  •  '
              'المرفوض: ${_value(counts['مرفوض'])}',
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
                fontSize: 12,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Map<String, dynamic> _map(dynamic value) =>
      value is Map ? Map<String, dynamic>.from(value) : {};

  List<Map<String, dynamic>> _rows(dynamic value) {
    if (value is! List) return [];
    return value
        .whereType<Map>()
        .map((row) => Map<String, dynamic>.from(row))
        .toList();
  }

  String _value(dynamic value) => value?.toString() ?? '0';
}

class _DashboardHero extends StatelessWidget {
  const _DashboardHero({
    required this.botReady,
    required this.emergency,
    required this.updatedAt,
    required this.refreshing,
    required this.onRefresh,
    required this.onOperations,
  });

  final bool botReady;
  final bool emergency;
  final DateTime? updatedAt;
  final bool refreshing;
  final VoidCallback onRefresh;
  final VoidCallback onOperations;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final critical = emergency || !botReady;
    final criticalColor = critical ? scheme.error : scheme.primary;
    return Container(
      padding: const EdgeInsets.all(AdminTokens.s6),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: AlignmentDirectional.topStart,
          end: AlignmentDirectional.bottomEnd,
          colors: critical
              ? const [Color(0xFF3B2028), Color(0xFF1E1822)]
              : const [Color(0xFF12352D), Color(0xFF152535)],
        ),
        borderRadius: BorderRadius.circular(AdminTokens.heroRadius),
        border: Border.all(
          color: criticalColor.withValues(alpha: 0.32),
        ),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final compact = constraints.maxWidth < 620;
          final heading = Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: AdminTokens.iconMedium * 2 + AdminTokens.s2,
                    height: AdminTokens.iconMedium * 2 + AdminTokens.s2,
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.09),
                      borderRadius: BorderRadius.circular(AdminTokens.controlRadius),
                    ),
                    child: const Icon(
                      Icons.shield_moon_outlined,
                      color: Color(0xFF8EE6C0),
                    ),
                  ),
                  const SizedBox(width: AdminTokens.s4),
                  Expanded(
                    child: Text(
                      'مركز قيادة البلاغات',
                      style: theme.textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: Colors.white,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AdminTokens.s2),
              Text(
                'ملخص تشغيلي يساعدك على رؤية الأولويات واتخاذ الإجراء المناسب.',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: Colors.white.withValues(alpha: 0.72),
                ),
              ),
              const SizedBox(height: AdminTokens.s4),
              Wrap(
                spacing: AdminTokens.s3,
                runSpacing: AdminTokens.s3,
                children: [
                  _HealthPill(
                    label: botReady ? 'البوت متصل بالخدمة' : 'البوت غير جاهز',
                    color: botReady
                        ? const Color(0xFF56D6A0)
                        : const Color(0xFFFF7C83),
                    icon: botReady
                        ? Icons.check_circle_outline
                        : Icons.error_outline,
                  ),
                  _HealthPill(
                    label: emergency
                        ? 'الاستقبال متوقف'
                        : 'استقبال البلاغات يعمل',
                    color: emergency
                        ? const Color(0xFFFF7C83)
                        : const Color(0xFF8EE6C0),
                    icon: emergency
                        ? Icons.pause_circle_outline
                        : Icons.radio_button_checked,
                  ),
                ],
              ),
            ],
          );
          final actions = Wrap(
            spacing: AdminTokens.s3,
            runSpacing: AdminTokens.s3,
            children: [
              OutlinedButton.icon(
                onPressed: onOperations,
                icon: const Icon(Icons.tune_rounded, size: AdminTokens.iconSmall),
                label: const Text('حالة النظام'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: Colors.white,
                  side: BorderSide(color: Colors.white.withValues(alpha: 0.25)),
                ),
              ),
              FilledButton.tonalIcon(
                onPressed: refreshing ? null : onRefresh,
                icon: refreshing
                    ? const SizedBox(
                        width: AdminTokens.iconSmall,
                        height: AdminTokens.iconSmall,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh_rounded, size: AdminTokens.iconSmall),
                label: const Text('تحديث'),
              ),
            ],
          );
          if (compact) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                heading,
                const SizedBox(height: AdminTokens.s4),
                actions,
                if (updatedAt != null) ...[
                  const SizedBox(height: AdminTokens.s2),
                  _UpdatedText(time: updatedAt!),
                ],
              ],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(child: heading),
              const SizedBox(width: AdminTokens.s4),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  actions,
                  if (updatedAt != null) ...[
                    const SizedBox(height: AdminTokens.s2),
                    _UpdatedText(time: updatedAt!),
                  ],
                ],
              ),
            ],
          );
        },
      ),
    );
  }
}

class _UpdatedText extends StatelessWidget {
  const _UpdatedText({required this.time});

  final DateTime time;

  @override
  Widget build(BuildContext context) => Text(
    'آخر تحديث ${TimeOfDay.fromDateTime(time).format(context)}',
    style: TextStyle(color: Colors.white.withValues(alpha: 0.65), fontSize: 12),
  );
}

class _HealthPill extends StatelessWidget {
  const _HealthPill({
    required this.label,
    required this.color,
    required this.icon,
  });

  final String label;
  final Color color;
  final IconData icon;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: AdminTokens.s3, vertical: AdminTokens.s1),
    decoration: BoxDecoration(
      color: Colors.black.withValues(alpha: 0.14),
      borderRadius: BorderRadius.circular(AdminTokens.chipRadius),
      border: Border.all(color: color.withValues(alpha: 0.4)),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: AdminTokens.iconSmall, color: color),
        const SizedBox(width: AdminTokens.s1),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 130),
          child: Text(
            label,
            style: const TextStyle(color: Colors.white, fontSize: AdminTokens.iconSmall),
            overflow: TextOverflow.ellipsis,
            maxLines: 1,
          ),
        ),
      ],
    ),
  );
}

class _MetricCard extends StatelessWidget {
  const _MetricCard({
    required this.width,
    required this.title,
    required this.value,
    required this.subtitle,
    required this.icon,
    required this.color,
    required this.onTap,
  });

  final double width;
  final String title;
  final String value;
  final String subtitle;
  final IconData icon;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      width: width,
      child: Material(
        color: theme.cardColor,
        borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
        child: InkWell(
          borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
          onTap: onTap,
          child: Container(
            constraints: const BoxConstraints(minHeight: 142),
            padding: const EdgeInsets.all(AdminTokens.cardInset),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
              border: Border.all(color: theme.dividerColor),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        title,
                        style: TextStyle(
                          color: theme.colorScheme.onSurfaceVariant,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    Container(
                      width: AdminTokens.iconMedium * 2 - AdminTokens.s1,
                      height: AdminTokens.iconMedium * 2 - AdminTokens.s1,
                      decoration: BoxDecoration(
                        color: color.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(AdminTokens.controlRadius),
                      ),
                      child: Icon(icon, color: color, size: AdminTokens.iconMedium),
                    ),
                  ],
                ),
                const SizedBox(height: AdminTokens.s4),
                Text(
                  value,
                  style: theme.textTheme.headlineMedium?.copyWith(
                    fontWeight: FontWeight.w800,
                    height: 1.05,
                  ),
                ),
                const SizedBox(height: AdminTokens.s1),
                Text(
                  subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: theme.colorScheme.onSurfaceVariant,
                    fontSize: AdminTokens.iconSmall,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.child,
    this.trailing,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => SectionCard(
    title,
    subtitle: subtitle,
    icon: icon,
    trailing: trailing,
    child: child,
  );
}

class _SmallAction extends StatelessWidget {
  const _SmallAction({required this.label, required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => TextButton(
    onPressed: onTap,
    style: TextButton.styleFrom(
      visualDensity: VisualDensity.compact,
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 7),
    ),
    child: Text(label, style: const TextStyle(fontSize: 12)),
  );
}

class _StatusDistribution extends StatelessWidget {
  const _StatusDistribution({required this.counts, required this.onTap});

  final Map<String, dynamic> counts;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final total = (counts['total'] as num?)?.toDouble() ?? 0;
    return Column(
      children: [
        for (final status in kStatusOrder)
          Padding(
            padding: const EdgeInsets.only(bottom: AdminTokens.s3),
            child: InkWell(
              onTap: onTap,
              borderRadius: BorderRadius.circular(AdminTokens.controlRadius),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Builder(
                  builder: (context) {
                    final style = kStatusStyles[status]!;
                    final value = (counts[status] as num?)?.toDouble() ?? 0;
                    final ratio = total <= 0 ? 0.0 : value / total;
                    return Column(
                      children: [
                        Row(
                          children: [
                            Icon(style.$2, size: AdminTokens.iconSmall, color: style.$1),
                            const SizedBox(width: AdminTokens.s1),
                            Expanded(child: Text(status)),
                            Text(
                              '${value.toInt()}',
                              style: const TextStyle(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            const SizedBox(width: AdminTokens.s2),
                            SizedBox(
                              width: 38,
                              child: Text(
                                '${(ratio * 100).round()}%',
                                textAlign: TextAlign.end,
                                style: TextStyle(
                                  color: Theme.of(
                                    context,
                                  ).colorScheme.onSurfaceVariant,
                                  fontSize: AdminTokens.iconSmall,
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: AdminTokens.s2),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(AdminTokens.chipRadius),
                          child: LinearProgressIndicator(
                            value: ratio.clamp(0.0, 1.0).toDouble(),
                            minHeight: AdminTokens.s1,
                            color: style.$1,
                            backgroundColor: style.$1.withValues(alpha: 0.12),
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
            ),
          ),
        if (total == 0) const _InlineEmpty('لا توجد بلاغات مسجلة حتى الآن'),
      ],
    );
  }
}

class _AttentionCard extends StatelessWidget {
  const _AttentionCard({
    required this.width,
    required this.title,
    required this.count,
    required this.hint,
    required this.icon,
    required this.color,
    required this.items,
    required this.onTap,
  });

  final double width;
  final String title;
  final String count;
  final String hint;
  final IconData icon;
  final Color color;
  final List<Map<String, dynamic>> items;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      width: width,
      child: Material(
        color: color.withValues(alpha: 0.055),
        borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
          child: Container(
            constraints: const BoxConstraints(minHeight: 134),
            padding: const EdgeInsets.all(AdminTokens.cardInset),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
              border: Border.all(color: color.withValues(alpha: 0.22)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(icon, color: color, size: AdminTokens.iconMedium),
                    const SizedBox(width: AdminTokens.s1),
                    Expanded(
                      child: Text(
                        title,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                    ),
                    Text(
                      count,
                      style: TextStyle(
                        fontSize: 22,
                        fontWeight: FontWeight.w800,
                        color: color,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AdminTokens.s1),
                Text(
                  hint,
                  style: TextStyle(
                    color: theme.colorScheme.onSurfaceVariant,
                    fontSize: AdminTokens.iconSmall,
                  ),
                ),
                if (items.isNotEmpty) ...[
                  const SizedBox(height: AdminTokens.s3),
                  Text(
                    _reportLabel(items.first),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: AdminTokens.iconSmall),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _RecentReportsPanel extends StatelessWidget {
  const _RecentReportsPanel({required this.items, required this.onOpen});

  final List<Map<String, dynamic>> items;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) => _Panel(
    title: 'أحدث البلاغات',
    subtitle: 'آخر البلاغات الواردة إلى النظام',
    icon: Icons.receipt_long_outlined,
    trailing: _SmallAction(label: 'عرض الكل', onTap: onOpen),
    child: items.isEmpty
        ? const _InlineEmpty(
            'لا توجد بلاغات حديثة للعرض',
            icon: Icons.inbox_outlined,
          )
        : Column(
            children: [
              for (var index = 0; index < items.length; index++) ...[
                _RecentReportRow(item: items[index], onTap: onOpen),
                if (index != items.length - 1)
                  Divider(
                    height: 1,
                    color: Theme.of(
                      context,
                    ).dividerColor.withValues(alpha: 0.7),
                  ),
              ],
            ],
          ),
  );
}

class _RecentReportRow extends StatelessWidget {
  const _RecentReportRow({required this.item, required this.onTap});

  final Map<String, dynamic> item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final status = item['status']?.toString() ?? '-';
    final style = kStatusStyles[status];
    final createdAt = DateTime.tryParse(
      item['created_at']?.toString() ?? '',
    )?.toLocal();
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(AdminTokens.cardRadius),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AdminTokens.s3),
        child: Row(
          children: [
            Container(
              width: AdminTokens.iconMedium * 2 - AdminTokens.s1,
              height: AdminTokens.iconMedium * 2 - AdminTokens.s1,
              decoration: BoxDecoration(
                color: (style?.$1 ?? theme.colorScheme.primary).withValues(
                  alpha: 0.12,
                ),
                borderRadius: BorderRadius.circular(AdminTokens.controlRadius),
              ),
              child: Icon(
                style?.$2 ?? Icons.description_outlined,
                color: style?.$1 ?? theme.colorScheme.primary,
                size: AdminTokens.iconMedium,
              ),
            ),
            const SizedBox(width: AdminTokens.s3),
            Expanded(
              flex: 3,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _reportLabel(item),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: AdminTokens.s1),
                  Text(
                    '${item['seq'] ?? 'بلاغ ${item['id'] ?? '-'}'}'
                    '  •  ${createdAt == null ? '-' : TimeOfDay.fromDateTime(createdAt).format(context)}',
                    style: TextStyle(
                      color: theme.colorScheme.onSurfaceVariant,
                      fontSize: AdminTokens.iconSmall,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: AdminTokens.s2),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: AdminTokens.s3, vertical: AdminTokens.s1),
              decoration: BoxDecoration(
                color: (style?.$1 ?? theme.colorScheme.outline).withValues(
                  alpha: 0.12,
                ),
                borderRadius: BorderRadius.circular(AdminTokens.chipRadius),
              ),
              child: Text(
                status,
                style: TextStyle(
                  color: style?.$1 ?? theme.colorScheme.onSurfaceVariant,
                  fontSize: AdminTokens.iconSmall,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

String _reportLabel(Map<String, dynamic> item) {
  final name = item['full_name']?.toString().trim();
  final username = item['username']?.toString().trim();
  if (name != null && name.isNotEmpty) return name;
  if (username != null && username.isNotEmpty) return '@$username';
  return 'مستخدم غير معروف';
}

class _PartialErrorBanner extends StatelessWidget {
  const _PartialErrorBanner({required this.errors});

  final List<String> errors;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(AdminTokens.s3),
    decoration: BoxDecoration(
      color: AdminTokens.statusNew.withValues(alpha: 0.1),
      borderRadius: BorderRadius.circular(AdminTokens.controlRadius),
      border: Border.all(color: AdminTokens.statusNew.withValues(alpha: 0.4)),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.info_outline, color: AdminTokens.statusNew, size: AdminTokens.iconMedium),
        const SizedBox(width: AdminTokens.s2),
        Expanded(
          child: Text(
            'بعض الأقسام لم تُحمّل: ${errors.join('، ')}',
            style: const TextStyle(fontSize: AdminTokens.iconSmall),
          ),
        ),
      ],
    ),
  );
}

class _InlineEmpty extends StatelessWidget {
  const _InlineEmpty(this.message, {this.icon = Icons.hourglass_empty_rounded});

  final String message;
  final IconData icon;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: AdminTokens.s6),
    child: Center(
      child: Column(
        children: [
          Icon(
            icon,
            color: Theme.of(context).colorScheme.onSurfaceVariant,
            size: AdminTokens.iconMedium * 1.2,
          ),
          const SizedBox(height: AdminTokens.s2),
          Text(
            message,
            style: TextStyle(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
              fontSize: AdminTokens.iconSmall,
            ),
          ),
        ],
      ),
    ),
  );
}

class WeekChart extends StatelessWidget {
  const WeekChart({super.key, required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final now = DateTime.now();
    final days = List<DateTime>.generate(
      7,
      (index) => DateTime(now.year, now.month, now.day - (6 - index)),
    );
    String key(DateTime date) =>
        '${date.year.toString().padLeft(4, '0')}-'
        '${date.month.toString().padLeft(2, '0')}-'
        '${date.day.toString().padLeft(2, '0')}';
    final byDay = <String, int>{
      for (final row in items)
        if (row['day'] != null)
          row['day'].toString(): (row['n'] as num?)?.toInt() ?? 0,
    };
    final values = [for (final day in days) byDay[key(day)] ?? 0];
    final maximum = values.fold<int>(
      1,
      (max, value) => value > max ? value : max,
    );
    const weekdayNames = ['إثن', 'ثلا', 'أرب', 'خمي', 'جمع', 'سبت', 'أحد'];

    if (items.isEmpty || values.every((value) => value == 0)) {
      return const _InlineEmpty(
        'لا توجد حركة مسجلة خلال الأيام السبعة الماضية',
        icon: Icons.show_chart_rounded,
      );
    }

    return SizedBox(
      height: 190,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          for (var index = 0; index < days.length; index++)
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 5),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    Text(
                      '${values[index]}',
                      style: TextStyle(
                        color: values[index] > 0
                            ? Theme.of(context).colorScheme.onSurface
                            : muted,
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Expanded(
                      child: Align(
                        alignment: Alignment.bottomCenter,
                        child: Tooltip(
                          message: '${values[index]} بلاغ',
                          child: AnimatedContainer(
                            duration: const Duration(milliseconds: 350),
                            width: 30,
                            height: 104 * values[index] / maximum + 5,
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                                colors: values[index] > 0
                                    ? const [
                                        Color(0xFF79E0B0),
                                        Color(0xFF218C70),
                                      ]
                                    : [
                                        Theme.of(context)
                                            .colorScheme
                                            .onSurfaceVariant
                                            .withValues(alpha: 0.12),
                                        Theme.of(context)
                                            .colorScheme
                                            .onSurfaceVariant
                                            .withValues(alpha: 0.06),
                                      ],
                              ),
                              borderRadius: BorderRadius.circular(9),
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 9),
                    Text(
                      weekdayNames[days[index].weekday - 1],
                      style: TextStyle(color: muted, fontSize: 10),
                    ),
                    Text(
                      '${days[index].day}/${days[index].month}',
                      style: TextStyle(
                        color: muted.withValues(alpha: 0.72),
                        fontSize: 9,
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
