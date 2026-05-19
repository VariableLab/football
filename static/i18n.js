(function() {
  var STORAGE_KEY = 'wc_lang';
  var FALLBACK_LANG = 'zh';
  var SUPPORTED = ['zh','en','fr','es','de','it'];
  var cache = {};
  var currentLang = FALLBACK_LANG;

  function detectLang() {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved && SUPPORTED.indexOf(saved) >= 0) return saved;
    var nav = (navigator.language || '').split('-')[0];
    if (SUPPORTED.indexOf(nav) >= 0) return nav;
    return FALLBACK_LANG;
  }

  function loadLang(lang, cb) {
    if (cache[lang]) { if (cb) cb(cache[lang]); return; }
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/static/locales/' + lang + '.json?v=20260519', true);
    xhr.onload = function() {
      if (xhr.status === 200) {
        try {
          cache[lang] = JSON.parse(xhr.responseText);
          if (cb) cb(cache[lang]);
        } catch(e) { fallback(cb); }
      } else { fallback(cb); }
    };
    xhr.onerror = function() { fallback(cb); };
    xhr.send();
  }

  function fallback(cb) {
    if (!cache[FALLBACK_LANG]) {
      loadLang(FALLBACK_LANG, function(d) { if (cb) cb(d); });
    } else {
      if (cb) cb(cache[FALLBACK_LANG]);
    }
  }

  function t(key) {
    var data = cache[currentLang] || cache[FALLBACK_LANG] || {};
    var val = data[key];
    if (val === undefined) {
      val = cache[FALLBACK_LANG] ? cache[FALLBACK_LANG][key] : undefined;
      if (val === undefined) return key;
    }
    if (arguments.length <= 1) return val;
    var args = Array.prototype.slice.call(arguments, 1);
    return val.replace(/%d/g, function() { var a = args.shift(); return a !== undefined ? a : '%d'; })
              .replace(/%s/g, function() { var a = args.shift(); return a !== undefined ? a : '%s'; });
  }

  function applyDataI18n() {
    var els = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var key = el.getAttribute('data-i18n');
      var attr = el.getAttribute('data-i18n-attr');
      var text = t(key);
      if (attr) {
        if (attr === 'placeholder') el.placeholder = text;
        else el.setAttribute(attr, text);
      } else {
        el.innerHTML = text;
      }
    }
  }

  function setLang(lang) {
    if (SUPPORTED.indexOf(lang) < 0) return;
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : lang;
    loadLang(lang, function() {
      applyDataI18n();
      var evt = document.createEvent('CustomEvent');
      evt.initCustomEvent('i18n:change', true, true, { lang: lang });
      window.dispatchEvent(evt);
    });
  }

  function getLang() { return currentLang; }

  function init(cb) {
    currentLang = detectLang();
    document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : currentLang;
    loadLang(currentLang, function() {
      applyDataI18n();
      if (cb) cb();
    });
  }

  window.I18n = { t: t, setLang: setLang, getLang: getLang, init: init, applyDataI18n: applyDataI18n, SUPPORTED: SUPPORTED };
})();
