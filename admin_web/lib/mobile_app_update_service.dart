import 'mobile_app_update_stub.dart'
    if (dart.library.io) 'mobile_app_update_io.dart'
    as platform;

abstract final class MobileAppUpdateService {
  static Future<String> installedVersion() => platform.installedVersion();

  static Future<Map<String, dynamic>> fetchPolicy(String baseUrl) =>
      platform.fetchPolicy(baseUrl);

  static Future<String> downloadAndInstall({
    required String url,
    required String expectedSha256,
    required void Function(int downloaded, int? total) onProgress,
  }) => platform.downloadAndInstall(
    url: url,
    expectedSha256: expectedSha256,
    onProgress: onProgress,
  );
}
