import 'dart:async';
import 'package:flutter/material.dart';
import '../api.dart';
import '../widgets.dart';

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
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تم حفظ التعديل')),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('فشل الحفظ: ${res['error']}')),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تعذر حفظ التعديل')),
      );
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
                      final controller = TextEditingController(text: item['content']?.toString() ?? '');
                      return Card(
                        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                        child: ListTile(
                          title: Text(item['label']?.toString() ?? item['key']?.toString() ?? ''),
                          subtitle: Text(item['category']?.toString() ?? 'user_facing'),
                        ),
                      );
                    },
                  ),
      ),
    );
  }
}
