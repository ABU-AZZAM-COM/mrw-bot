/* ==========================================================
   تطبيق بلاغات المخالفات العسكرية — منطق التطبيق
   يعمل داخل تلغرام (Mini App) أو كتطبيق مستقل (PWA)
   ========================================================== */
(function () {
  "use strict";

  /* ---------- أنواع المخالفات ---------- */
  var TYPES = [];
  var CATEGORIES = [];
  var TYPE_GROUPS = {};
  var MAX_DESC_LEN = 1500;
  var MIN_DESC_LEN = 40;
  var MIN_DESC_WORDS = 6;
  var MAX_PHOTO_MB = 10;

  /* ---------- الحالة ---------- */
  var state = {
    step: "intro",
    category: "",
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
  var chatAfterId = 0;
  var chatPolling = null;
  var chatLoaded = false;

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
    "instagr.am": "إنستغرام",
    "twitter.com": "إكس (تويتر)",
    "x.com": "إكس (تويتر)",
    "tiktok.com": "تيك توك",
    "snapchat.com": "سناب شات",
    "youtube.com": "يوتيوب",
    "youtu.be": "يوتيوب",
    "t.me": "تيليغرام",
    "telegram.me": "تيليغرام",
    "wa.me": "واتساب",
    "reddit.com": "ريديت",
    "tumblr.com": "تَمبلر",
    "pinterest.com": "بنترست",
    "linkedin.com": "لينكدإن"
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
    if (!tg) {
      $("whoami").textContent = "وضع التطبيق المستقل";
      return;
    }
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
    ["intro", "step1", "step2", "step3", "done", "contact", "settings"].forEach(function (name) {
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
    var navButtons = document.querySelectorAll("[data-open-view]");
    for (var j = 0; j < navButtons.length; j++) {
      navButtons[j].classList.toggle(
        "primary",
        navButtons[j].getAttribute("data-open-view") ===
          (state.step === "contact" || state.step === "settings" ? state.step : "report")
      );
    }
  }

  function go(step) { state.step = step; render(); window.scrollTo(0, 0); }

  function goBack() {
    if (state.step === "step1") { go("intro"); }
    else if (state.step === "step2") { go("step1"); }
    else if (state.step === "step3") { go("step2"); }
    else if (state.step === "contact" || state.step === "settings") { go("intro"); }
  }

  /* ---------- أنواع المخالفات ---------- */
  function buildTypes() {
    var categoryBox = $("categories");
    categoryBox.innerHTML = "";
    $("types").innerHTML = "";
    $("types").hidden = true;
    $("types-label").hidden = true;
    CATEGORIES.forEach(function (category) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.textContent = category.label;
      button.addEventListener("click", function () {
        var all = categoryBox.querySelectorAll(".chip");
        for (var i = 0; i < all.length; i++) { all[i].classList.remove("on"); }
        button.classList.add("on");
        state.category = category.id;
        state.type = "";
        buildCategoryTypes(category);
      });
      categoryBox.appendChild(button);
    });
  }

  function buildCategoryTypes(category) {
    var box = $("types");
    box.innerHTML = "";
    (TYPE_GROUPS[category.id] || []).forEach(function (name) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.textContent = name;
      button.addEventListener("click", function () {
        var all = box.querySelectorAll(".chip");
        for (var i = 0; i < all.length; i++) { all[i].classList.remove("on"); }
        button.classList.add("on");
        state.type = name;
      });
      box.appendChild(button);
    });
    $("types-label").textContent = "اختر النوع داخل " + category.label;
    $("types-label").hidden = false;
    box.hidden = false;
  }

  function loadTypes() {
    $("btn-begin").disabled = true;
    $("types-error").textContent = "جارٍ تحميل التصنيفات...";
    return fetch("/api/public/vtypes", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) { throw new Error("types_http_" + response.status); }
        return response.json();
      })
      .then(function (result) {
        if (!result || result.ok !== true || !Array.isArray(result.items) ||
            result.items.length === 0 ||
            !result.items.every(function (item) {
              return typeof item === "string" && item.trim().length > 0;
            }) || !Array.isArray(result.categories) ||
            !result.categories.length || !result.groups ||
            typeof result.groups !== "object") {
          throw new Error("bad_types_response");
        }
        TYPES = result.items;
        CATEGORIES = result.categories.filter(function (category) {
          return category && typeof category.id === "string" &&
            typeof category.label === "string" &&
            Array.isArray(result.groups[category.id]) &&
            result.groups[category.id].length > 0 &&
            result.groups[category.id].every(function (item) {
              return TYPES.indexOf(item) !== -1;
            });
        });
        if (!CATEGORIES.length) { throw new Error("bad_category_groups"); }
        TYPE_GROUPS = result.groups;
        MAX_DESC_LEN = Number.isInteger(result.max_desc_len) && result.max_desc_len > 0
          ? result.max_desc_len : MAX_DESC_LEN;
        MIN_DESC_LEN = Number.isInteger(result.min_desc_len) && result.min_desc_len > 0
          ? result.min_desc_len : MIN_DESC_LEN;
        MIN_DESC_WORDS = Number.isInteger(result.min_desc_words) && result.min_desc_words > 0
          ? result.min_desc_words : MIN_DESC_WORDS;
        MAX_PHOTO_MB = Number.isInteger(result.max_photo_mb) && result.max_photo_mb > 0
          ? result.max_photo_mb : MAX_PHOTO_MB;
        buildTypes();
        $("desc").maxLength = MAX_DESC_LEN;
        $("desc-counter").textContent = "0 / " + MAX_DESC_LEN;
        $("desc-help").textContent =
          "اكتب " + MIN_DESC_LEN + " حرفًا على الأقل و" + MIN_DESC_WORDS + " كلمات على الأقل.";
        $("types-error").textContent = "";
        $("btn-begin").disabled = false;
      })
      .catch(function (error) {
        console.error("تعذر تحميل تصنيفات البلاغ:", error);
        $("types-error").textContent =
          "تعذر تحميل تصنيفات البلاغ من الخادم. تحقق من الاتصال ثم أعد تحميل الصفحة.";
        status("التطبيق غير جاهز — تعذر تحميل التصنيفات");
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
    if (file.size > MAX_PHOTO_MB * 1024 * 1024) {
      alert("حجم الصورة كبير (الحد " + MAX_PHOTO_MB + " ميجابايت). اختر صورة أصغر.");
      return;
    }
    state.photoName = file.name || "صورة";
    state.photoSize = file.size;

    var preview = $("preview");
    var reader = new FileReader();
    reader.onload = function (e) {
      preview.style.backgroundImage = "url('" + e.target.result + "')";
      preview.hidden = false;
      $("drop-label").hidden = true;
      $("drop").classList.add("filled");
    };
    reader.readAsDataURL(file);
  }

  /* ---------- الملخص ---------- */
  function buildSummary() {
    var selectedCategory = CATEGORIES.find(function (category) {
      return category.id === state.category;
    });
    var rows = [
      ["نوع المخالفة", state.type || "غير محدد"],
      ["القسم", selectedCategory ? selectedCategory.label : "غير محدد"],
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
      category: state.category,
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

  function utf8Size(value) {
    if (window.TextEncoder) { return new TextEncoder().encode(value).length; }
    return new Blob([value]).size;
  }

  // المسار الآمن للخادم: يعمل من أي طريقة فتح (زر القائمة ☰ · زر لوحة الرد · رابط مباشر).
  // الخادم يتحقق من توقيع تيليغرام (initData) فلا يُقبل طلب مزوّر.
  function postToServer(p) {
    var initData = (tg && tg.initData) || "";
    if (!initData) { return Promise.resolve({ ok: false, error: "no_init_data" }); }
    return fetch("/api/app-data", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: initData, payload: p })
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false }; });
    }).catch(function () {
      return { ok: false, error: "network" };
    });
  }

  function showDone(title, body, closeAfter) {
    $("done-title").textContent = title;
    $("done-body").textContent = body;
    go("done");
    if (closeAfter) {
      setTimeout(function () {
        try { if (tg && tg.close) { tg.close(); } } catch (e) { /* تجاهل */ }
      }, closeAfter);
    }
  }

  function send() {
    var p = payload();
    var data = JSON.stringify(p);

    status("جارٍ الإرسال…");
    postToServer(p).then(function (res) {
      if (res && res.ok) {
        showDone(
          "تم الإرسال ✅",
          "ارجع إلى المحادثة: سيطلب منك البوت إرسال الصورة ثم يعطيك رقم البلاغ.",
          1400
        );
        return;
      }
      fallbackSend(data);
    });
  }

  function userApi(path, options) {
        options = options || {};
        options.headers = options.headers || {};
        if (tg && tg.initData) {
          options.headers["X-Telegram-Init-Data"] = tg.initData;
        }
        return fetch(path, options).then(function (response) {
          return response.json().catch(function () { return { ok: false }; })
            .then(function (result) {
              if (!response.ok || !result || result.ok !== true) {
                throw new Error(result && result.error ? result.error : "request_failed");
              }
              return result;
            });
        });
      }

  function openView(view) {
        if (view === "report") {
          if (chatPolling) { clearInterval(chatPolling); chatPolling = null; }
          go("intro");
          return;
        }
        go(view);
        if (view === "contact") {
          loadContactSettings();
          loadChatMessages(true);
          if (chatPolling) { clearInterval(chatPolling); }
          chatPolling = setInterval(function () { loadChatMessages(false); }, 8000);
        } else if (view === "settings") {
          if (chatPolling) { clearInterval(chatPolling); chatPolling = null; }
          loadUserSettings();
        }
      }

  function loadContactSettings() {
        if (!tg || !tg.initData) {
          $("contact-state").textContent = "يتطلب Telegram";
          $("contact-state").className = "status-pill closed";
          $("contact-notice").textContent = "افتح هذه الصفحة من تطبيق البوت في Telegram لبدء محادثة آمنة.";
          $("chat-send").disabled = true;
          return;
        }
        userApi("/api/user/settings", { cache: "no-store" }).then(function (result) {
          var open = result.can_send_contact === true;
          $("contact-state").textContent = open ? "متاح" : "غير متاح";
          $("contact-state").className = "status-pill " + (open ? "open" : "closed");
          $("contact-notice").textContent = open
            ? "تُحفظ رسائلك في محادثة خاصة واحدة مع الإدارة."
            : "التواصل غير متاح لهذا الحساب حاليًا. يمكنك قراءة سجل المحادثة.";
          $("chat-text").disabled = !open;
          $("chat-file").disabled = !open;
          $("chat-send").disabled = !open;
          $("chat-form").setAttribute("data-can-send", open ? "1" : "0");
        }).catch(function (error) {
          $("contact-state").textContent = "تعذّر التحقق";
          $("contact-state").className = "status-pill closed";
          $("contact-notice").textContent = "تعذّر تحميل حالة التواصل. أعد فتح الصفحة وحاول مجددًا.";
          $("chat-send").disabled = true;
          console.error("تعذر تحميل حالة التواصل:", error);
        });
      }

  function appendChatMessage(item) {
        var feed = $("chat-feed");
        var bubble = document.createElement("article");
        bubble.className = "chat-message" + (item.direction === "out" ? " outgoing" : "");
        bubble.appendChild(document.createTextNode(item.is_deleted ? "تم حذف هذه الرسالة" : (item.text || "")));
        if (item.has_media && !item.is_deleted) {
          var link = document.createElement("a");
          link.className = "chat-attachment";
          link.href = "#";
          link.textContent = "📎 " + (item.filename || "عرض المرفق");
          link.addEventListener("click", function (event) {
            event.preventDefault();
            fetch("/api/user/messages/" + encodeURIComponent(item.id) + "/attachment", {
              headers: tg && tg.initData ? { "X-Telegram-Init-Data": tg.initData } : {}
            }).then(function (response) {
              if (!response.ok) { throw new Error("attachment_download_failed"); }
              return response.blob();
            }).then(function (blob) {
              var objectUrl = URL.createObjectURL(blob);
              var download = document.createElement("a");
              download.href = objectUrl;
              download.download = item.filename || "attachment";
              document.body.appendChild(download);
              download.click();
              download.remove();
              setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 1000);
            }).catch(function (error) {
              console.error("تعذر تنزيل المرفق:", error);
              status("تعذر تنزيل المرفق");
            });
          });
          bubble.appendChild(document.createElement("br"));
          bubble.appendChild(link);
        }
        if (item.created_at) {
          var time = document.createElement("time");
          time.textContent = new Date(item.created_at).toLocaleString("ar");
          bubble.appendChild(time);
        }
        feed.appendChild(bubble);
      }

  function loadChatMessages(reset) {
        if (!tg || !tg.initData) { return; }
        if (reset) {
          chatAfterId = 0;
          chatLoaded = false;
          $("chat-feed").textContent = "";
        }
        userApi("/api/user/messages?after_id=" + chatAfterId, { cache: "no-store" })
          .then(function (result) {
            var items = Array.isArray(result.items) ? result.items : [];
            items.forEach(function (item) {
              appendChatMessage(item);
              chatAfterId = Math.max(chatAfterId, Number(item.id) || 0);
            });
            if (items.length) { $("chat-feed").scrollTop = $("chat-feed").scrollHeight; }
            chatLoaded = true;
          })
          .catch(function (error) {
            if (!chatLoaded) {
              $("chat-feed").textContent = "تعذّر تحميل المحادثة. تحقق من الاتصال ثم أعد المحاولة.";
            }
            console.error("تعذر تحميل المحادثة:", error);
          });
      }

  function sendChatMessage(event) {
        event.preventDefault();
        if ($("chat-form").getAttribute("data-can-send") !== "1") { return; }
        var text = $("chat-text").value.trim();
        var file = $("chat-file").files[0];
        if (!file && !text) { return; }
        if (file && file.size > 20 * 1024 * 1024) {
          alert("حجم المرفق يتجاوز الحد الأقصى وهو 20 ميغابايت.");
          return;
        }
        $("chat-send").disabled = true;
        var request;
        if (file) {
          var form = new FormData();
          form.append("file", file);
          form.append("text", text);
          request = userApi("/api/user/messages/attachment", { method: "POST", body: form });
        } else {
          request = userApi("/api/user/messages", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text })
          });
        }
        request.then(function () {
          $("chat-text").value = "";
          $("chat-file").value = "";
          $("chat-file-name").textContent = "";
          loadChatMessages(false);
        }).catch(function (error) {
          var message = error.message === "contact_unavailable"
            ? "التواصل غير متاح لهذا الحساب حاليًا."
            : error.message === "unsupported_attachment"
            ? "نوع الملف غير مسموح. أرسل صورة أو PDF أو مستند Office."
            : "تعذّر إرسال الرسالة. حاول مرة أخرى.";
          status(message);
          console.error("تعذر إرسال رسالة التواصل:", error);
        }).then(function () {
          $("chat-send").disabled = $("chat-form").getAttribute("data-can-send") !== "1";
        });
      }

  function loadUserSettings() {
        if (!tg || !tg.initData) {
          $("verification-summary").textContent = "افتح التطبيق من Telegram لتحميل إعدادات حسابك.";
          return;
        }
        userApi("/api/user/settings", { cache: "no-store" }).then(function (result) {
          var preferences = result.preferences || {};
          $("setting-reply-alerts").checked = preferences.telegram_reply_alerts !== false;
          $("setting-publication-alerts").checked = preferences.publication_alerts !== false;
          $("setting-reply-alerts").disabled = false;
          $("setting-publication-alerts").disabled = false;
          var verification = result.verification;
          if (!verification) {
            $("verification-summary").textContent = "لم يبدأ طلب توثيق لهذا الحساب.";
            return;
          }
          var labels = {
            verified: "موثّق",
            pending: "قيد المراجعة",
            capturing: "استكمال خطوات التوثيق",
            rejected: "مرفوض"
          };
          var summary = "الحالة: " + (labels[verification.status] || verification.status || "غير محددة");
          if (verification.message) { summary += "\nرسالة المشرف: " + verification.message; }
          if (verification.reason) { summary += "\nسبب الرفض: " + verification.reason; }
          $("verification-summary").textContent = summary;
        }).catch(function (error) {
          $("verification-summary").textContent = "تعذّر تحميل الإعدادات. حاول مجددًا.";
          console.error("تعذر تحميل الإعدادات:", error);
        });
      }

  function saveUserSettings() {
        userApi("/api/user/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            telegram_reply_alerts: $("setting-reply-alerts").checked,
            publication_alerts: $("setting-publication-alerts").checked
          })
        }).then(function () {
          status("تم حفظ الإعدادات");
        }).catch(function (error) {
          status("تعذر حفظ الإعدادات");
          console.error("تعذر حفظ الإعدادات:", error);
          loadUserSettings();
        });
  }

  function fallbackSend(data) {
    // احتياطي: الإرسال الأصلي عبر تيليغرام (يعمل عند الفتح من زر لوحة الرد)
    if (tg && tg.sendData) {
      if (utf8Size(data) > 4000) {
        status("تعذر الاتصال بالخادم والبيانات أكبر من حد تيليغرام. تحقق من الاتصال ثم أعد المحاولة.");
        return;
      }
      try {
        tg.sendData(data);
        showDone(
          "تم الإرسال ✅",
          "ارجع إلى المحادثة: سيطلب منك البوت إرسال الصورة ثم يعطيك رقم البلاغ.",
          0
        );
        return;
      } catch (e) { /* نكمل إلى الخيار الأخير */ }
    }

    // وضع التطبيق المستقل (بلا تيليغرام): مشاركة عبر رابط تيليغرام
    var text = "بلاغ جديد عبر التطبيق:\n" + data;
    var bot = (tg && tg.initDataUnsafe && tg.initDataUnsafe.bot_username) || "blgh_bot";
    var url = "https://t.me/share/url?url=" + encodeURIComponent("https://t.me/" + bot) +
              "&text=" + encodeURIComponent(text);
    window.open(url, "_blank");
    showDone(
      "تم فتح تيليغرام",
      "أرسل الرسالة إلى @blgh_bot ثم أرسل الصورة، وسيصلك رقم البلاغ.",
      0
    );
  }

  /* ---------- التهيئة ---------- */
  function init() {
    initTelegram();
    loadTypes();

    $("btn-begin").addEventListener("click", function () { go("step1"); });
    $("btn-locate").addEventListener("click", locate);
    $("btn-to-2").addEventListener("click", function () {
      if (!state.category || !state.type) {
        $("taxonomy-error").textContent = "اختر القسم والنوع أولًا.";
        return;
      }
      $("taxonomy-error").textContent = "";
      state.place = $("place").value.trim();
      go("step2");
    });
    $("btn-to-3").addEventListener("click", function () {
      state.desc = $("desc").value.trim();
      state.link = $("link").value.trim();

      var wordCount = state.desc ? state.desc.split(/\s+/).length : 0;
      if (state.desc.length < MIN_DESC_LEN || wordCount < MIN_DESC_WORDS) {
        $("desc-note").textContent =
          "⚠️ الوصف يحتاج إلى " + MIN_DESC_LEN + " حرفًا و" + MIN_DESC_WORDS + " كلمات على الأقل.";
        $("desc-note").className = "req-note bad";
        $("desc").focus();
        return;
      }
      $("desc-note").textContent = "";
      $("desc-note").className = "req-note";

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
      state.category = "";
      state.place = ""; state.desc = ""; state.link = "";
      state.photoName = ""; state.photoSize = 0;
      $("place").value = ""; $("desc").value = ""; $("link").value = "";
      $("preview").hidden = true; $("preview").style.backgroundImage = "";
      $("drop-label").hidden = false;
      $("drop").classList.remove("filled");
      $("photo-input").value = "";
      $("types").hidden = true;
      $("types-label").hidden = true;
      $("taxonomy-error").textContent = "";
      var all = document.querySelectorAll(".chip");
      for (var i = 0; i < all.length; i++) { all[i].classList.remove("on"); }
      go("intro");
    });

    $("desc").addEventListener("input", function () {
      $("desc-counter").textContent = this.value.length + " / " + MAX_DESC_LEN;
    });

    $("drop").addEventListener("click", function () { $("photo-input").click(); });
    $("drop").addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        $("photo-input").click();
      }
    });
    $("photo-input").addEventListener("change", function () { onPhoto(this.files[0]); });
    $("chat-form").addEventListener("submit", sendChatMessage);
    $("chat-file").addEventListener("change", function () {
      $("chat-file-name").textContent = this.files[0] ? this.files[0].name : "";
    });
    $("setting-reply-alerts").addEventListener("change", saveUserSettings);
    $("setting-publication-alerts").addEventListener("change", saveUserSettings);
    var viewButtons = document.querySelectorAll("[data-open-view]");
    for (var v = 0; v < viewButtons.length; v++) {
      viewButtons[v].addEventListener("click", function () {
        openView(this.getAttribute("data-open-view"));
      });
    }

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
    var requestedView = new URLSearchParams(window.location.search).get("view");
    if (requestedView === "contact" || requestedView === "settings") {
      openView(requestedView);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
