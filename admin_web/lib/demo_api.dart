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
  final List<Map<String, dynamic>> _audit = [];
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
    if (path == '/api/admin/session') {
      return {'ok': true, 'role': 'owner', 'admin_id': 0};
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
  Future<Uint8List> getBytes(String path) async {
    throw ApiException(404, 'demo_photo_unavailable');
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
