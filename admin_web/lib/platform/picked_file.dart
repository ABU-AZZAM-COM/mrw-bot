import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';

class PickedFileData {
  const PickedFileData({
    required this.name,
    required this.size,
    required this.bytes,
  });

  final String name;
  final int size;
  final Uint8List bytes;
}

Future<PickedFileData?> pickSingleFile(List<String> extensions) async {
  final file = await FilePicker.pickFile(
    type: FileType.custom,
    allowedExtensions: extensions,
  );
  if (file == null) return null;

  final size = await file.length() ?? file.lengthSync() ?? 0;
  return PickedFileData(
    name: file.name,
    size: size,
    bytes: await file.readAsBytes(),
  );
}
