Future<String> installedVersion() => Future<String>.error(
  UnsupportedError('Android app updates are unavailable'),
);

Future<Map<String, dynamic>> fetchPolicy(String baseUrl) =>
    Future<Map<String, dynamic>>.error(
      UnsupportedError('Android app updates are unavailable'),
    );

Future<String> downloadAndInstall({
  required String url,
  required String expectedSha256,
  required void Function(int downloaded, int? total) onProgress,
}) => Future<String>.error(
  UnsupportedError('Android app updates are unavailable'),
);
