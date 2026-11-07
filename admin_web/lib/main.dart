// لوحة الإدارة — Flutter Web تُخدم من المساحة على /admin.
// العربية RTL أولاً: شاشة اتصال بالتوكن ثم الهيكل الرئيسي بثماني صفحات.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'api.dart';
import 'admin_shell.dart';
import 'app_credits.dart';
import 'app_theme.dart';
import 'mobile_app_update_service.dart';
import 'platform/admin_api_client.dart';
import 'platform/admin_storage.dart';
import 'theme_menu.dart';
import 'widgets.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AdminStorage.initialize();
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

class _AdminGateState extends State<AdminGate> with WidgetsBindingObserver {
  AdminApi? _api;
  bool _loading = true;
  bool _mobileLoading = false;
  bool _mobileChecking = false;
  bool _sessionLoaded = false;
  bool _downloadingUpdate = false;
  int _downloadedBytes = 0;
  int? _totalBytes;
  String? _storageError;
  String? _mobileError;
  String? _mobileNotice;
  String? _downloadError;
  String? _installedVersion;
  String? _skippedUpdateVersion;
  Map<String, dynamic>? _mobilePolicy;
  Timer? _mobilePolicyTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (_isAndroid) {
      unawaited(_checkMobilePolicy(startup: true));
      _mobilePolicyTimer = Timer.periodic(
        const Duration(seconds: 30),
        (_) => unawaited(_checkMobilePolicy()),
      );
    } else {
      unawaited(_restoreSession());
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _isAndroid) {
      unawaited(_checkMobilePolicy());
    }
  }

  bool get _isAndroid =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  String get _apiBaseUrl => Api.loadBaseUrl() ?? Api.defaultBaseUrl;

  bool get _hasNewVersion {
    final latest = _mobilePolicy?['latest_version']?.toString() ?? '';
    final installed = _installedVersion ?? '';
    if (latest.isEmpty || installed.isEmpty) return false;
    final currentParts = latest.split('.').map(int.tryParse).toList();
    final installedParts = installed.split('.').map(int.tryParse).toList();
    if (currentParts.any((part) => part == null) ||
        installedParts.any((part) => part == null)) {
      return false;
    }
    final length = currentParts.length > installedParts.length
        ? currentParts.length
        : installedParts.length;
    for (var index = 0; index < length; index++) {
      final current = index < currentParts.length ? currentParts[index]! : 0;
      final installed = index < installedParts.length
          ? installedParts[index]!
          : 0;
      if (current != installed) return current > installed;
    }
    return false;
  }

  bool get _mobileAccessBlocked {
    final status = _mobilePolicy?['status']?.toString() ?? 'active';
    final requiredUpdate =
        _hasNewVersion && _mobilePolicy?['update_mode'] == 'required';
    final optionalUpdate =
        _hasNewVersion &&
        _mobilePolicy?['update_mode'] == 'optional' &&
        _skippedUpdateVersion != _mobilePolicy?['latest_version']?.toString();
    return status != 'active' || requiredUpdate || optionalUpdate;
  }

  Future<void> _checkMobilePolicy({bool startup = false}) async {
    if (!_isAndroid || _mobileChecking || _downloadingUpdate) return;
    _mobileChecking = true;
    if (mounted && startup) {
      setState(() {
        _mobileLoading = true;
        _mobileError = null;
      });
    }
    try {
      final policy = await MobileAppUpdateService.fetchPolicy(_apiBaseUrl);
      final installed = await MobileAppUpdateService.installedVersion();
      if (!mounted) return;
      setState(() {
        _mobilePolicy = policy;
        _installedVersion = installed;
        _mobileError = null;
      });
      if (!_mobileAccessBlocked && !_sessionLoaded) {
        await _restoreSession();
      }
    } catch (error) {
      if (mounted) setState(() => _mobileError = errMsg(error));
    } finally {
      _mobileChecking = false;
      if (mounted && startup) setState(() => _mobileLoading = false);
    }
  }

  Future<void> _restoreSession() async {
    if (_sessionLoaded) return;
    try {
      final token = await Api.loadToken();
      if (token != null && token.isNotEmpty) {
        _api = Api(
          token: token,
          onUnauthorized: _logout,
          baseUrl: Api.loadBaseUrl(),
        );
      }
    } catch (error, stackTrace) {
      debugPrint('تعذرت استعادة جلسة الإدارة: $error\n$stackTrace');
      _storageError = 'تعذرت قراءة جلسة الإدارة المحفوظة';
    }
    _sessionLoaded = true;
    if (mounted) setState(() => _loading = false);
  }

  void _logout() {
    unawaited(Api.clearToken());
    if (mounted) setState(() => _api = null);
  }

  Future<void> _skipOptionalUpdate() async {
    _skippedUpdateVersion = _mobilePolicy?['latest_version']?.toString();
    setState(() {});
    await _restoreSession();
  }

  Future<void> _downloadUpdate() async {
    final policy = _mobilePolicy;
    if (policy == null) return;
    setState(() {
      _downloadingUpdate = true;
      _downloadedBytes = 0;
      _totalBytes = null;
      _mobileError = null;
      _mobileNotice = null;
      _downloadError = null;
    });
    try {
      final installerStatus = await MobileAppUpdateService.downloadAndInstall(
        url: policy['apk_url']?.toString() ?? '',
        expectedSha256: policy['sha256']?.toString() ?? '',
        onProgress: (downloaded, total) {
          if (!mounted) return;
          setState(() {
            _downloadedBytes = downloaded;
            _totalBytes = total;
          });
        },
      );
      if (mounted) {
        setState(() {
          _mobileNotice = installerStatus == 'permission_required'
              ? 'افتح Android إعدادات السماح بالتثبيت لهذا التطبيق، ثم عُد '
                    'واضغط «تنزيل وتثبيت» مجددًا لفتح المُثبّت الرسمي.'
              : 'عُرض مُثبّت Android. أكّد التثبيت للمتابعة.';
        });
      }
    } catch (error) {
      if (mounted) setState(() => _downloadError = errMsg(error));
    } finally {
      if (mounted) setState(() => _downloadingUpdate = false);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _mobilePolicyTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_isAndroid && _mobileLoading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (_isAndroid && _mobileError != null) {
      return _mobileMessage(
        context,
        icon: Icons.cloud_off_outlined,
        title: 'تعذر التحقق من التطبيق',
        message:
            'لم نتمكن من قراءة سياسة تطبيق الهاتف: $_mobileError\n'
            'تحقق من اتصال الإنترنت وحاول مجددًا.',
        actions: [
          FilledButton.icon(
            onPressed: () => unawaited(_checkMobilePolicy(startup: true)),
            icon: const Icon(Icons.refresh),
            label: const Text('إعادة المحاولة'),
          ),
        ],
      );
    }
    if (_isAndroid && _mobileAccessBlocked) {
      final policy = _mobilePolicy ?? const <String, dynamic>{};
      final status = policy['status']?.toString() ?? 'active';
      final hasUpdate = _hasNewVersion;
      final paused = status == 'paused';
      final permanentlyDisabled = status == 'disabled';
      final updateNotes = policy['release_notes']?.toString().trim() ?? '';
      return _mobileMessage(
        context,
        icon: hasUpdate
            ? Icons.system_update_alt
            : Icons.phone_android_outlined,
        title: hasUpdate
            ? 'يتوفر تحديث للتطبيق'
            : permanentlyDisabled
            ? 'تم إيقاف التطبيق'
            : paused
            ? 'التطبيق متوقف مؤقتًا'
            : 'تطبيق الهاتف',
        message: hasUpdate
            ? 'الإصدار المثبت $_installedVersion · الإصدار الجديد '
                  '${policy['latest_version']}\n'
                  '${updateNotes.isEmpty ? '' : '$updateNotes\n'}'
                  '${_mobileNotice == null ? '' : '$_mobileNotice\n'}'
                  '${_downloadError == null ? '' : '$_downloadError\n'}'
                  'سيُنزل الملف داخل التطبيق، ثم يعرض Android شاشة تأكيد التثبيت.'
            : '${policy['status_message']?.toString().trim().isNotEmpty == true ? policy['status_message'] : 'لا يمكن استخدام التطبيق حاليًا.'}'
                  '${_mobileNotice == null ? '' : '\n$_mobileNotice'}',
        actions: [
          if (hasUpdate)
            FilledButton.icon(
              onPressed: _downloadingUpdate ? null : _downloadUpdate,
              icon: _downloadingUpdate
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.download_for_offline_outlined),
              label: Text(
                _downloadingUpdate
                    ? 'جارٍ التنزيل ${_downloadProgressLabel()}'
                    : 'تنزيل وتثبيت',
              ),
            ),
          if (hasUpdate &&
              policy['update_mode'] == 'optional' &&
              status == 'active')
            TextButton(
              onPressed: _skipOptionalUpdate,
              child: const Text('المتابعة دون تحديث'),
            ),
          if (!hasUpdate && !permanentlyDisabled)
            OutlinedButton.icon(
              onPressed: () => unawaited(_checkMobilePolicy(startup: true)),
              icon: const Icon(Icons.refresh),
              label: const Text('التحقق مجددًا'),
            ),
        ],
      );
    }
    if (_loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    final api = _api;
    if (api == null) {
      return LoginScreen(
        storageError: _storageError,
        onConnected: (t) async {
          await Api.saveToken(t);
          if (!mounted) return;
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
      onOwnerTokenChanged: (token) async {
        await Api.saveToken(token);
        if (!mounted) return;
        setState(
          () => _api = Api(
            token: token,
            onUnauthorized: _logout,
            baseUrl: Api.loadBaseUrl(),
          ),
        );
      },
    );
  }

  String _downloadProgressLabel() {
    final total = _totalBytes;
    if (total == null || total <= 0) {
      return '${(_downloadedBytes / (1024 * 1024)).toStringAsFixed(1)} م.ب';
    }
    return '${(_downloadedBytes * 100 / total).toStringAsFixed(0)}٪';
  }

  Widget _mobileMessage(
    BuildContext context, {
    required IconData icon,
    required String title,
    required String message,
    required List<Widget> actions,
  }) {
    final colors = Theme.of(context).colorScheme;
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, size: 44, color: colors.primary),
                const SizedBox(height: 20),
                Text(
                  title,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 12),
                Text(
                  message,
                  textAlign: TextAlign.center,
                  style: TextStyle(height: 1.6, color: colors.onSurfaceVariant),
                ),
                if (_downloadingUpdate) ...[
                  const SizedBox(height: 22),
                  LinearProgressIndicator(
                    value: _totalBytes == null || _totalBytes == 0
                        ? null
                        : _downloadedBytes / _totalBytes!,
                  ),
                ],
                if (actions.isNotEmpty) ...[
                  const SizedBox(height: 24),
                  Wrap(
                    alignment: WrapAlignment.center,
                    spacing: 10,
                    runSpacing: 8,
                    children: actions,
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ==================================================================
// شاشة الاتصال: فحص التوكن ضد /overview قبل الدخول
// ==================================================================
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onConnected, this.storageError});

  final Future<void> Function(String) onConnected;
  final String? storageError;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _c = TextEditingController();
  final TextEditingController _adminName = TextEditingController();
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
    _adminName.dispose();
    _baseUrl.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final credential = _c.text;
    final adminName = _adminName.text.trim();
    final base = _baseUrl.text.trim().replaceFirst(RegExp(r'/+$'), '');
    if (credential.isEmpty) {
      setState(() => _err = 'أدخل كلمة المرور أو توكن الإدارة');
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
    final probe = Api(token: '', onUnauthorized: null, baseUrl: base);
    try {
      final session = await probe.post('/api/admin/login', {
        'name': adminName,
        'credential': credential,
      });
      final token = session['token']?.toString() ?? '';
      if (token.isEmpty) throw ApiException(502, 'bad_response');
      final authenticated = Api(
        token: token,
        onUnauthorized: null,
        baseUrl: base,
      );
      final result = await authenticated.get('/api/admin/overview');
      if (result['ok'] != true) {
        throw ApiException(502, (result['error'] ?? 'bad_response').toString());
      }
      if (!mounted) return;
      await widget.onConnected(token);
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
              icon: const Icon(Icons.info_outline),
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
                        Text(
                          'منظومة استقبال البلاغات العسكرية',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: Theme.of(
                              context,
                            ).colorScheme.onSurfaceVariant,
                          ),
                        ),
                        const SizedBox(height: 20),
                        TextField(
                          controller: _adminName,
                          textInputAction: TextInputAction.next,
                          autocorrect: false,
                          decoration: const InputDecoration(
                            labelText:
                                'اسم حساب المشرف (اتركه فارغًا للدخول بالتوكن)',
                            prefixIcon: Icon(Icons.badge_outlined),
                          ),
                        ),
                        const SizedBox(height: 10),
                        TextField(
                          controller: _c,
                          obscureText: true,
                          autocorrect: false,
                          enableSuggestions: false,
                          onSubmitted: (_) => _connect(),
                          decoration: const InputDecoration(
                            labelText: 'كلمة المرور أو توكن الإدارة',
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
                        Text(
                          'يُحفظ على هذا الجهاز ويُرسل إلى خدمة الإدارة عبر HTTPS',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: Theme.of(
                              context,
                            ).colorScheme.onSurfaceVariant,
                            fontSize: 12,
                          ),
                        ),
                        if (widget.storageError != null) ...[
                          const SizedBox(height: 10),
                          Text(
                            widget.storageError!,
                            textAlign: TextAlign.center,
                            style: const TextStyle(color: Color(0xFFE06C75)),
                          ),
                        ],
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
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
                fontSize: 11,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
