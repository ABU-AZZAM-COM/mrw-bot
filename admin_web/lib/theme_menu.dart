import 'package:flutter/material.dart';

import 'app_theme.dart';

/// قائمة اختيار الثيم — مشتركة بين شاشة الدخول والهيكل الرئيسي.
class ThemeMenu extends StatelessWidget {
  const ThemeMenu({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<String>(
      valueListenable: themeController,
      builder: (context, current, _) {
        return PopupMenuButton<String>(
          tooltip: 'تغيير الثيم',
          icon: const Icon(Icons.palette_outlined),
          onSelected: themeController.select,
          itemBuilder: (context) => [
            for (final name in AdminTheme.names)
              PopupMenuItem<String>(
                value: name,
                child: Row(
                  children: [
                    Icon(
                      current == name
                          ? Icons.radio_button_checked
                          : Icons.radio_button_unchecked,
                      size: 18,
                      color: current == name
                          ? AdminTheme.accent
                          : Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                    const SizedBox(width: 8),
                    Text(AdminTheme.labels[name] ?? name),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}
