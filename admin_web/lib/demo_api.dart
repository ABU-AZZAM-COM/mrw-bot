import 'dart:typed_data';

import 'api.dart';

class DemoApi implements AdminApi {
  final List<String> _types = [
    'مخالفة الزي',
    'مخالفة التعليمات',
    'سلوك غير نظامي',
  ];
  final List<String> _templates = [
    'تم استلام البلاغ وسيتم التحقق منه.',
    'نرجو تزويدنا بمعلومات إضافية عند توفرها.',
  ];
  bool _emergency = false;
  final Map<String, dynamic> _settings = {
    'max_description_length': 1500,
    'max_photo_mb': 10,
    'max_reports_per_day': 10,
    'max_reports_per_minute': 2,
  };
  final Map<String, dynamic> _botProfile = {
    'name': 'بوت البلاغات',
    'short_description': 'استقبال ومتابعة البلاغات.',
    'description': 'قناة لاستقبال البلاغات ومتابعتها.',
    'welcome_text':
        'أهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته من هذه المحادثة.\nاختر الخدمة التي تحتاجها:',
    'username': 'demo_bot',
  };
  final List<Map<String, dynamic>> _audit = [];
  final List<Map<String, dynamic>> _osintSources = [];

  // ---- أزرار قائمة المستخدم (نسخة تجريبية داخلية) ----
  static const List<Map<String, String>> _botMenuActions = [
    {'id': 'report', 'label': '📝 تقديم بلاغ جديد'},
    {'id': 'status', 'label': '📊 متابعة بلاغاتي'},
    {'id': 'about', 'label': 'ℹ️ دليل الاستخدام'},
    {'id': 'contact', 'label': '📞 التواصل مع الإدارة'},
    {'id': 'privacy', 'label': '🔒 سياسة الخصوصية'},
    {'id': 'verify', 'label': '🪪 التسجيل كمراسل موثوق'},
    {'id': 'verify-status', 'label': '🔎 حالة توثيق الحساب'},
    {'id': 'menu', 'label': '🏠 القائمة الرئيسية'},
    {'id': 'home', 'label': '🏠 العودة للقائمة'},
    {'id': 'cancel', 'label': '❌ إلغاء'},
  ];
  static const Map<String, int> _botMenuLimits = {
    'max_rows': 8,
    'max_row_buttons': 3,
    'max_buttons': 16,
    'max_label': 40,
    'max_url': 512,
    'max_copy': 256,
  };
  static const Map<String, List<String>> _botMenuOptions = {
    'styles': ['primary', 'success', 'danger'],
    'types': ['callback', 'url', 'web_app', 'copy', 'inline'],
    'visibility': ['all', 'admins', 'non_admins'],
  };
  List<List<Map<String, dynamic>>>? _botMenu;
  bool get _botMenuCustomized => _botMenu != null;

  List<List<Map<String, dynamic>>> _defaultBotMenuRows() => [
    [
      {'label': '📝 تقديم بلاغ جديد', 'action': 'report'},
    ],
    [
      {'label': '🪪 التسجيل كمراسل موثوق', 'action': 'verify'},
    ],
    [
      {'label': '🔎 حالة توثيق الحساب', 'action': 'verify-status'},
    ],
    [
      {'label': '📊 متابعة بلاغاتي', 'action': 'status'},
    ],
    [
      {'label': 'ℹ️ دليل الاستخدام', 'action': 'about'},
      {'label': '📞 التواصل مع الإدارة', 'action': 'contact'},
    ],
    [
      {'label': '🔒 سياسة الخصوصية', 'action': 'privacy'},
    ],
  ];

  List<List<Map<String, dynamic>>> _botMenuRows() =>
      _botMenu ?? _defaultBotMenuRows();

