// عناصر مشتركة للوحة: بطاقات إحصاء + أقسام + رسائل + ترجمة أخطاء.
import 'package:flutter/material.dart';

import 'api.dart';

const _snackErr = Color(0xFF7F1D1D);
const _snackOk = Color(0xFF14532D);

class StatCard extends StatelessWidget {
  const StatCard({
    super.key,
    required this.title,
    required this.value,
    required this.color,
    required this.icon,
  });

  final String title;
  final String value;
  final Color color;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(children: [
              Icon(icon, color: color, size: 18),
              const SizedBox(width: 6),
              Text(title,
                  style: TextStyle(color: color, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 6),
            Text(value,
                style: const TextStyle(
                    fontSize: 26, fontWeight: FontWeight.w800)),
          ],
        ),
      ),
    );
  }
}

class SectionCard extends StatelessWidget {
  const SectionCard(
      this.title, {super.key, required this.child, this.trailing, this.icon});

  final String title;
  final Widget child;
  final Widget? trailing;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              if (icon != null) ...[
                Icon(icon, size: 20),
                const SizedBox(width: 6),
              ],
              Text(title,
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.w700)),
              const Spacer(),
              if (trailing != null) trailing!,
            ]),
            const Divider(height: 22),
            child,
          ],
        ),
      ),
    );
  }
}

/// رسالة عابرة إرشادية أسفل الشاشة.
void toast(BuildContext context, String msg, {bool error = false}) {
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
    content: Text(msg, textDirection: TextDirection.rtl,
        style: const TextStyle(color: Colors.white)),
    backgroundColor: error ? _snackErr : _snackOk,
    behavior: SnackBarBehavior.floating,
  ));
}

/// ترجمة أكواد الأخطاء إلى العربية بطيبة.
String errMsg(Object e) {
  if (e is ApiException) {
    switch (e.code) {
      case 'network_error':
        return 'تعذّر الوصول للخادم — تحقق من الاتصال';
      case 'unauthorized':
        return 'التوكن غير مقبول — أعد الاتصال';
      case 'bot_not_ready':
        return 'البوت قيد التشغيل — أعِد المحاولة بعد لحظات';
      case 'reason_required':
        return 'سبب الرفض الإلزامي مفقود';
      case 'text_length':
        return 'النص قصيراً جداً أو طويلاً جداً';
      case 'must_confirm_seq':
        return 'التأكيد لا يطابق الرقم التسلسلي';
      case 'empty':
        return 'لا بيانات للتصدير بعد';
      case 'too_many_users':
        return 'عدد المبلّغين يتجاوز سقف التعميم (300)';
      case 'bad_query':
        return 'معطيات البحث غير مقبولة';
      case 'not_found':
        return 'غير موجود (قد يكون محذوفاً)';
      case 'api_token_not_configured':
        return 'سر الخادم غير مضبوط — راجع الإعداد';
      default:
        return e.code;
    }
  }
  return 'خطأ غير متوقع';
}

/// حوار تأكيد عام يعيد true إن وافق المستخدم.
Future<bool> confirmDialog(
    BuildContext context, String title, String body,
    {String okLabel = 'تنفيذ', bool danger = false}) async {
  final res = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: Text(body, textDirection: TextDirection.rtl),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('إلغاء')),
        FilledButton(
          style: danger
              ? FilledButton.styleFrom(backgroundColor: Colors.red.shade700)
              : null,
          onPressed: () => Navigator.of(ctx).pop(true),
          child: Text(okLabel),
        ),
      ],
    ),
  );
  return res == true;
}

class EmptyView extends StatelessWidget {
  const EmptyView(this.msg, {super.key, this.icon = Icons.inbox_outlined});

  final String msg;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(30),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 44, color: Colors.white24),
          const SizedBox(height: 10),
          Text(msg, textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white38)),
        ]),
      ),
    );
  }
}

class ErrorView extends StatelessWidget {
  const ErrorView(this.err, this.onRetry, {super.key});

  final String err;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(30),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.cloud_off, size: 44, color: Colors.redAccent),
          const SizedBox(height: 10),
          Text(err, textAlign: TextAlign.center),
          TextButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh),
            label: const Text('إعادة المحاولة'),
          ),
        ]),
      ),
    );
  }
}

/// شارة حالة مصغرة (نشط/محجوب… إلخ).
class StateBadge extends StatelessWidget {
  const StateBadge(this.text,
      {super.key, this.color = const Color(0xFF3FA66B), this.outline = false});

  final String text;
  final Color color;
  final bool outline;

  @override
  Widget build(BuildContext context) {
    final c = outline ? Colors.white24 : color;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: c.withOpacity(outline ? 0 : 0.16),
        border: Border.all(color: c),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(text,
          style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w600)),
    );
  }
}
