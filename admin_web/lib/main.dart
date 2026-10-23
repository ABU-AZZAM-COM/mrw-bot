// لوحة الإدارة — Flutter Web تُخدم من المساحة على /admin.
// العربية RTL أولاً: شاشة اتصال بالتوكن ثم الهيكل الرئيسي بسبع صفحات.
import 'package:flutter/material.dart';

import 'api.dart';
import 'pages.dart';
import 'pages2.dart';
import 'widgets.dart';

// استيراد صفحة المُقيَّدون (من pages2.dart)
export 'pages2.dart' show BlockedPage;

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
      theme: ThemeData(
        useMaterial3: true,
        fontFamily: 'NotoSansArabic',
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF3FA66B),
          brightness: Brightness.dark,
          surface: const Color(0xFF132119),
        ),
        scaffoldBackgroundColor: const Color(0xFF0A1410),
        dividerColor: const Color(0xFF24382F),
        snackBarTheme: const SnackBarThemeData(
            behavior: SnackBarBehavior.floating),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xFF0F1D17),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: const BorderSide(color: Color(0xFF31473C)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: const BorderSide(color: Color(0xFF31473C)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: const BorderSide(color: Color(0xFF3FA66B)),
          ),
        ),
      ),
      // القاعدة: كل الصفحة من اليمين لليسار — أنظمة RTL لا تحتاج إعداداً لكل ويدجت
      builder: (context, child) => Directionality(
          textDirection: TextDirection.rtl,
          child: child ?? const SizedBox.shrink()),
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
      await probe.get('/api/admin/overview');
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
                    Icon(Icons.shield_outlined,
                        size: 46, color: const Color(0xFF3FA66B)),
                    const SizedBox(height: 8),
                    Text('لوحة الإدارة',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.headlineSmall),
                    const SizedBox(height: 4),
                    const Text('منظومة استقبال البلاغات العسكرية',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: Colors.white38)),
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
                      Text(_err!,
                          style: const TextStyle(color: Color(0xFFE06C75))),
                    ],
                    const SizedBox(height: 14),
                    FilledButton.icon(
                      onPressed: _busy ? null : _connect,
                      icon: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2, color: Colors.white))
                          : const Icon(Icons.login),
                      label: const Text('الاتصال'),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'يُخزَّن التوكن في متصفحك فقط ولا يُرسل لأي طرف آخر',
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

// ==================================================================
// الهيكل الرئيسي: شريط جانبي + 7 صفحات
// ==================================================================
class MainShell extends StatefulWidget {
  const MainShell({super.key, required this.api, required this.onLogout});

  final Api api;
  final VoidCallback onLogout;

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  int _i = 0;

  static const List<String> _titles = [
    'نظرة عامة',
    'البلاغات',
    'المتبلّغون',
    'التشغيل',
    'الرسائل',
    'التصنيفات',
    'سجل التدقيق',
    'المُقيَّدون',
  ];

  static const List<IconData> _railIcons = [
    Icons.dashboard_outlined,
    Icons.report_outlined,
    Icons.people_outline,
    Icons.emergency_outlined,
    Icons.campaign_outlined,
    Icons.category_outlined,
    Icons.history_outlined,
    Icons.block,
  ];

  @override
  Widget build(BuildContext context) {
    final api = widget.api;
    final pages = <Widget>[
      OverviewPage(api: api),
      ReportsPage(api: api),
      UsersPage(api: api),
      OperationsPage(api: api),
      MessagingPage(api: api),
      TypesPage(api: api),
      AuditPage(api: api),
      BlockedPage(api: api),
    ];
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_i],
            style: const TextStyle(fontWeight: FontWeight.w700)),
        actions: [
          IconButton(
            tooltip: 'خروج',
            onPressed: () {
              Api.clearToken();
              widget.onLogout();
            },
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: _i,
            onDestinationSelected: (i) => setState(() => _i = i),
            labelType: NavigationRailLabelType.all,
            groupAlignment: -1,
            destinations: [
              for (int k = 0; k < _titles.length; k++)
                NavigationRailDestination(
                  icon: Icon(_railIcons[k]),
                  selectedIcon: Icon(_railIcons[k]),
                  label: Text(_titles[k]),
                ),
            ],
          ),
          const VerticalDivider(width: 1),
          Expanded(child: pages[_i]),
        ],
      ),
    );
  }
}