  // ---- مركز التحكم (نسخة تجريبية) ----
  static const List<Map<String, dynamic>> _demoReplyIds = [
    {'id': 'cancel', 'label': 'لوحة الإلغاء (أثناء إدخال البلاغ)'},
    {'id': 'confirm', 'label': 'لوحة تأكيد الإرسال'},
    {'id': 'app', 'label': 'لوحة فتح تطبيق البلاغ'},
    {'id': 'verification_contact', 'label': 'لوحة توثيق المراسل'},
    {'id': 'contact', 'label': 'لوحة التواصل مع الإدارة'},
    {'id': 'location', 'label': 'لوحة طلب الموقع'},
  ];
  static Map<String, dynamic> _demoReplyDefaults() => {
    'cancel': {
      'rows': [
        [
          {'text': '❌ إلغاء', 'type': 'cancel'},
        ],
      ],
      'placeholder': '✏️ اكتب هنا… أو اختر زرًا',
      'persistent': false,
    },
    'confirm': {
      'rows': [
        [
          {'text': '✅ إرسال البلاغ', 'type': 'confirm'},
          {'text': '❌ إلغاء', 'type': 'cancel'},
        ],
      ],
      'placeholder': 'اختر زرًا 👇',
      'persistent': false,
    },
    'app': {
      'rows': [
        [
          {'text': '🚀 فتح تطبيق البلاغ', 'type': 'web_app'},
        ],
      ],
      'placeholder': '✏️ اكتب هنا… أو اضغط زر التطبيق 🚀',
      'persistent': true,
    },
    'verification_contact': {
      'rows': [
        [
          {'text': '📞 مشاركة رقم هاتفي', 'type': 'contact'},
        ],
        [
          {'text': '❌ إلغاء', 'type': 'cancel'},
        ],
      ],
      'placeholder': '📞 شارك رقمك أو اختر إلغاء',
      'persistent': false,
    },
    'contact': {
      'rows': [
        [
          {'text': '📞 مشاركة رقم هاتفي', 'type': 'contact'},
        ],
        [
          {'text': '⏭️ تخطي', 'type': 'skip'},
        ],
        [
          {'text': '↩️ رجوع', 'type': 'back'},
        ],
        [
          {'text': '❌ إلغاء', 'type': 'cancel'},
        ],
      ],
      'placeholder': 'اختر زرًا 👇',
      'persistent': false,
    },
    'location': {
      'rows': [
        [
          {'text': '📍 مشاركة الموقع', 'type': 'location'},
        ],
        [
          {'text': '⏭️ تخطي', 'type': 'skip'},
        ],
        [
          {'text': '↩️ رجوع', 'type': 'back'},
        ],
        [
          {'text': '❌ إلغاء', 'type': 'cancel'},
        ],
      ],
      'placeholder': 'اختر زرًا 👇',
      'persistent': false,
    },
  };
  Map<String, dynamic>? _demoReply;
  Map<String, dynamic>? _demoCommands;
  Map<String, dynamic>? _demoMenuButton;
  Map<String, dynamic>? _demoBotSettings;
  final Map<String, int> _demoAnalytics = {'report': 42, 'status': 17, 'about': 9};

  static Map<String, dynamic> _demoCommandsDefaults() => {
    'scopes': [
      {
        'scope': 'default',
        'chat_id': null,
        'language': '',
        'commands': [
          {'command': 'start', 'description': 'فتح الواجهة الرئيسية'},
        ],
      },
      {
        'scope': 'chat',
        'chat_id': 1557500001,
        'language': '',
        'commands': [
          {'command': 'start', 'description': 'فتح الواجهة الرئيسية'},
          {'command': 'admin', 'description': 'لوحة إدارة البلاغات'},
          {'command': 'stats', 'description': 'إحصائيات البلاغات'},
        ],
      },
    ],
  };

