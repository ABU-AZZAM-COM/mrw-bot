import 'package:flutter/material.dart';

class AdminNavigationDrawer extends StatefulWidget {
  const AdminNavigationDrawer({
    super.key,
    required this.destinations,
    required this.selectedIndex,
    required this.onDestinationSelected,
    required this.isOwner,
    required this.onOpenAdmins,
  });

  final List<(String, IconData)> destinations;
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final bool isOwner;
  final VoidCallback onOpenAdmins;

  @override
  State<AdminNavigationDrawer> createState() => _AdminNavigationDrawerState();
}

class _AdminNavigationDrawerState extends State<AdminNavigationDrawer> {
  final ScrollController _scrollController = ScrollController();

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Drawer(
    child: SafeArea(
      child: Scrollbar(
        controller: _scrollController,
        thumbVisibility: true,
        child: ListView(
          controller: _scrollController,
          padding: EdgeInsets.zero,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 18, 20, 14),
              child: Row(
                children: [
                  CircleAvatar(
                    backgroundColor: Theme.of(
                      context,
                    ).colorScheme.primary.withValues(alpha: 0.14),
                    child: Icon(
                      Icons.shield_outlined,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'مركز إدارة البلاغات',
                          style: Theme.of(context).textTheme.titleSmall,
                        ),
                        const Text(
                          'منظومة المخالفات العسكرية',
                          style: TextStyle(fontSize: 11),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            if (widget.isOwner)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 18),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton.tonalIcon(
                    onPressed: widget.onOpenAdmins,
                    icon: const Icon(Icons.manage_accounts_outlined),
                    label: const Text('المشرفون والصلاحيات'),
                  ),
                ),
              ),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 20, vertical: 8),
              child: Divider(),
            ),
            for (var i = 0; i < widget.destinations.length; i++)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: ListTile(
                  selected: i == widget.selectedIndex,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14),
                  ),
                  leading: Icon(widget.destinations[i].$2),
                  title: Text(widget.destinations[i].$1),
                  onTap: () => widget.onDestinationSelected(i),
                ),
              ),
          ],
        ),
      ),
    ),
  );
}
