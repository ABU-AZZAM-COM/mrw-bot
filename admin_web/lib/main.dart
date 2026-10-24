// لوحة الإدارة — Flutter Web تُخدم من المساحة على /admin.
// العربية RTL أولاً: شاشة اتصال بالتوكن ثم الهيكل الرئيسي بثماني صفحات.
import 'package:flutter/material.dart';

import 'api.dart';
import 'admin_shell.dart';
import 'app_theme.dart';
import 'web_api.dart';
import 'widgets.dart';

void main() {
  runApp(const AdminApp());
}

class AdminApp extends StatelessWidget {
  const AdminApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'لوحة الإدارة',
      debugShowCheckedModeBanner: false,
      theme: AdminTheme.dark,
      // القاعدة: كل الصفحة من اليمين لليسار — أنظمة RTL لا تحتاج إعداداً لكل ويدجت
      builder: (context, child) => Directionality(
        textDirection: TextDirection.rtl,
        child: child ?? const SizedBox.shrink(),
      ),
      home: const AdminGate(),
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
        throw ApiException(
          502,
          (result['error'] ?? 'bad_response').toString(),
        );
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
      body: Center(
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
                      color: const Color(0xFF3FA66B),
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
    );
  }
}