  static Map<String, dynamic> _demoSettingsDefaults() => {
    'mini_app_url': 'https://example.com/app',
    'default_admin_rights': {
      'is_anonymous': false,
      'can_manage_chat': true,
      'can_delete_messages': true,
      'can_manage_video_chats': true,
      'can_restrict_members': true,
      'can_promote_members': false,
      'can_change_info': true,
      'can_invite_users': true,
      'can_pin_messages': true,
      'can_manage_topics': true,
    },
    'default_admin_rights_customized': false,
  };
  int _publicationJobId = 0;
  Map<String, dynamic>? _publicationJob;
  final List<Map<String, dynamic>> _reports = [
    {
      'id': 101,
      'seq': 'DEMO-001',
      'user_id': 10001,
      'username': 'demo_reporter',
      'full_name': 'مستخدم تجريبي',
      'status': 'جديد',
      'violation_type': 'مخالفة الزي',
      'suggested_type': 'مخالفة الزي',
      'description': 'بيانات تدريبية توضيحية وليست بلاغًا حقيقيًا.',
      'post_link': '',
      'photo_file_id': '',
      'admin_note': '',
      'created_at': '2026-10-24T08:00:00',
    },
    {
      'id': 102,
      'seq': 'DEMO-002',
      'user_id': 10002,
      'username': 'sample_user',
      'full_name': 'مبلّغ تجريبي آخر',
      'status': 'قيد المراجعة',
      'violation_type': 'مخالفة التعليمات',
      'suggested_type': 'مخالفة التعليمات',
      'description': 'مثال تجريبي لعرض تفاصيل البلاغ وإجراءاته.',
      'post_link': '',
      'photo_file_id': '',
      'admin_note': '',
      'created_at': '2026-10-23T13:25:00',
    },
    {
      'id': 103,
      'seq': 'DEMO-003',
      'user_id': 10001,
      'username': 'demo_reporter',
      'full_name': 'مستخدم تجريبي',
      'status': 'معتمد',
      'violation_type': 'سلوك غير نظامي',
      'suggested_type': 'سلوك غير نظامي',
      'description': 'مثال معتمد للمعاينة فقط.',
      'post_link': '',
      'photo_file_id': '',
      'admin_note': 'ملاحظة تجريبية',
      'created_at': '2026-10-22T10:10:00',
    },
    {
      'id': 104,
      'seq': 'DEMO-004',
      'user_id': 10003,
      'username': 'training_user',
      'full_name': 'متبلّغ تحت التدريب',
      'status': 'مرفوض',
      'violation_type': 'مخالفة الزي',
      'suggested_type': 'مخالفة الزي',
      'description': 'مثال مرفوض لعرض حالات البلاغات.',
      'post_link': '',
      'photo_file_id': '',
      'admin_note': 'مثال تدريبي',
      'created_at': '2026-10-20T16:45:00',
    },
  ];
  final Map<int, Map<String, dynamic>> _users = {
    10001: {
      'user_id': 10001,
      'username': 'demo_reporter',
      'full_name': 'مستخدم تجريبي',
      'total_reports': 2,
      'approved_n': 1,
      'rejected_n': 0,
      'first_at': '2026-10-22T10:10:00',
      'last_at': '2026-10-24T08:00:00',
      'block': {'frozen_until': '', 'reason': ''},
    },
    10002: {
      'user_id': 10002,
      'username': 'sample_user',
      'full_name': 'مبلّغ تجريبي آخر',
      'total_reports': 1,
      'approved_n': 0,
      'rejected_n': 0,
      'first_at': '2026-10-23T13:25:00',
      'last_at': '2026-10-23T13:25:00',
      'block': {'frozen_until': '', 'reason': ''},
    },
    10003: {
      'user_id': 10003,
      'username': 'training_user',
      'full_name': 'متبلّغ تحت التدريب',
      'total_reports': 1,
      'approved_n': 0,
      'rejected_n': 1,
      'first_at': '2026-10-20T16:45:00',
      'last_at': '2026-10-20T16:45:00',
      'block': {
        'frozen_until': '9999-12-31T00:00:00',
        'reason': 'مثال حجب للتدريب',
      },
    },
  };

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async {
    if (path == '/api/admin/publications/summary') {
      return {
        'ok': true,
        'recipients': _users.length,
        'count_capped': false,
        'photo_max_bytes': 10 * 1024 * 1024,
        'other_media_max_bytes': 20 * 1024 * 1024,
      };
    }
    final broadcastStatus = RegExp(
      r'^/api/admin/broadcast/(\d+)$',
    ).firstMatch(path);
    if (broadcastStatus != null) {
      final id = int.parse(broadcastStatus.group(1)!);
      if (_publicationJob == null || id != _publicationJobId) {
        throw ApiException(404, 'not_found');
      }
      return {'ok': true, ..._publicationJob!};
    }
    if (path == '/api/admin/notifications') {
      return {'ok': true, 'items': <Map<String, dynamic>>[], 'unread_count': 0};
    }
    if (path == '/api/admin/session') {
      return {'ok': true, 'role': 'owner', 'admin_id': 0};
    }
    if (path == '/api/admin/bot-profile') {
      return {
        'ok': true,
        'items': {..._botProfile},
        'bot_ready': true,
        'can_edit': true,
      };
    }
    if (path == '/api/admin/conversations') {
      return {'ok': true, 'items': <Map<String, dynamic>>[]};
    }
    if (path == '/api/admin/overview') {
      final counts = <String, int>{
        'جديد': 0,
        'قيد المراجعة': 0,
        'معتمد': 0,
        'مرفوض': 0,
        'total': _reports.length,
        'last24': _reports.length,
      };
      for (final report in _reports) {
        final status = report['status'] as String;
        counts[status] = (counts[status] ?? 0) + 1;
      }
      return {
        'ok': true,
        'counts': counts,
        'emergency': _emergency,
        'bot_ready': false,
        'workflow': {
          'urgent': _reports.where((r) => r['priority'] == 'urgent').length,
          'overdue': 0,
          'unassigned': _reports
              .where(
                (r) =>
                    r['assigned_to'] == null &&
                    r['status'] != 'معتمد' &&
                    r['status'] != 'مرفوض',
              )
              .length,
        },
      };
    }
    if (path == '/api/admin/stats') {
      return {
        'ok': true,
        'week_avg': 0.6,
        'daily7': [
          {'day': '2026-10-20', 'n': 1},
          {'day': '2026-10-22', 'n': 1},
          {'day': '2026-10-23', 'n': 1},
          {'day': '2026-10-24', 'n': 1},
        ],
        'by_type': [
          for (final type in _types)
            {
              'vtype': type,
              'n': _reports.where((r) => r['violation_type'] == type).length,
            },
        ],
        'top_users': [
          {'user_id': 10001, 'full_name': 'مستخدم تجريبي', 'n': 2},
        ],
      };
    }
    if (path == '/api/admin/vtypes') {
      return {
        'ok': true,
        'items': [..._types],
      };
    }
    if (path == '/api/admin/bot-buttons') {
      return {
        'ok': true,
        'items': _botMenuRows(),
        'defaults': _defaultBotMenuRows(),
        'customized': _botMenuCustomized,
        'actions': _botMenuActions,
        'limits': _botMenuLimits,
        'options': _botMenuOptions,
      };
    }
    if (path == '/api/admin/bot-reply-keyboards') {
      final reply = _demoReply ?? _demoReplyDefaults();
      return {
        'ok': true,
        'items': reply,
        'defaults': _demoReplyDefaults(),
        'ids': _demoReplyIds,
        'customized': _demoReply != null,
        'bot_ready': true,
        'mini_app_url': (_demoBotSettings ?? _demoSettingsDefaults())['mini_app_url'],
        'types': [
          'text',
          'cancel',
          'confirm',
          'contact',
          'location',
          'skip',
          'back',
          'web_app',
        ],
        'styles': ['primary', 'success', 'danger'],
      };
    }
    if (path == '/api/admin/bot-commands') {
      return {
        'ok': true,
        'items': _demoCommands ?? _demoCommandsDefaults(),
        'defaults': _demoCommandsDefaults(),
        'customized': _demoCommands != null,
        'scopes': [
          {'id': 'default', 'label': 'الافتراضي (كل المحادثات)'},
          {'id': 'all_private_chats', 'label': 'كل المحادثات الخاصة'},
          {'id': 'all_group_chats', 'label': 'كل المجموعات'},
          {'id': 'all_chat_administrators', 'label': 'كل مشرفي المحادثات'},
          {'id': 'chat', 'label': 'محادثة محددة'},
          {'id': 'chat_administrators', 'label': 'مشرفو محادثة محددة'},
        ],
        'bot_ready': true,
        'limits': {'max_commands': 100, 'max_scopes': 50},
      };
    }
    if (path == '/api/admin/bot-menu-button') {
      return {
        'ok': true,
        'items':
            _demoMenuButton ??
            {'type': 'default', 'text': 'فتح التطبيق', 'url': ''},
        'defaults': {'type': 'default', 'text': 'فتح التطبيق', 'url': ''},
        'customized': _demoMenuButton != null,
        'bot_ready': true,
        'mini_app_url': (_demoBotSettings ?? _demoSettingsDefaults())['mini_app_url'],
      };
    }
    if (path == '/api/admin/bot-settings') {
      final values = _demoBotSettings ?? _demoSettingsDefaults();
      return {
        'ok': true,
        'items': values,
        'defaults': _demoSettingsDefaults(),
        'customized': _demoBotSettings != null,
        'bot_ready': true,
        'bot': {
          'username': 'demo_bot',
          'supports_inline_queries': false,
          'can_join_groups': true,
          'can_read_all_group_messages': false,
        },
        'rights_keys': [
          for (final key
              in (_demoSettingsDefaults()['default_admin_rights'] as Map).keys)
            '$key',
        ],
      };
    }
    if (path == '/api/admin/bot-analytics') {
      return {
        'ok': true,
        'items': [
          for (final entry in _demoAnalytics.entries)
            {
              'action': entry.key,
              'label': switch (entry.key) {
                'report' => 'تقديم بلاغ',
                'status' => 'متابعة بلاغاتي',
                'about' => 'دليل الاستخدام',
                _ => entry.key,
              },
              'presses': entry.value,
              'updated_at': DateTime.now().toUtc().toIso8601String(),
            },
        ],
        'total': _demoAnalytics.values.fold<int>(0, (sum, value) => sum + value),
        'tracked_actions': _demoAnalytics.length,
      };
    }
    if (path == '/api/admin/bot-links') {
      final payload = query?['payload'] ?? '';
      final base = 'https://t.me/demo_bot';
      return {
        'ok': true,
        'payload': payload,
        'username': 'demo_bot',
        'links': {
          'start': payload.isEmpty ? base : '$base?start=$payload',
          'startgroup': payload.isEmpty
              ? base
              : '$base?startgroup=$payload',
          'startapp': payload.isEmpty ? base : '$base?startapp=$payload',
          'plain': base,
        },
        'mini_app_url': (_demoBotSettings ?? _demoSettingsDefaults())['mini_app_url'],
      };
    }
    if (path == '/api/admin/meta') {
      return {
        'ok': true,
        'vtypes': [..._types],
        'admin_ids': [1557500001, 1557500002],
      };
    }
    if (path == '/api/admin/settings') {
      return {
        'ok': true,
        'items': {..._settings},
      };
    }
    if (path == '/api/admin/system/health') {
      return {
        'ok': true,
        'bot_ready': false,
        'bot_alive': false,
        'webhook_ready': false,
        'database_ready': true,
        'database_error': null,
        'reports_total': _reports.length,
        'backup_configured': false,
        'emergency': _emergency,
      };
    }
    if (path == '/api/admin/system/integrations') {
      return {
        'ok': true,
        'checked_at': DateTime.now().toUtc().toIso8601String(),
        'services': [
          {
            'id': 'render',
            'name': 'خدمة الاستضافة الحالية',
            'status': 'healthy',
            'detail': 'بيانات تدريبية',
          },
          {
            'id': 'database',
            'name': 'قاعدة البيانات',
            'status': 'healthy',
            'detail': 'بيانات تدريبية',
          },
          {
            'id': 'github',
            'name': 'المستودع والفحوص الآلية',
            'status': 'unknown',
            'detail': 'نشاط تدريبي غير متصل',
          },
        ],
        'github': {
          'repository': 'مثال تدريبي',
          'checked_at': DateTime.now().toUtc().toIso8601String(),
          'error': 'demo_data',
          'recent_runs': [],
          'recent_merges': [],
        },
      };
    }
    if (path == '/api/admin/osint/sources') {
      final reportId = int.tryParse(query?['report_id'] ?? '');
      final items = reportId == null
          ? _osintSources
          : _osintSources
                .where((item) => item['report_id'] == reportId)
                .toList();
      return {'ok': true, 'items': items};
    }
    if (path == '/api/admin/templates') {
      return {
        'ok': true,
        'items': [..._templates],
      };
    }
    if (path == '/api/admin/audit') {
      return {
        'ok': true,
        'items': [..._audit],
      };
    }
    if (path == '/api/admin/reports') {
      var items = [..._reports];
      final status = query?['status'];
      final type = query?['type'];
      final priority = query?['priority'];
      final assignedTo = int.tryParse(query?['assigned_to'] ?? '');
      final q = (query?['q'] ?? '').toLowerCase();
      if (status != null && status.isNotEmpty) {
        items = items.where((r) => r['status'] == status).toList();
      }
      if (type != null && type.isNotEmpty) {
        items = items.where((r) => r['violation_type'] == type).toList();
      }
      if (priority != null && priority.isNotEmpty) {
        items = items
            .where((r) => (r['priority'] ?? 'normal') == priority)
            .toList();
      }
      if (assignedTo != null) {
        items = items.where((r) => r['assigned_to'] == assignedTo).toList();
      }
      if (query?['unassigned'] == 'true') {
        items = items.where((r) => r['assigned_to'] == null).toList();
      }
      if (query?['overdue'] == 'true') {
        final now = DateTime.now();
        items = items.where((r) {
          final due = DateTime.tryParse('${r['due_at'] ?? ''}');
          return due != null &&
              due.isBefore(now) &&
              r['status'] != 'معتمد' &&
              r['status'] != 'مرفوض';
        }).toList();
      }
      if (q.isNotEmpty) {
        items = items
            .where(
              (r) => '${r['seq']} ${r['description']} ${r['full_name']}'
                  .toLowerCase()
                  .contains(q),
            )
            .toList();
      }
      return _page(items, query);
    }
    if (path == '/api/admin/users') {
      var items = _users.values.toList();
      final q = (query?['q'] ?? '').toLowerCase();
      if (q.isNotEmpty) {
        items = items
            .where(
              (u) => '${u['full_name']} ${u['username']} ${u['user_id']}'
                  .toLowerCase()
                  .contains(q),
            )
            .toList();
      }
      return _page(items, query);
    }
    if (path == '/api/admin/blocked-users') {
      final items = _users.values
          .where((u) {
            final block = u['block'] as Map<String, dynamic>;
            return (block['frozen_until'] as String).isNotEmpty;
          })
          .map((u) {
            final block = u['block'] as Map<String, dynamic>;
            return {
              ...u,
              'frozen_until': block['frozen_until'],
              'reason': block['reason'],
            };
          })
          .toList();
      return _page(items, query);
    }

    final reportMatch = RegExp(r'^/api/admin/reports/(\d+)$').firstMatch(path);
    if (reportMatch != null) {
      final report = _report(int.parse(reportMatch.group(1)!));
      if (report == null) throw ApiException(404, 'not_found');
      return {'ok': true, 'item': report};
    }
    final userMatch = RegExp(r'^/api/admin/users/(\d+)$').firstMatch(path);
    if (userMatch != null) {
      final id = int.parse(userMatch.group(1)!);
      final user = _users[id];
      if (user == null) throw ApiException(404, 'not_found');
      return {
        'ok': true,
        'profile': user,
        'reports_total': _reports.where((r) => r['user_id'] == id).length,
        'reports': _reports.where((r) => r['user_id'] == id).toList(),
      };
    }
    throw ApiException(404, 'not_found');
  }

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (path == '/api/admin/bot-apply') {
      _record('bot_apply_all', 'إعادة تطبيق محلية');
      return {
        'ok': true,
        'result': {'ok': true, 'command_errors': <String>[]},
      };
    }    if (path == '/api/admin/publications/send') {
      _publicationJobId++;
      _publicationJob = {
        'job_id': _publicationJobId,
        'status': 'completed',
        'sent': _users.length,
        'failed': 0,
        'total': _users.length,
      };
      _record('publication_sent', 'تجربة محلية');
      return {'ok': true, ..._publicationJob!};
    }
    final cancelJob = RegExp(
      r'^/api/admin/broadcast/(\d+)/cancel$',
    ).firstMatch(path);
    if (cancelJob != null) {
      if (_publicationJob == null ||
          int.parse(cancelJob.group(1)!) != _publicationJobId ||
          _publicationJob!['status'] != 'running') {
        throw ApiException(409, 'broadcast_not_active');
      }
      _publicationJob!['status'] = 'cancelled';
      return {'ok': true, 'status': 'cancel_requested'};
    }
    if (path == '/api/admin/notifications/read-all' ||
        RegExp(r'^/api/admin/notifications/\d+/read$').hasMatch(path)) {
      return {'ok': true};
    }
    if (path == '/api/admin/system/emergency') {
      _emergency = body['on'] == true;
      _record(_emergency ? 'emergency_on' : 'emergency_off', 'النظام');
      return {'ok': true};
    }
    if (path == '/api/admin/backup/now') {
      return {'ok': true, 'uploaded': false};
    }
    final followup = RegExp(
      r'^/api/admin/reports/(\d+)/followup$',
    ).firstMatch(path);
    if (followup != null) {
      final report = _report(int.parse(followup.group(1)!));
      if (report == null) throw ApiException(404, 'not_found');
      if (report['status'] == 'معتمد' || report['status'] == 'مرفوض') {
        throw ApiException(409, 'report_closed');
      }
      report['followup_note'] = body['note'];
      report['followup_requested_at'] = DateTime.now().toIso8601String();
      _record('report_followup_requested', report['seq'] as String);
      return {'ok': true, 'sent': true};
    }
    if (path == '/api/admin/broadcast') {
      _record('broadcast', 'تجربة محلية');
      return {'ok': true, 'sent': 3, 'failed': 0, 'total': 3};
    }
    if (path == '/api/admin/osint/sources') {
      final item = {
        'id': _osintSources.length + 1,
        'report_id': body['report_id'],
        'url': body['url'],
        'title': body['title'],
        'notes': body['notes'] ?? '',
        'source_level': body['source_level'] ?? 'unverified',
        'accessed_at': DateTime.now().toUtc().toIso8601String(),
      };
      _osintSources.insert(0, item);
      return {'ok': true, 'item': item};
    }
    final setStatus = RegExp(
      r'^/api/admin/reports/(\d+)/status$',
    ).firstMatch(path);
    if (setStatus != null) {
      final report = _report(int.parse(setStatus.group(1)!));
      if (report == null) throw ApiException(404, 'not_found');
      const statusNames = {
        'new': 'جديد',
        'review': 'قيد المراجعة',
        'approved': 'معتمد',
        'rejected': 'مرفوض',
      };
      final status = statusNames[body['status']];
      if (status == null) throw ApiException(400, 'bad_status');
      if (status == 'مرفوض' && '${body['reason'] ?? ''}'.trim().isEmpty) {
        throw ApiException(400, 'reason_required');
      }
      report['status'] = status;
      _record('status_changed', report['seq'] as String);
      return {'ok': true, 'notified': false};
    }
    final reportType = RegExp(
      r'^/api/admin/reports/(\d+)/type$',
    ).firstMatch(path);
    if (reportType != null) {
      final report = _report(int.parse(reportType.group(1)!));
      if (report == null) throw ApiException(404, 'not_found');
      report['violation_type'] = body['violation_type'] ?? '';
      _record('type_changed', report['seq'] as String);
      return {'ok': true};
    }
    final reportNote = RegExp(
      r'^/api/admin/reports/(\d+)/note$',
    ).firstMatch(path);
    if (reportNote != null) {
      final report = _report(int.parse(reportNote.group(1)!));
      if (report == null) throw ApiException(404, 'not_found');
      report['admin_note'] = body['note'] ?? '';
      _record('note_changed', report['seq'] as String);
      return {'ok': true};
    }
    final reportReply = RegExp(
      r'^/api/admin/reports/(\d+)/reply$',
    ).firstMatch(path);
    if (reportReply != null) {
      _record('panel_reply', reportReply.group(1)!);
      return {'ok': true};
    }
    final userMessage = RegExp(
      r'^/api/admin/users/(\d+)/message$',
    ).firstMatch(path);
    if (userMessage != null) {
      _record('user_message', userMessage.group(1)!);
      return {'ok': true, 'sent': true};
    }
    final blockUser = RegExp(
      r'^/api/admin/users/(\d+)/block$',
    ).firstMatch(path);
    if (blockUser != null) {
      final user = _users[int.parse(blockUser.group(1)!)];
      if (user == null) throw ApiException(404, 'not_found');
      final duration = '${body['duration'] ?? 'permanent'}';
      final until = duration == 'permanent'
          ? '9999-12-31T00:00:00'
          : DateTime.now()
                .add(Duration(hours: int.tryParse(duration) ?? 72))
                .toIso8601String();
      user['block'] = {
        'frozen_until': until,
        'reason': '${body['reason'] ?? 'مثال تدريبي'}',
      };
      _record('user_blocked', blockUser.group(1)!);
      return {'ok': true};
    }
    final unblockUser = RegExp(
      r'^/api/admin/users/(\d+)/unblock$',
    ).firstMatch(path);
    if (unblockUser != null) {
      final user = _users[int.parse(unblockUser.group(1)!)];
      if (user == null) throw ApiException(404, 'not_found');
      user['block'] = {'frozen_until': '', 'reason': ''};
      _record('user_unblocked', unblockUser.group(1)!);
      return {'ok': true};
    }
    throw ApiException(404, 'not_found');
  }

  @override
  Future<Map<String, dynamic>> put(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (path == '/api/admin/vtypes') {
      _types
        ..clear()
        ..addAll(List<String>.from(body['items'] as List));
      _record('vtypes_changed', 'قائمة محلية');
      return {
        'ok': true,
        'items': [..._types],
      };
    }
    if (path == '/api/admin/bot-buttons') {
      final items = body['items'];
      if (items is! List || items.isEmpty) {
        throw ApiException(422, 'bad_bot_menu_layout');
      }
      _botMenu = [
        for (final row in items)
          if (row is List)
            [
              for (final item in row)
                if (item is Map)
                  <String, dynamic>{
                    'label': '${item['label']}',
                    'type': '${item['type'] ?? 'callback'}',
                    'action': '${item['action'] ?? ''}',
                    if (item['style'] != null) 'style': '${item['style']}',
                    if (item['icon'] != null) 'icon': '${item['icon']}',
                    'disabled': item['disabled'] == true,
                    'visibility': '${item['visibility'] ?? 'all'}',
                    if (item['url'] != null) 'url': '${item['url']}',
                    if (item['copy_text'] != null)
                      'copy_text': '${item['copy_text']}',
                    if (item['inline_query'] != null)
                      'inline_query': '${item['inline_query']}',
                  },
            ],
      ];
      _record('bot_menu_changed', 'أزرار محلية');
      return {
        'ok': true,
        'items': _botMenuRows(),
        'customized': true,
        'actions': _botMenuActions,
        'limits': _botMenuLimits,
        'options': _botMenuOptions,
      };
    }
    if (path == '/api/admin/bot-reply-keyboards') {
      final items = body['items'];
      if (items is! Map) throw ApiException(422, 'bad_bot_reply_keyboards');
      _demoReply = items.cast<String, dynamic>();
      _record('bot_reply_keyboards_changed', 'لوحات رد محلية');
      return {
        'ok': true,
        'items': _demoReply,
        'ids': _demoReplyIds,
        'customized': true,
        'types': [
          'text',
          'cancel',
          'confirm',
          'contact',
          'location',
          'skip',
          'back',
          'web_app',
        ],
        'styles': ['primary', 'success', 'danger'],
      };
    }
    if (path == '/api/admin/bot-commands') {
      final items = body['items'];
      if (items is! Map) throw ApiException(422, 'bad_bot_commands');
      _demoCommands = items.cast<String, dynamic>();
      _record('bot_commands_changed', 'أوامر محلية');
      return {
        'ok': true,
        'items': _demoCommands,
        'customized': true,
        'applied': {'ok': true, 'errors': <String>[]},
        'bot_ready': true,
      };
    }
    if (path == '/api/admin/bot-menu-button') {
      final items = body['items'];
      if (items is! Map) throw ApiException(422, 'bad_bot_menu_button');
      _demoMenuButton = items.cast<String, dynamic>();
      _record('bot_menu_button_changed', 'زر قائمة محلي');
      return {
        'ok': true,
        'items': _demoMenuButton,
        'customized': true,
        'applied': true,
        'bot_ready': true,
      };
    }
    if (path == '/api/admin/bot-settings') {
      final items = body['items'];
      if (items is! Map) throw ApiException(422, 'bad_bot_settings');
      _demoBotSettings = items.cast<String, dynamic>();
      _record('bot_settings_changed', 'إعدادات بوت محلية');
      return {
        'ok': true,
        'items': _demoBotSettings,
        'customized': true,
        'applied': true,
        'bot_ready': true,
      };
    }
    if (path == '/api/admin/templates') {
      _templates
        ..clear()
        ..addAll(List<String>.from(body['items'] as List));
      _record('templates_changed', 'قوالب محلية');
      return {
        'ok': true,
        'items': [..._templates],
      };
    }
    if (path == '/api/admin/settings') {
      _settings
        ..clear()
        ..addAll(body);
      _record('runtime_settings_changed', 'إعدادات محلية');
      return {
        'ok': true,
        'items': {..._settings},
      };
    }
    final workflow = RegExp(
      r'^/api/admin/reports/(\d+)/workflow$',
    ).firstMatch(path);
    if (workflow != null) {
      final report = _report(int.parse(workflow.group(1)!));
      if (report == null) throw ApiException(404, 'not_found');
      report
        ..['priority'] = body['priority'] ?? report['priority'] ?? 'normal'
        ..['assigned_to'] = body['assigned_to']
        ..['due_at'] = body['due_at'];
      _record('report_workflow_changed', report['seq'] as String);
      return {
        'ok': true,
        'priority': report['priority'],
        'assigned_to': report['assigned_to'],
        'due_at': report['due_at'],
      };
    }
    throw ApiException(404, 'not_found');
  }

  @override
  Future<Map<String, dynamic>> del(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (path == '/api/admin/bot-buttons') {
      _botMenu = null;
      _record('bot_menu_reset', 'أزرار محلية');
      return {
        'ok': true,
        'items': _botMenuRows(),
        'defaults': _defaultBotMenuRows(),
        'customized': false,
        'actions': _botMenuActions,
        'limits': _botMenuLimits,
        'options': _botMenuOptions,
      };
    }
    if (path == '/api/admin/bot-reply-keyboards') {
      _demoReply = null;
      _record('bot_reply_keyboards_reset', 'لوحات رد محلية');
      return {
        'ok': true,
        'items': _demoReplyDefaults(),
        'ids': _demoReplyIds,
        'customized': false,
        'types': [
          'text',
          'cancel',
          'confirm',
          'contact',
          'location',
          'skip',
          'back',
          'web_app',
        ],
        'styles': ['primary', 'success', 'danger'],
      };
    }
    if (path == '/api/admin/bot-menu-button') {
      _demoMenuButton = null;
      _record('bot_menu_button_reset', 'زر قائمة محلي');
      return {
        'ok': true,
        'items': {'type': 'default', 'text': 'فتح التطبيق', 'url': ''},
        'customized': false,
        'applied': true,
        'bot_ready': true,
      };
    }
    if (path == '/api/admin/bot-analytics') {
      _demoAnalytics.clear();
      _record('bot_analytics_reset', 'تحليلات محلية');
      return {'ok': true, 'items': <Map<String, dynamic>>[], 'total': 0};
    }
    final source = RegExp(r'^/api/admin/osint/sources/(\d+)$').firstMatch(path);
    if (source != null) {
      final id = int.parse(source.group(1)!);
      _osintSources.removeWhere((item) => item['id'] == id);
      return {'ok': true, 'deleted': true};
    }
    final match = RegExp(r'^/api/admin/reports/(\d+)$').firstMatch(path);
    if (match == null) throw ApiException(404, 'not_found');
    final report = _report(int.parse(match.group(1)!));
    if (report == null) throw ApiException(404, 'not_found');
    if (body['confirm_seq'] != report['seq']) {
      throw ApiException(400, 'must_confirm_seq');
    }
    _reports.remove(report);
    _record('report_deleted', report['seq'] as String);
    return {'ok': true};
  }

  Map<String, dynamic> _page(
    List<Map<String, dynamic>> items,
    Map<String, String>? query,
  ) {
    final page = int.tryParse(query?['page'] ?? '0') ?? 0;
    final size = int.tryParse(query?['size'] ?? '25') ?? 25;
    final start = (page * size).clamp(0, items.length);
    final end = (start + size).clamp(start, items.length);
    return {
      'ok': true,
      'total': items.length,
      'items': items.sublist(start, end),
    };
  }

  Map<String, dynamic>? _report(int id) {
    for (final report in _reports) {
      if (report['id'] == id) return report;
    }
    return null;
  }

  @override
  Stream<Map<String, dynamic>> watchEvents() => const Stream.empty();

  @override
  Future<Uint8List> getBytes(String path) async {
    throw ApiException(404, 'demo_photo_unavailable');
  }

  @override
  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    String? filename,
    Uint8List? bytes,
  }) async {
    if (path == '/api/admin/bot-profile') {
      _botProfile
        ..['name'] = fields['name'] ?? ''
        ..['short_description'] = fields['short_description'] ?? ''
        ..['description'] = fields['description'] ?? ''
        ..['welcome_text'] = fields['welcome_text'] ?? '';
      _record('bot_profile_changed', filename ?? 'تحديث نصوص الهوية');
      return {
        'ok': true,
        'items': {..._botProfile},
      };
    }
    if (path != '/api/admin/publications/preview') {
      throw ApiException(404, 'not_found');
    }
    if ((fields['media_type'] ?? '').isNotEmpty && (bytes?.isEmpty ?? true)) {
      throw ApiException(400, 'media_required');
    }
    _record('publication_preview', fields['media_type'] ?? 'text');
    return {
      'ok': true,
      'preview_sent': true,
      'media_type': fields['media_type']?.isNotEmpty == true
          ? fields['media_type']
          : null,
      'file_id': bytes == null ? null : 'demo-file-id',
      'filename': filename,
    };
  }

  void _record(String action, String target) {
    _audit.insert(0, {
      'ts': DateTime.now().toIso8601String(),
      'actor_id': 0,
      'action': action,
      'target': target,
      'details': 'عملية محاكاة محلية — لا أثر على البوت',
    });
  }

  @override
  String photoUrl(num reportId, {Object? bust}) => '';

  @override
  @override
  Future<void> download(String path) async {}
}
