import 'package:flutter/material.dart';

import '../api.dart';
import '../design_tokens.dart';
import '../widgets.dart';
import 'overview_page.dart' show WeekChart;

class AnalyticsPage extends StatefulWidget {
  const AnalyticsPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<AnalyticsPage> createState() => _AnalyticsPageState();
}

class _AnalyticsPageState extends State<AnalyticsPage> {
  Map<String, dynamic>? _overview;
  Map<String, dynamic>? _stats;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        widget.api.get('/api/admin/overview'),
        widget.api.get('/api/admin/stats'),
      ]);
      if (!mounted) return;
      setState(() {
        _overview = results[0];
        _stats = results[1];
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = errMsg(error);
        _loading = false;
      });
    }
  }

  Future<void> _downloadExport() async {
    try {
      await widget.api.download('/api/admin/export/xlsx');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  List<Map<String, dynamic>> _rows(dynamic value) {
    if (value is! List) return [];
    return value
        .whereType<Map>()
        .map((row) => Map<String, dynamic>.from(row))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) return ErrorView(_error!, _load);

    final counts = Map<String, dynamic>.from(
      (_overview?['counts'] as Map?) ?? const {},
    );
    final stats = _stats ?? const <String, dynamic>{};
    final byType = _rows(
      stats['by_type'],
    )..sort((a, b) => ((b['n'] as num?) ?? 0).compareTo((a['n'] as num?) ?? 0));
    final topUsers = _rows(stats['top_users']);

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AdminTokens.pageInset,
          AdminTokens.s4,
          AdminTokens.pageInset,
          AdminTokens.s8,
        ),
        children: [
          _Header(
            onExport: _downloadExport,
            onRefresh: _loading ? null : _load,
          ),
          const SizedBox(height: AdminTokens.sectionGap),
          if (_loading)
            const _AnalyticsSkeleton()
          else ...[
            _MetricGrid(counts: counts, stats: stats, byType: byType),
            const SizedBox(height: AdminTokens.sectionGap),
            SectionCard(
              'حركة البلاغات اليومية — آخر 7 أيام',
              icon: Icons.show_chart,
              child: WeekChart(items: _rows(stats['daily7'])),
            ),
            const SizedBox(height: AdminTokens.sectionGap),
            LayoutBuilder(
              builder: (context, constraints) {
                final split = constraints.maxWidth >= 760;
                final typeCard = SectionCard(
                  'التصنيفات الأكثر ورودًا',
                  icon: Icons.donut_large_outlined,
                  child: byType.isEmpty
                      ? const EmptyView(
                          'لا توجد بيانات كافية بعد',
                          icon: Icons.donut_large_outlined,
                        )
                      : Column(
                          children: [
                            for (final row in byType.take(8))
                              _RankRow(
                                label: '${row['vtype'] ?? 'غير مصنف'}',
                                value: (row['n'] as num?)?.toInt() ?? 0,
                                maximum:
                                    (byType.first['n'] as num?)?.toInt() ?? 1,
                                color: AdminTokens.statusApproved,
                              ),
                          ],
                        ),
                );
                final userCard = SectionCard(
                  'أكثر المبلّغين نشاطًا',
                  icon: Icons.leaderboard_outlined,
                  child: topUsers.isEmpty
                      ? const EmptyView(
                          'لا توجد بيانات كافية بعد',
                          icon: Icons.leaderboard_outlined,
                        )
                      : Column(
                          children: [
                            for (var i = 0; i < topUsers.take(7).length; i++)
                              ListTile(
                                dense: true,
                                contentPadding: EdgeInsets.zero,
                                leading: CircleAvatar(
                                  radius: 15,
                                  child: Text('${i + 1}'),
                                ),
                                title: Text(
                                  '${topUsers[i]['full_name'] ?? 'مستخدم'}',
                                ),
                                subtitle: Text('ID ${topUsers[i]['user_id']}'),
                                trailing: Text(
                                  '${topUsers[i]['n'] ?? 0} بلاغ',
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                          ],
                        ),
                );
                if (!split) {
                  return Column(
                    children: [
                      typeCard,
                      const SizedBox(height: AdminTokens.sectionGap),
                      userCard,
                    ],
                  );
                }
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(child: typeCard),
                    const SizedBox(width: AdminTokens.sectionGap),
                    Expanded(child: userCard),
                  ],
                );
              },
            ),
          ],
        ],
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.onExport, required this.onRefresh});

  final VoidCallback onExport;
  final VoidCallback? onRefresh;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'التحليلات',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: AdminTokens.s1),
              Text(
                'قراءة مباشرة من واجهة الإدارة',
                style: TextStyle(color: muted, fontSize: 12),
              ),
            ],
          ),
        ),
        IconButton(
          tooltip: 'تصدير جميع البلاغات',
          onPressed: onExport,
          icon: const Icon(Icons.download_outlined),
        ),
        IconButton(
          tooltip: 'تحديث التحليلات',
          onPressed: onRefresh,
          icon: const Icon(Icons.refresh),
        ),
      ],
    );
  }
}

