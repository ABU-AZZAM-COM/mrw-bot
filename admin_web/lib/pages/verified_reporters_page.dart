import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'overview_page.dart' show fmt;

class VerifiedReportersPage extends StatefulWidget {
  const VerifiedReportersPage({
    super.key,
    required this.api,
    this.initialRequestId,
    this.onFocusHandled,
    this.canViewDocuments = false,
    this.canDeleteDocuments = false,
  });

  final AdminApi api;
  final int? initialRequestId;
  final VoidCallback? onFocusHandled;
  final bool canViewDocuments;
  final bool canDeleteDocuments;

  @override
  State<VerifiedReportersPage> createState() => _VerifiedReportersPageState();
}

class _VerifiedReportersPageState extends State<VerifiedReportersPage> {
  final _search = TextEditingController();
  final _startDate = TextEditingController();
  final _endDate = TextEditingController();
  Timer? _searchTimer;
  List<Map<String, dynamic>> _items = [];
  Map<String, dynamic> _analytics = const {};
  Map<String, dynamic>? _detail;
  Map<String, dynamic> _versions = const {};
  List<Map<String, dynamic>> _supplements = [];
  Uint8List? _image;
  String? _imageKind;
  int? _imageVersionId;
  String _status = 'pending';
  bool _loading = true;
  bool _detailLoading = false;
  bool _analyticsLoading = true;
  String? _error;
  int _page = 0;
  int _total = 0;
  static const _pageSize = 25;

