import 'package:flutter/material.dart';

import 'api.dart';
import 'app_credits.dart';
import 'app_theme.dart';
import 'pages.dart';
import 'pages2.dart';
import 'theme_menu.dart';
import 'widgets.dart';
import 'web_api.dart';

class MainShell extends StatefulWidget {
  const MainShell({
    super.key,
    required this.api,
    required this.onLogout,
    required this.preferences,
    required this.onPreferencesChanged,
  });

  final AdminApi api;
  final VoidCallback onLogout;
  final Map<String, dynamic> preferences;
  final ValueChanged<Map<String, dynamic>> onPreferencesChanged;

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  int _selectedIndex = 0;
  String _roleLabel = 'لوحة الإدارة';

  @override
  void initState() {
    super.initState();
    _loadRole();
  }

  Future<void> _loadRole() async {
    try {
      final session = await widget.api.get('/api/admin/session');
      if (!mounted) return;
      const roles = {
        'viewer': 'مشاهدة',
        'reviewer': 'مراجع',
        'operator': 'مشغّل',
        'owner': 'مالك',
      };
      setState(() => _roleLabel = roles[session['role']] ?? 'لوحة الإدارة');
    } on ApiException catch (error) {
      if (error.status == 404 && mounted) {
        setState(() => _roleLabel = 'لوحة الإدارة');
        return;
      }
      if (mounted) toast(context, errMsg(error), error: true);
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  static const _destinations = <(String, IconData)>[
    ('نظرة عامة', Icons.dashboard_outlined),
    ('البلاغات', Icons.report_outlined),
    ('المتبلّغون', Icons.people_outline),
    ('التشغيل', Icons.emergency_outlined),
    ('الرسائل', Icons.campaign_outlined),
    ('التصنيفات', Icons.category_outlined),
    ('سجل التدقيق', Icons.history_outlined),
    ('المُقيَّدون', Icons.block_outlined),
    ('التحليلات', Icons.query_stats_outlined),
    ('سلامة النظام', Icons.monitor_heart_outlined),
    ('التفضيلات', Icons.palette_outlined),
  ];

  List<Widget> get _pages => [
    OverviewPage(api: widget.api),
    ReportsPage(api: widget.api),
    UsersPage(api: widget.api),
    OperationsPage(api: widget.api),
    MessagingPage(api: widget.api),
    TypesPage(api: widget.api),
    AuditPage(api: widget.api),
    BlockedPage(api: widget.api),
    AnalyticsPage(api: widget.api),
    HealthPage(api: widget.api),
    PreferencesPage(
      mode: ThemeMode.values.firstWhere(
        (value) => value.name == widget.preferences['mode'],
        orElse: () => ThemeMode.dark,
      ),
      textScale: (widget.preferences['scale'] as num?)?.toDouble() ?? 1,
      fontFamily: widget.preferences['font'] as String? ?? 'NotoSansArabic',
      compact: widget.preferences['compact'] == true,
      seed: Color(
        (widget.preferences['seed'] as num?)?.toInt() ??
            AdminTheme.accent.toARGB32(),
      ),
      onChanged: widget.onPreferencesChanged,
    ),
  ];

  void _select(int index) {
    setState(() => _selectedIndex = index);
    if (Navigator.of(context).canPop()) Navigator.of(context).pop();
  }

  Widget _drawer() => Drawer(
    child: SafeArea(
      child: NavigationDrawer(
        selectedIndex: _selectedIndex,
        onDestinationSelected: _select,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 14),
            child: Row(
              children: [
                const CircleAvatar(
                  backgroundColor: Color(0xFF183B2B),
                  child: Icon(Icons.shield_outlined, color: Color(0xFF67D6A6)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'مركز إدارة البلاغات',
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                      const Text(
                        'منظومة المخالفات العسكرية',
                        style: TextStyle(fontSize: 11),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 20, vertical: 8),
            child: Divider(),
          ),
          for (var i = 0; i < _destinations.length; i++)
            NavigationDrawerDestination(
              icon: Icon(_destinations[i].$2),
              selectedIcon: Icon(_destinations[i].$2),
              label: Text(_destinations[i].$1),
            ),
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final compact = width < 880;
    final extendedRail = width >= 1220;
    final pages = _pages;

    return Scaffold(
      drawer: compact ? _drawer() : null,
      appBar: AppBar(
        titleSpacing: 20,
        title: Row(
          children: [
            Text(
              _destinations[_selectedIndex].$1,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            if (!compact) ...[
              const SizedBox(width: 14),
              Container(
                width: 1,
                height: 22,
                color: Theme.of(context).dividerColor,
              ),
              const SizedBox(width: 14),
              const Text('مركز التحكم الموحد', style: TextStyle(fontSize: 12)),
            ],
          ],
        ),
        actions: [
          if (!compact)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Center(
                child: StateBadge(_roleLabel, color: Color(0xFF67D6A6)),
              ),
            ),
          const ThemeMenu(),
          IconButton(
            tooltip: 'الحقوق والإصدار',
            onPressed: () => showCredits(context),
            icon: const Icon(Icons.info_outline),
          ),
          IconButton(
            tooltip: 'تسجيل الخروج',
            onPressed: () {
              Api.clearToken();
              widget.onLogout();
            },
            icon: const Icon(Icons.logout),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Row(
        children: [
          if (!compact) ...[
            NavigationRail(
              extended: extendedRail,
              selectedIndex: _selectedIndex,
              onDestinationSelected: _select,
              labelType: extendedRail ? null : NavigationRailLabelType.all,
              groupAlignment: -1,
              leading: Padding(
                padding: const EdgeInsets.fromLTRB(8, 8, 8, 18),
                child: Icon(
                  Icons.shield_outlined,
                  color: Theme.of(context).colorScheme.primary,
                  size: 28,
                ),
              ),
              destinations: [
                for (final destination in _destinations)
                  NavigationRailDestination(
                    icon: Icon(destination.$2),
                    selectedIcon: Icon(destination.$2),
                    label: Text(destination.$1),
                  ),
              ],
            ),
            const VerticalDivider(width: 1),
          ],
          Expanded(
            child: Padding(
              padding: EdgeInsets.all(compact ? 8 : 16),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(18),
                child: ColoredBox(
                  color: Theme.of(context).colorScheme.surface,
                  child: pages[_selectedIndex],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
