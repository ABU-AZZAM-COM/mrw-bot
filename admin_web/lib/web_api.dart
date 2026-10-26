import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:js_interop';
import 'dart:typed_data';

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

  Map<String, String> get _headers => <String, String>{
    'Authorization': 'Bearer $token',
  };

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
  Stream<Map<String, dynamic>> watchEvents() {
    late final StreamController<Map<String, dynamic>> controller;
    final request = html.HttpRequest();
    var offset = 0;
    var pending = '';

    void consume(String chunk) {
      pending += chunk.replaceAll('\r\n', '\n');
      while (true) {
        final boundary = pending.indexOf('\n\n');
        if (boundary < 0) return;
        final event = pending.substring(0, boundary);
        pending = pending.substring(boundary + 2);
        final data = event
            .split('\n')
            .where((line) => line.startsWith('data:'))
            .map((line) => line.substring(5).trimLeft())
            .join('\n');
        if (data.isEmpty) continue;
        try {
          final decoded = jsonDecode(data);
          if (decoded is Map<String, dynamic>) controller.add(decoded);
        } catch (_) {
          controller.addError(ApiException(502, 'invalid_event'));
        }
      }
    }

    controller = StreamController<Map<String, dynamic>>(
      onListen: () {
        request
          ..open('GET', _uri('/api/admin/events/stream').toString())
          ..setRequestHeader('Authorization', 'Bearer $token');
        request.onProgress.listen((_) {
          final response = request.responseText ?? '';
          if (response.length < offset) offset = 0;
          consume(response.substring(offset));
          offset = response.length;
        });
        request.onLoad.listen((_) {
          if (request.status == 401) {
            onUnauthorized?.call();
            controller.addError(ApiException(401, 'unauthorized'));
          } else if (request.status != 200) {
            controller.addError(
              ApiException(request.status ?? 0, 'event_stream_failed'),
            );
          }
          controller.close();
        });
        request.onError.listen((_) {
          controller.addError(ApiException(0, 'event_stream_disconnected'));
          controller.close();
        });
        request.onAbort.listen((_) => controller.close());
        request.send();
      },
      onCancel: () {
        request.abort();
      },
    );
    return controller.stream;
  }

  @override
  Future<Uint8List> getBytes(String path) async {
    http.Response response;
    try {
      response = await _client.get(_uri(path), headers: _headers);
    } catch (_) {
      throw ApiException(0, 'network_error');
    }
    if (response.statusCode == 401) {
      onUnauthorized?.call();
      throw ApiException(401, 'unauthorized');
    }
    if (response.statusCode >= 400) {
      var code = 'error';
      try {
        final decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) {
          code = (decoded['error'] ?? code).toString();
        }
      } catch (_) {
        code = 'error';
      }
      throw ApiException(response.statusCode, code);
    }
    if (!(response.headers['content-type'] ?? '').startsWith('image/')) {
      throw ApiException(response.statusCode, 'invalid_image_response');
    }
    return response.bodyBytes;
  }

  @override
  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) =>
      _go('POST', path, body: body);

  @override
  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    String? filename,
    Uint8List? bytes,
  }) async {
    final request = http.MultipartRequest('POST', _uri(path))
      ..headers.addAll(_headers)
      ..fields.addAll(fields);
    if (bytes != null) {
      request.files.add(
        http.MultipartFile.fromBytes(
          'media',
          bytes,
          filename: filename ?? 'attachment',
        ),
      );
    }
    http.Response response;
    try {
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
  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) =>
      _go('PUT', path, body: body);

  @override
  Future<Map<String, dynamic>> del(String path, Map<String, dynamic> body) =>
      _go('DELETE', path, body: body);

  @override
  String photoUrl(num reportId, {Object? bust}) {
    final uri = Uri.parse('$base/api/admin/photo/$reportId');
    return bust == null
        ? uri.toString()
        : uri.replace(queryParameters: {'ts': '$bust'}).toString();
  }

  @override
  Future<void> download(String path) async {
    http.Response response;
    try {
      response = await _client.get(_uri(path), headers: _headers);
    } catch (_) {
      throw ApiException(0, 'network_error');
    }
    if (response.statusCode == 401) {
      onUnauthorized?.call();
      throw ApiException(401, 'unauthorized');
    }
    if (response.statusCode >= 400) {
      var code = 'error';
      try {
        final decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) {
          code = (decoded['error'] ?? code).toString();
        }
      } catch (_) {
        code = 'error';
      }
      throw ApiException(response.statusCode, code);
    }

    final pathSegments = Uri.parse(path).pathSegments;
    final filename = _downloadFilename(
      response.headers['content-disposition'],
      pathSegments.isEmpty ? 'download' : pathSegments.last,
    );
    final blob = web.Blob(
      <web.BlobPart>[response.bodyBytes.toJS as web.BlobPart].toJS,
    );
    final objectUrl = web.URL.createObjectURL(blob);
    final anchor = web.document.createElement('a') as web.HTMLAnchorElement
      ..href = objectUrl
      ..download = filename;
    web.document.body?.append(anchor);
    anchor.click();
    anchor.remove();
    unawaited(
      Future<void>.delayed(
        const Duration(seconds: 1),
        () => web.URL.revokeObjectURL(objectUrl),
      ),
    );
  }

  String _downloadFilename(String? contentDisposition, String fallback) {
    if (contentDisposition == null) return fallback;
    final extended = RegExp(
      r"filename\*=UTF-8''([^;]+)",
      caseSensitive: false,
    ).firstMatch(contentDisposition);
    if (extended != null) {
      return Uri.decodeComponent(extended.group(1)!);
    }
    final standard = RegExp(
      r'filename="?([^";]+)"?',
      caseSensitive: false,
    ).firstMatch(contentDisposition);
    return standard?.group(1) ?? fallback;
  }
}
