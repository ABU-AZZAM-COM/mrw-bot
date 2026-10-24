// لوحة الإدارة — Flutter Web تُخدم من المساحة على /admin.
// العربية RTL أولاً: شاشة اتصال بالتوكن ثم الهيكل الرئيسي بثماني صفحات.
import 'package:flutter/material.dart';

import 'api.dart';
import 'admin_shell.dart';
import 'app_credits.dart';
import 'app_theme.dart';
import 'theme_menu.dart';
import 'web_api.dart';
import 'widgets.dart';

void main() {
  runApp(const AdminApp());
}

class AdminApp extends StatefulWidget {
  const AdminApp({super.key});

  @override
  State<AdminApp> createState() => _AdminAppState();
}

class _AdminAppState extends State<AdminApp> {
  late Map<String, dynamic> _preferences;

  @override
  void initState() {
    super.initState();
    _preferences = AdminPreferences.load();
  }

  void _savePreferences(Map<String, dynamic> values) {
    setState(() => _preferences = values);
    AdminPreferences.save(values);
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<String>(
      valueListenable: themeController,
      builder: (context, themeName, _) {
        return MaterialApp(
          title: 'لوحة الإدارة — ${AppCredits.project}',
          debugShowCheckedModeBanner: false,
          theme: AdminTheme.build(
            mode: ThemeMode.light,
            fontFamily: _preferences['font'] as String,
            compact: _preferences['compact'] as bool,
            seed: Color(_preferences['seed'] as int),
            paletteName: themeName,
          ),
          darkTheme: AdminTheme.build(
            fontFamily: _preferences['font'] as String,
            compact: _preferences['compact'] as bool,
            seed: Color(_preferences['seed'] as int),
            paletteName: themeName,
          ),
          themeMode: ThemeMode.values.firstWhere(
            (value) => value.name == _preferences['mode'],
            orElse: () => ThemeMode.dark,
          ),
          // القاعدة: كل الصفحة من اليمين لليسار — أنظمة RTL لا تحتاج إعداداً لكل ويدجت
          builder: (context, child) => Directionality(
            textDirection: TextDirection.rtl,
            child: MediaQuery(
              data: MediaQuery.of(context).copyWith(
                textScaler: TextScaler.linear(
                  (_preferences['scale'] as num).toDouble(),
                ),
              ),
              child: child ?? const SizedBox.shrink(),
            ),
          ),
          home: AdminGate(
            preferences: _preferences,
            onPreferencesChanged: _savePreferences,
          ),
        );
      },
    );
  }
}

// ==================================================================
// بوابة الدخول: توكن من التخزين المحلي أو شاشة الاتصال
// ==================================================================
class AdminGate extends StatefulWidget {
  const AdminGate({
    super.key,
    required this.preferences,
    required this.onPreferencesChanged,
  });

  final Map<String, dynamic> preferences;
  final ValueChanged<Map<String, dynamic>> onPreferencesChanged;

  @override
  State<AdminGate> createState() => _AdminGateState();
}

class _AdminGateState extends State<AdminGate> {
  AdminApi? _api;

  @override
  void initState() {
    super.initState();
    final t = Api.loadToken();
    if (t != null && t.isNotEmpty) {
      _api = Api(token: t, onUnauthorized: _logout, baseUrl: Api.loadBaseUrl());
    }
  }

  void _logout() {
    Api.clearToken();
    if (mounted) setState(() => _api = null);
  }

  @override
  Widget build(BuildContext context) {
    final api = _api;
    if (api == null) {
      return LoginScreen(
        onConnected: (t) {
          Api.saveToken(t);
          setState(
            () => _api = Api(
              token: t,
              onUnauthorized: _logout,
              baseUrl: Api.loadBaseUrl(),
            ),
          );
        },
      );
    }
    return MainShell(
      api: api,
      onLogout: _logout,
      preferences: widget.preferences,
      onPreferencesChanged: widget.onPreferencesChanged,
    );
  }
}

// ==================================================================
// شاشة الاتصال: فحص التوكن ضد /overview قبل الدخول
// ==================================================================
class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    required this.onConnected,
  });

  final ValueChanged<String> onConnected;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _c = TextEditingController();
  late final TextEditingController _baseUrl;
  bool _busy = false;
  String? _err;

  @override
  void initState() {
    super.initState();
    _baseUrl = TextEditingController(
      text:
          Api.loadBaseUrl() ??
          const String.fromEnvironment(
            'ADMIN_API_BASE_URL',
            defaultValue: 'https://mrw-bot.onrender.com',
          ),
    );
  }

  @override
  void dispose() {
    _c.dispose();
    _baseUrl.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final t = _c.text.trim();
    final base = _baseUrl.text.trim().replaceFirst(RegExp(r'/+$'), '');
    if (t.isEmpty) {
      setState(() => _err = 'أدخل توكن الإدارة');
      return;
    }
    final parsedBase = Uri.tryParse(base);
    if (parsedBase == null ||
        !parsedBase.hasScheme ||
        (parsedBase.scheme != 'https' && parsedBase.scheme != 'http') ||
        parsedBase.host.isEmpty) {
      setState(
        () => _err = 'أدخل عنوان API صحيحًا يبدأ بـ https:// أو http://',
      );
      return;
    }
    Api.saveBaseUrl(base);
    setState(() {
      _busy = true;
      _err = null;
    });
    final probe = Api(token: t, onUnauthorized: null, baseUrl: base);
    try {
      final result = await probe.get('/api/admin/overview');
      if (result['ok'] != true) {
        throw ApiException(502, (result['error'] ?? 'bad_response').toString());
      }
      if (!mounted) return;
      widget.onConnected(t);
    } catch (e) {
      if (mounted) {
        setState(() {
          _err = errMsg(e);
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          Positioned(top: 4, left: 4, child: ThemeMenu()),
          Positioned(
            top: 4,
            right: 4,
            child: IconButton(
              tooltip: 'الحقوق والإصدار',
              onPressed: () => showCredits(context),
              icon: const Icon(Icons.info_outline, color: Colors.white70),
            ),
          ),
          Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 430),
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(26),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Icon(
                          Icons.shield_outlined,
                          size: 46,
                          color: AdminTheme.accent,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'لوحة الإدارة',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.headlineSmall,
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'منظومة استقبال البلاغات العسكرية',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.white38),
                        ),
                        const SizedBox(height: 20),
                        TextField(
                          controller: _c,
                          obscureText: true,
                          autofocus: true,
                          autocorrect: false,
                          enableSuggestions: false,
                          onSubmitted: (_) => _connect(),
                          decoration: const InputDecoration(
                            labelText: 'توكن الإدارة',
                            prefixIcon: Icon(Icons.key_outlined),
                          ),
                        ),
                        if (_err != null) ...[
                          const SizedBox(height: 10),
                          Text(
                            _err!,
                            style: const TextStyle(color: Color(0xFFE06C75)),
                          ),
                        ],
                        const SizedBox(height: 14),
                        FilledButton.icon(
                          onPressed: _busy ? null : _connect,
                          icon: _busy
                              ? const SizedBox(
                                  width: 18,
                                  height: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white,
                                  ),
                                )
                              : const Icon(Icons.login),
                          label: const Text('الاتصال'),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'يُحفظ في هذا المتصفح ويُرسل إلى خدمة الإدارة عبر HTTPS فقط',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.white24, fontSize: 12),
                        ),
                      ],
                    ),
 ),
                ),
              ),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 8,
            child: Text(
              '${AppCredits.footer} · ${AppCredits.versionLabel}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white24, fontSize: 11),
            ),
          ),
        ],
      ),
    );
  }
}
