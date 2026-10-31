import 'dart:async';
import 'package:flutter/material.dart';
import '../api.dart';

class BotTextManagementPage extends StatefulWidget {
  const BotTextManagementPage({super.key, required this.api});
  final AdminApi api;
  @override
  State<BotTextManagementPage> createState() => _BotTextManagementPageState();
}

class _BotTextManagementPageState extends State<BotTextManagementPage> {
  List<dynamic> _texts = [];
  bool _loading = true;
  bool _saving = false;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final res = await widget.api.get('/admin_api/texts');
      if (res['ok'] == true) {
        setState(() => _texts = List<dynamic>.from(res['texts'] ?? []));
      } else {
        setState(() => _err = res['error']?.toString() ?? 'خطأ في التحميل');
      }
    } catch (e) {
      setState(() => _err = 'تعذر الاتصال بالخادم');
    } finally {
      setState(() => _loading = false);
    }
  }

  Future<void> _save(dynamic item, String content) async {
    setState(() => _saving = true);
    try {
      final res = await widget.api.post('/admin_api/texts/update', {
        'key': item['key'],
        'content': content,
        'label': item['label'],
        'category': item['category'] ?? 'user_facing',
      });
      if (res['ok'] == true) {
        await _load();
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('تم حفظ التعديل')),
          );
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('فشل الحفظ: ${res['error']}')),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر حفظ التعديل')),
        );
      }
    } finally {
      setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('إدارة النصوص')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _texts.isEmpty
                ? const Center(child: Text('لا توجد نصوص'))
                : ListView.builder(
                    itemCount: _texts.length,
                    itemBuilder: (context, i) {
                      final item = _texts[i];
                      final ctrl = TextEditingController(text: item['content']?.toString() ?? '');
                      return Card(
                        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                item['label']?.toString() ?? item['key']?.toString() ?? '',
                                style: Theme.of(context).textTheme.titleMedium,
                              ),
                              Text(
                                '${item['key']}  |  ${item['category']}',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                              const SizedBox(height: 8),
                              TextField(
                                controller: ctrl,
                                maxLines: 8,
                                decoration: const InputDecoration(
                                  border: OutlineInputBorder(),
                                  hintText: 'نص قابل للتعديل من اللوحة...',
                                  labelText: 'المحتوى',
                                ),
                              ),
                              const SizedBox(height: 8),
                              ElevatedButton.icon(
                                onPressed: _saving ? null : () => _save(item, ctrl.text),
                                icon: const Icon(Icons.save),
                                label: const Text('حفظ التعديل'),
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
      ),
    );
  }
}
