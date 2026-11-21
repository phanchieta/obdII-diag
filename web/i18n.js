/* Tiny i18n layer.
 * Dictionaries live in web/i18n/<lang>.json (copy en.json to add a language and list it in LANGS/LOCALES below).
 * Use t('key', {param}) in code, data-i18n="key" for text, data-i18n-html="key" for trusted HTML snippets
 * and data-i18n-attr="title:key;aria-label:key" for attributes.
 * Server messages arrive as {"pt": "...", "en": "..."}: L(msg) picks the current language.
 */
(function (global) {
  'use strict';
  const LANGS = ['pt', 'en'];
  const LOCALES = { pt: 'pt-BR', en: 'en-US' };
  const dict = {};
  let lang = 'en';

  function detect() {
    const q = new URLSearchParams(location.search).get('lang');
    if (LANGS.includes(q)) return q;
    let saved = null;
    try { saved = localStorage.getItem('obd-lang'); } catch (e) { /* no storage */ }
    if (LANGS.includes(saved)) return saved;
    const nav = ((navigator.languages && navigator.languages[0]) || navigator.language || 'en').toLowerCase();
    return LANGS.includes(nav.slice(0, 2)) ? nav.slice(0, 2) : 'en';
  }

  async function load(l) {
    if (!dict[l]) {
      const r = await fetch('i18n/' + l + '.json');
      dict[l] = await r.json();
    }
    return dict[l];
  }

  function t(key, params) {
    let s = dict[lang] && dict[lang][key];
    if (s === undefined) s = dict.en && dict.en[key];
    if (s === undefined) return key;
    return params ? s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m)) : s;
  }
  const tn = (key, n, params) => t(key + (n === 1 ? '_one' : '_other'), Object.assign({ n }, params));
  const L = x => (x == null ? '' : typeof x === 'string' ? x : x[lang] || x.en || x.pt || '');
  const locale = () => LOCALES[lang];

  function applyStatic(root) {
    root = root || document;
    root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });   // our own dictionaries: trusted
    root.querySelectorAll('[data-i18n-attr]').forEach(el => {
      el.dataset.i18nAttr.split(';').forEach(pair => {
        const i = pair.indexOf(':');
        if (i > 0) el.setAttribute(pair.slice(0, i).trim(), t(pair.slice(i + 1).trim()));
      });
    });
    document.title = t('app.title');
    document.querySelectorAll('.lang-btn').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === lang)));
  }

  async function init() {
    lang = detect();
    await Promise.all([load(lang), load('en')]);
    document.documentElement.lang = lang === 'pt' ? 'pt-BR' : lang;
    applyStatic(document);
    return lang;
  }

  async function setLang(l) {
    if (!LANGS.includes(l)) return;
    await Promise.all([load(l), load('en')]);
    lang = l;
    try { localStorage.setItem('obd-lang', l); } catch (e) { /* no storage */ }
    document.documentElement.lang = l === 'pt' ? 'pt-BR' : l;
    applyStatic(document);
  }

  global.I18N = { init, setLang, t, tn, L, locale, applyStatic, getLang: () => lang, LANGS };
})(window);
