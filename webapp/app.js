/* ==========================================================
   تطبيق بلاغات المخالفات العسكرية — منطق التطبيق
   يعمل داخل تلغرام (Mini App) أو كتطبيق مستقل (PWA)
   ========================================================== */
(function () {
  "use strict";

  /* ---------- أنواع المخالفات ---------- */
  var TYPES = [
    "تجاوز السرعة",
    "مخالفة إشارة",
    "إعاقة الطريق",
    "مخالفة زي",
    "مواد ممنوعة",
    "سلاح أو عنيف",
    "تصوير في مكان ممنوع",
    "أخرى"
  ];

  /* ---------- الحالة ---------- */
  var state = {
    step: "intro",
    type: "",
    lat: null,
    lon: null,
    place: "",
    desc: "",
    link: "",
    platform: "",
    photoName: "",
    photoSize: 0,
    user: null
  };

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  /* ---------- أدوات ---------- */
  function $(id) { return document.getElementById(id); }
  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }
  function status(text) { $("status").textContent = text; }

  /* ---------- منصات التواصل المعروفة (نفس قائمة البوت) ---------- */
  var PLATFORMS = {
    "facebook.com": "فيسبوك",
    "fb.com": "فيسبوك",
    "m.facebook.com": "فيسبوك",
    "instagram.com": "إنستغرام",
    "twitter.com": "إكس (تويتر)",
    "x.com": "إكس (تويتر)",
    "tiktok.com": "تيك توك",
    "snapchat.com": "سناب شات",
    "youtube.com": "يوتيوب",
    "youtu.be": "يوتيوب",
    "t.me": "تيليغرام",
    "wa.me": "واتساب",
    "reddit.com": "ريديت"
  };

  function detectPlatform(url) {
    var host = "";
    try {
      host = new URL(url).hostname.toLowerCase();
    } catch (e) {
      return "";
    }
    if (host.indexOf("www.") === 0) { host = host.slice(4); }
    if (PLATFORMS[host]) { return PLATFORMS[host]; }
    for (var domain in PLATFORMS) {
      if (PLATFORMS.hasOwnProperty(domain) && host.slice(-(domain.length + 1)) === "." + domain) {
        return PLATFORMS[domain];
      }
    }
    return "";
  }

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* ---------- تهيئة تيليغرام ---------- */
  function initTelegram() {
    if (!tg) { return; }
    try {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) { tg.setHeaderColor("#0d1b16"); }
      if (tg.setBackgroundColor) { tg.setBackgroundColor("#0d1b16"); }
      if (tg.disableVerticalSwipes) { tg.disableVerticalSwipes(); }
      if (tg.BackButton) {
        tg.BackButton.onClick(function () { goBack(); });
      }
    } catch (e) { /* تجاهل — التطبيق يعمل خارج تلغرام أيضًا */ }

    var user = (tg.initDataUnsafe && tg.initDataUnsafe.user) || null;
    if (user) {
      state.user = user;
      var name = user.first_name || "";
      if (user.last_name) { name += " " + user.last_name; }
      $("whoami").textContent = "👤 " + name;
    } else {
      $("whoami").textContent = "وضع التطبيق المستقل";
    }
  }

  /* ---------- عرض الخطوات ---------- */
  function render() {
    ["intro", "step1", "step2", "step3", "done"].forEach(function (name) {
      var card = $("card-" + name);
      if (card) { hide(card); }
    });
    show($("card-" + state.step));

    var order = { intro: 0, step1: 1, step2: 2, step3: 3, done: 3 };
    var current = order[state.step] || 0;
    var dots = document.querySelectorAll(".dot");
    for (var i = 0; i < dots.length; i++) {
      var n = i + 1;
      dots[i].className = "dot" + (n === current ? " active" : (n < current ? " done" : ""));
    }

    if (tg && tg.BackButton) {
      if (state.step === "intro" || state.step === "done") { tg.BackButton.hide(); }
      else { tg.BackButton.show(); }
    }
  }

  function go(step) { state.step = step; render(); window.scrollTo(0, 0); }

  function goBack() {
    if (state.step === "step1") { go("intro"); }
    else if (state.step === "step2") { go("step1"); }
    else if (state.step === "step3") { go("step2"); }
  }

  /* ---------- أنواع المخالفات ---------- */
  function buildTypes() {
    var box = $("types");
    box.innerHTML = "";
    TYPES.forEach(function (name) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = name;
      b.addEventListener("click", function () {
        var all = box.querySelectorAll(".chip");
        for (var i = 0; i < all.length; i++) { all[i].classList.remove("on"); }
        b.classList.add("on");
        state.type = name;
      });
      box.appendChild(b);
    });
  }

  /* ---------- الموقع ---------- */
  function locate() {
    if (!navigator.geolocation) {
      $("loc-status").textContent = "متصفحك لا يدعم تحديد الموقع";
      return;
    }
    $("loc-status").textContent = "جارٍ تحديد الموقع…";
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        state.lat = pos.coords.latitude;
        state.lon = pos.coords.longitude;
        var box = $("loc-status");
        box.className = "loc-status ok";
        box.innerHTML = "✅ تم تحديد الموقع" +
          '<span class="loc-map">📍 ' + state.lat.toFixed(5) + ", " + state.lon.toFixed(5) + "</span>";
        status("تم تحديد الموقع");
      },
      function () {
        $("loc-status").textContent = "تعذّر تحديد الموقع — يمكنك كتابة الوصف يدويًا";
        status("تعذّر تحديد الموقع");
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 }
    );
  }

  /* ---------- الصورة ---------- */
  function onPhoto(file) {
    if (!file) { return; }
    if (file.size > 12 * 1024 * 1024) {
      alert("حجم الصورة كبير (الحد 12 ميجابايت). اختر صورة أصغر.");
      return;
    }
    state.photoName = file.name || "صورة";
    state.photoSize = file.size;

    var preview = $("preview");
    var reader = new FileReader();
    reader.onload = function (e) {
      preview.src = e.target.result;
      preview.hidden = false;
      $("drop-label").hidden = true;
      $("drop").classList.add("filled");
    };
    reader.readAsDataURL(file);
  }

  /* ---------- الملخص ---------- */
  function buildSummary() {
    var rows = [
      ["نوع المخالفة", state.type || "غير محدد"],
      ["منصة النشر", state.platform || "غير معروفة"],
      ["الموقع", (state.lat != null) ? (state.lat.toFixed(5) + " , " + state.lon.toFixed(5)) : "غير محدد"],
      ["وصف المكان", state.place || "—"],
      ["الوصف", state.desc || "—"],
      ["رابط المنشور", state.link || "—"],
      ["الصورة", state.photoName ? "✅ مرفقة" : "❌ لم تُرفق بعد"]
    ];
    var html = "";
    rows.forEach(function (r) {
      html += '<div class="row"><b>' + esc(r[0]) + '</b><span>' + esc(r[1]) + '</span></div>';
    });
    $("summary").innerHTML = html;
    $("photo-note").innerHTML = state.photoName
      ? "📎 الصورة جاهزة — ستُرسل مع طلب من البوت في المحادثة (تلغرام يحفظها إلى الأبد)."
      : "ℹ️ لم تُرفق صورة — سيطلب منك البوت إرسالها في المحادثة بعد الإرسال.";
  }

  /* ---------- الإرسال ---------- */
  function payload() {
    return {
      v: 1,
      type: state.type,
      lat: state.lat,
      lon: state.lon,
      place: state.place,
      desc: state.desc,
      link: state.link,
      platform: state.platform,
      photo: state.photoName ? 1 : 0,
      ts: Math.floor(Date.now() / 1000)
    };
  }

  function send() {
    var data = JSON.stringify(payload());

    if (tg && tg.sendData) {
      // الحد الرسمي لبيانات التطبيق: 4096 بايت
      if (data.length > 4000) {
        state.desc = state.desc.slice(0, 600);
        data = JSON.stringify(payload());
      }
      tg.sendData(data);
      $("done-title").textContent = "تم الإرسال ✅";
      $("done-body").textContent = "ارجع إلى المحادثة: سيطلب منك البوت إرسال الصورة ثم يعطيك رقم البلاغ.";
      go("done");
      return;
    }

    // وضع التطبيق المستقل: نستخدم مشاركة تيليغرام
    var text = "بلاغ جديد عبر التطبيق:\n" + data;
    var bot = (tg && tg.initDataUnsafe && tg.initDataUnsafe.bot_username) || "blgh_bot";
    var url = "https://t.me/share/url?url=" + encodeURIComponent("https://t.me/" + bot) +
              "&text=" + encodeURIComponent(text);
    window.open(url, "_blank");
    $("done-title").textContent = "تم فتح تيليغرام";
    $("done-body").textContent = "أرسل الرسالة إلى @blgh_bot ثم أرسل الصورة، وسيصلك رقم البلاغ.";
    go("done");
  }

  /* ---------- التهيئة ---------- */
  function init() {
    initTelegram();
    buildTypes();

    $("btn-begin").addEventListener("click", function () { go("step1"); });
    $("btn-locate").addEventListener("click", locate);
    $("btn-to-2").addEventListener("click", function () {
      state.place = $("place").value.trim();
      go("step2");
    });
    $("btn-to-3").addEventListener("click", function () {
      state.desc = $("desc").value.trim();
      state.link = $("link").value.trim();

      // الرابط إلزامي ضمن نطاق النظام (مخالفات منشورة على وسائل التواصل)
      if (!state.link) {
        $("link-note").textContent = "⚠️ رابط المنشور إلزامي — أرسل رابط المنشور الأصلي.";
        $("link-note").className = "req-note bad";
        $("link").focus();
        return;
      }
      if (!/^https?:\/\/\S{4,500}$/i.test(state.link)) {
        $("link-note").textContent = "⚠️ الرابط غير صالح — يجب أن يبدأ بـ http أو https.";
        $("link-note").className = "req-note bad";
        $("link").focus();
        return;
      }

      state.platform = detectPlatform(state.link);
      $("link-note").textContent = state.platform
        ? "✅ تم التعرّف على منصة النشر: " + state.platform
        : "⚠️ لم يُعرف المصدر — اذكر في الوصف أين نُشر المنشور.";
      $("link-note").className = "req-note" + (state.platform ? " good" : " warn");

      buildSummary();
      go("step3");
    });
    $("btn-send").addEventListener("click", send);
    $("btn-again").addEventListener("click", function () {
      state.type = ""; state.lat = null; state.lon = null;
      state.place = ""; state.desc = ""; state.link = "";
      state.photoName = ""; state.photoSize = 0;
      $("place").value = ""; $("desc").value = ""; $("link").value = "";
      $("preview").hidden = true; $("drop-label").hidden = false;
      $("drop").classList.remove("filled");
      var all = document.querySelectorAll(".chip");
      for (var i = 0; i < all.length; i++) { all[i].classList.remove("on"); }
      go("intro");
    });

    $("desc").addEventListener("input", function () {
      $("desc-counter").textContent = this.value.length + " / 400";
    });

    $("drop").addEventListener("click", function () { $("photo-input").click(); });
    $("photo-input").addEventListener("change", function () { onPhoto(this.files[0]); });

    var backs = document.querySelectorAll("[data-back]");
    for (var i = 0; i < backs.length; i++) {
      backs[i].addEventListener("click", function () {
        var target = this.getAttribute("data-back");
        if (target === "intro") { go("intro"); }
        else { go(target); }
      });
    }

    render();
    status(tg ? "جاهز — داخل تلغرام" : "جاهز — نسخة مستقلة");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
