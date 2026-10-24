abstract interface class AdminApi {
  Future<Map<String, dynamic>> get(String path, [Map<String, String>? query]);

  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body);

  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body);

  Future<Map<String, dynamic>> del(String path, Map<String, dynamic> body);

  String photoUrl(num reportId, {Object? bust});

  String downloadUrl(String path);

  void download(String path);
}

class ApiException implements Exception {
  ApiException(this.status, this.code);

  final int status;
  final String code;

  bool get unauthorized => status == 401;
  bool get isMissingToken => code == 'api_token_not_configured';

  @override
  String toString() => code;
}
