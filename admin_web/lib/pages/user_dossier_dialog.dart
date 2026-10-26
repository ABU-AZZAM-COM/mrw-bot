import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

Future<void> showUserDossier(BuildContext context, AdminApi api, int userId) =>
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => Dialog(
        child: SizedBox(
          width: 980,
          height: 700,
          child: _UserDossierDialog(api: api, userId: userId),
        ),
      ),
    );

class _UserDossierDialog extends StatefulWidget {
  const _UserDossierDialog({required this.api, required this.userId});

  final AdminApi api;
  final int userId;

  @override
  State<_UserDossierDialog> createState() => _UserDossierDialogState();
}

class _UserDossierDialogState extends State<_UserDossierDialog> {
  Map<String, dynamic>? _profile;
  List<Map<String, dynamic>> _reports = [];
  List<Map<String, dynamic>> _audit = [];
  String? _error;
  bool _loading = true;
  bool _auditTruncated = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final result = await widget.api.get(
        '/api/admin/users/${widget.userId}/dossier',
      );
      if (!mounted) return;
      setState(() {
        _profile = Map<String, dynamic>.from(result['profile'] as Map);
        _reports = (result['reports'] as List? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _audit = (result['audit'] as List? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _auditTruncated = result['audit_truncated'] == true;
        _loading = false;
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = errMsg(error);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = _profile;
    final theme = Theme.of(context);
    return Column(
      children: [
        Container(
          padding: const EdgeInsetsDirectional.only(start: 18, end: 8),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
          ),
          child: Row(
            children: [
              const Icon(Icons.manage_search_outlined),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  'بطاقة الكشف — ${profile?['full_name'] ?? 'مستخدم ${widget.userId}'}',
                  style: theme.textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              IconButton(
                tooltip: 'تحديث السجل',
                onPressed: _load,
                icon: const Icon(Icons.refresh),
              ),
              IconButton(
                tooltip: 'إغلاق',
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close),
              ),
            ],
          ),
        ),
        if (_loading)
          const Expanded(child: Center(child: CircularProgressIndicator()))
        else if (_error != null)
          Expanded(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_error!, textAlign: TextAlign.center),
                  const SizedBox(height: 8),
                  FilledButton.icon(
                    onPressed: _load,
                    icon: const Icon(Icons.refresh),
                    label: const Text('إعادة المحاولة'),
                  ),
                ],
              ),
            ),
          )
        else
          Expanded(
            child: DefaultTabController(
              length: 3,
              child: Column(
                children: [
                  const TabBar(
                    tabs: [
                      Tab(text: 'الملخص'),
                      Tab(text: 'البلاغات المقدّمة'),
                      Tab(text: 'سجل الإجراءات'),
                    ],
                  ),
                  Expanded(
                    child: TabBarView(
                      children: [
                        _summary(profile!),
                        _buildReportsTab(),
                        _buildAuditTab(),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _summary(Map<String, dynamic> profile) {
    final block = profile['block'] is Map
        ? Map<String, dynamic>.from(profile['block'] as Map)
        : <String, dynamic>{};
    final frozenUntil = block['frozen_until']?.toString() ?? '';
    final active =
        DateTime.tryParse(frozenUntil)?.isAfter(DateTime.now()) ?? false;
    final phone = profile['contact_phone']?.toString().trim() ?? '';
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _metric('معرّف تيليجرام', '${profile['user_id'] ?? widget.userId}'),
            _metric('إجمالي البلاغات', '${profile['total_reports'] ?? 0}'),
            _metric('معتمدة', '${profile['approved_n'] ?? 0}'),
            _metric('مرفوضة', '${profile['rejected_n'] ?? 0}'),
            _metric('أول ظهور', '${profile['first_at'] ?? '-'}'),
            _metric('آخر نشاط', '${profile['last_at'] ?? '-'}'),
          ],
        ),
        const SizedBox(height: 16),
        Card(
          child: ListTile(
            leading: Icon(
              active ? Icons.block : Icons.verified_user_outlined,
              color: active ? Colors.red : Colors.green,
            ),
            title: Text(
              active ? 'يوجد قيد حجب/تجميد سارٍ' : 'لا يوجد حجب سارٍ',
            ),
            subtitle: Text(
              [
                    if (block['reason'] != null) 'السبب: ${block['reason']}',
                    if (frozenUntil.isNotEmpty) 'ينتهي: $frozenUntil',
                    if (block['strikes'] != null)
                      'المخالفات المسجّلة: ${block['strikes']}',
                  ].join('\n').isEmpty
                  ? 'لا توجد بيانات حجب حالية.'
                  : [
                      if (block['reason'] != null) 'السبب: ${block['reason']}',
                      if (frozenUntil.isNotEmpty) 'ينتهي: $frozenUntil',
                      if (block['strikes'] != null)
                        'المخالفات المسجّلة: ${block['strikes']}',
                    ].join('\n'),
            ),
          ),
        ),
        if (phone.isNotEmpty)
          Card(
            child: ListTile(
              leading: const Icon(Icons.phone_outlined),
              title: const Text('رقم تواصل موثق في بلاغ'),
              subtitle: Text('$phone · ${profile['contact_phone_seq'] ?? ''}'),
            ),
          ),
        const SizedBox(height: 12),
        const Card(
          child: Padding(
            padding: EdgeInsets.all(14),
            child: Text(
              'حدود السجل: البلاغات المعروضة هي البلاغات التي قدّمها هذا المستخدم. '
              'قاعدة البيانات لا تحفظ معرّفًا مستقلًا موثوقًا للشخص المذكور في البلاغ؛ '
              'لذلك لا تُنسب إليه شكاوى أو اتهامات بالاستنتاج من النص.',
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildReportsTab() {
    if (_reports.isEmpty) {
      return const Center(
        child: Text('لا توجد بلاغات مقدّمة مسجّلة لهذا المستخدم.'),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: _reports.length,
      itemBuilder: (context, index) {
        final report = _reports[index];
        final title = report['seq']?.toString() ?? 'بلاغ ${report['id']}';
        return Card(
          child: ExpansionTile(
            leading: const Icon(Icons.assignment_outlined),
            title: Text('$title · ${report['status'] ?? '-'}'),
            subtitle: Text(
              '${report['created_at'] ?? '-'} · '
              '${report['category'] ?? report['violation_type'] ?? 'بلا تصنيف'}',
            ),
            childrenPadding: const EdgeInsetsDirectional.fromSTEB(
              16,
              0,
              16,
              16,
            ),
            children: [
              _field('الرابط', report['post_link']),
              _field('الوصف', report['description']),
              _field('التصنيف', report['category'] ?? report['violation_type']),
              _field('الموقع', report['place']),
              _field('الأولوية', report['priority']),
              _field('المكلّف', report['assigned_to']),
              _field('الموعد المحدد', report['due_at']),
              _field('ملاحظة الإدارة', report['admin_note']),
              _field('المتابعة المطلوبة', report['followup_note']),
              _field('رد المتبلّغ على المتابعة', report['followup_response']),
              _field('رُوجع بواسطة', report['reviewed_by']),
              _field('وقت المراجعة', report['reviewed_at']),
            ],
          ),
        );
      },
    );
  }

  Widget _buildAuditTab() {
    if (_audit.isEmpty) {
      return const Center(
        child: Text('لا توجد إجراءات إدارية مسجّلة لهذا المستخدم.'),
      );
    }
    return Column(
      children: [
        if (_auditTruncated)
          const MaterialBanner(
            content: Text('عُرض أحدث 500 إجراء فقط.'),
            leading: Icon(Icons.info_outline),
            actions: [SizedBox.shrink()],
          ),
        Expanded(
          child: ListView.separated(
            padding: const EdgeInsets.all(12),
            itemCount: _audit.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) {
              final event = _audit[index];
              return ListTile(
                leading: const CircleAvatar(
                  child: Icon(Icons.history, size: 18),
                ),
                title: Text(event['action']?.toString() ?? 'إجراء'),
                subtitle: Text(
                  [
                    event['ts']?.toString() ?? '',
                    'المنفّذ: ${event['actor_id'] ?? '-'}',
                    if ((event['target']?.toString() ?? '').isNotEmpty)
                      'الهدف: ${event['target']}',
                    if ((event['details']?.toString() ?? '').isNotEmpty)
                      event['details'].toString(),
                  ].where((value) => value.isNotEmpty).join('\n'),
                ),
                isThreeLine: true,
              );
            },
          ),
        ),
      ],
    );
  }

  Widget _metric(String label, String value) => SizedBox(
    width: 180,
    child: Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 5),
            Text(
              value.isEmpty ? '-' : value,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.titleSmall,
            ),
          ],
        ),
      ),
    ),
  );

  Widget _field(String label, dynamic rawValue) {
    final value = rawValue?.toString().trim() ?? '';
    if (value.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Align(
        alignment: AlignmentDirectional.centerStart,
        child: Text('$label: $value'),
      ),
    );
  }
}
