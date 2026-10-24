import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'shared_dialogs.dart';

class OperationsPage extends StatefulWidget {
  const OperationsPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<OperationsPage> createState() => _OperationsPageState();
}

class _OperationsPageState extends State<OperationsPage> {
  Map<String, dynamic>? _ov;
  String? _err;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final ov = await widget.api.get('/api/admin/overview');
      if (!mounted) return;
      setState(() {
        _ov = ov;
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = errMsg(e));
    }
  }

  Future<void> _setEmergency(bool on) async {
    if (_busy) return;
    final ctx = context;
    final bool emergencyNow = _ov?['emergency'] == true;
    if (on == emergencyNow) {
      toast(ctx, on ? 'الإيقاف الطارئ مفعّل بالفعل' : 'الاستقبال شغّال بالفعل');
      return;
    }
    String? reason;
    if (on) {
      // سبب الإيقاف إلزامي — يُسجل في سجل التدقيق ويظهر للنظام
      reason = await requiredTextDialog(
        ctx,
        title: 'إيقاف طارئ — السبب إلزامي',
        hint: 'اكتب سبب إيقاف الاستقبال (مثال: صيانة / حالة طارئة)…',
        danger: true,
      );
      if (reason == null) return;
    } else {
      final ok = await confirmDialog(
        ctx,
        'استئناف الاستقبال',
        'إعادة تفعيل استقبال البلاغات من تلغرام؟',
        okLabel: 'استئناف',
      );
      if (!ok) return;
    }
    setState(() => _busy = true);
    try {
      await widget.api.post('/api/admin/system/emergency', {
        'on': on,
        'reason': reason,
      });
      if (!mounted) return;
      toast(
        context,
        on
            ? '🛑 الإيقاف الطارئ مفعّل — تُمنع البلاغات الجديدة'
            : '🟢 استؤنفت منظومة الاستقبال',
      );
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _backupNow() async {
    setState(() => _busy = true);
    try {
      final d = await widget.api.post('/api/admin/backup/now', {});
      if (!mounted) return;
      toast(
        context,
        d['uploaded'] == true
            ? 'رُفعت النسخة إلى المستودع السحابي ✅'
            : 'تمت المحاولة — مستودع النسخ غير مُهيأ على الخادم',
      );
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ov = _ov;
    if (_err != null && ov == null) {
      return ErrorView(_err!, _load);
    }
    if (ov == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final bool emergency = ov['emergency'] == true;
    final bool botReady = ov['bot_ready'] == true;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            StateBadge(
              botReady ? '🤖 البوت متصل ويعمل' : '🤖 البوت غير متصل حالياً',
              color: botReady
                  ? const Color(0xFF3FA66B)
                  : const Color(0xFFE06C75),
            ),
            const SizedBox(width: 8),
            IconButton(
              tooltip: 'تحديث',
              onPressed: _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: 10),
        SectionCard(
          'الإيقاف الطارئ — السيطرة الكامل',
          icon: Icons.emergency,
          trailing: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : null,
          child: SwitchListTile(
            activeColor: const Color(0xFFE06C75),
            contentPadding: EdgeInsets.zero,
            title: Text(
              emergency
                  ? 'الاستقبال متوقف — البلاغات الجديدة تُرفض'
                  : 'الاستقبال تعمل',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            subtitle: const Text(
              'يمنع فورًا بدء البلاغات الجديدة من تلغرام — لا يقطع المحادثات الجارية',
            ),
            value: emergency,
            onChanged: _busy ? null : (v) => _setEmergency(v == true),
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'النسخ الاحتياطي والتصدير',
          icon: Icons.cloud_download_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FilledButton.tonalIcon(
                onPressed: () => widget.api.download('/api/admin/export/xlsx'),
                icon: const Icon(Icons.table_chart),
                label: const Text('تصدير اكسل (xlsx) — كل البلاغات'),
              ),
              const SizedBox(height: 6),
              OutlinedButton.icon(
                onPressed: () => widget.api.download('/api/admin/export/txt'),
                icon: const Icon(Icons.description_outlined),
                label: const Text('تصدير نصي (txt) — كل البلاغات'),
              ),
              const Divider(height: 22),
              ListTile(
                leading: const Icon(Icons.storage_outlined),
                title: const Text('تنزيل نسخة قاعدة البيانات'),
                subtitle: const Text(
                  'ملف SQLite كامل — يُسجَّل في سجل التدقيق',
                  style: TextStyle(fontSize: 12),
                ),
                trailing: const Icon(Icons.download),
                onTap: () => widget.api.download('/api/admin/backup/download'),
              ),
              ListTile(
                leading: const Icon(Icons.backup_outlined),
                title: const Text('رفع نسخة فورية إلى مستودع الحفظ'),
                subtitle: const Text(
                  'يُرفع لقطة متسقة إلى مستودع النسخ السحابي الخاص',
                  style: TextStyle(fontSize: 12),
                ),
                trailing: const Icon(Icons.cloud_upload_outlined),
                onTap: _busy ? null : _backupNow,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ==================================================================
// 5) الرسائل: تعميم لكل من بلغ + قوالب الردود الجاهزة
// ==================================================================
