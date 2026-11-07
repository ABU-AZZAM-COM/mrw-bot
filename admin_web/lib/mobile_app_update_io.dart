import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

const _installerChannel = MethodChannel('com.abuazzam.mrwbot.admin/mobile_app');
const _maxApkBytes = 250 * 1024 * 1024;

Future<String> installedVersion() async {
  final version = await _installerChannel.invokeMethod<String>('version');
  if (version == null || version.isEmpty) {
    throw StateError('تعذر قراءة إصدار التطبيق المثبت');
  }
  return version;
}

Future<Map<String, dynamic>> fetchPolicy(String baseUrl) async {
  final uri = Uri.parse(
    '${baseUrl.replaceFirst(RegExp(r'/+$'), '')}/api/mobile/policy',
  );
  final response = await http.get(uri).timeout(const Duration(seconds: 15));
  if (response.statusCode != 200) {
    throw HttpException('تعذر قراءة سياسة التطبيق (${response.statusCode})');
  }
  final decoded = jsonDecode(utf8.decode(response.bodyBytes));
  if (decoded is! Map<String, dynamic> || decoded['ok'] != true) {
    throw const FormatException('استجابة سياسة التطبيق غير صالحة');
  }
  return decoded;
}

Future<String> downloadAndInstall({
  required String url,
  required String expectedSha256,
  required void Function(int downloaded, int? total) onProgress,
}) async {
  final expectedHash = expectedSha256.toLowerCase();
  if (RegExp(r'^[a-f0-9]{64}$').hasMatch(expectedHash) == false) {
    throw const FormatException('بصمة APK غير صالحة');
  }
  var uri = Uri.parse(url);
  if (uri.scheme != 'https' || uri.host.isEmpty) {
    throw const FormatException('رابط APK يجب أن يستخدم HTTPS');
  }

  final directory = await getTemporaryDirectory();
  final temporaryFile = File(
    '${directory.path}/mrw-app-$expectedHash.apk.part',
  );
  final apkFile = File('${directory.path}/mrw-app-$expectedHash.apk');
  final client = http.Client();
  try {
    http.StreamedResponse? response;
    for (var redirects = 0; redirects <= 3; redirects++) {
      final request = http.Request('GET', uri)..followRedirects = false;
      response = await client
          .send(request)
          .timeout(const Duration(seconds: 30));
      if (response.statusCode >= 300 && response.statusCode < 400) {
        final location = response.headers['location'];
        if (location == null || redirects == 3) {
          throw const HttpException('رابط APK أعاد تحويلًا غير صالح');
        }
        uri = uri.resolve(location);
        if (uri.scheme != 'https') {
          throw const HttpException('تحويل رابط APK إلى اتصال غير آمن مرفوض');
        }
        continue;
      }
      break;
    }
    if (response == null || response.statusCode != 200) {
      throw HttpException(
        'تعذر تنزيل APK (${response?.statusCode ?? 'no response'})',
      );
    }
    final total = response.contentLength;
    if (total != null && (total <= 0 || total > _maxApkBytes)) {
      throw const HttpException('حجم APK يتجاوز الحد المسموح');
    }

    final fileSink = temporaryFile.openWrite();
    final digestResult = _DigestSink();
    final hashSink = sha256.startChunkedConversion(digestResult);
    var downloaded = 0;
    final progressTimer = Stopwatch()..start();
    try {
      await for (final chunk in response.stream) {
        downloaded += chunk.length;
        if (downloaded > _maxApkBytes) {
          throw const HttpException('حجم APK يتجاوز الحد المسموح');
        }
        fileSink.add(chunk);
        hashSink.add(chunk);
        if (progressTimer.elapsedMilliseconds >= 150) {
          onProgress(downloaded, total);
          progressTimer.reset();
        }
      }
      hashSink.close();
      await fileSink.flush();
      onProgress(downloaded, total);
    } finally {
      await fileSink.close();
    }

    if (digestResult.value?.toString() != expectedHash) {
      throw const FormatException('فشلت مطابقة بصمة APK؛ أُلغي التثبيت');
    }
    if (await apkFile.exists()) await apkFile.delete();
    await temporaryFile.rename(apkFile.path);
    final installerStatus = await _installerChannel.invokeMethod<String>(
      'installApk',
      {'path': apkFile.path},
    );
    return installerStatus ?? 'installer_opened';
  } catch (_) {
    if (await temporaryFile.exists()) await temporaryFile.delete();
    rethrow;
  } finally {
    client.close();
  }
}

class _DigestSink implements Sink<Digest> {
  Digest? value;

  @override
  void add(Digest data) => value = data;

  @override
  void close() {}
}
