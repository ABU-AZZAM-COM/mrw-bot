import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web/web.dart' as web;

import 'api.dart';

class Api implements AdminApi {
  Api({required this.token, required this.onUnauthorized, this.baseUrl});

  static const _configuredBase = String.fromEnvironment(
    'ADMIN_API_BASE_URL',
    defaultValue: '',
  );
  static const String _baseUrlKey = 'mrw_admin_api_base_url';

  final String token;
  final void Function()? onUnauthorized;
  final String? baseUrl;
  final http.Client _client = http.Client();

  /// منبع الـ API:
  /// - فارغ (الافتراضي) → نفس منبع الصفحة، وهو الأنسب على Render.
  /// - عنوان صريح → عند تشغيل اللوحة محليًا وربطها بخادم Render.
  String get base {
    final explicit = (baseUrl ?? loadBaseUrl() ?? '').trim();
    if (explicit.isNotEmpty) {
      return explicit.replaceFirst(RegExp(r'/+$'), '');
    }
    final configured = _configuredBase.trim().replaceFirst(RegExp(r'/+$'), '');
    if (configured.isNotEmpty) return configured;
    return web.window.location.origin;
  }

  static const String _tokenKey = 'mrw_admin_token';

  static String? loadBaseUrl() {
    try {
      final value = web.window.localStorage.getItem(_baseUrlKey);
      if (value == null || value.trim().isEmpty) return null;
      return value.trim();
    } catch (_) {
      return null;
    }
  }

  static void saveBaseUrl(String baseUrl) =>
      web.window.localStorage.setItem(_baseUrlKey, baseUrl.trim());

  static String? loadToken() {
    try {
      final value = web.window.localStorage.getItem(_tokenKey);
      if (value == null || value.trim().isEmpty) return null;
      return value.trim();
    } catch (_) {
      return null;
    }
  }

  static void saveToken(String token) =>
      web.window.localStorage.setItem(_tokenKey, token.trim());

  static void clearToken() => web.window.localStorage.removeItem(_tokenKey);

  Uri _uri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$base$path');
    if (query == null || query.isEmpty) return uri;
    return uri.replace(
      queryParameters: <String, String>{...uri.queryParameters, ...query},
    );
  }

  Map<String, String> get _headers => <String, String>{'x-admin-token': token};

  Future<Map<String, dynamic>> _go(
    String method,
    String path, {
    Map<String, String>? query,
    Object? body,
  }) async {
    http.Response response;
    try {
      final request = http.Request(method, _uri(path, query))
        ..headers.addAll(_headers);
      if (body != null) request.body = jsonEncode(body);
      response = await http.Response.fromStream(await _client.send(request));
    } catch (_) {
      throw ApiException(0, 'network_error');
    }

    Map<String, dynamic> json;
    try {
      final decoded = jsonDecode(response.body);
      json = decoded is Map<String, dynamic>
          ? decoded
          : <String, dynamic>{'ok': false, 'error': 'bad_response'};
    } catch (_) {
      json = <String, dynamic>{'ok': false, 'error': 'bad_response'};
    }
    if (response.statusCode == 401) {
      onUnauthorized?.call();
      throw ApiException(401, 'unauthorized');
    }
    if (response.statusCode == 404) {
      throw ApiException(404, 'service_not_found');
    }
    if (response.statusCode >= 400) {
      throw ApiException(
        response.statusCode,
        (json['error'] ?? 'error').toString(),
      );
    }
    return json;
  }

  @override
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]) =>
      _go('GET', path, query: query);

  @override
  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) =>
      _go('POST', path, body: body);

  @override
  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) =>
      _go('PUT', path, body: body);

  @override
  Future<Map<String, dynamic>> del(String path, Map<String, dynamic> body) =>
      _go('DELETE', path, body: body);

  @override
  String photoUrl(num reportId, {Object? bust}) {
    final ts = bust == null ? '' : '&ts=$bust';
    return '$base/api/admin/photo/$reportId?token=${Uri.encodeComponent(token)}$ts';
  }

  @override
  String downloadUrl(String path) {
    final separator = path.contains('?') ? '&' : '?';
    return '$base$path${separator}token=${Uri.encodeComponent(token)}';
  }

  @override
  void download(String path) => web.window.open(downloadUrl(path), '_blank');
}
