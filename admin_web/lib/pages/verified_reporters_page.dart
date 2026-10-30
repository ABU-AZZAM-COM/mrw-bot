import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'overview_page.dart' show fmt;

class VerifiedReportersPage extends StatefulWidget {
  const VerifiedReportersPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<VerifiedReportersPage> createState() => _VerifiedReportersPageState();
}

class _VerifiedReportersPageState extends State<VerifiedReportersPage> {
  List<Map<String, dynamic>> _items = [];
  final Map<String, Future<Uint8List>> _documentLoads = {};
  bool _loading = true;
  String? _error;
  int? _busyId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _documentLoads.clear();
    });
    try {
      final result = await widget.api.get(
        '/api/admin/verified-reporters',
        const {'status': 'pending', 'limit': '100'},
      );
      if (!mounted) return;
      setState(() {
        _items = (result['items'] as List<dynamic>? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
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

  Future<void> _decide(Map<String, dynamic> item, String decision) async {
    final approved = decision == 'verified';
    final confirmed = await confirmDialog(
      context,
      approved ? 'اعتماد المراسل' : 'رفض طلب التوثيق',
      approved
          ? 'هل راجعت الوجه والوجهين الأمامي والخلفي؟ اعتماد الحساب لا يعني اعتماد الشكاوى التي يرسلها.'
          : 'سيُعلَم المستخدم بقرار الرفض دون إرفاق بيانات الهوية.',
      okLabel: approved ? 'اعتماد' : 'رفض',
      danger: !approved,
    );
    if (!confirmed || !mounted) return;
    final id = int.tryParse('${item['id'] ?? ''}');
    if (id == null) return;
    setState(() => _busyId = id);
    try {
      await widget.api.post('/api/admin/verified-reporters/$id/decision', {
        'decision': decision,
      });
      if (!mounted) return;
      toast(context, approved ? 'تم اعتماد المراسل' : 'تم رفض طلب التوثيق');
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _busyId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
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
              const Icon(Icons.verified_user_outlined),
              const SizedBox(width: 10),
              const Expanded(
                child: Text(
                  'مراجعة توثيق المراسلين',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              IconButton(
                tooltip: 'تحديث',
                onPressed: _loading ? null : _load,
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
        const Padding(
          padding: EdgeInsets.all(12),
          child: Text(
            'وثائق الهوية بيانات شديدة الحساسية. لا تحفظها أو تنسخها خارج النظام. '
            'بعد القرار تُحذف تلقائيًا بعد 30 يومًا؛ لا تُحذف أثناء انتظار المراجعة.',
          ),
        ),
        Expanded(child: _body()),
      ],
    );
  }

  Widget _body() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) return ErrorView(_error!, _load);
    if (_items.isEmpty) return const EmptyView('لا توجد طلبات توثيق معلّقة');
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: _items.length,
      itemBuilder: (context, index) => _requestCard(_items[index]),
    );
  }

  Widget _requestCard(Map<String, dynamic> item) {
    final id = int.tryParse('${item['id'] ?? ''}');
    final hasDocuments =
        item['has_documents'] == 1 || item['has_documents'] == true;
    return Card(
      margin: const EdgeInsets.only(bottom: 14),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              '${item['full_name'] ?? 'مستخدم'} · ${item['user_id'] ?? '-'}',
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            Text(
              '@${item['username'] ?? '—'} · قُدّم ${fmt('${item['submitted_at'] ?? ''}')}',
            ),
            const SizedBox(height: 10),
            if (hasDocuments && id != null)
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  _document(id, 'selfie', 'صورة الوجه'),
                  _document(id, 'id_front', 'الهوية — أمام'),
                  _document(id, 'id_back', 'الهوية — خلف'),
                ],
              )
            else
              const Text(
                'صور الطلب غير متاحة (قد تكون أزيلت من نسخة احتياطية قديمة). '
                'لا تعتمد هذا الطلب دون الوثائق.',
              ),
            const SizedBox(height: 12),
            if (hasDocuments && id != null)
              Wrap(
                alignment: WrapAlignment.end,
                spacing: 8,
                children: [
                  OutlinedButton(
                    onPressed: _busyId == id
                        ? null
                        : () => _decide(item, 'rejected'),
                    child: const Text('رفض'),
                  ),
                  FilledButton.icon(
                    onPressed: _busyId == id
                        ? null
                        : () => _decide(item, 'verified'),
                    icon: const Icon(Icons.verified_outlined),
                    label: const Text('اعتماد الهوية'),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _document(int requestId, String kind, String title) {
    final key = '$requestId/$kind';
    final future = _documentLoads.putIfAbsent(
      key,
      () => widget.api.getBytes(
        '/api/admin/verified-reporters/$requestId/documents/$kind',
      ),
    );
    return SizedBox(
      width: 250,
      child: Column(
        children: [
          Text(title),
          const SizedBox(height: 6),
          SizedBox(
            height: 190,
            width: 250,
            child: FutureBuilder<Uint8List>(
              future: future,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return const Center(child: Text('تعذر تحميل الوثيقة'));
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                return Image.memory(
                  snapshot.data!,
                  fit: BoxFit.contain,
                  gaplessPlayback: false,
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
