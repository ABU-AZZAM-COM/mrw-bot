import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/admin_navigation_drawer.dart';

void main() {
  testWidgets('owner drawer scrolls to the final navigation destination', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final destinations = <(String, IconData)>[
      for (var index = 0; index < 15; index++)
        ('قسم $index', Icons.dashboard_outlined),
    ];
    destinations[14] = ('ما الجديد', Icons.new_releases_outlined);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          drawer: AdminNavigationDrawer(
            destinations: destinations,
            selectedIndex: 0,
            onDestinationSelected: (_) {},
            isOwner: true,
            onOpenAdmins: () {},
          ),
          appBar: AppBar(
            leading: Builder(
              builder: (context) => IconButton(
                onPressed: () => Scaffold.of(context).openDrawer(),
                icon: const Icon(Icons.menu),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.byIcon(Icons.menu));
    await tester.pumpAndSettle();

    expect(find.text('المشرفون والصلاحيات'), findsOneWidget);
    expect(find.text('ما الجديد'), findsNothing);

    await tester.drag(find.byType(ListView), const Offset(0, -900));
    await tester.pumpAndSettle();

    expect(find.text('ما الجديد'), findsOneWidget);
  });
}
