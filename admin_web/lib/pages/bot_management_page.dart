import 'package:flutter/material.dart';

import '../api.dart';
import 'bot_button_map_page.dart';
import 'bot_commands_page.dart';
import 'bot_menu_button_page.dart';
import 'bot_profile_page.dart';
import 'bot_reply_page.dart';
import 'bot_settings_page.dart';
import 'types_page.dart';

class BotManagementPage extends StatelessWidget {
  const BotManagementPage({super.key, required this.api});

  final AdminApi api;

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 7,
    child: Column(
      children: [
        Material(
          color: Theme.of(context).colorScheme.surface,
          child: const TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            tabs: [
              Tab(icon: Icon(Icons.badge_outlined), text: 'هوية البوت'),
              Tab(icon: Icon(Icons.category_outlined), text: 'التصنيفات'),
              Tab(icon: Icon(Icons.account_tree_outlined), text: 'أزرار القائمة'),
              Tab(icon: Icon(Icons.keyboard_alt_outlined), text: 'لوحات الرد'),
              Tab(icon: Icon(Icons.terminal_rounded), text: 'الأوامر'),
              Tab(icon: Icon(Icons.more_horiz_rounded), text: 'زر القائمة'),
              Tab(icon: Icon(Icons.tune_rounded), text: 'الإعدادات'),
            ],
          ),
        ),
        Expanded(
          child: TabBarView(
            children: [
              BotProfilePage(api: api),
              TypesPage(api: api),
              BotButtonMapPage(api: api),
              BotReplyPage(api: api),
              BotCommandsPage(api: api),
              BotMenuButtonPage(api: api),
              BotSettingsPage(api: api),
            ],
          ),
        ),
      ],
    ),
  );
}