class _MetricGrid extends StatelessWidget {
  const _MetricGrid({
    required this.counts,
    required this.stats,
    required this.byType,
  });

  final Map<String, dynamic> counts;
  final Map<String, dynamic> stats;
  final List<Map<String, dynamic>> byType;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 900
            ? 4
            : constraints.maxWidth >= 560
            ? 2
            : 1;
        final gap = AdminTokens.s3;
        final width = (constraints.maxWidth - gap * (columns - 1)) / columns;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [
            SizedBox(
              width: width,
              child: StatCard(
                title: 'إجمالي البلاغات',
                value: '${counts['total'] ?? 0}',
                color: AdminTokens.statusApproved,
                icon: Icons.inventory_2_outlined,
              ),
            ),
            SizedBox(
              width: width,
              child: StatCard(
                title: 'آخر 24 ساعة',
                value: '${counts['last24'] ?? 0}',
                color: AdminTokens.statusReview,
                icon: Icons.schedule,
              ),
            ),
            SizedBox(
              width: width,
              child: StatCard(
                title: 'متوسط البلاغات اليومي',
                value: '${stats['week_avg'] ?? 0}',
                color: AdminTokens.statusNew,
                icon: Icons.trending_up,
              ),
            ),
            SizedBox(
              width: width,
              child: StatCard(
                title: 'أنواع مستخدمة',
                value:
                    '${byType.where((row) => (row['n'] as num? ?? 0) > 0).length}',
                color: AdminTokens.statusRejected,
                icon: Icons.category_outlined,
              ),
            ),
          ],
        );
      },
    );
  }
}

/// هيكل تحميل يحاكي تخطيط الصفحة قبل وصول البيانات، بلا حركة متكررة.
class _AnalyticsSkeleton extends StatelessWidget {
  const _AnalyticsSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        LayoutBuilder(
          builder: (context, constraints) {
            final columns = constraints.maxWidth >= 900
                ? 4
                : constraints.maxWidth >= 560
                ? 2
                : 1;
            final gap = AdminTokens.s3;
            final width =
                (constraints.maxWidth - gap * (columns - 1)) / columns;
            return Wrap(
              spacing: gap,
              runSpacing: gap,
              children: [
                for (var i = 0; i < 4; i++)
                  SizedBox(
                    width: width,
                    child: const Card(
                      child: Padding(
                        padding: EdgeInsets.all(AdminTokens.cardInset),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SkeletonBox(width: 110, height: 14),
                            SizedBox(height: AdminTokens.s3),
                            SkeletonBox(width: 64, height: 26),
                          ],
                        ),
                      ),
                    ),
                  ),
              ],
            );
          },
        ),
        const SizedBox(height: AdminTokens.sectionGap),
        const Card(
          child: Padding(
            padding: EdgeInsets.all(AdminTokens.cardInset),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SkeletonBox(width: 180, height: 16),
                SizedBox(height: AdminTokens.s4),
                SkeletonBox(width: double.infinity, height: 160),
              ],
            ),
          ),
        ),
        const SizedBox(height: AdminTokens.sectionGap),
        const Card(
          child: Padding(
            padding: EdgeInsets.all(AdminTokens.cardInset),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SkeletonBox(width: 150, height: 16),
                SizedBox(height: AdminTokens.s4),
                SkeletonBox(width: double.infinity, height: 13),
                SizedBox(height: AdminTokens.s2),
                SkeletonBox(width: 220, height: 13),
                SizedBox(height: AdminTokens.s2),
                SkeletonBox(width: 180, height: 13),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _RankRow extends StatelessWidget {
  const _RankRow({
    required this.label,
    required this.value,
    required this.maximum,
    required this.color,
  });

  final String label;
  final int value;
  final int maximum;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final progress = maximum <= 0 ? 0.0 : (value / maximum).clamp(0.0, 1.0);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AdminTokens.s2),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(child: Text(label)),
              Text(
                '$value',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurface,
                ),
              ),
            ],
          ),
          const SizedBox(height: AdminTokens.s1),
          LinearProgressIndicator(
            value: progress,
            minHeight: AdminTokens.s1 + 2,
            color: color,
            backgroundColor: color.withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(AdminTokens.chipRadius),
          ),
        ],
      ),
    );
  }
}
