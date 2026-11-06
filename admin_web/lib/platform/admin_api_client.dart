import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:file_saver/file_saver.dart';
import 'package:http/http.dart' as http;

import '../api.dart';
import 'admin_storage.dart';

class Api implements AdminApi {
  Api({
    required this.token,
    required this.onUnauthorized,
    this.baseUrl,
    http.Client? client,
  }) : _client = client ?? http.Client();

  static const _configuredBase = String.fromEnvironment(
    'ADMIN_API_BASE_URL',
    defaultValue: '',
  );
  static const String _baseUrlKey = 'mrw_admin_api_base_url';

  final String token;
  final void Function()? onUnauthorized;
  final String? baseUrl;
  final http.Client _client;

  String get base {
    final explicit = (baseUrl ?? loadBaseUrl() ?? '').trim();
    if (explicit.isNotEmpty) {
      return explicit.replaceFirst(RegExp(r'/+$'), '');
    }
    final configured = _configuredBase.trim().replaceFirst(RegExp(r'/+$'), '');
    if (configured.isNotEmpty) return configured;
    return AdminStorage.origin;
  }

  static String? loadBaseUrl() {
    final value = AdminStorage.getValue(_baseUrlKey);
    if (value == null || value.trim().isEmpty) return null;
    return value.trim();
  }

  static void saveBaseUrl(String baseUrl) =>
      AdminStorage.setValue(_baseUrlKey, baseUrl.trim());

  static Future<String?> loadToken() async {
    final value = await AdminStorage.readToken();
    if (value == null || value.trim().isEmpty) return null;
    return value.trim();
  }

  static Future<void> saveToken(String token) =>
      AdminStorage.writeToken(token.trim());

  static Future<void> clearToken() => AdminStorage.clearToken();

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
      if (body != null) {
        request.headers['content-type'] = 'application/json; charset=utf-8';
        request.body = jsonEncode(body);
      }
      response = await http.Response.fromStream(await _client.send(request));
    } catch (error) {
      throw ApiException(0, 'network_error');
    }
    return _decodeJsonResponse(response);
  }

  Map<String, dynamic> _decodeJsonResponse(http.Response response) {
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
    StreamSubscription<String>? subscription;
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
        } on FormatException {
          controller.addError(ApiException(502, 'invalid_event'));
        }
      }
    }

    controller = StreamController<Map<String, dynamic>>(
      onListen: () async {
        final request = http.Request('GET', _uri('/api/admin/events/stream'))
          ..headers.addAll(_headers)
          ..headers['accept'] = 'text/event-stream';
        try {
          final response = await _client.send(request);
          if (response.statusCode == 401) {
            onUnauthorized?.call();
            controller.addError(ApiException(401, 'unauthorized'));
            await controller.close();
            return;
          }
          if (response.statusCode != 200) {
            controller.addError(
              ApiException(response.statusCode, 'event_stream_failed'),
            );
            await controller.close();
            return;
          }
          subscription = response.stream
              .transform(utf8.decoder)
              .listen(
                consume,
                onError: (Object error, StackTrace stackTrace) {
                  controller.addError(
                    ApiException(0, 'event_stream_disconnected'),
                    stackTrace,
                  );
                  unawaited(controller.close());
                },
                onDone: () => unawaited(controller.close()),
              );
        } catch (error) {
          controller.addError(ApiException(0, 'event_stream_disconnected'));
          await controller.close();
        }
      },
      onCancel: () => subscription?.cancel(),
    );
    return controller.stream;
  }

  @override
  Future<Uint8List> getBytes(String path) async {
    http.Response response;
    try {
      response = await _client.get(_uri(path), headers: _headers);
    } catch (error) {
      throw ApiException(0, 'network_error');
    }
    if (response.statusCode == 401) {
      onUnauthorized?.call();
      throw ApiException(401, 'unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, _errorCode(response.body));
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
    try {
      final response = await http.Response.fromStream(await _client.send(request));
      return _decodeJsonResponse(response);
    } catch (error) {
      if (error is ApiException) rethrow;
      throw ApiException(0, 'network_error');
    }
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
    } catch (error) {
      throw ApiException(0, 'network_error');
    }
    if (response.statusCode == 401) {
      onUnauthorized?.call();
      throw ApiException(401, 'unauthorized');
    }
    if (response.statusCode >= 400) {
      throw ApiException(response.statusCode, _errorCode(response.body));
    }

    final segments = Uri.parse(path).pathSegments;
    final filename = _downloadFilename(
      response.headers['content-disposition'],
      segments.isEmpty ? 'download' : segments.last,
    );
    final extension = filename.contains('.') ? filename.split('.').last : '';
    final name = extension.isEmpty
        ? filename
        : filename.substring(0, filename.length - extension.length - 1);
    final contentType = response.headers['content-type']?.split(';').first.trim();
    await FileSaver.instance.saveAs(
      name: name,
      bytes: response.bodyBytes,
      fileExtension: extension,
      mimeType: MimeType.custom,
      customMimeType: contentType?.isNotEmpty == true
          ? contentType!
          : 'application/octet-stream',
      dialogTitle: 'حفظ الملف',
    );
  }

  String _errorCode(String body) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic>) {
        return (decoded['error'] ?? 'error').toString();
      }
    } on FormatException {
      return 'error';
    }
    return 'error';
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
