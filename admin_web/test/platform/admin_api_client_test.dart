import 'dart:convert';

import 'package:admin_web/platform/admin_api_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('login request sends JSON content type and body', () async {
    http.Request? capturedRequest;
    final client = MockClient((request) async {
      capturedRequest = request;
      return http.Response(
        jsonEncode({'token': 'session-token'}),
        200,
        headers: {'content-type': 'application/json'},
      );
    });
    addTearDown(client.close);

    final api = Api(
      token: 'test-token',
      onUnauthorized: null,
      baseUrl: 'https://example.test',
      client: client,
    );
    final body = {'name': 'operator', 'credential': 'test-credential'};

    final result = await api.post('/api/admin/login', body);

    expect(result['token'], 'session-token');
    expect(capturedRequest?.method, 'POST');
    expect(capturedRequest?.url.path, '/api/admin/login');
    expect(
      capturedRequest?.headers['content-type'],
      startsWith('application/json'),
    );
    expect(capturedRequest?.headers['authorization'], 'Bearer test-token');
    expect(jsonDecode(capturedRequest!.body), body);
  });
}
