import 'package:web/web.dart' as web;

const _tokenKey = 'mrw_admin_token';
String get origin => web.window.location.origin;

Future<void> initialize() async {}

String? getValue(String key) => web.window.localStorage.getItem(key);

void setValue(String key, String value) =>
    web.window.localStorage.setItem(key, value);

Future<String?> readToken() async => getValue(_tokenKey);

Future<void> writeToken(String token) async =>
    setValue(_tokenKey, token.trim());

Future<void> clearToken() async =>
    web.window.localStorage.removeItem(_tokenKey);
