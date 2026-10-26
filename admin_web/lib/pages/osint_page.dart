import 'dart:async';
import 'dart:html' as html;

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class OsintPage extends StatefulWidget {
  const OsintPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<OsintPage> createState() => _OsintPageState();
}

class _OsintPageState extends State<OsintPage> {
  final TextEditingController _searchText = TextEditingController();
  final TextEditingController _sourceUrl = TextEditingController();
  final TextEditingController _sourceTitle = TextEditingController();
  final TextEditingController _sourceNotes = TextEditingController();
  List<Map<String, dynamic>> _reports = [];
  List<Map<String, dynamic>> _sources = [];
  int? _reportId;
  String _sourceLevel = 'unverified';
  bool _allowExternalSearch = false;
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadReports();
  }

  @override
  void dispose() {
    _searchText.dispose();
    _sourceUrl.dispose();
    _sourceTitle.dispose();
    _sourceNotes.dispose();
    super.dispose();
  }

  Future<void> _loadReports() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.api.get('/api/admin/reports', {
        'size': '100',
        if (_searchText.text.trim().isNotEmpty) 'q': _searchText.text.trim(),
      });
      if (!mounted) return;
      final rows = (result['items'] as List? ?? const [])
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      setState(() {
        _reports = rows;
        if (_reportId != null &&
            !rows.any((item) => (item['id'] as num?)?.toInt() == _reportId)) {
          _reportId = null;
          _sources = [];
        }
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

  Future<void> _selectReport(int? id) async {
    setState(() {
      _reportId = id;
      _sources = [];
    });
    await _loadSources();
  }

  Future<void> _loadSources() async {
    try {
      final result = await widget.api.get('/api/admin/osint/sources', {
        if (_reportId != null) 'report_id': '$_reportId',
        'limit': '100',
      });
      if (!mounted) return;
      setState(() {
        _sources = (result['items'] as List? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
      });
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Map<String, dynamic>? get _selectedReport {
    for (final report in _reports) {
      if ((report['id'] as num?)?.toInt() == _reportId) return report;
    }
    return null;
  }

  void _insertSelectedReportText() {
    final report = _selectedReport;
    if (report == null) return;
    final sequence = report['seq']?.toString() ?? '';
    final sourceUrl = report['post_link']?.toString() ?? '';
    _searchText.text = [
      sequence,
      sourceUrl,
    ].where((value) => value.trim().isNotEmpty).join(' ');
    setState(() => _allowExternalSearch = false);
  }

  void _openSearch(String host) {
    final query = _searchText.text.trim();
    if (!_allowExternalSearch || query.isEmpty) {
      toast(
        context,
        'راجع نص البحث وفعّل الإذن قبل إرساله إلى موقع خارجي.',
        error: true,
      );
      return;
    }
    final url = Uri.https(host, '/search', {'q': query});
    html.window.open(url.toString(), '_blank');
  }

  Future<void> _saveSource() async {
    final title = _sourceTitle.text.trim();
    final url = _sourceUrl.text.trim();
    final notes = _sourceNotes.text.trim();
    if (title.length < 2 || url.isEmpty) {
      toast(context, 'أدخل عنوان المصدر ورابطه.', error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      await widget.api.post('/api/admin/osint/sources', {
        'report_id': _reportId,
        'url': url,
        'title': title,
        'notes': notes,
        'source_level': _sourceLevel,
      });
      if (!mounted) return;
      _sourceTitle.clear();
      _sourceUrl.clear();
      _sourceNotes.clear();
      toast(context, 'حُفظ المصدر مع وقت التوثيق.');
      await _loadSources();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _deleteSource(int id) async {
    final confirmed = await confirmDialog(
      context,
      'حذف مرجع',
      'سيُحذف المرجع من سجل هذا التحقيق.',
      okLabel: 'حذف',
      danger: true,
    );
    if (!confirmed) return;
    try {
      await widget.api.del('/api/admin/osint/sources/$id', {});
      if (mounted) await _loadSources();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  String _levelLabel(String level) => switch (level) {
    'primary' => 'مصدر أولي',
    'secondary' => 'مصدر ثانوي',
    _ => 'غير متحقق',
  };

  String _formatTime(Object? raw) {
    final value = DateTime.tryParse(raw?.toString() ?? '')?.toLocal();
    if (value == null) return 'وقت غير معروف';
    return '${value.year}-${value.month.toString().padLeft(2, '0')}-'
        '${value.day.toString().padLeft(2, '0')} '
        '${value.hour.toString().padLeft(2, '0')}:'
        '${value.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final selected = _selectedReport;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        SectionCard(
          'مساحة التحقق من المصادر العامة',
          icon: Icons.manage_search_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'اختر بلاغًا للاطلاع على بياناته ومرفقاته المسجلة. لا يُرسل أي نص '
                'أو مرفق إلى موقع خارجي تلقائيًا.',
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _searchText,
                      decoration: const InputDecoration(
                        labelText: 'البحث عن بلاغ',
                        prefixIcon: Icon(Icons.search),
                      ),
                      onSubmitted: (_) => _loadReports(),
                    ),
                  ),
                  IconButton(
                    tooltip: 'تحديث البلاغات',
                    onPressed: _loadReports,
                    icon: const Icon(Icons.refresh),
                  ),
                ],
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ),
              if (_loading)
                const Padding(
                  padding: EdgeInsets.only(top: 10),
                  child: LinearProgressIndicator(),
                )
              else
                DropdownButtonFormField<int>(
                  key: ValueKey(_reportId),
                  initialValue: _reportId,
                  decoration: const InputDecoration(
                    labelText: 'البلاغ المرتبط',
                  ),
                  items: [
                    const DropdownMenuItem<int>(
                      value: null,
                      child: Text('تحقيق عام غير مرتبط ببلاغ'),
                    ),
                    for (final report in _reports)
                      DropdownMenuItem<int>(
                        value: (report['id'] as num).toInt(),
                        child: Text(
                          '${report['seq'] ?? report['id']} · '
                          '${report['full_name'] ?? 'مبلّغ'}',
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                  onChanged: _selectReport,
                ),
              if (selected != null) ...[
                const SizedBox(height: 10),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Theme.of(
                      context,
                    ).colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(
                        '${selected['seq'] ?? 'بلاغ'} · ${selected['status'] ?? ''}',
                        style: const TextStyle(fontWeight: FontWeight.bold),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        selected['description']?.toString() ??
                            'لا يوجد وصف محفوظ.',
                      ),
                      if ((selected['post_link']?.toString() ?? '').isNotEmpty)
                        SelectableText(
                          'الرابط المرفق: ${selected['post_link']}',
                        ),
                      Text(
                        selected['has_photo'] == true
                            ? 'يتضمن البلاغ صورة؛ لم تُرفع إلى أي خدمة خارجية.'
                            : 'لا توجد صورة متاحة في سجل البلاغ.',
                      ),
                      Align(
                        alignment: AlignmentDirectional.centerEnd,
                        child: TextButton.icon(
                          onPressed: _insertSelectedReportText,
                          icon: const Icon(Icons.add_to_photos_outlined),
                          label: const Text(
                            'إضافة رقم البلاغ ورابطه إلى البحث',
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'بحث خارجي بموافقة صريحة',
          icon: Icons.public,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                minLines: 1,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: 'نص البحث الذي سيُرسل كما هو',
                  helperText: 'لا تضع بيانات شخصية أو معلومات سرية.',
                ),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _allowExternalSearch,
                onChanged: (value) =>
                    setState(() => _allowExternalSearch = value == true),
                title: const Text(
                  'أوافق على إرسال نص البحث الظاهر أعلاه إلى محرك البحث الخارجي.',
                ),
                controlAffinity: ListTileControlAffinity.leading,
              ),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  OutlinedButton.icon(
                    onPressed: _allowExternalSearch
                        ? () => _openSearch('www.google.com')
                        : null,
                    icon: const Icon(Icons.search),
                    label: const Text('بحث في غوغل'),
                  ),
                  OutlinedButton.icon(
                    onPressed: _allowExternalSearch
                        ? () => _openSearch('www.bing.com')
                        : null,
                    icon: const Icon(Icons.travel_explore),
                    label: const Text('بحث في بينغ'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              const Text(
                'هذه الواجهة لا تجلب نتائج البحث ولا تصنف الأشخاص. تحقق من '
                'المصدر والتاريخ والسياق يدويًا قبل الاستنتاج أو اتخاذ إجراء.',
                style: TextStyle(fontSize: 12),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'توثيق المصادر والنتائج',
          icon: Icons.fact_check_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _sourceTitle,
                maxLength: 180,
                decoration: const InputDecoration(labelText: 'عنوان المصدر'),
              ),
              TextField(
                controller: _sourceUrl,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'رابط عام',
                  helperText: 'تُحذف معاملات الرابط وأجزاؤه اللاحقة عند الحفظ.',
                ),
              ),
              DropdownButtonFormField<String>(
                key: ValueKey(_sourceLevel),
                initialValue: _sourceLevel,
                decoration: const InputDecoration(labelText: 'نوع المصدر'),
                items: const [
                  DropdownMenuItem(value: 'primary', child: Text('مصدر أولي')),
                  DropdownMenuItem(
                    value: 'secondary',
                    child: Text('مصدر ثانوي'),
                  ),
                  DropdownMenuItem(
                    value: 'unverified',
                    child: Text('غير متحقق'),
                  ),
                ],
                onChanged: (value) =>
                    setState(() => _sourceLevel = value ?? 'unverified'),
              ),
              TextField(
                controller: _sourceNotes,
                maxLength: 2000,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'ملاحظات التحقق',
                  alignLabelWithHint: true,
                ),
              ),
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: FilledButton.icon(
                  onPressed: _saving ? null : _saveSource,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('حفظ المرجع'),
                ),
              ),
              const Divider(),
              if (_sources.isEmpty)
                const Text('لا توجد مراجع موثقة لهذا التحقيق.')
              else
                for (final source in _sources)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.link_outlined),
                    title: Text(source['title']?.toString() ?? ''),
                    subtitle: Text(
                      '${_levelLabel(source['source_level']?.toString() ?? '')}'
                      ' · ${_formatTime(source['accessed_at'])}\n'
                      '${source['url'] ?? ''}\n${source['notes'] ?? ''}',
                      maxLines: 5,
                      overflow: TextOverflow.ellipsis,
                    ),
                    isThreeLine: true,
                    onTap: () => html.window.open(
                      source['url']?.toString() ?? '',
                      '_blank',
                    ),
                    trailing: IconButton(
                      tooltip: 'حذف المرجع',
                      onPressed: () =>
                          _deleteSource((source['id'] as num).toInt()),
                      icon: const Icon(Icons.delete_outline),
                    ),
                  ),
            ],
          ),
        ),
      ],
    );
  }
}