  @override
  void initState() {
    super.initState();
    _load();
    final requestId = widget.initialRequestId;
    if (requestId != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _openDetail(requestId);
      });
    }
  }

  @override
  void dispose() {
    _searchTimer?.cancel();
    _search.dispose();
    _startDate.dispose();
    _endDate.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final query = <String, String>{
        'status': _status,
        'page': '$_page',
        'size': '$_pageSize',
        if (_search.text.trim().isNotEmpty) 'q': _search.text.trim(),
        if (_startDate.text.trim().isNotEmpty)
          'start_date': _startDate.text.trim(),
        if (_endDate.text.trim().isNotEmpty) 'end_date': _endDate.text.trim(),
      };
      final result = await widget.api.get(
        '/api/admin/verified-reporters',
        query,
      );
      if (!mounted) return;
      setState(() {
        _items = (result['items'] as List<dynamic>? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _total = (result['total'] as num?)?.toInt() ?? 0;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = errMsg(error);
        _loading = false;
      });
    }
    _loadAnalytics();
  }

  Future<void> _loadAnalytics() async {
    setState(() => _analyticsLoading = true);
    try {
      final result = await widget.api.get(
        '/api/admin/verified-reporters/analytics',
        const {'days': '30'},
      );
      if (!mounted) return;
      setState(() {
        _analytics = result;
        _analyticsLoading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _analyticsLoading = false);
      toast(context, errMsg(error), error: true);
    }
  }

  void _scheduleSearch() {
    _searchTimer?.cancel();
    _searchTimer = Timer(const Duration(milliseconds: 350), () {
      if (!mounted) return;
      _page = 0;
      _load();
    });
  }

  Future<void> _openDetail(int id) async {
    setState(() {
      _detailLoading = true;
      _detail = null;
      _image = null;
    });
    try {
      final result = await widget.api.get('/api/admin/verified-reporters/$id');
      if (!mounted) return;
      setState(() {
        _detail = Map<String, dynamic>.from(result['item'] as Map);
        _versions = Map<String, dynamic>.from(
          result['versions'] as Map? ?? const {},
        );
        _supplements = (result['supplements'] as List<dynamic>? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _detailLoading = false;
      });
      widget.onFocusHandled?.call();
    } catch (error) {
      if (!mounted) return;
      setState(() => _detailLoading = false);
      toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _showDocument(String kind, {int? versionId}) async {
    if (!widget.canViewDocuments) {
      toast(context, 'لا تملك صلاحية الاطلاع على صور الهوية', error: true);
      return;
    }
    final id = (_detail?['id'] as num?)?.toInt();
    if (id == null) return;
    setState(() {
      _image = null;
      _imageKind = kind;
      _imageVersionId = versionId;
    });
    try {
      final path =
          '/api/admin/verified-reporters/$id/documents/$kind'
          '${versionId == null ? '' : '?version_id=$versionId'}';
      final bytes = await widget.api.getBytes(path);
      if (!mounted) return;
      setState(() => _image = bytes);
    } catch (error) {
      if (!mounted) return;
      toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _decide(String decision) async {
    final item = _detail;
    final id = (item?['id'] as num?)?.toInt();
    if (id == null) return;
    String? rejectionReason;
    if (decision == 'verified') {
      final confirmed = await confirmDialog(
        context,
        'اعتماد المراسل',
        'هل راجعت الوجه والوجهين الأمامي والخلفي؟ اعتماد الحساب لا يعني اعتماد الشكاوى التي يرسلها.',
        okLabel: 'اعتماد',
      );
      if (!confirmed || !mounted) return;
    } else {
      final controller = TextEditingController();
      rejectionReason = await showDialog<String>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('رفض طلب التوثيق'),
          content: SizedBox(
            width: 440,
            child: TextField(
              controller: controller,
              autofocus: true,
              minLines: 3,
              maxLines: 6,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: 'سبب الرفض (إلزامي)',
                border: OutlineInputBorder(),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: () {
                final value = controller.text.trim();
                if (value.isNotEmpty) Navigator.pop(context, value);
              },
              child: const Text('رفض وإرسال السبب'),
            ),
          ],
        ),
      );
      controller.dispose();
      if (rejectionReason == null || !mounted) return;
    }
    try {
      final result = await widget.api
          .post('/api/admin/verified-reporters/$id/decision', {
            'decision': decision,
            if (rejectionReason != null) 'rejection_reason': rejectionReason,
          });
      if (!mounted) return;
      final notified = result['notification_sent'] == true;
      toast(
        context,
        decision == 'verified'
            ? 'تم اعتماد المراسل${notified ? ' وإشعار المستخدم' : '؛ تعذر إشعار المستخدم'}'
            : 'تم رفض الطلب وإرسال السبب${notified ? '' : '؛ تعذر إشعار المستخدم'}',
        error: !notified,
      );
      await _openDetail(id);
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _requestSupplement() async {
    final id = (_detail?['id'] as num?)?.toInt();
    if (id == null) return;
    final selected = <String>{};
    final message = TextEditingController();
    final data = await showDialog<(List<String>, String)>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('طلب استكمال صور'),
          content: SizedBox(
            width: 480,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final entry in _documentKinds.entries)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(entry.value),
                      value: selected.contains(entry.key),
                      onChanged: (value) => setDialogState(() {
                        if (value == true) {
                          selected.add(entry.key);
                        } else {
                          selected.remove(entry.key);
                        }
                      }),
                    ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: message,
                    minLines: 3,
                    maxLines: 6,
                    maxLength: 1000,
                    decoration: const InputDecoration(
                      labelText: 'رسالة للمراسل (إلزامية)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: selected.isEmpty || message.text.trim().isEmpty
                  ? null
                  : () => Navigator.pop(context, (
                      selected.toList(),
                      message.text.trim(),
                    )),
              child: const Text('إرسال طلب الاستكمال'),
            ),
          ],
        ),
      ),
    );
    message.dispose();
    if (data == null || !mounted) return;
    try {
      final result = await widget.api.post(
        '/api/admin/verified-reporters/$id/supplement',
        {'requested_documents': data.$1, 'message': data.$2},
      );
      if (!mounted) return;
      toast(
        context,
        result['user_notified'] == true
            ? 'أُرسل طلب الاستكمال للمراسل'
            : 'حُفظ الطلب لكن تعذر إرسال إشعار تيليجرام',
        error: result['user_notified'] != true,
      );
      await _openDetail(id);
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _deleteDocuments() async {
    final id = (_detail?['id'] as num?)?.toInt();
    if (id == null || !widget.canDeleteDocuments) return;
    final confirmed = await confirmDialog(
      context,
      'حذف صور ونسخ الهوية',
      'سيتم حذف الصور الحالية ونسخها فقط، مع إبقاء سجل الطلب وبيانات التدقيق. لا يمكن التراجع عن ذلك.',
      okLabel: 'متابعة',
    );
    if (!confirmed || !mounted) return;
    final controller = TextEditingController();
    final reason = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('سبب الحذف الإلزامي'),
        content: SizedBox(
          width: 440,
          child: TextField(
            controller: controller,
            autofocus: true,
            minLines: 2,
            maxLines: 5,
            maxLength: 500,
            decoration: const InputDecoration(
              labelText: 'سبب الحذف',
              border: OutlineInputBorder(),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () {
              final value = controller.text.trim();
              if (value.isNotEmpty) Navigator.pop(context, value);
            },
            child: const Text('حذف الصور والنسخ'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (reason == null || !mounted) return;
    try {
      await widget.api.post('/api/admin/verified-reporters/$id/delete', {
        'reason': reason,
      });
      if (!mounted) return;
      toast(context, 'حُذفت الصور والنسخ وسُجل السبب في التدقيق');
      await _openDetail(id);
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _downloadPdf() async {
    final id = (_detail?['id'] as num?)?.toInt();
    if (id == null || !widget.canViewDocuments) return;
    try {
      await widget.api.download('/api/admin/verified-reporters/$id/pdf');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  @override
  Widget build(BuildContext context) =>
      _detail == null ? _dashboard() : _detailPage();

  Widget _dashboard() {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Icon(Icons.verified_user_outlined),
            const SizedBox(width: 10),
            Expanded(
              child: Text('توثيق المراسلين', style: theme.textTheme.titleLarge),
            ),
            IconButton(
              tooltip: 'تحديث',
              onPressed: _loading ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: 12),
        _analyticsCards(),
        const SizedBox(height: 12),
        _trendChart(),
        const SizedBox(height: 12),
        _filters(),
        const SizedBox(height: 8),
        Expanded(child: _listBody()),
        _pagination(),
      ],
    );
  }

  Widget _analyticsCards() {
    final counts = _analytics['counts'] as Map<String, dynamic>? ?? const {};
    final waiting = _analytics['waiting'] as Map<String, dynamic>? ?? const {};
    final values = <(String, String, IconData)>[
      ('بانتظار المراجعة', '${counts['pending'] ?? 0}', Icons.hourglass_top),
      ('بانتظار الاستكمال', '${counts['needs_info'] ?? 0}', Icons.more_time),
      ('معتمد', '${counts['verified'] ?? 0}', Icons.verified_outlined),
      ('مرفوض', '${counts['rejected'] ?? 0}', Icons.block_outlined),
      (
        'متوسط الانتظار',
        waiting['average_wait_hours'] == null
            ? '—'
            : '${waiting['average_wait_hours']} س',
        Icons.schedule,
      ),
    ];
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        for (final item in values)
          SizedBox(
            width: 170,
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  children: [
                    Icon(item.$3),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(item.$1, style: const TextStyle(fontSize: 12)),
                          Text(
                            _analyticsLoading ? '…' : item.$2,
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }

  Widget _trendChart() {
    final trend = (_analytics['trend'] as List<dynamic>? ?? const [])
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
    return Card(
      child: Padding(
        padding: const EdgeInsetsDirectional.fromSTEB(16, 12, 16, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'اتجاه الطلبات — آخر 30 يومًا',
              style: TextStyle(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            SizedBox(
              height: 110,
              child: _analyticsLoading || trend.isEmpty
                  ? const Center(child: Text('لا توجد بيانات كافية'))
                  : CustomPaint(
                      painter: _TrendPainter(
                        trend
                            .map((item) => (item['count'] as num).toDouble())
                            .toList(),
                        Theme.of(context).colorScheme.primary,
                        Theme.of(context).colorScheme.outlineVariant,
                      ),
                      child: const SizedBox.expand(),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _filters() => Wrap(
    spacing: 10,
    runSpacing: 8,
    crossAxisAlignment: WrapCrossAlignment.center,
    children: [
      SizedBox(
        width: 240,
        child: TextField(
          controller: _search,
          onChanged: (_) => _scheduleSearch(),
          decoration: const InputDecoration(
            prefixIcon: Icon(Icons.search),
            labelText: 'اسم أو مستخدم أو معرف تيليجرام',
            isDense: true,
          ),
        ),
      ),
      SizedBox(
        width: 170,
        child: DropdownButtonFormField<String>(
          initialValue: _status,
          decoration: const InputDecoration(labelText: 'الحالة', isDense: true),
          items: const [
            DropdownMenuItem(value: 'pending', child: Text('بانتظار المراجعة')),
            DropdownMenuItem(
              value: 'needs_info',
              child: Text('بانتظار الاستكمال'),
            ),
            DropdownMenuItem(value: 'verified', child: Text('معتمد')),
            DropdownMenuItem(value: 'rejected', child: Text('مرفوض')),
            DropdownMenuItem(value: 'all', child: Text('الكل')),
          ],
          onChanged: (value) {
            if (value == null) return;
            setState(() {
              _status = value;
              _page = 0;
            });
            _load();
          },
        ),
      ),
      SizedBox(
        width: 145,
        child: TextField(
          controller: _startDate,
          onSubmitted: (_) => _load(),
          decoration: const InputDecoration(
            labelText: 'من تاريخ YYYY-MM-DD',
            isDense: true,
          ),
        ),
      ),
      SizedBox(
        width: 145,
        child: TextField(
          controller: _endDate,
          onSubmitted: (_) => _load(),
          decoration: const InputDecoration(
            labelText: 'إلى تاريخ YYYY-MM-DD',
            isDense: true,
          ),
        ),
      ),
      OutlinedButton.icon(
        onPressed: _load,
        icon: const Icon(Icons.filter_alt_outlined),
        label: const Text('تطبيق'),
      ),
    ],
  );

  Widget _listBody() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) return ErrorView(_error!, _load);
    if (_items.isEmpty) return const EmptyView('لا توجد طلبات مطابقة');
    return ListView.separated(
      itemCount: _items.length,
      separatorBuilder: (_, _) => const SizedBox(height: 6),
      itemBuilder: (context, index) {
        final item = _items[index];
        final reviewState = item['review_state']?.toString();
        final status = item['status']?.toString();
        final label = reviewState == 'needs_info'
            ? 'بانتظار الاستكمال'
            : switch (status) {
                'pending' => 'قيد المراجعة',
                'verified' => 'معتمد',
                'rejected' => 'مرفوض',
                _ => status ?? '—',
              };
        final id = (item['id'] as num?)?.toInt();
        return Card(
          child: ListTile(
            leading: CircleAvatar(child: Text('${item['id'] ?? '—'}')),
            title: Text(
              '${item['full_name'] ?? 'مستخدم'} · ${item['user_id'] ?? '—'}',
            ),
            subtitle: Text(
              '@${item['username'] ?? '—'} · $label · '
              '${fmt('${item['submitted_at'] ?? ''}')}\n'
              '${item['has_documents'] == 1 ? 'الوثائق محفوظة' : 'الوثائق غير متاحة'}',
            ),
            isThreeLine: true,
            trailing: const Icon(Icons.chevron_right),
            onTap: id == null ? null : () => _openDetail(id),
          ),
        );
      },
    );
  }

  Widget _pagination() => Row(
    mainAxisAlignment: MainAxisAlignment.center,
    children: [
      IconButton(
        tooltip: 'السابق',
        onPressed: _page > 0
            ? () {
                setState(() => _page--);
                _load();
              }
            : null,
        icon: const Icon(Icons.chevron_left),
      ),
      Text(
        '${_total == 0 ? 0 : _page * _pageSize + 1}–'
        '${((_page + 1) * _pageSize).clamp(0, _total)} من $_total',
      ),
      IconButton(
        tooltip: 'التالي',
        onPressed: (_page + 1) * _pageSize < _total
            ? () {
                setState(() => _page++);
                _load();
              }
            : null,
        icon: const Icon(Icons.chevron_right),
      ),
    ],
  );

  Widget _detailPage() {
    if (_detailLoading || _detail == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final item = _detail!;
    final id = (item['id'] as num?)?.toInt();
    final pending = item['status'] == 'pending';
    final hasDocuments =
        item['has_documents'] == 1 || item['has_documents'] == true;
    final reviewState = item['review_state']?.toString();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            IconButton(
              tooltip: 'العودة للقائمة',
              onPressed: () => setState(() {
                _detail = null;
                _image = null;
              }),
              icon: const Icon(Icons.arrow_back),
            ),
            Expanded(
              child: Text(
                'طلب توثيق #${item['id']}',
                style: Theme.of(context).textTheme.titleLarge,
              ),
            ),
            if (widget.canViewDocuments && hasDocuments)
              OutlinedButton.icon(
                onPressed: _downloadPdf,
                icon: const Icon(Icons.picture_as_pdf_outlined),
                label: const Text('تنزيل PDF'),
              ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(8),
            children: [
              Card(
                child: ListTile(
                  title: Text(
                    '${item['full_name'] ?? 'مستخدم'} · ${item['user_id'] ?? '—'}',
                  ),
                  subtitle: Text(
                    '@${item['username'] ?? '—'} · ${item['status']}'
                    '${reviewState == 'needs_info' ? ' — بانتظار الاستكمال' : ''}'
                    '\nتاريخ التقديم: ${fmt('${item['submitted_at'] ?? ''}')}\n'
                    'قرار المراجعة: ${fmt('${item['decided_at'] ?? ''}')}\n'
                    '${item['rejection_reason'] == null ? '' : 'سبب الرفض: ${item['rejection_reason']}'}',
                  ),
                  isThreeLine: true,
                ),
              ),
              if (item['documents_deleted_at'] != null)
                Card(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: ListTile(
                    leading: const Icon(Icons.delete_outline),
                    title: const Text('حُذفت الصور والنسخ إداريًا'),
                    subtitle: Text(
                      '${fmt('${item['documents_deleted_at']}')} · '
                      'المشرف ${item['documents_deleted_by'] ?? '—'}\n'
                      '${item['documents_deleted_reason'] ?? ''}',
                    ),
                  ),
                ),
              if (hasDocuments && widget.canViewDocuments && id != null)
                _documentPanel(id),
              if (!widget.canViewDocuments)
                const Card(
                  child: ListTile(
                    leading: Icon(Icons.lock_outline),
                    title: Text('عرض صور الهوية يتطلب صلاحية منفصلة'),
                  ),
                ),
              if (pending)
                Wrap(
                  alignment: WrapAlignment.end,
                  spacing: 8,
                  children: [
                    OutlinedButton.icon(
                      onPressed: _requestSupplement,
                      icon: const Icon(Icons.add_photo_alternate_outlined),
                      label: const Text('طلب استكمال'),
                    ),
                    OutlinedButton(
                      onPressed: () => _decide('rejected'),
                      child: const Text('رفض'),
                    ),
                    FilledButton.icon(
                      onPressed: () => _decide('verified'),
                      icon: const Icon(Icons.verified_outlined),
                      label: const Text('اعتماد الهوية'),
                    ),
                  ],
                ),
              if (widget.canDeleteDocuments &&
                  hasDocuments &&
                  (item['status'] == 'verified' ||
                      item['status'] == 'rejected'))
                Align(
                  alignment: AlignmentDirectional.centerEnd,
                  child: OutlinedButton.icon(
                    onPressed: _deleteDocuments,
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('حذف الصور والنسخ مع سبب'),
                  ),
                ),
              _supplementHistory(),
            ],
          ),
        ),
      ],
    );
  }

  Widget _documentPanel(int id) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final entry in _documentKinds.entries)
            OutlinedButton(
              onPressed: () => _showDocument(entry.key),
              child: Text(entry.value),
            ),
        ],
      ),
      if (_imageKind != null) ...[
        const SizedBox(height: 12),
        _ImageInspector(
          key: ValueKey('$_imageKind-$_imageVersionId'),
          bytes: _image,
          title:
              '${_documentKinds[_imageKind]}'
              '${_imageVersionId == null ? '' : ' · نسخة $_imageVersionId'}',
        ),
      ],
      for (final entry in _versions.entries)
        if ((entry.value as List<dynamic>).length > 1)
          ExpansionTile(
            title: Text('نسخ سابقة — ${_documentKinds[entry.key]}'),
            children: [
              for (final version in entry.value as List<dynamic>)
                ListTile(
                  title: Text(
                    'نسخة ${version['id']} · ${fmt('${version['submitted_at'] ?? ''}')}',
                  ),
                  trailing: IconButton(
                    tooltip: 'عرض النسخة',
                    onPressed: () => _showDocument(
                      entry.key,
                      versionId: (version['id'] as num).toInt(),
                    ),
                    icon: const Icon(Icons.visibility_outlined),
                  ),
                ),
            ],
          ),
    ],
  );

  Widget _supplementHistory() => Card(
    child: ExpansionTile(
      title: Text('سجل الاستكمال (${_supplements.length})'),
      children: [
        if (_supplements.isEmpty)
          const ListTile(title: Text('لا يوجد استكمال سابق')),
        for (final supplement in _supplements)
          ListTile(
            title: Text(
              'طُلب ${fmt('${supplement['requested_at'] ?? ''}')}'
              '${supplement['response_at'] == null ? ' · لم يرد بعد' : ' · استُلم الرد'}',
            ),
            subtitle: Text(
              '${(supplement['requested_documents'] as List<dynamic>).map((doc) => _documentKinds[doc]).join('، ')}\n'
              '${supplement['message'] ?? ''}',
            ),
            isThreeLine: true,
          ),
      ],
    ),
  );

  static const _documentKinds = <String, String>{
    'selfie': 'صورة الوجه',
    'id_front': 'الهوية — أمام',
    'id_back': 'الهوية — خلف',
  };
}

class _ImageInspector extends StatefulWidget {
  const _ImageInspector({super.key, required this.bytes, required this.title});

  final Uint8List? bytes;
  final String title;

  @override
  State<_ImageInspector> createState() => _ImageInspectorState();
}

class _ImageInspectorState extends State<_ImageInspector> {
  final _transformation = TransformationController();
  double _rotation = 0;

  @override
  void dispose() {
    _transformation.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Column(
      children: [
        ListTile(
          title: Text(widget.title),
          trailing: Wrap(
            children: [
              IconButton(
                tooltip: 'تكبير',
                onPressed: () =>
                    _transformation.value = _transformation.value..scale(1.2),
                icon: const Icon(Icons.zoom_in),
              ),
              IconButton(
                tooltip: 'تصغير',
                onPressed: () =>
                    _transformation.value = _transformation.value..scale(0.8),
                icon: const Icon(Icons.zoom_out),
              ),
              IconButton(
                tooltip: 'تدوير',
                onPressed: () => setState(() => _rotation += 1.5708),
                icon: const Icon(Icons.rotate_right),
              ),
              IconButton(
                tooltip: 'إعادة الضبط',
                onPressed: () => setState(() {
                  _rotation = 0;
                  _transformation.value = Matrix4.identity();
                }),
                icon: const Icon(Icons.restart_alt),
              ),
            ],
          ),
        ),
        SizedBox(
          height: 450,
          child: widget.bytes == null
              ? const Center(child: CircularProgressIndicator())
              : InteractiveViewer(
                  transformationController: _transformation,
                  minScale: 0.5,
                  maxScale: 8,
                  child: Center(
                    child: Transform.rotate(
                      angle: _rotation,
                      child: Image.memory(
                        widget.bytes!,
                        fit: BoxFit.contain,
                        gaplessPlayback: false,
                      ),
                    ),
                  ),
                ),
        ),
      ],
    ),
  );
}

class _TrendPainter extends CustomPainter {
  const _TrendPainter(this.values, this.color, this.gridColor);

  final List<double> values;
  final Color color;
  final Color gridColor;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.isEmpty) return;
    final maxValue = values.reduce((a, b) => a > b ? a : b);
    final scale = maxValue <= 0 ? 1 : maxValue;
    final grid = Paint()
      ..color = gridColor
      ..strokeWidth = 1;
    for (var row = 1; row < 4; row++) {
      final y = size.height * row / 4;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    final line = Paint()
      ..color = color
      ..strokeWidth = 2.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final points = <Offset>[];
    for (var index = 0; index < values.length; index++) {
      final x = values.length == 1
          ? size.width / 2
          : size.width * index / (values.length - 1);
      final y = size.height - (values[index] / scale * (size.height - 12)) - 6;
      points.add(Offset(x, y));
    }
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (final point in points.skip(1)) {
      path.lineTo(point.dx, point.dy);
    }
    canvas.drawPath(path, line);
    final dot = Paint()..color = color;
    for (final point in points) {
      canvas.drawCircle(point, 2.5, dot);
    }
  }

  @override
  bool shouldRepaint(covariant _TrendPainter oldDelegate) =>
      oldDelegate.values != values ||
      oldDelegate.color != color ||
      oldDelegate.gridColor != gridColor;
}
