import 'package:flutter/material.dart';

import '../api.dart';

class BotTextManagementPage extends StatefulWidget {
  const BotTextManagementPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotTextManagementPage> createState() => _BotTextManagementPageState();
}

class _BotTextManagementPageState extends State<BotTextManagementPage> {
  final _searchController = TextEditingController();
  final _contentController = TextEditingController();
  List<Map<String, dynamic>> _texts = [];
  String? _selectedKey;
  bool _loading = true;
  bool _saving = false;
  String? _err;

  Map<String, dynamic>? get _selected {
    for (final item in _texts) {
      if (item['key'] == _selectedKey) return item;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _searchController.addListener(_refresh);
    _load();
  }

  @override
  void dispose() {
    _searchController
      ..removeListener(_refresh)
      ..dispose();
    _contentController.dispose();
    super.dispose();
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _err = null;
    });
    try {
      final res = await widget.api.get('/admin_api/texts');
      if (!mounted) return;
      if (res['ok'] == true) {
        final texts =
            (res['texts'] as List? ?? const [])
                .whereType<Map>()
                .map((item) => Map<String, dynamic>.from(item))
                .toList()
              ..sort(
                (a, b) => (a['key'] ?? '').toString().compareTo(
                  (b['key'] ?? '').toString(),
                ),
              );
        setState(() {
          _texts = texts;
          if (_selectedKey == null ||
              !texts.any((item) => item['key'] == _selectedKey)) {
            _selectedKey = null;
            _contentController.clear();
          } else {
            _contentController.text = _selected?['content']?.toString() ?? '';
          }
        });
      } else {
        setState(() => _err = res['error']?.toString() ?? 'خطأ في التحميل');
      }
    } catch (_) {
      if (mounted) setState(() => _err = 'تعذر الاتصال بالخادم');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    final item = _selected;
    if (item == null) return;
    setState(() => _saving = true);
    try {
      final res = await widget.api.post('/admin_api/texts/update', {
        'key': item['key'],
        'content': _contentController.text,
        'label': item['label'],
        'category': item['category'] ?? 'user_facing',
      });
      if (!mounted) return;
      if (res['ok'] == true) {
        final applied = res['applied'] == true;
        await _load();
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              applied
                  ? 'تم الحفظ والتطبيق على البوت مباشرة'
                  : 'تم الحفظ؛ سيتطبق النص عند إعادة تشغيل البوت',
            ),
          ),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('فشل الحفظ: ${res['error'] ?? 'خطأ غير معروف'}'),
          ),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('تعذر حفظ التعديل')));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  _TextTreeNode _buildTree() {
    final roots = <String, _TextTreeNode>{};
    for (final item in _texts) {
      final key = (item['key'] ?? '').toString();
      if (key.isEmpty) continue;
      final segments = key.startsWith('bot.')
          ? key.substring(4).split('.')
          : <String>['نصوص مستقلة', key];
      var siblings = roots;
      _TextTreeNode? current;
      var path = '';
      for (final segment in segments) {
        path = path.isEmpty ? segment : '$path.$segment';
        current = siblings.putIfAbsent(
          segment,
          () => _TextTreeNode(segment, path: path),
        );
        siblings = current.children;
      }
      current?.item = item;
    }
    return _TextTreeNode('root', path: 'root', children: roots);
  }

  _TextTreeNode? _filtered(_TextTreeNode node, String query) {
    if (query.isEmpty) return node;
    final titleMatches = '${_segmentTitle(node.segment)} ${node.segment}'
        .toLowerCase()
        .contains(query);
    if (titleMatches) return node;
    final children = <String, _TextTreeNode>{};
    for (final entry in node.children.entries) {
      final result = _filtered(entry.value, query);
      if (result != null) children[entry.key] = result;
    }
    final item = node.item;
    final itemMatches =
        item != null &&
        '${item['key']} ${item['label']} ${item['content']}'
            .toLowerCase()
            .contains(query);
    if (itemMatches || children.isNotEmpty) {
      return _TextTreeNode(
        node.segment,
        path: node.path,
        children: children,
        item: itemMatches ? item : null,
      );
    }
    return null;
  }

  void _select(Map<String, dynamic> item) {
    setState(() {
      _selectedKey = item['key']?.toString();
      _contentController.text = item['content']?.toString() ?? '';
    });
  }

  String _segmentTitle(String segment) {
    const titles = {
      'INLINE_MESSAGES': 'رسائل وتفاعلات البوت',
      'CATEGORY_INTROS': 'مقدمات الأقسام',
      'BLOCK_MESSAGES': 'رسائل الحماية والإيقاف',
      'STATUS_EXPLANATIONS': 'شرح حالات البلاغ',
      'STATUS_EMOJI': 'رموز حالات البلاغ',
      'KEYBOARD_LABELS': 'تسميات الأزرار',
      'ADMIN_KEYBOARD_LABELS': 'تسميات أزرار الإدارة',
      'USER_MENU_ACTIONS': 'أزرار القائمة الرئيسية',
      'REPORT_CATEGORIES': 'تصنيفات البلاغ',
      'SOCIAL_PLATFORM_LABELS': 'منصات التواصل',
      'FILTER_LABELS': 'تسميات المرشحات',
      'STATUS_LABELS': 'تسميات الحالات',
      'control': 'لوحات الرد',
      'keyboards': 'لوحات وأزرار البوت',
      'texts': 'النصوص والرسائل',
      'نصوص مستقلة': 'نصوص مستقلة',
    };
    return titles[segment] ?? segment.replaceAll('_', ' ');
  }

  Widget _treeNode(_TextTreeNode node, {bool root = false}) {
    final children = node.children.values.toList()
      ..sort((a, b) => a.segment.compareTo(b.segment));
    if (children.isEmpty && node.item != null) {
      final item = node.item!;
      final selected = item['key'] == _selectedKey;
      final label = (item['label'] ?? '').toString();
      return ListTile(
        dense: true,
        selected: selected,
        leading: Icon(
          selected ? Icons.edit_note : Icons.notes_outlined,
          size: 20,
        ),
        title: Text(
          label.isEmpty ? _segmentTitle(node.segment) : label,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Text(
          item['key']?.toString() ?? '',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          textDirection: TextDirection.ltr,
        ),
        onTap: () => _select(item),
      );
    }

    return ExpansionTile(
      key: PageStorageKey<String>(
        'bot-text-tree-${node.path}-${_searchController.text.trim()}',
      ),
      initiallyExpanded: _searchController.text.trim().isNotEmpty,
      leading: Icon(
        root ? Icons.folder_outlined : Icons.folder_open_outlined,
        size: 20,
      ),
      title: Text(
        _segmentTitle(node.segment),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: node.item == null
          ? Text('${_leafCount(node)} نص')
          : Text(node.item!['key']?.toString() ?? ''),
      children: [
        if (node.item != null)
          _treeNode(
            _TextTreeNode(node.segment, path: node.path, item: node.item),
          ),
        ...children.map((child) => _treeNode(child)),
      ],
    );
  }

  int _leafCount(_TextTreeNode node) {
    var count = node.item == null ? 0 : 1;
    for (final child in node.children.values) {
      count += _leafCount(child);
    }
    return count;
  }

  Widget _editor() {
    final item = _selected;
    if (item == null) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.account_tree_outlined, size: 44),
            SizedBox(height: 12),
            Text('اختر نصًا من الشجرة لعرضه وتعديله'),
          ],
        ),
      );
    }
    return Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              item['label']?.toString() ?? item['key'].toString(),
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 6),
            SelectableText(
              item['key']?.toString() ?? '',
              textDirection: TextDirection.ltr,
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 16),
            Expanded(
              child: TextField(
                controller: _contentController,
                expands: true,
                minLines: null,
                maxLines: null,
                textAlignVertical: TextAlignVertical.top,
                decoration: const InputDecoration(
                  border: OutlineInputBorder(),
                  hintText: 'اكتب النص المراد أن يظهر في البوت...',
                  labelText: 'محتوى النص',
                  alignLabelWithHint: true,
                ),
              ),
            ),
            const SizedBox(height: 12),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.save_outlined),
                label: Text(_saving ? 'جارٍ الحفظ...' : 'حفظ وتطبيق على البوت'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final query = _searchController.text.trim().toLowerCase();
    final filtered = _filtered(_buildTree(), query);
    return Scaffold(
      appBar: AppBar(
        title: const Text('نصوص البوت'),
        actions: [
          IconButton(
            tooltip: 'تحديث النصوص',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _loading && _texts.isEmpty
          ? const Center(child: CircularProgressIndicator())
          : _err != null && _texts.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_err!),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: _load,
                    child: const Text('إعادة المحاولة'),
                  ),
                ],
              ),
            )
          : Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
                  child: TextField(
                    controller: _searchController,
                    decoration: InputDecoration(
                      prefixIcon: const Icon(Icons.search),
                      hintText: 'ابحث بالاسم أو المفتاح أو محتوى النص...',
                      suffixIcon: query.isEmpty
                          ? null
                          : IconButton(
                              onPressed: _searchController.clear,
                              icon: const Icon(Icons.close),
                            ),
                      border: const OutlineInputBorder(),
                    ),
                  ),
                ),
                if (_err != null)
                  MaterialBanner(
                    content: Text(_err!),
                    actions: [
                      TextButton(
                        onPressed: _load,
                        child: const Text('إعادة المحاولة'),
                      ),
                    ],
                  ),
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final tree = filtered == null
                          ? const Center(child: Text('لا توجد نتائج مطابقة'))
                          : ListView(
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              children: filtered.children.values
                                  .map((node) => _treeNode(node, root: true))
                                  .toList(),
                            );
                      if (constraints.maxWidth < 760) {
                        return Column(
                          children: [
                            SizedBox(height: 280, child: tree),
                            const Divider(height: 1),
                            Expanded(child: _editor()),
                          ],
                        );
                      }
                      return Row(
                        children: [
                          SizedBox(
                            width: 360,
                            child: Column(
                              children: [
                                Padding(
                                  padding: const EdgeInsets.fromLTRB(
                                    16,
                                    8,
                                    16,
                                    4,
                                  ),
                                  child: Align(
                                    alignment: AlignmentDirectional.centerStart,
                                    child: Text(
                                      '${_texts.length} نص — اختر مجموعة لفتح فروعها',
                                      style: Theme.of(
                                        context,
                                      ).textTheme.bodySmall,
                                    ),
                                  ),
                                ),
                                Expanded(child: tree),
                              ],
                            ),
                          ),
                          const VerticalDivider(width: 1),
                          Expanded(child: _editor()),
                        ],
                      );
                    },
                  ),
                ),
              ],
            ),
    );
  }
}

class _TextTreeNode {
  _TextTreeNode(
    this.segment, {
    required this.path,
    Map<String, _TextTreeNode>? children,
    this.item,
  }) : children = children ?? <String, _TextTreeNode>{};

  final String segment;
  final String path;
  final Map<String, _TextTreeNode> children;
  Map<String, dynamic>? item;
}
