import 'package:flutter/material.dart';

import '../app_credits.dart';
import '../release_notes.dart';

class WhatsNewPage extends StatelessWidget {
  const WhatsNewPage({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final installedRelease = releaseNotes
        .where((release) => release.version == AppCredits.version)
        .firstOrNull;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 920),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Row(
                      children: [
                        CircleAvatar(
                          radius: 26,
                          backgroundColor: theme.colorScheme.primaryContainer,
                          child: Icon(
                            Icons.new_releases_outlined,
                            color: theme.colorScheme.onPrimaryContainer,
                          ),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'ما الجديد في لوحة الإدارة؟',
                                style: theme.textTheme.titleLarge?.copyWith(
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                installedRelease == null
                                    ? 'الإصدار المثبت ${AppCredits.versionLabel} · سجل التحسينات والميزات حسب الإصدار.'
                                    : 'الإصدار المثبت ${AppCredits.versionLabel} · ${installedRelease.title}',
                                style: theme.textTheme.bodyMedium?.copyWith(
                                  color: theme.colorScheme.onSurfaceVariant,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 8),
                        const Icon(Icons.auto_awesome_outlined),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                for (var index = 0; index < releaseNotes.length; index++)
                  _ReleaseCard(
                    release: releaseNotes[index],
                    installed:
                        releaseNotes[index].version == AppCredits.version,
                    expanded:
                        index == 0 ||
                        releaseNotes[index].version == AppCredits.version,
                  ),
                const SizedBox(height: 8),
                Text(
                  'يُضاف ملخص لكل إصدار جديد إلى هذا السجل.',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _ReleaseCard extends StatelessWidget {
  const _ReleaseCard({
    required this.release,
    required this.installed,
    required this.expanded,
  });

  final ReleaseNote release;
  final bool installed;
  final bool expanded;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        initiallyExpanded: expanded,
        leading: CircleAvatar(
          backgroundColor: installed
              ? theme.colorScheme.primaryContainer
              : theme.colorScheme.surfaceContainerHighest,
          child: Icon(
            installed ? Icons.check_circle_outline : Icons.history_outlined,
            color: installed
                ? theme.colorScheme.onPrimaryContainer
                : theme.colorScheme.onSurfaceVariant,
          ),
        ),
        title: Wrap(
          spacing: 8,
          runSpacing: 4,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(release.title),
            if (installed)
              const Chip(
                label: Text('الإصدار المثبت'),
                visualDensity: VisualDensity.compact,
              ),
          ],
        ),
        subtitle: Text('${release.date} · v${release.version}'),
        childrenPadding: const EdgeInsetsDirectional.fromSTEB(20, 0, 20, 18),
        children: [
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: Text(release.summary),
          ),
          for (final section in [
            (
              title: 'ميزات جديدة',
              icon: Icons.add_circle_outline,
              items: release.features,
            ),
            (
              title: 'تحسينات',
              icon: Icons.upgrade_outlined,
              items: release.improvements,
            ),
            (
              title: 'ملاحظات وحدود',
              icon: Icons.info_outline,
              items: release.notes,
            ),
          ])
            if (section.items.isNotEmpty) ...[
              const SizedBox(height: 14),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: Row(
                  children: [
                    Icon(section.icon, size: 18),
                    const SizedBox(width: 7),
                    Text(
                      section.title,
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 6),
              for (final item in section.items)
                Padding(
                  padding: const EdgeInsetsDirectional.only(
                    start: 4,
                    bottom: 7,
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('•  '),
                      Expanded(child: Text(item)),
                    ],
                  ),
                ),
            ],
        ],
      ),
    );
  }
}
