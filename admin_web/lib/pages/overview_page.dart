import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

const Map<String, (Color, IconData)> kStatusStyles = {
  'جديد': (Color(0xFFFFD166), Icons.fiber_new_outlined),
  'قيد المراجعة': (Color(0xFF8AB4FF), Icons.search),
  'معتمد': (Color(0xFF3FA66B), Icons.verified_outlined),
  'مرفوض': (Color(0xFFE06C75), Icons.cancel_outlined),
};
const List<String> kStatusOrder = ['جديد', 'قيد المراجعة', 'معتمد', 'مرفوض'];

String fmt(String? iso, {int chars = 16}) {
  if (iso == null) return '-';
  return iso.length > chars ? iso.substring(0, chars) : iso;
}

// ==================================================================
// 1) النظرة العامة
// ==================================================================
class OverviewPage extends StatefulWidget {
  const OverviewPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage> {
  Map<String, dynamic>? _ov;
  Map<String, dynamic>? _st;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load();
    // تحديث لحظي كل 7 ثوانٍ — يتوقف تلقائياً عند نزع الصفحة.
    Timer.periodic(const Duration(seconds: 7), (t) {
      if (!mounted) {
        t.cancel();
        return;
      }
      _load(silent: true);
    });
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() => _err = null);
    try {
      final ov = await widget.api.get('/api/admin/overview');
      Map<String, dynamic>? st;
      try {
        st = await widget.api.get('/api/admin/stats');
      } catch (_) {
        st = null; // الإحصاءات ليست حاكمة للنظرة العامة
      }
      if (!mounted) return;
      setState(() {
        _ov = ov;
        _st = st;
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = silent ? (_err ?? errMsg(e)) : errMsg(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final ov = _ov;
    if (_err != null && ov == null) {
      return ErrorView(_err!, () => _load());
    }
    if (ov == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final counts =
        (ov['counts'] ?? <String, dynamic>{}) as Map<String, dynamic>;
    final st = _st;
    final bool emergency = ov['emergency'] == true;
    return RefreshIndicator(
      onRefresh: () => _load(),
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              StateBadge(
                ov['bot_ready'] == true
                    ? '🤖 البوت جاهز'
                    : '🤖 البوت غير جاهز!',
                color: ov['bot_ready'] == true
                    ? const Color(0xFF3FA66B)
                    : const Color(0xFFE06C75),
              ),
              const SizedBox(width: 8),
              StateBadge(
                emergency ? '🛑 إيقاف طارئ مفعّل' : '🟢 الاستقبال شغّال',
                color: emergency
                    ? const Color(0xFFE06C75)
                    : const Color(0xFF3FA66B),
              ),
              const SizedBox(width: 8),
              Text(
                'تحديث: ${TimeOfDay.now().format(context)}',
                style: const TextStyle(color: Colors.white38, fontSize: 12),
              ),
            ],
          ),
          const SizedBox(height: 12),
          LayoutBuilder(
            builder: (context, constraints) {
              final columns = constraints.maxWidth >= 1080
                  ? 4
                  : constraints.maxWidth >= 720
                  ? 3
                  : constraints.maxWidth >= 420
                  ? 2
                  : 1;
              return GridView.count(
                crossAxisCount: columns,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                childAspectRatio: columns == 1 ? 3.3 : 2.6,
                mainAxisSpacing: 8,
                crossAxisSpacing: 8,
                children: [
                  StatCard(
                    title: 'جديد',
                    value: '${counts['جديد'] ?? 0}',
                    color: const Color(0xFFFFD166),
                    icon: Icons.fiber_new_outlined,
                  ),
                  StatCard(
                    title: 'قيد المراجعة',
                    value: '${counts['قيد المراجعة'] ?? 0}',
                    color: const Color(0xFF8AB4FF),
                    icon: Icons.search,
                  ),
                  StatCard(
                    title: 'معتمد',
                    value: '${counts['معتمد'] ?? 0}',
                    color: const Color(0xFF67D6A6),
                    icon: Icons.verified_outlined,
                  ),
                  StatCard(
                    title: 'مرفوض',
                    value: '${counts['مرفوض'] ?? 0}',
                    color: const Color(0xFFE06C75),
                    icon: Icons.cancel_outlined,
                  ),
                  StatCard(
                    title: 'الإجمالي',
                    value: '${counts['total'] ?? 0}',
                    color: const Color(0xFF9AB8AA),
                    icon: Icons.inventory_2_outlined,
                  ),
                  StatCard(
                    title: 'آخر 24 ساعة',
                    value: '${counts['last24'] ?? 0}',
                    color: const Color(0xFFC4A2FF),
                    icon: Icons.schedule,
                  ),
                  StatCard(
                    title: 'متوسط الأسبوع/يوم',
                    value: '${_st?['week_avg'] ?? '-'}',
                    color: const Color(0xFF8BD4AE),
                    icon: Icons.insights,
                  ),
                  StatCard(
                    title: 'حالة الاستقبال',
                    value: emergency ? 'متوقف' : 'يعمل',
                    color: emergency
                        ? const Color(0xFFE06C75)
                        : const Color(0xFF67D6A6),
                    icon: Icons.emergency_outlined,
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 10),
          if (st != null) ...[
            SectionCard(
              'حركة البلاغات — آخر 7 أيام',
              icon: Icons.stacked_bar_chart,
              child: WeekChart(
                items: (st['daily7'] ?? <dynamic>[])
                    .cast<Map<String, dynamic>>(),
              ),
            ),
            const SizedBox(height: 10),
            SectionCard(
              'توزيع أنواع المخالفات',
              icon: Icons.category,
              child: ((st['by_type'] ?? []) as List).isEmpty
                  ? const Text('لا توجد بيانات بعد')
                  : Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final row in st['by_type'])
                          Chip(label: Text('${row['vtype']} (${row['n']})')),
                      ],
                    ),
            ),
            const SizedBox(height: 10),
            SectionCard(
              'أكثر المتبلّغين نشاطاً',
              icon: Icons.leaderboard_outlined,
              child: ((st['top_users'] ?? []) as List).isEmpty
                  ? const Text('لا توجد بيانات بعد')
                  : Column(
                      children: [
                        for (final row in st['top_users'])
                          ListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            leading: const Icon(Icons.person_outline),
                            title: Text('${row['full_name'] ?? '-'}'),
                            subtitle: SelectableText(
                              'ID ${row['user_id']}',
                              textDirection: TextDirection.ltr,
                            ),
                            trailing: Text(
                              '${row['n']} بلاغ',
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                      ],
                    ),
            ),
          ],
        ],
      ),
    );
  }
}

/// رسم أعمدة بسيط مبني يدوياً — بلا مكتبات خارجية.
class WeekChart extends StatelessWidget {
  const WeekChart({super.key, required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final days = List<String>.generate(7, (i) {
      final d = now.subtract(Duration(days: 6 - i));
      return '${d.year.toString().padLeft(4, '0')}-'
          '${d.month.toString().padLeft(2, '0')}-'
          '${d.day.toString().padLeft(2, '0')}';
    });
    final byDay = <String, int>{
      for (final e in items) (e['day'] ?? '') as String: (e['n'] ?? 0) as int,
    };
    int maxN = 1;
    for (final d in days) {
      maxN = (byDay[d] ?? 0) > maxN ? byDay[d] ?? 0 : maxN;
    }
    const wdNames = ['إثن', 'ثلا', 'أرب', 'خمي', 'جمع', 'سبت', 'أحد'];
    return SizedBox(
      height: 160,
      child: Row(
        children: [
          for (int i = 0; i < days.length; i++)
            Expanded(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  Text(
                    '${byDay[days[i]] ?? 0}',
                    style: TextStyle(
                      fontSize: 12,
                      color: (byDay[days[i]] ?? 0) > 0
                          ? Colors.white70
                          : Colors.white24,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Flexible(
                    child: Align(
                      alignment: Alignment.bottomCenter,
                      child: Container(
                        width: 26,
                        height: ((byDay[days[i]] ?? 0) / maxN) * 92 + 8,
                        decoration: BoxDecoration(
                          color: const Color(
                            0xFF3FA66B,
                          ).withOpacity((byDay[days[i]] ?? 0) > 0 ? 1 : 0.22),
                          borderRadius: BorderRadius.circular(6),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    wdNames[now.subtract(Duration(days: 6 - i)).weekday - 1],
                    style: const TextStyle(fontSize: 11, color: Colors.white38),
                  ),
                  Text(
                    days[i].substring(5),
                    style: const TextStyle(fontSize: 10, color: Colors.white24),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

// ==================================================================
// 2) البلاغات
// ==================================================================
