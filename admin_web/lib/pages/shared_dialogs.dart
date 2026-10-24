import 'package:flutter/material.dart';

// حوار نصّ إلزامي (سبب الرفض / تأكيد الحذف) — يعيد النص أو null عند إلغاء.
Future<String?> requiredTextDialog(
  BuildContext context, {
  required String title,
  required String hint,
  String? mustMatch,
  bool danger = false,
  int maxLines = 3,
}) async {
  final c = TextEditingController();
  final res = await showDialog<String>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: TextField(
        controller: c,
        autofocus: true,
        maxLines: maxLines,
        maxLength: mustMatch != null ? 120 : 600,
        decoration: InputDecoration(hintText: hint),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(null),
          child: const Text('إلغاء'),
        ),
        FilledButton(
          style: danger
              ? FilledButton.styleFrom(backgroundColor: Colors.red.shade700)
              : null,
          onPressed: () {
            final t = c.text.trim();
            if (t.isEmpty) return;
            if (mustMatch != null && t != mustMatch) return;
            Navigator.of(ctx).pop(t);
          },
          child: const Text('تأكيد'),
        ),
      ],
    ),
  );
  return res;
}

// ==================================================================
// 3) المتبلّغون
// ==================================================================
