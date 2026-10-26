// عناصر مشتركة للوحة: بطاقات إحصاء + أقسام + رسائل + ترجمة أخطاء.
import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';

const _snackErr = Color(0xFF7F1D1D);
const _snackOk = Color(0xFF14532D);

class StatCard extends StatefulWidget {
  const StatCard({
    super.key,
    required this.title,
    required this.value,
    required this.color,
    required this.icon,
    this.onTap,
  });

  final String title;
  final String value;
  final Color color;
  final IconData icon;
  final VoidCallback? onTap;

  @override
  State<StatCard> createState() => _StatCardState();
}

class _StatCardState extends State<StatCard> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return MouseRegion(
      cursor: widget.onTap == null
          ? SystemMouseCursors.basic
          : SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedScale(
          scale: _hovered ? 1.035 : 1,
          duration: const Duration(milliseconds: 160),
          curve: Curves.easeOut,
          child: Card(
            elevation: _hovered ? 7 : 2,
            color: theme.cardColor,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: BorderSide(color: theme.dividerColor),
            ),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Icon(widget.icon, color: widget.color, size: 18),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          widget.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: widget.color,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    widget.value,
                    style: const TextStyle(
                      fontSize: 26,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class SectionCard extends StatelessWidget {
  const SectionCard(
    this.title, {
    super.key,
    required this.child,
    this.trailing,
    this.icon,
  });

  final String title;
  final Widget child;
  final Widget? trailing;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      color: theme.cardColor,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: theme.dividerColor),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (icon != null) ...[
                  Icon(icon, size: 20),
                  const SizedBox(width: 6),
                ],
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const Spacer(),
                if (trailing != null) trailing!,
              ],
            ),
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
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(
      content: Text(
        msg,
        textDirection: TextDirection.rtl,
        style: const TextStyle(color: Colors.white),
      ),
      backgroundColor: error ? _snackErr : _snackOk,
      behavior: SnackBarBehavior.floating,
    ),
  );
}

/// ترجمة أكواد الأخطاء إلى العربية بطيبة.
String errMsg(Object e) {
  if (e is ApiException) {
    switch (e.code) {
      case 'network_error':
        return 'تعذّر الاتصال بالخدمة. تحقق من عنوان API واتصال الإنترنت.';
      case 'bad_response':
        return 'استجابة الخدمة غير مفهومة. تحقق من أن عنوان API يشير إلى واجهة الإدارة.';
      case 'service_not_found':
        return 'المسار غير موجود على هذه الخدمة. قد تكون الخدمة متوقفة أو لا تستضيف API الإدارة.';
      case 'unauthorized':
        return 'التوكن غير مقبول — أعد الاتصال';
      case 'forbidden':
        return 'صلاحيات هذا الحساب لا تسمح بهذا الإجراء';
      case 'api_token_configuration_invalid':
        return 'إعداد رموز الإدارة غير صالح على الخادم';
      case 'bot_not_ready':
        return 'البوت قيد التشغيل — أعِد المحاولة بعد لحظات';
      case 'bad_bot_name':
        return 'اسم البوت مطلوب وبحد أقصى 64 حرفاً';
      case 'short_description_too_long':
        return 'النبذة القصيرة تتجاوز 120 حرفاً';
      case 'description_too_long':
        return 'الوصف الكامل يتجاوز 512 حرفاً';
      case 'bad_welcome_text':
        return 'رسالة البداية مطلوبة ويجب ألا تتجاوز 3500 حرف';
      case 'profile_photo_too_large':
        return 'حجم صورة الحساب يتجاوز 10 ميغابايت';
      case 'profile_photo_must_be_jpeg':
        return 'صورة الحساب يجب أن تكون بصيغة JPEG';
      case 'telegram_profile_method_unavailable':
        return 'إصدار واجهة تيليجرام لا يدعم تعديل بيانات الحساب المطلوبة';
      case 'telegram_photo_update_unavailable':
        return 'تحديث صورة الحساب غير مدعوم في إصدار مكتبة تيليجرام المثبت';
      case 'telegram_profile_update_rejected':
      case 'telegram_photo_update_rejected':
      case 'telegram_profile_update_failed':
        return 'رفضت تيليجرام التغيير. راجع البيانات وحاول مرة أخرى';
      case 'multipart_unavailable':
        return 'تعذر استقبال الملف على الخادم';
      case 'telegram_send_failed':
        return 'تعذر تسليم الرسالة عبر Telegram؛ قد يكون المستخدم حظر البوت';
      case 'reason_required':
        return 'سبب الرفض إلزامي — لم تُغيّر حالة البلاغ';
      case 'report_closed':
        return 'لا يمكن طلب معلومات إضافية عن بلاغ مغلق';
      case 'bad_priority':
      case 'setting_out_of_range':
        return 'القيمة المختارة خارج الحدود المسموح بها';
      case 'unknown_assignee':
        return 'المشرف المحدد غير موجود في إعدادات الخدمة';
      case 'due_at_timezone_required':
      case 'bad_due_at':
        return 'موعد المتابعة غير صالح';
      case 'note_length':
        return 'اكتب طلب معلومات من 3 إلى 2000 حرف';
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
        return 'لم يُضبط رمز الإدارة على الخدمة بعد';
      default:
        return e.code;
    }
  }
  return 'خطأ غير متوقع';
}

