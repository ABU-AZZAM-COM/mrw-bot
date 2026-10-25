import 'package:flutter/material.dart';

import '../widgets.dart';

class PreferencesPage extends StatelessWidget {
  const PreferencesPage({
    super.key,
    required this.mode,
    required this.textScale,
    required this.fontFamily,
    required this.compact,
    required this.seed,
    required this.soundEnabled,
    required this.soundUnlocked,
    required this.soundSounds,
    required this.onChanged,
  });

  final ThemeMode mode;
  final double textScale;
  final String fontFamily;
  final bool compact;
  final Color seed;
  final bool soundEnabled;
  final bool soundUnlocked;
  final Map<String, String> soundSounds;
  final ValueChanged<Map<String, dynamic>> onChanged;

  void _update(String key, Object value) {
    onChanged({
      'mode': mode.name,
      'scale': textScale,
      'font': fontFamily,
      'compact': compact,
      'seed': seed.toARGB32(),
      'soundEnabled': soundEnabled,
      'soundUnlocked': soundUnlocked,
      for (final type in const [
        'report_new',
        'report_urgent',
        'user_blocked',
        'user_message',
        'emergency',
        'heartbeat_fail',
      ])
        'sound_$type':
            soundSounds[type] ??
            (type == 'report_new'
                ? 'report'
                : type == 'user_message'
                ? 'message'
                : 'alert'),
      key: value,
    });
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(18),
    children: [
      const Text(
        'تُحفظ هذه التفضيلات على هذا المتصفح فقط ولا تغيّر إعدادات الخادم.',
      ),
      const SizedBox(height: 12),
      SectionCard(
        'المظهر وإمكانية الوصول',
        icon: Icons.palette_outlined,
        child: Column(
          children: [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              secondary: Icon(
                mode == ThemeMode.light
                    ? Icons.light_mode_outlined
                    : Icons.dark_mode_outlined,
              ),
              title: Text(
                mode == ThemeMode.light ? 'السمة الفاتحة' : 'السمة الداكنة',
              ),
              subtitle: const Text('تُطبّق فورًا على لوحة الإدارة'),
              value: mode == ThemeMode.light,
              onChanged: (value) => _update(
                'mode',
                value ? ThemeMode.light.name : ThemeMode.dark.name,
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: fontFamily,
              decoration: const InputDecoration(labelText: 'الخط'),
              items: const [
                DropdownMenuItem(
                  value: 'NotoSansArabic',
                  child: Text('Noto Sans Arabic'),
                ),
                DropdownMenuItem(value: '', child: Text('خط النظام')),
              ],
              onChanged: (value) {
                if (value != null) _update('font', value);
              },
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                const Expanded(child: Text('حجم النص')),
                Text('${(textScale * 100).round()}%'),
              ],
            ),
            Slider(
              value: textScale,
              min: 0.85,
              max: 1.25,
              divisions: 8,
              label: '${(textScale * 100).round()}%',
              onChanged: (value) => _update('scale', value),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('كثافة مضغوطة'),
              subtitle: const Text('يعرض مزيدًا من المحتوى في المساحة نفسها'),
              value: compact,
              onChanged: (value) => _update('compact', value),
            ),
            const SizedBox(height: 8),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: Text(
                'لون التمييز',
                style: Theme.of(context).textTheme.titleSmall,
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              children: [
                for (final color in const [
                  Color(0xFF67D6A6),
                  Color(0xFF64B5F6),
                  Color(0xFFB39DDB),
                  Color(0xFFFFB74D),
                ])
                  InkWell(
                    onTap: () => _update('seed', color.toARGB32()),
                    borderRadius: BorderRadius.circular(24),
                    child: CircleAvatar(
                      backgroundColor: color,
                      radius: 17,
                      child: seed.toARGB32() == color.toARGB32()
                          ? const Icon(
                              Icons.check,
                              color: Colors.black,
                              size: 18,
                            )
                          : null,
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
      const SizedBox(height: 14),
      SectionCard(
        'الإشعارات الصوتية',
        icon: Icons.notifications_active_outlined,
        child: Column(
          children: [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('تشغيل أصوات الإشعارات'),
              subtitle: const Text('تُحفظ هذه الخيارات على هذا المتصفح فقط'),
              value: soundEnabled,
              onChanged: (value) => _update('soundEnabled', value),
            ),
            const SizedBox(height: 8),
            for (final item in const [
              ('report_new', 'بلاغ جديد'),
              ('report_urgent', 'بلاغ عاجل'),
              ('user_blocked', 'حظر مستخدم'),
              ('user_message', 'رسالة مستخدم'),
              ('emergency', 'الإيقاف الطارئ'),
              ('heartbeat_fail', 'فشل نبضة البوت'),
            ])
              DropdownButtonFormField<String>(
                initialValue: soundSounds[item.$1] ?? 'report',
                decoration: InputDecoration(labelText: item.$2),
                items: const [
                  DropdownMenuItem(value: 'report', child: Text('بلاغ')),
                  DropdownMenuItem(value: 'alert', child: Text('تنبيه')),
                  DropdownMenuItem(value: 'message', child: Text('رسالة')),
                ],
                onChanged: soundEnabled
                    ? (value) {
                        if (value != null) _update('sound_${item.$1}', value);
                      }
                    : null,
              ),
          ],
        ),
      ),
    ],
  );
}
