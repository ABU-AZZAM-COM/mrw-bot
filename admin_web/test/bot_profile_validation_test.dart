import 'package:admin_web/bot_profile_validation.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const validProfile = {
    'name': 'بوت البلاغات',
    'shortDescription': '',
    'description': '',
    'welcomeText': 'أهلًا بك',
  };

  test('allows an empty optional verification consent text', () {
    expect(
      botProfileValidationError(
        name: validProfile['name']!,
        shortDescription: validProfile['shortDescription']!,
        description: validProfile['description']!,
        welcomeText: validProfile['welcomeText']!,
        verificationConsentText: '',
      ),
      isNull,
    );
  });

  test('reports the specific profile field that exceeds its limit', () {
    expect(
      botProfileValidationError(
        name: validProfile['name']!,
        shortDescription: 'ن' * 121,
        description: validProfile['description']!,
        welcomeText: validProfile['welcomeText']!,
        verificationConsentText: '',
      ),
      'النبذة القصيرة يجب ألا تتجاوز 120 حرفًا',
    );
    expect(
      botProfileValidationError(
        name: validProfile['name']!,
        shortDescription: validProfile['shortDescription']!,
        description: 'و' * 513,
        welcomeText: validProfile['welcomeText']!,
        verificationConsentText: '',
      ),
      'الوصف الكامل يجب ألا يتجاوز 512 حرفًا',
    );
    expect(
      botProfileValidationError(
        name: validProfile['name']!,
        shortDescription: validProfile['shortDescription']!,
        description: validProfile['description']!,
        welcomeText: 'ب' * 3501,
        verificationConsentText: '',
      ),
      'رسالة البداية يجب ألا تتجاوز 3500 حرف',
    );
  });
}
