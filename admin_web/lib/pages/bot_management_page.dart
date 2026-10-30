import 'package:flutter/material.dart';

import '../api.dart';
import 'bot_button_map_page.dart';
import 'bot_profile_page.dart';
import 'types_page.dart';

class BotManagementPage extends StatelessWidget {
  const BotManagementPage({super.key, required this.api});

  final AdminApi api;

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 3,
    child: Column(
      children: [
        Material(
          color: Theme.of(context).colorScheme.surface,
          child: const TabBar(
            tabs: [
              Tab(icon: Icon(Icons.badge_outlined), text: 'هوية البوت'),
              Tab(icon: Icon(Icons.category_outlined), text: 'التصنيفات'),
              Tab(
                icon: Icon(Icons.account_tree_outlined),
                text: 'خريطة الأزرار',
              ),
            ],
          ),
        ),
        Expanded(
          child: TabBarView(
            children: [
              BotProfilePage(api: api),
              TypesPage(api: api),
              const BotButtonMapPage(),
            ],
          ),
        ),
      ],
    ),
  );
}
