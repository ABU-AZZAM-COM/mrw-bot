// عميل اللوحة: كل الاتصالات بنفس منبع الصفحة (المساحة) مع توكن الإدارة.
// التوكن يُخزَّن محلياً في المتصفح (localStorage) ولا يُرسل لأي طرف ثالث.
// استعمال package:web الرسمي — dart:html لم يعد مدعوماً في فلاتر الحديثة.
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web/web.dart' as web;

class ApiException implements Exception {
  ApiException(this.status, this.code);

  final int status;
  final String code;

  bool get unauthorized => status == 401;
  bool get isMissingToken => code == 'api_token_not_configured';

  @override
  String toString() => code;
}

class Api {
  Api({required this.token, required this.onUnauthorized});

  final String token;
  final void Function()? onUnauthorized;
  final http.Client _client = http.Client();

  /// منبع الصفحة نفسه — اللوحة تعيش داخل المساحة ولا نظير خارجي.
  String get base => web.window.location.origin;

  // ----------------------------------------------------------------
  // تخزين التوكن محلياً
  // ----------------------------------------------------------------
  static const String _tokenKey = 'mrw_admin_token';

  static String? loadToken() {
    try {
      final v = web.window.localStorage.getItem(_tokenKey);
      if (v == null || v.trim().isEmpty) return null;
      return v.trim();
    } catch (_) {
      return null;
    }
  }

  static void saveToken(String t) =>
      web.window.localStorage.setItem(_tokenKey, t.trim());

  static void clearToken() =>
      web.window.localStorage.removeItem(_tokenKey);

  // ----------------------------------------------------------------
  // التنفيذ
  // ----------------------------------------------------------------
  Uri _uri(String path, [Map<String, String>? q]) {
    final u = Uri.parse('$base$path');
    if (q == null || q.isEmpty) return u;
    return u.replace(
        queryParameters: <String, String>{...u.queryParameters, ...q});
  }

  Map<String, String> get _h => <String, String>{'x-admin-token': token};

  Future<Map<String, dynamic>> _go(String method, String path,
      {Map<String, String>? q, Object? body}) async {
    http.Response r;
    try {
      final req = http.Request(method, _uri(path, q))..headers.addAll(_h);
      if (body != null) req.body = jsonEncode(body);
      r = await http.Response.fromStream(await _client.send(req));
    } catch (_) {
      throw ApiException(0, 'network_error');
    }
    Map<String, dynamic> j;
    try {
      final decoded = jsonDecode(r.body);
      j = decoded is Map<String, dynamic>
          ? decoded
          : <String, dynamic>{'ok': false, 'error': 'bad_response'};
    } catch (_) {
      j = <String, dynamic>{'ok': false, 'error': 'bad_response'};
    }
    if (r.statusCode == 401) {
      onUnauthorized?.call();
      throw ApiException(401, 'unauthorized');
    }
    if (r.statusCode >= 400) {
      throw ApiException(r.statusCode, (j['error'] ?? 'error').toString());
    }
    return j;
  }

  Future<Map<String, dynamic>> get(String p, [Map<String, String>? q]) async =>
      _go('GET', p, q: q);

  Future<Map<String, dynamic>> post(String p, Map<String, dynamic> b) async =>
      _go('POST', p, body: b);

  Future<Map<String, dynamic>> put(String p, Map<String, dynamic> b) async =>
      _go('PUT', p, body: b);

  Future<Map<String, dynamic>> del(String p, Map<String, dynamic> b) async =>
      _go('DELETE', p, body: b);

  // صورة البلاغ عبر file_id (التوكن في الاستعلام لأن الصورة تُطلب كرابط مباشر).
  String photoUrl(num rid, {Object? bust}) {
    final ts = bust == null ? '' : '&ts=$bust';
    return '$base/api/admin/photo/$rid?token=${Uri.encodeComponent(token)}$ts';
  }

  // روابط التنزيل: تصدير Excel/نص + نسخة القاعدة.
  String downloadUrl(String path) {
    final sep = path.contains('?') ? '&' : '?';
    return '$base$path${sep}token=${Uri.encodeComponent(token)}';
  }

  void download(String path) => web.window.open(downloadUrl(path), '_blank');
}
