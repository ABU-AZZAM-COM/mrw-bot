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

class AdminApp extends StatelessWidget {
  const AdminApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<String>(
      valueListenable: themeController,
      builder: (context, themeName, _) {
        return MaterialApp(
          title: 'لوحة الإدارة — ${AppCredits.project}',
          debugShowCheckedModeBanner: false,
          theme: AdminTheme.of(themeName),
          // القاعدة: كل الصفحة من اليمين لليسار — أنظمة RTL لا تحتاج إعداداً لكل ويدجت
          builder: (context, child) => Directionality(
            textDirection: TextDirection.rtl,
            child: child ?? const SizedBox.shrink(),
          ),
          home: const AdminGate(),
        );
      },
    );
  }
}

// ==================================================================
// بوابة الدخول: توكن من التخزين المحلي أو شاشة الاتصال
// ==================================================================
class AdminGate extends StatefulWidget {
  const AdminGate({super.key});

  @override
  State<AdminGate> createState() => _AdminGateState();
}

class _AdminGateState extends State<AdminGate> {
  Api? _api;

  @override
  void initState() {
    super.initState();
    final t = Api.loadToken();
    if (t != null && t.isNotEmpty) {
      _api = Api(token: t, onUnauthorized: _logout);
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
          setState(() => _api = Api(token: t, onUnauthorized: _logout));
        },
      );
    }
    return MainShell(api: api, onLogout: _logout);
  }
}

// ==================================================================
// شاشة الاتصال: فحص التوكن ضد /overview قبل الدخول
// ==================================================================
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onConnected});

  final ValueChanged<String> onConnected;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _c = TextEditingController();
  bool _busy = false;
  String? _err;

  Future<void> _connect() async {
    final t = _c.text.trim();
    if (t.isEmpty) {
      setState(() => _err = 'أدخل توكن الإدارة');
      return;
    }
    setState(() {
      _busy = true;
      _err = null;
    });
    final probe = Api(token: t, onUnauthorized: null);
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
