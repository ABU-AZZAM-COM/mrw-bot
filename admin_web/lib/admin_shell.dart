import 'dart:async';
import 'dart:html' as html;

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
  String _role = '';
  String _roleLabel = 'لوحة الإدارة';
  int _unreadCount = 0;
  int _eventRevision = 0;
  int _notificationFetchSerial = 0;
  List<Map<String, dynamic>> _notifications = [];
  List<Map<String, dynamic>> _heartbeatNotifications = [];
  Map<String, dynamic> _notificationStats = const {};
  int? _pendingReportId;
  int? _pendingBlockedUserId;
  int? _pendingUserId;
  StreamSubscription<Map<String, dynamic>>? _eventsSubscription;
  Timer? _reconnectTimer;

  @override
  void initState() {
    super.initState();
    _loadRole();
    _connectEvents();
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _eventsSubscription?.cancel();
    super.dispose();
  }

  Future<void> _refreshNotifications() async {
    final requestSerial = ++_notificationFetchSerial;
    try {
      final results = await Future.wait([
        widget.api.get('/api/admin/notifications', {
          'unread': 'true',
          'limit': '20',
        }),
        widget.api.get('/api/admin/notifications', {
          'limit': '20',
          'kind': 'regular',
        }),
        widget.api.get('/api/admin/notifications', {
          'limit': '20',
          'kind': 'heartbeat',
        }),
        widget.api.get('/api/admin/notifications/stats'),
      ]);
      if (!mounted || requestSerial != _notificationFetchSerial) return;
      setState(() {
        _unreadCount = (results[0]['unread_count'] as num?)?.toInt() ?? 0;
        _notifications = (results[1]['items'] as List<dynamic>? ?? [])
            .whereType<Map<String, dynamic>>()
            .toList();
        _heartbeatNotifications = (results[2]['items'] as List<dynamic>? ?? [])
            .whereType<Map<String, dynamic>>()
            .toList();
        _notificationStats = results[3];
      });
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  void _connectEvents() {
    _eventsSubscription = widget.api.watchEvents().listen(
      _onEvent,
      onError: (_) => _scheduleReconnect(),
      onDone: _scheduleReconnect,
    );
    unawaited(_refreshNotifications());
  }

  void _scheduleReconnect() {
    _eventsSubscription = null;
    if (!mounted || _reconnectTimer?.isActive == true) return;
    _reconnectTimer = Timer(const Duration(seconds: 3), () {
      if (mounted) _connectEvents();
    });
  }

  void _onEvent(Map<String, dynamic> event) {
    if (!mounted) return;
    setState(() => _eventRevision++);
    final type = event['type']?.toString() ?? '';
    final prefs = widget.preferences;
    final muted = type == 'heartbeat_fail' && _heartbeatMuted;
    if (!muted &&
        prefs['soundEnabled'] == true &&
        prefs['soundUnlocked'] == true) {
      final sound = prefs['sound_$type'] as String?;
      if (sound != null) _playSound(sound);
    }
    if (!muted) _showNotificationBubble(event);
    unawaited(_refreshNotifications());
  }

  bool get _heartbeatMuted {
    final until = (widget.preferences['heartbeatMuteUntil'] as num?)?.toInt();
    return until == -1 ||
        (until != null && until > DateTime.now().millisecondsSinceEpoch);
  }

  void _setHeartbeatMute(int hours) {
    final until = hours == 0
        ? 0
        : hours < 0
        ? -1
        : DateTime.now().add(Duration(hours: hours)).millisecondsSinceEpoch;
    widget.onPreferencesChanged({
      ...widget.preferences,
      'heartbeatMuteUntil': until,
    });
  }

  void _showNotificationBubble(Map<String, dynamic> event) {
    final type = event['type']?.toString() ?? '';
    final payload = event['payload'] as Map<String, dynamic>? ?? const {};
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          behavior: SnackBarBehavior.floating,
          duration: const Duration(seconds: 6),
          content: Row(
            children: [
              Icon(_notificationIcon(type), color: Colors.white),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _notificationTitle(type),
                      style: const TextStyle(fontWeight: FontWeight.bold),
                    ),
                    Text(
                      _notificationDetails(payload),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      _formatNotificationTime(event['created_at']?.toString()),
                      style: const TextStyle(fontSize: 11),
                    ),
                  ],
                ),
              ),
            ],
          ),
          action: SnackBarAction(
            label: 'فتح',
            onPressed: () => unawaited(_openBubbleNotification(event)),
          ),
        ),
      );
  }

  Future<void> _cleanupNotifications() async {
    final confirmed = await confirmDialog(
      context,
      'تنظيف الإشعارات القديمة',
      'سيُحذف المقروء الأقدم من 7 أيام، وغير المقروء الأقدم من 30 يومًا. '
          'تُحفظ تنبيهات الطوارئ وفشل النبض 90 يومًا على الأقل.',
      okLabel: 'تنظيف',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    try {
      final result = await widget.api.post(
        '/api/admin/notifications/cleanup',
        {},
      );
      if (!mounted) return;
      toast(context, 'اكتمل التنظيف؛ حُذف ${result['deleted'] ?? 0} إشعار');
      await _refreshNotifications();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _openBubbleNotification(
    Map<String, dynamic> notification,
  ) async {
    final id = (notification['id'] as num?)?.toInt();
    try {
      if (id != null) {
        await widget.api.post('/api/admin/notifications/$id/read', {});
      }
      await _refreshNotifications();
      if (mounted) _navigateNotification(notification);
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  void _playSound(String sound) {
    if (!const {'report', 'alert', 'message'}.contains(sound)) return;
    final audio = html.AudioElement()
      ..src = 'assets/assets/sounds/$sound.mp3'
      ..preload = 'auto';
    unawaited(
      audio.play().catchError((Object error) {
        debugPrint('تعذر تشغيل صوت الإشعار: $error');
      }),
    );
  }

  void _enableSounds() {
    widget.onPreferencesChanged({...widget.preferences, 'soundUnlocked': true});
    _playSound('message');
  }

  Future<void> _showNotifications() async {
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => DefaultTabController(
        length: 2,
        child: StatefulBuilder(
          builder: (dialogContext, setDialogState) => AlertDialog(
            title: Row(
              children: [
                const Expanded(child: Text('الإشعارات')),
                if (_role == 'owner')
                  IconButton(
                    tooltip: 'تنظيف الإشعارات القديمة',
                    onPressed: _cleanupNotifications,
                    icon: const Icon(Icons.cleaning_services_outlined),
                  ),
                PopupMenuButton<int>(
                  tooltip: _heartbeatMuted
                      ? 'تنبيهات النبض مكتومة'
                      : 'خيارات كتم النبض',
                  icon: Icon(
                    _heartbeatMuted
                        ? Icons.notifications_off_outlined
                        : Icons.notifications_active_outlined,
                  ),
                  onSelected: (hours) {
                    _setHeartbeatMute(hours);
                    setDialogState(() {});
                  },
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 0, child: Text('إلغاء الكتم')),
                    PopupMenuItem(value: 1, child: Text('كتم ساعة')),
                    PopupMenuItem(value: 8, child: Text('كتم 8 ساعات')),
                    PopupMenuItem(value: 24, child: Text('كتم 24 ساعة')),
                    PopupMenuItem(value: -1, child: Text('كتم حتى إلغاءه')),
                  ],
                ),
                TextButton(
                  onPressed: _unreadCount == 0
                      ? null
                      : () async {
                          await widget.api.post(
                            '/api/admin/notifications/read-all',
                            {},
                          );
                          await _refreshNotifications();
                          if (mounted) setDialogState(() {});
                        },
                  child: const Text('تعليم الكل كمقروء'),
                ),
              ],
            ),
            content: SizedBox(
              width: 440,
              height: 460,
              child: Column(
                children: [
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: Text(
                      'الإجمالي ${_notificationStats['total'] ?? 0} · '
                      'غير المقروء ${_notificationStats['unread'] ?? 0} · '
                      'أقدم إشعار ${_formatNotificationTime(_notificationStats['oldest_created_at']?.toString())}',
                      style: Theme.of(dialogContext).textTheme.bodySmall,
                    ),
                  ),
                  const SizedBox(height: 8),
                  TabBar(
                    tabs: [
                      Tab(text: 'العامة (${_notifications.length})'),
                      Tab(
                        text: 'نبضات البوت (${_heartbeatNotifications.length})',
                      ),
                    ],
                  ),
                  Expanded(
                    child: TabBarView(
                      children: [
                        _notificationList(_notifications, dialogContext),
                        _notificationList(
                          _heartbeatNotifications,
                          dialogContext,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(),
                child: const Text('إغلاق'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _notificationList(
    List<Map<String, dynamic>> items,
    BuildContext dialogContext,
  ) {
    if (items.isEmpty) return const Center(child: Text('لا توجد إشعارات'));
    return ListView.separated(
      itemCount: items.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final notification = items[index];
        final payload =
            notification['payload'] as Map<String, dynamic>? ?? const {};
        final unread = notification['is_read'] != true;
        final id = (notification['id'] as num).toInt();
        return ListTile(
          leading: Icon(
            _notificationIcon(notification['type']?.toString() ?? ''),
            color: unread ? Theme.of(context).colorScheme.primary : null,
          ),
          title: Text(
            _notificationTitle(notification['type']?.toString() ?? ''),
          ),
          subtitle: Text(
            '${_notificationDetails(payload)}\n'
            '${_formatNotificationTime(notification['created_at']?.toString())}',
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
          ),
          isThreeLine: true,
          trailing: unread ? const Icon(Icons.circle, size: 9) : null,
          onTap: () => _openNotification(notification, dialogContext, id),
        );
      },
    );
  }

  String _formatNotificationTime(String? raw) {
    final parsed = DateTime.tryParse(raw ?? '')?.toLocal();
    if (parsed == null) return raw ?? 'وقت غير معروف';
    final y = parsed.year.toString().padLeft(4, '0');
    final m = parsed.month.toString().padLeft(2, '0');
    final d = parsed.day.toString().padLeft(2, '0');
    final h = parsed.hour.toString().padLeft(2, '0');
    final min = parsed.minute.toString().padLeft(2, '0');
    return '$y-$m-$d $h:$min';
  }

  Future<void> _openNotification(
    Map<String, dynamic> notification,
    BuildContext dialogContext,
    int id,
  ) async {
    try {
      if (notification['is_read'] != true) {
        await widget.api.post('/api/admin/notifications/$id/read', {});
      }
      await _refreshNotifications();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
      return;
    }
    if (!mounted || !dialogContext.mounted) return;
    Navigator.of(dialogContext).pop();
    _navigateNotification(notification);
  }

  void _navigateNotification(Map<String, dynamic> notification) {
    final payload =
        notification['payload'] as Map<String, dynamic>? ?? const {};
    final type = notification['type']?.toString() ?? '';
    setState(() {
      switch (type) {
        case 'report_new':
        case 'report_urgent':
          _pendingReportId = (payload['report_id'] as num?)?.toInt();
          _selectedIndex = 1;
        case 'user_blocked':
          _pendingBlockedUserId = (payload['user_id'] as num?)?.toInt();
          _selectedIndex = 7;
        case 'user_message':
          _pendingUserId = (payload['user_id'] as num?)?.toInt();
          _selectedIndex = 2;
        case 'emergency':
        case 'heartbeat_fail':
          _selectedIndex = type == 'heartbeat_fail' ? 9 : 3;
        default:
          break;
      }
    });
  }

  static IconData _notificationIcon(String type) => switch (type) {
    'report_new' || 'report_urgent' => Icons.assignment_outlined,
    'user_blocked' => Icons.block_outlined,
    'user_message' => Icons.chat_bubble_outline,
    'emergency' || 'heartbeat_fail' => Icons.warning_amber_outlined,
    _ => Icons.notifications_outlined,
  };

  static String _notificationTitle(String type) => switch (type) {
    'report_new' => 'بلاغ جديد',
    'report_urgent' => 'بلاغ عاجل',
    'user_blocked' => 'حظر مستخدم',
    'user_message' => 'رسالة مستخدم',
    'emergency' => 'تفعيل الإيقاف الطارئ',
    'heartbeat_fail' => 'انقطاع نبضة البوت',
    _ => 'إشعار',
  };

  static String _notificationDetails(Map<String, dynamic> payload) =>
      (payload['seq'] ??
              payload['full_name'] ??
              payload['preview'] ??
              payload['reason'] ??
              payload['message'] ??
              payload['user_id'] ??
              '')
          .toString();

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
      setState(() {
        _role = session['role']?.toString() ?? '';
        _roleLabel = roles[_role] ?? 'لوحة الإدارة';
      });
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
    OverviewPage(
      key: ValueKey('overview-$_eventRevision'),
      api: widget.api,
      onNavigate: _select,
    ),
    ReportsPage(
      key: ValueKey('reports-$_eventRevision-${_pendingReportId ?? 0}'),
      api: widget.api,
      focusReportId: _pendingReportId,
      onFocusHandled: () {
        if (mounted) setState(() => _pendingReportId = null);
      },
    ),
    UsersPage(
      key: ValueKey('users-$_eventRevision-${_pendingUserId ?? 0}'),
      api: widget.api,
      initialUserId: _pendingUserId,
      onFocusHandled: () {
        if (mounted) setState(() => _pendingUserId = null);
      },
    ),
    OperationsPage(api: widget.api),
    MessagingPage(api: widget.api),
    TypesPage(api: widget.api),
    AuditPage(api: widget.api),
    BlockedPage(
      key: ValueKey('blocked-$_eventRevision-${_pendingBlockedUserId ?? 0}'),
      api: widget.api,
      initialUserId: _pendingBlockedUserId,
      onFocusHandled: () {
        if (mounted) setState(() => _pendingBlockedUserId = null);
      },
    ),
    AnalyticsPage(key: ValueKey('analytics-$_eventRevision'), api: widget.api),
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
      soundEnabled: widget.preferences['soundEnabled'] != false,
      soundUnlocked: widget.preferences['soundUnlocked'] == true,
      heartbeatMuteUntil: (widget.preferences['heartbeatMuteUntil'] as num?)
          ?.toInt(),
      onHeartbeatMuteHours: _setHeartbeatMute,
      soundSounds: {
        for (final type in const [
          'report_new',
          'report_urgent',
          'user_blocked',
          'user_message',
          'emergency',
          'heartbeat_fail',
        ])
          type:
              widget.preferences['sound_$type'] as String? ??
              (type == 'report_new'
                  ? 'report'
                  : type == 'user_message'
                  ? 'message'
                  : 'alert'),
      },
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
          if (widget.preferences['soundUnlocked'] != true) ...[
            if (compact)
              IconButton(
                tooltip: 'تفعيل الأصوات',
                onPressed: _enableSounds,
                icon: const Icon(Icons.volume_up_outlined),
              )
            else
              TextButton.icon(
                onPressed: _enableSounds,
                icon: const Icon(Icons.volume_up_outlined),
                label: const Text('تفعيل الأصوات'),
              ),
          ],
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Center(
              child: Badge(
                isLabelVisible: _unreadCount > 0,
                label: Text(_unreadCount > 99 ? '99+' : '$_unreadCount'),
                child: IconButton(
                  tooltip: 'الإشعارات',
                  onPressed: _showNotifications,
                  icon: const Icon(Icons.notifications_outlined),
                ),
              ),
            ),
          ),
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
