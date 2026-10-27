import 'package:flutter/material.dart';

import '../api.dart';
import 'operations_page.dart';
import 'preferences_page.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({
    super.key,
    required this.api,
    required this.mode,
    required this.textScale,
    required this.fontFamily,
    required this.compact,
    required this.seed,
    required this.soundEnabled,
    required this.soundUnlocked,
    required this.heartbeatMuteUntil,
    required this.onHeartbeatMuteHours,
    required this.soundSounds,
    required this.onChanged,
  });

  final AdminApi api;
  final ThemeMode mode;
  final double textScale;
  final String fontFamily;
  final bool compact;
  final Color seed;
  final bool soundEnabled;
  final bool soundUnlocked;
  final int? heartbeatMuteUntil;
  final ValueChanged<int> onHeartbeatMuteHours;
  final Map<String, String> soundSounds;
  final ValueChanged<Map<String, dynamic>> onChanged;

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 2,
    child: Column(
      children: [
        Material(
          color: Theme.of(context).colorScheme.surface,
          child: const TabBar(
            tabs: [
              Tab(icon: Icon(Icons.tune_outlined), text: 'إعدادات النظام'),
              Tab(icon: Icon(Icons.palette_outlined), text: 'التفضيلات'),
            ],
          ),
        ),
        Expanded(
          child: TabBarView(
            children: [
              OperationsPage(api: api),
              PreferencesPage(
                mode: mode,
                textScale: textScale,
                fontFamily: fontFamily,
                compact: compact,
                seed: seed,
                onChanged: onChanged,
                soundEnabled: soundEnabled,
                soundUnlocked: soundUnlocked,
                heartbeatMuteUntil: heartbeatMuteUntil,
                onHeartbeatMuteHours: onHeartbeatMuteHours,
                soundSounds: soundSounds,
              ),
            ],
          ),
        ),
      ],
    ),
  );
}
