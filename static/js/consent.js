/* Cookie consent + Google Consent Mode v2 + conditional GTM loading.
 *
 * Config is read from <script id="consent-config" type="application/json">:
 *   enabled  - false => banner off, GTM loads unconditionally
 *   region   - "strict" (opt-in: EU/UK/rest of world) or "us" (opt-out)
 *   version  - policy version; bumping it re-asks everyone
 *   gtm      - GTM container ID or ""
 * Choice is kept only in a first-party cookie (no server round-trip).
 */
(function () {
  'use strict';
  var node = document.getElementById('consent-config');
  if (!node) return;
  var cfg = {};
  try { cfg = JSON.parse(node.textContent); } catch (e) { return; }

  var COOKIE = 'amp_consent';
  var MAX_AGE = 60 * 60 * 24 * 180;
  var gpc = navigator.globalPrivacyControl === true;
  var gtmLoaded = false;

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;

  function readCookie() {
    var m = document.cookie.match(new RegExp('(?:^|; )' + COOKIE + '=([^;]*)'));
    if (!m) return null;
    var p = {};
    decodeURIComponent(m[1]).split('&').forEach(function (kv) {
      var s = kv.split('=');
      p[s[0]] = s[1];
    });
    if (String(p.v) !== String(cfg.version)) return null;
    return { analytics: p.a === '1', ads: p.m === '1' };
  }

  function writeCookie(state) {
    var value = 'v=' + cfg.version + '&a=' + (state.analytics ? 1 : 0) +
                '&m=' + (state.ads ? 1 : 0);
    document.cookie = COOKIE + '=' + encodeURIComponent(value) +
      '; Max-Age=' + MAX_AGE + '; Path=/; SameSite=Lax' +
      (location.protocol === 'https:' ? '; Secure' : '');
  }

  function defaults() {
    if (!cfg.enabled) return { analytics: true, ads: true };
    if (cfg.region === 'us' && !gpc) return { analytics: true, ads: true };
    return { analytics: false, ads: false };
  }

  function mode(state) {
    return {
      analytics_storage: state.analytics ? 'granted' : 'denied',
      ad_storage: state.ads ? 'granted' : 'denied',
      ad_user_data: state.ads ? 'granted' : 'denied',
      ad_personalization: state.ads ? 'granted' : 'denied',
      functionality_storage: 'granted',
      security_storage: 'granted'
    };
  }

  function loadGtm(state) {
    if (gtmLoaded || !cfg.gtm || !(state.analytics || state.ads)) return;
    gtmLoaded = true;
    window.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtm.js?id=' +
            encodeURIComponent(cfg.gtm);
    document.head.appendChild(s);
  }

  function apply(state, isUpdate) {
    if (isUpdate) gtag('consent', 'update', mode(state));
    gtag('set', 'ads_data_redaction', !state.ads);
    loadGtm(state);
    document.dispatchEvent(new CustomEvent('amp:consent', { detail: state }));
  }

  var stored = readCookie();
  var current = stored || defaults();
  gtag('consent', 'default', mode(current));
  apply(current, false);

  /* ---- UI (banner + preferences modal) ---- */
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    if (!cfg.enabled) return;
    var banner = document.getElementById('cookie-banner');
    var modalEl = document.getElementById('cookie-modal');
    if (!banner || !modalEl) return;
    var modal = window.bootstrap ? new window.bootstrap.Modal(modalEl) : null;
    var chkA = document.getElementById('consent-analytics');
    var chkM = document.getElementById('consent-ads');

    function show() { banner.classList.remove('d-none'); }
    function hide() { banner.classList.add('d-none'); }

    function save(state) {
      var hadTracking = gtmLoaded;
      writeCookie(state);
      current = state;
      apply(state, true);
      hide();
      if (modal) modal.hide();
      // Already-loaded third-party scripts can't be unloaded; reload on withdrawal.
      if (hadTracking && !(state.analytics && state.ads)) location.reload();
    }

    function openPrefs() {
      chkA.checked = current.analytics;
      chkM.checked = current.ads;
      if (modal) modal.show();
    }

    document.addEventListener('click', function (ev) {
      var t = ev.target.closest('[data-consent]');
      if (!t) return;
      ev.preventDefault();
      var act = t.getAttribute('data-consent');
      if (act === 'accept') save({ analytics: true, ads: true });
      else if (act === 'reject') save({ analytics: false, ads: false });
      else if (act === 'ack') save(current);
      else if (act === 'save') save({ analytics: chkA.checked, ads: chkM.checked });
      else if (act === 'open') openPrefs();
    });

    if (!stored) show();
  });
})();
