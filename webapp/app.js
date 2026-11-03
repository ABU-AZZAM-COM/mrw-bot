(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp
    ? window.Telegram.WebApp
    : null;
  var state = {
    canSend: false,
    loaded: false,
    loading: false,
    loadingOlder: false,
    sending: false,
    beforeId: 0,
    lastId: 0,
    pollTimer: null
  };
  var MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
  var ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".docx", ".xlsx", ".pptx"];

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(message, isError) {
    var status = $("chat-status");
    status.textContent = message || "";
    status.classList.toggle("error", Boolean(isError));
    status.hidden = !message;
  }

  function setContactNotice(message, isError) {
    var notice = $("contact-notice");
    notice.textContent = message || "";
    notice.classList.toggle("error", Boolean(isError));
    notice.hidden = !message;
  }

  function setContactState(label, kind) {
    var badge = $("contact-state");
    badge.className = "contact-state " + kind;
    $("contact-state-label").textContent = label;
  }

  function apiErrorText(error) {
    var messages = {
      invalid_init_data: "تعذّر التحقق من Telegram. أغلق التطبيق وافتح المحادثة مجددًا من البوت.",
      bot_not_ready: "الخدمة غير متاحة مؤقتًا. ستبقى رسائلك السابقة محفوظة.",
      contact_unavailable: "أوقفت الإدارة استقبال الرسائل الجديدة مؤقتًا.",
      text_length: "يجب أن تحتوي الرسالة على نص قبل إرسالها.",
      attachment_too_large: "حجم المرفق يتجاوز الحد الأقصى، وهو 20 ميغابايت.",
      unsupported_attachment: "نوع الملف غير مدعوم. أرسل صورة أو PDF أو مستند Office.",
      caption_too_long: "اختصر وصف المرفق إلى 1000 حرف أو أقل.",
      attachment_required: "اختر ملفًا لإرساله.",
      network: "تعذّر الاتصال. تحقق من الإنترنت ثم حاول مجددًا."
    };
    return messages[error && error.message] || "تعذّر إكمال الطلب. حاول مجددًا.";
  }

  function api(path, options) {
    options = options || {};
    options.headers = options.headers || {};
    if (!tg || !tg.initData) {
      return Promise.reject(new Error("invalid_init_data"));
    }
    options.headers["X-Telegram-Init-Data"] = tg.initData;
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () {
        return { ok: false };
      }).then(function (result) {
        if (!response.ok || !result || result.ok !== true) {
          throw new Error(result && result.error ? result.error : "request_failed");
        }
        return result;
      });
    }).catch(function (error) {
      if (error instanceof TypeError) {
        throw new Error("network");
      }
      throw error;
    });
  }

  function clearFeed() {
    var feed = $("chat-feed");
    while (feed.firstChild) {
      feed.removeChild(feed.firstChild);
    }
  }

  function createEmptyState(message) {
    var stateNode = document.createElement("div");
    stateNode.className = "conversation-state";

    var symbol = document.createElement("span");
    symbol.className = "empty-symbol";
    symbol.setAttribute("aria-hidden", "true");
    symbol.innerHTML = '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8A8.5 8.5 0 0 1 8.7 3.9a8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z"/></svg>';

    var copy = document.createElement("p");
    copy.textContent = message;
    stateNode.appendChild(symbol);
    stateNode.appendChild(copy);
    return stateNode;
  }

  function formatTime(value) {
    if (!value) {
      return "";
    }
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return date.toLocaleTimeString("ar", {
      hour: "numeric",
      minute: "2-digit"
    });
  }

  function getFilename(item) {
    return String(item.filename || (item.media_type === "image" ? "صورة" : "مرفق"));
  }

  function fetchAttachment(item) {
    return fetch("/api/user/messages/" + encodeURIComponent(item.id) + "/attachment", {
      headers: tg && tg.initData
        ? { "X-Telegram-Init-Data": tg.initData }
        : {}
    }).then(function (response) {
      if (!response.ok) {
        throw new Error(response.status === 404 ? "attachment_not_found" : "attachment_download_failed");
      }
      return response.blob();
    });
  }

  function addAttachment(message, item) {
    var isImage = item.media_type === "image" || item.media_type === "photo";
    var button = document.createElement("button");
    button.className = "message-attachment";
    button.type = "button";
    button.setAttribute("aria-label", (isImage ? "عرض الصورة: " : "تنزيل الملف: ") + getFilename(item));

    var icon = document.createElement("span");
    icon.className = "message-attachment-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = isImage ? "▧" : "PDF";

    var copy = document.createElement("span");
    copy.className = "message-attachment-copy";
    var filename = document.createElement("span");
    filename.className = "message-attachment-name";
    filename.textContent = getFilename(item);
    var action = document.createElement("span");
    action.className = "message-attachment-action";
    action.textContent = isImage ? "اضغط لعرض الصورة" : "اضغط لتنزيل المرفق";
    copy.appendChild(filename);
    copy.appendChild(action);
    button.appendChild(icon);
    button.appendChild(copy);
    message.appendChild(button);

    button.addEventListener("click", function () {
      if (button.disabled) {
        return;
      }
      var existingImage = message.querySelector(".message-image");
      if (existingImage) {
        existingImage.hidden = !existingImage.hidden;
        action.textContent = existingImage.hidden ? "اضغط لعرض الصورة" : "اضغط لإخفاء الصورة";
        return;
      }

      button.disabled = true;
      action.textContent = "جارٍ تحميل المرفق...";
      fetchAttachment(item).then(function (blob) {
        var objectUrl = URL.createObjectURL(blob);
        if (isImage) {
          var image = document.createElement("img");
          image.className = "message-image";
          image.src = objectUrl;
          image.alt = "مرفق صورة من " + (item.direction === "in" ? "المستخدم" : "الإدارة");
          message.insertBefore(image, message.querySelector(".message-meta"));
          action.textContent = "اضغط لإخفاء الصورة";
        } else {
          var download = document.createElement("a");
          download.href = objectUrl;
          download.download = getFilename(item);
          download.hidden = true;
          document.body.appendChild(download);
          download.click();
          download.remove();
          action.textContent = "تم تنزيل المرفق";
          window.setTimeout(function () {
            URL.revokeObjectURL(objectUrl);
          }, 10000);
        }
      }).catch(function (error) {
        action.textContent = apiErrorText(error);
        setStatus(apiErrorText(error), true);
      }).then(function () {
        button.disabled = false;
      });
    });
  }

  function createMessage(item) {
    var userMessage = item.direction === "in";
    var deleted = item.is_deleted === true;
    var message = document.createElement("article");
    message.className = "message " +
      (userMessage ? "message--user" : "message--admin") +
      (deleted ? " message--deleted" : "");
    message.setAttribute("aria-label", userMessage ? "رسالة منك" : "رسالة من الإدارة");

    var author = document.createElement("span");
    author.className = "message-author";
    author.textContent = userMessage ? "أنت" : "الإدارة";
    message.appendChild(author);

    var text = document.createElement("p");
    text.className = "message-text";
    text.textContent = deleted ? "تم حذف هذه الرسالة" : (item.text || "");
    message.appendChild(text);

    if (item.has_media && !deleted) {
      addAttachment(message, item);
    }

    var meta = document.createElement("div");
    meta.className = "message-meta";
    var time = document.createElement("time");
    time.textContent = formatTime(item.created_at);
    if (item.created_at) {
      time.dateTime = item.created_at;
    }
    meta.appendChild(time);

    if (item.is_edited && !deleted) {
      var edited = document.createElement("span");
      edited.textContent = "تم التعديل";
      meta.appendChild(edited);
    }

    if (userMessage && item.delivery_status === "received" && !deleted) {
      var sent = document.createElement("span");
      sent.className = "message-status";
      sent.textContent = "✓ أُرسلت";
      meta.appendChild(sent);
    } else if (!userMessage && item.delivery_status === "failed" && !deleted) {
      var failed = document.createElement("span");
      failed.className = "message-status failed";
      failed.textContent = "تعذّر الإرسال";
      meta.appendChild(failed);
    }

    message.appendChild(meta);
    return message;
  }

  function scrollToLatest() {
    var feed = $("chat-feed");
    feed.scrollTop = feed.scrollHeight;
    $("latest-button").hidden = true;
  }

  function showEmptyState() {
    clearFeed();
    $("chat-feed").appendChild(createEmptyState(
      "لا توجد رسائل في هذه المحادثة بعد. اكتب رسالتك للإدارة."
    ));
  }

  function updateOlderButton(items) {
    var button = $("older-button");
    if (!button) {
      button = document.createElement("button");
      button.id = "older-button";
      button.className = "older-button";
      button.type = "button";
      button.textContent = "عرض رسائل أقدم";
      button.addEventListener("click", loadOlderMessages);
    }
    button.hidden = items.length < 100;
    if (!button.parentNode) {
      $("chat-feed").insertBefore(button, $("chat-feed").firstChild);
    }
  }

  function loadLatestMessages() {
    if (!tg || !tg.initData || state.loading) {
      return Promise.resolve();
    }
    state.loading = true;
    return api("/api/user/messages?before_id=0", { cache: "no-store" })
      .then(function (result) {
        var items = Array.isArray(result.items) ? result.items : [];
        clearFeed();
        if (!items.length) {
          showEmptyState();
          state.beforeId = 0;
          state.lastId = 0;
          state.loaded = true;
          return;
        }
        items.forEach(function (item) {
          $("chat-feed").appendChild(createMessage(item));
        });
        state.beforeId = Number(items[0].id) || 0;
        state.lastId = Number(items[items.length - 1].id) || 0;
        updateOlderButton(items);
        scrollToLatest();
        state.loaded = true;
      })
      .catch(function (error) {
        clearFeed();
        $("chat-feed").appendChild(createEmptyState(apiErrorText(error)));
        setContactNotice(apiErrorText(error), true);
        console.error("تعذر تحميل سجل المحادثة:", error);
      })
      .then(function () {
        state.loading = false;
      });
  }

  function loadOlderMessages() {
    if (state.loadingOlder || !state.beforeId) {
      return;
    }
    state.loadingOlder = true;
    var feed = $("chat-feed");
    var oldHeight = feed.scrollHeight;
    var oldScrollTop = feed.scrollTop;
    api("/api/user/messages?before_id=" + encodeURIComponent(state.beforeId), {
      cache: "no-store"
    }).then(function (result) {
      var items = Array.isArray(result.items) ? result.items : [];
      var olderButton = $("older-button");
      if (!items.length) {
        if (olderButton) {
          olderButton.hidden = true;
        }
        return;
      }
      var fragment = document.createDocumentFragment();
      items.forEach(function (item) {
        fragment.appendChild(createMessage(item));
      });
      feed.insertBefore(fragment, olderButton || feed.firstChild);
      state.beforeId = Number(items[0].id) || state.beforeId;
      if (items.length < 100 && olderButton) {
        olderButton.hidden = true;
      }
      feed.scrollTop = oldScrollTop + (feed.scrollHeight - oldHeight);
    }).catch(function (error) {
      setStatus(apiErrorText(error), true);
      console.error("تعذر تحميل الرسائل الأقدم:", error);
    }).then(function () {
      state.loadingOlder = false;
    });
  }

  function loadNewMessages() {
    if (!tg || !tg.initData || !state.loaded || state.loading) {
      return Promise.resolve();
    }
    state.loading = true;
    var feed = $("chat-feed");
    var wasNearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
    var hadMessages = state.lastId > 0;
    return api("/api/user/messages?after_id=" + encodeURIComponent(state.lastId), {
      cache: "no-store"
    }).then(function (result) {
      var items = Array.isArray(result.items) ? result.items : [];
      if (items.length) {
        var emptyState = feed.querySelector(".conversation-state");
        if (emptyState) {
          emptyState.remove();
        }
      }
      items.forEach(function (item) {
        feed.appendChild(createMessage(item));
        state.lastId = Math.max(state.lastId, Number(item.id) || 0);
      });
      if (items.length && !hadMessages) {
        state.beforeId = Number(items[0].id) || 0;
        updateOlderButton(items);
      }
      if (items.length && wasNearBottom) {
        scrollToLatest();
      } else if (items.length) {
        $("latest-button").hidden = false;
      }
    }).catch(function (error) {
      if (error.message === "invalid_init_data") {
        setContactNotice(apiErrorText(error), true);
      }
      console.error("تعذر تحديث المحادثة:", error);
    }).then(function () {
      state.loading = false;
    });
  }

  function updateComposer() {
    var text = $("chat-text").value.trim();
    var file = $("chat-file").files[0];
    var canEdit = state.canSend && !state.sending;
    $("chat-text").disabled = !canEdit;
    $("chat-file").disabled = !canEdit;
    $("attach-button").classList.toggle("disabled", !canEdit);
    $("chat-send").disabled = !canEdit || (!text && !file);
    $("chat-send").setAttribute(
      "aria-label",
      state.sending ? "جارٍ الإرسال" : "إرسال الرسالة"
    );
  }

  function formatFileSize(size) {
    if (size >= 1024 * 1024) {
      return (size / (1024 * 1024)).toFixed(1) + " MB";
    }
    return Math.max(1, Math.round(size / 1024)) + " KB";
  }

  function setSelectedFile(file) {
    if (!file) {
      $("chat-file").value = "";
      $("attachment-preview").hidden = true;
      $("attachment-name").textContent = "";
      $("attachment-size").textContent = "";
      updateComposer();
      return;
    }
    var filename = String(file.name || "").toLowerCase();
    var supported = ALLOWED_EXTENSIONS.some(function (extension) {
      return filename.endsWith(extension);
    });
    if (!supported) {
      $("chat-file").value = "";
      setStatus("نوع الملف غير مدعوم. أرسل صورة أو PDF أو مستند Office.", true);
      updateComposer();
      return;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      $("chat-file").value = "";
      setStatus("حجم المرفق يتجاوز الحد الأقصى، وهو 20 ميغابايت.", true);
      updateComposer();
      return;
    }
    setStatus("");
    $("attachment-name").textContent = file.name || "مرفق";
    $("attachment-size").textContent = formatFileSize(file.size);
    $("attachment-preview").hidden = false;
    updateComposer();
  }

  function sendMessage(event) {
    event.preventDefault();
    if (!state.canSend || state.sending) {
      return;
    }
    var text = $("chat-text").value.trim();
    var file = $("chat-file").files[0];
    if (!text && !file) {
      return;
    }
    if (file && text.length > 1000) {
      setStatus("اختصر وصف المرفق إلى 1000 حرف أو أقل.", true);
      $("chat-text").focus();
      return;
    }

    state.sending = true;
    updateComposer();
    setStatus("جارٍ إرسال الرسالة...");
    var request;
    if (file) {
      var form = new FormData();
      form.append("file", file);
      form.append("text", text);
      request = api("/api/user/messages/attachment", {
        method: "POST",
        body: form
      });
    } else {
      request = api("/api/user/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      });
    }

    request.then(function () {
      $("chat-text").value = "";
      $("chat-text").style.height = "";
      setSelectedFile(null);
      setStatus("تم إرسال رسالتك.");
      return loadNewMessages();
    }).catch(function (error) {
      setStatus(apiErrorText(error), true);
      if (error.message === "contact_unavailable") {
        state.canSend = false;
        setContactState("للاطلاع فقط", "read-only");
        setContactNotice("أوقفت الإدارة استقبال الرسائل الجديدة. يمكنك متابعة قراءة المحادثة.");
      }
      console.error("تعذر إرسال الرسالة:", error);
    }).then(function () {
      state.sending = false;
      updateComposer();
    });
  }

  function loadContactAvailability() {
    if (!tg || !tg.initData) {
      setContactState("يتطلب Telegram", "read-only");
      setContactNotice("افتح هذه المحادثة من داخل Telegram لعرض سجلّك وإرسال الرسائل.");
      state.canSend = false;
      updateComposer();
      return Promise.resolve();
    }
    return api("/api/user/settings", { cache: "no-store" }).then(function (result) {
      state.canSend = result.can_send_contact === true;
      if (state.canSend) {
        setContactState("متاح للتواصل", "available");
        setContactNotice("");
      } else {
        setContactState("للاطلاع فقط", "read-only");
        setContactNotice("أوقفت الإدارة استقبال الرسائل الجديدة. يمكنك متابعة قراءة المحادثة.");
      }
      updateComposer();
    }).catch(function (error) {
      state.canSend = false;
      setContactState("تعذّر التحقق", "error");
      setContactNotice(apiErrorText(error), true);
      updateComposer();
      console.error("تعذر التحقق من حالة التواصل:", error);
    });
  }

  function initializeTelegram() {
    if (!tg) {
      return;
    }
    try {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) {
        tg.setHeaderColor("#075e54");
      }
      if (tg.setBackgroundColor) {
        tg.setBackgroundColor("#efeae2");
      }
      if (tg.disableVerticalSwipes) {
        tg.disableVerticalSwipes();
      }
      if (tg.BackButton) {
        tg.BackButton.onClick(function () {
          if (tg.close) {
            tg.close();
          }
        });
      }
    } catch (error) {
      console.warn("تعذر تطبيق إعدادات عرض Telegram:", error);
    }
  }

  function closeChat() {
    if (tg && typeof tg.close === "function") {
      tg.close();
      return;
    }
    setStatus("افتح التطبيق من Telegram للعودة إلى المحادثة.", true);
  }

  function initialize() {
    initializeTelegram();
    $("close-chat").addEventListener("click", closeChat);
    $("latest-button").addEventListener("click", scrollToLatest);
    $("chat-form").addEventListener("submit", sendMessage);
    $("chat-text").addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 112) + "px";
      updateComposer();
    });
    $("chat-file").addEventListener("change", function () {
      setSelectedFile(this.files[0]);
    });
    $("remove-attachment").addEventListener("click", function () {
      setSelectedFile(null);
    });
    $("chat-feed").addEventListener("scroll", function () {
      if ($("chat-feed").scrollHeight - $("chat-feed").scrollTop -
          $("chat-feed").clientHeight < 50) {
        $("latest-button").hidden = true;
      }
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) {
        loadNewMessages();
      }
    });

    updateComposer();
    if (!tg || !tg.initData) {
      clearFeed();
      $("chat-feed").appendChild(createEmptyState(
        "هذه المحادثة متاحة من داخل Telegram فقط."
      ));
      loadContactAvailability();
      return;
    }

    Promise.all([loadContactAvailability(), loadLatestMessages()]).then(function () {
      if (state.pollTimer) {
        window.clearInterval(state.pollTimer);
      }
      state.pollTimer = window.setInterval(function () {
        if (!document.hidden) {
          loadNewMessages();
        }
      }, 6000);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