/// حوار تأكيد عام يعيد true إن وافق المستخدم.
Future<bool> confirmDialog(
  BuildContext context,
  String title,
  String body, {
  String okLabel = 'تنفيذ',
  bool danger = false,
}) async {
  final res = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: Text(body, textDirection: TextDirection.rtl),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(false),
          child: const Text('إلغاء'),
        ),
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
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(30),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 44, color: muted.withValues(alpha: 0.38)),
            const SizedBox(height: 10),
            Text(
              msg,
              textAlign: TextAlign.center,
              style: TextStyle(color: muted),
            ),
          ],
        ),
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
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 44, color: Colors.redAccent),
            const SizedBox(height: 10),
            Text(err, textAlign: TextAlign.center),
            TextButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: const Text('إعادة المحاولة'),
            ),
          ],
        ),
      ),
    );
  }
}

/// شارة حالة مصغرة (نشط/محجوب… إلخ).
class StateBadge extends StatelessWidget {
  const StateBadge(
    this.text, {
    super.key,
    this.color = const Color(0xFF3FA66B),
    this.outline = false,
  });

  final String text;
  final Color color;
  final bool outline;

  @override
  Widget build(BuildContext context) {
    final c = outline ? Theme.of(context).colorScheme.outline : color;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: c.withValues(alpha: outline ? 0 : 0.16),
        border: Border.all(color: c),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        text,
        style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}

class UserActivityIndicator extends StatefulWidget {
  const UserActivityIndicator({super.key, this.lastSeen});

  final String? lastSeen;

  @override
  State<UserActivityIndicator> createState() => _UserActivityIndicatorState();
}

class _UserActivityIndicatorState extends State<UserActivityIndicator> {
  Timer? _timer;
  DateTime _now = DateTime.now();

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() => _now = DateTime.now());
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final seen = DateTime.tryParse(widget.lastSeen ?? '')?.toLocal();
    final elapsed = seen == null ? null : _now.difference(seen);
    final color = elapsed == null || elapsed > const Duration(hours: 24)
        ? Colors.red
        : elapsed > const Duration(minutes: 2)
        ? Colors.amber
        : Colors.green;
    final label = elapsed == null
        ? 'لم يستلم البوت تحديثاً لهذا المستخدم'
        : elapsed > const Duration(hours: 24)
        ? 'لم يستلم البوت تحديثاً خلال آخر 24 ساعة'
        : elapsed > const Duration(minutes: 2)
        ? 'استلم البوت تحديثاً خلال آخر 24 ساعة'
        : 'استلم البوت تحديثاً خلال آخر دقيقتين';

    return Tooltip(
      message:
          '$label. مؤشر نشاط تقريبي من آخر تحديث استلمه البوت، '
          'وليس حالة اتصال Telegram فعلية.',
      child: Semantics(
        label: '$label؛ لا تمثل حالة اتصال Telegram الفعلية',
        child: Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(
                color: color.withValues(alpha: 0.35),
                blurRadius: 5,
                spreadRadius: 1,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
