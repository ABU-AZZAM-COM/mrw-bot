String? botProfileValidationError({
  required String name,
  required String shortDescription,
  required String description,
  required String welcomeText,
  required String verificationConsentText,
}) {
  if (name.isEmpty || name.length > 64) {
    return 'اسم البوت مطلوب وبحد أقصى 64 حرفاً';
  }
  if (shortDescription.length > 120) {
    return 'النبذة القصيرة يجب ألا تتجاوز 120 حرفًا';
  }
  if (description.length > 512) {
    return 'الوصف الكامل يجب ألا يتجاوز 512 حرفًا';
  }
  if (welcomeText.isEmpty) {
    return 'رسالة البداية مطلوبة';
  }
  if (welcomeText.length > 3500) {
    return 'رسالة البداية يجب ألا تتجاوز 3500 حرف';
  }
  if (verificationConsentText.length > 2500) {
    return 'نص موافقة التوثيق يجب ألا يتجاوز 2500 حرف';
  }
  return null;
}
