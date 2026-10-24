import 'package:flutter/material.dart';

import '../api.dart';
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

  List<Map<String, dynamic>> _rows(dynamic value) {
    if (value is! List) return [];
    return value
        .whereType<Map>()
        .map((row) => Map<String, dynamic>.from(row))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
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
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'قراءة مباشرة من واجهة الإدارة',
                  style: TextStyle(color: Colors.white54),
                ),
              ),
              IconButton(
                tooltip: 'تصدير جميع البلاغات',
                onPressed: () => widget.api.download('/api/admin/export/xlsx'),
                icon: const Icon(Icons.download_outlined),
              ),
              IconButton(
                tooltip: 'تحديث التحليلات',
                onPressed: _load,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          const SizedBox(height: 8),
          LayoutBuilder(
            builder: (context, constraints) {
              final columns = constraints.maxWidth >= 900
                  ? 4
                  : constraints.maxWidth >= 560
                  ? 2
                  : 1;
              return GridView.count(
                crossAxisCount: columns,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                childAspectRatio: columns == 1 ? 3.3 : 2.25,
                mainAxisSpacing: 8,
                crossAxisSpacing: 8,
                children: [
                  StatCard(
                    title: 'إجمالي البلاغات',
                    value: '${counts['total'] ?? 0}',
                    color: const Color(0xFF8BD4AE),
                    icon: Icons.inventory_2_outlined,
                  ),
                  StatCard(
                    title: 'آخر 24 ساعة',
                    value: '${counts['last24'] ?? 0}',
                    color: const Color(0xFFC4A2FF),
                    icon: Icons.schedule,
                  ),
                  StatCard(
                    title: 'متوسط البلاغات اليومي',
                    value: '${stats['week_avg'] ?? 0}',
                    color: const Color(0xFF8AB4FF),
                    icon: Icons.trending_up,
                  ),
                  StatCard(
                    title: 'أنواع مستخدمة',
                    value:
                        '${byType.where((row) => (row['n'] as num? ?? 0) > 0).length}',
                    color: const Color(0xFFFFD166),
                    icon: Icons.category_outlined,
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 10),
          SectionCard(
            'حركة البلاغات اليومية — آخر 7 أيام',
            icon: Icons.show_chart,
            child: WeekChart(items: _rows(stats['daily7'])),
          ),
          const SizedBox(height: 10),
          LayoutBuilder(
            builder: (context, constraints) {
              final split = constraints.maxWidth >= 760;
              final typeCard = SectionCard(
                'التصنيفات الأكثر ورودًا',
                icon: Icons.donut_large_outlined,
                child: byType.isEmpty
                    ? const Text('لا توجد بيانات كافية بعد')
                    : Column(
                        children: [
                          for (final row in byType.take(8))
                            _RankRow(
                              label: '${row['vtype'] ?? 'غير مصنف'}',
                              value: (row['n'] as num?)?.toInt() ?? 0,
                              maximum:
                                  (byType.first['n'] as num?)?.toInt() ?? 1,
                              color: const Color(0xFF67D6A6),
                            ),
                        ],
                      ),
              );
              final userCard = SectionCard(
                'أكثر المبلّغين نشاطًا',
                icon: Icons.leaderboard_outlined,
                child: topUsers.isEmpty
                    ? const Text('لا توجد بيانات كافية بعد')
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
                  children: [typeCard, const SizedBox(height: 10), userCard],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: typeCard),
                  const SizedBox(width: 10),
                  Expanded(child: userCard),
                ],
              );
            },
          ),
        ],
      ),
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
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(child: Text(label)),
              Text('$value', style: const TextStyle(color: Colors.white70)),
            ],
          ),
          const SizedBox(height: 6),
          LinearProgressIndicator(
            value: progress,
            minHeight: 6,
            color: color,
            backgroundColor: const Color(0xFF25382D),
            borderRadius: BorderRadius.circular(8),
          ),
        ],
      ),
    );
  }
}
