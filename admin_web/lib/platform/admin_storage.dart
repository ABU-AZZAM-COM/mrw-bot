import 'admin_storage_native.dart'
    if (dart.library.js_interop) 'admin_storage_web.dart' as implementation;

abstract final class AdminStorage {
  static Future<void> initialize() => implementation.initialize();

  static String? getValue(String key) => implementation.getValue(key);

  static String get origin => implementation.origin;

  static void setValue(String key, String value) =>
      implementation.setValue(key, value);

  static Future<String?> readToken() => implementation.readToken();

  static Future<void> writeToken(String token) =>
      implementation.writeToken(token);

  static Future<void> clearToken() => implementation.clearToken();
}
