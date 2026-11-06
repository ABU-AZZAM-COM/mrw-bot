import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _tokenKey = 'mrw_admin_token';
const _secureStorage = FlutterSecureStorage();
const origin = 'https://mrw-bot.onrender.com';

SharedPreferences? _preferences;

Future<void> initialize() async {
  _preferences = await SharedPreferences.getInstance();
}

SharedPreferences get _prefs {
  final preferences = _preferences;
  if (preferences == null) {
    throw StateError('AdminStorage.initialize must run before use.');
  }
  return preferences;
}

String? getValue(String key) => _prefs.getString(key);

void setValue(String key, String value) {
  unawaited(
    _prefs.setString(key, value).then((saved) {
      if (!saved) debugPrint('Could not persist admin preference: $key');
    }),
  );
}

Future<String?> readToken() => _secureStorage.read(key: _tokenKey);

Future<void> writeToken(String token) =>
    _secureStorage.write(key: _tokenKey, value: token);

Future<void> clearToken() => _secureStorage.delete(key: _tokenKey);
