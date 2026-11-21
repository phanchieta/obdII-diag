/* OBD-II Diag dashboard logic (no dependencies).
 * Every visible text goes through t('key') / L(serverMessage); text that comes from the server is always inserted
 * with textContent. Dictionaries: web/i18n/<lang>.json */
(() => {
  'use strict';
  const G = window.Gauges, I = window.I18N;
  const { t, tn, L } = I;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const fmt = G.fmt, clamp = G.clamp;
  const TOKEN = (document.querySelector('meta[name="obd-token"]') || {}).content || '';
  const WIN = 120;                                           // live chart window (s)
  const CONNECTING = ['opening', 'initializing', 'searching'];
  const SEV_ICON = { 1: 'info', 2: 'warn', 3: 'warn', 4: 'stop' };
  const STATE_ICON = { ok: 'check', warn: 'warn', bad: 'alert', info: 'info' };
  const HIST_EVENTS = { reading: 'hist.ev_reading', before_clear: 'hist.ev_before_clear', after_clear: 'hist.ev_after_clear' };
  const VERDICT = { 1: ['info', 'verdict.1_title', 'verdict.1_text'], 2: ['warn', 'verdict.2_title', 'verdict.2_text'],
                    3: ['warn', 'verdict.3_title', 'verdict.3_text'], 4: ['stop', 'verdict.4_title', 'verdict.4_text'] };
  const DIAG_TITLES = { radios: 'diag.radios', radio_state: 'diag.radio_state', paired: 'diag.paired', com: 'diag.com', nearby: 'diag.nearby',
                        controller: 'diag.controller', rfkill: 'diag.rfkill', serial: 'diag.serial', groups: 'diag.groups', sockets: 'diag.sockets' };

  // ------------------------------------------------------------------ helpers
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'html') e.innerHTML = v;               // constants from our own code only
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return e;
  }
  const ic = (name, cls = '') => h('span', { html: `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>` }).firstChild;

  async function api(path, body) {
    const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-OBD-Token': TOKEN }, body: JSON.stringify(body) };
    let r, j;
    try { r = await fetch(path, opt); } catch (e) { throw new Error(t('err.server_lost')); }
    try { j = await r.json(); } catch (e) { j = { ok: false, error: t('err.bad_response') }; }
    if (!r.ok || j.ok === false) throw new Error(L(j.error) || 'HTTP ' + r.status);
    return j;
  }
  function toast(msg, err) {
    const el = h('div', { class: 'toast' + (err ? ' err' : ''), role: err ? 'alert' : 'status', text: msg });
    $('#toast-root').append(el);
    setTimeout(() => el.remove(), err ? 8000 : 3800);
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fallback below */ }
    const ta = h('textarea', { style: 'position:fixed;opacity:0' }); ta.value = text; document.body.append(ta); ta.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    ta.remove(); return ok;
  }
  function modal(build) {
    const prev = document.activeElement;
    const back = h('div', { class: 'modal-back', role: 'presentation' });
    const box = h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' });
    back.append(box);
    const api_ = { el: box, close() { back.remove(); document.removeEventListener('keydown', onKey); if (prev && prev.focus) prev.focus(); if (api_.onclose) api_.onclose(); } };
    function onKey(e) {
      if (e.key === 'Escape') api_.close();
      if (e.key === 'Tab') {
        const f = $$('button:not([disabled]), input, select, a[href]', box);
        if (!f.length) return;
        const a = f[0], z = f[f.length - 1];
        if (e.shiftKey && document.activeElement === a) { z.focus(); e.preventDefault(); } else if (!e.shiftKey && document.activeElement === z) { a.focus(); e.preventDefault(); }
      }
    }
    back.addEventListener('mousedown', e => { if (e.target === back) api_.close(); });
    document.addEventListener('keydown', onKey);
    build(box, api_);
    $('#modal-root').append(back);
    const first = $('button, input', box); if (first) first.focus();
    return api_;
  }

  // -------------------------------------------------------------------- state
  const S = { snap: null, hist: {}, lastT: {}, gen: null, tab: 'diag', toggled: {}, dtcSig: '', evSeq: 0, chartKey: 'rpm', chartTable: false,
              linkOk: true, bannerSig: '', selHist: null, meta: {}, chartSig: '', conn: null, events: [] };
  const ZERO = new Set(['rpm', 'speed', 'throttle', 'load', 'map', 'abs_load', 'maf', 'fuel_level', 'evap_purge', 'baro', 'pedal_d', 'pedal_e', 'cmd_throttle', 'rel_throttle', 'fuel_rate']);
  const metaOf = k => S.meta[k] || { label: { pt: k, en: k }, unit: '', dec: 0, interval: 5 };
  const fmtVal = (v, dec, unit) => (v != null && typeof v === 'object' ? L(v) : fmt(v, dec) + (unit ? ' ' + unit : ''));
  const logTime = ts => new Date(ts * 1000).toLocaleTimeString(I.locale());

  // ----------------------------------------------------------- tile definitions
  const voltState = (v, snap) => {
    const rpm = snap.live.rpm && snap.live.rpm.v;
    if (rpm > 400) return v < 12.8 ? ['bad', 'st.alt_bad'] : v < 13.4 ? ['warn', 'st.volt_low'] : v <= 14.9 ? ['ok', 'st.volt_ok'] : ['bad', 'st.volt_high'];
    return v >= 12.4 ? ['ok', 'st.batt_good'] : v >= 12.0 ? ['warn', 'st.batt_weak'] : ['bad', 'st.batt_dead'];
  };
  const trimState = v => Math.abs(v) <= 10 ? ['ok', 'st.trim_ok'] : Math.abs(v) <= 20 ? ['warn', v > 0 ? 'st.trim_lean_comp' : 'st.trim_rich_comp'] : ['bad', v > 0 ? 'st.trim_lean' : 'st.trim_rich'];
  const TILES = [
    { key: 'ect', kind: 'meter', min: 40, max: 120, where: 'center', core: true,
      state: v => v < 50 ? ['info', 'st.cold'] : v < 75 ? ['info', 'st.warming'] : v <= 102 ? ['ok', 'st.temp_ok'] : v <= 109 ? ['warn', 'st.hot'] : ['bad', 'st.overheat'] },
    { key: 'volt', kind: 'meter', min: 10, max: 16, where: 'center', core: true, state: voltState },
    { key: 'load', kind: 'meter', min: 0, max: 100, where: 'center', core: true },
    { key: 'throttle', kind: 'spark', core: true },
    { key: 'map', kind: 'spark', core: true },
    { key: 'timing', kind: 'spark' },
    { key: 'iat', kind: 'spark' },
    { key: 'stft1', kind: 'trim', min: -25, max: 25, center: 0, state: trimState, core: true },
    { key: 'ltft1', kind: 'trim', min: -25, max: 25, center: 0, state: trimState, core: true },
    { key: 'lambda_b1s1', kind: 'trim', min: 0.8, max: 1.2, center: 1,
      state: v => Math.abs(v - 1) <= 0.08 ? ['ok', 'st.lambda_ok'] : v > 1 ? ['info', 'st.lambda_lean'] : ['info', 'st.lambda_rich'] },
    { key: 'o2_b1s2', kind: 'spark' },
    { key: 'maf', kind: 'spark' },
    { key: 'fuel_level', kind: 'meter', min: 0, max: 100, state: v => v < 10 ? ['warn', 'st.reserve'] : ['info', ''] },
    { key: 'fuel_status', kind: 'text' },
    { key: 'ambient', kind: 'spark' },
    { key: 'baro', kind: 'spark' },
    { key: 'evap_purge', kind: 'spark' },
    { key: 'oil_temp', kind: 'spark' },
    { key: 'fuel_rate', kind: 'spark' },
    { key: 'dist_mil', kind: 'text', state: v => v > 0 ? ['warn', 'st.mil_km'] : ['ok', ''] },
  ];

  function buildTile(def) {
    const m = metaOf(def.key);
    const root = h('div', { class: 'card tile na-hide', 'data-key': def.key });
    const lbl = h('span');
    const val = h('div', { class: 'val' + (def.kind === 'text' ? ' txt' : '') }, h('span', { class: 'n', text: '—' }), m.unit ? h('span', { class: 'u', text: m.unit }) : null);
    const st = h('div', { class: 'st' });
    root.append(h('div', { class: 'lbl' }, lbl), val, st);
    const ui = { def, root, lbl, num: $('.n', val), st, lblText: '' };
    if (def.kind === 'meter' || def.kind === 'trim') {
      ui.m = h('div', { class: 'meter' + (def.kind === 'trim' ? ' zero' : ''), role: 'meter', 'aria-valuemin': def.min, 'aria-valuemax': def.max });
      ui.f = h('div', { class: 'f' }); ui.m.append(ui.f);
      if (def.kind === 'trim') ui.m.append(h('i', { class: 'zero-tick' }));
      const dec = x => (x % 1 ? 1 : 0);
      const mid = def.kind === 'trim' ? [h('span', { text: fmt(def.center, dec(def.center)) })] : [];
      root.append(ui.m, h('div', { class: 'meter-scale' }, h('span', { text: def.min < 0 ? '−' + fmt(-def.min) : fmt(def.min, dec(def.min)) }), ...mid,
        h('span', { text: (def.kind === 'trim' && def.center === 0 ? '+' : '') + fmt(def.max, dec(def.max)) })));
    } else if (def.kind === 'spark') {
      const sp = h('div', { class: 'spark' }); root.append(sp); ui.spark = G.sparkline(sp);
    }
    return ui;
  }

  let gRpm, gSpd, chart, TUI = [], lampCard;
  function buildCluster() {
    gRpm = G.radialGauge($('#g-rpm'), { min: 0, max: 8000, major: 1000, minor: 500, tick: v => v / 1000, label: t('g.rpm'), unit: 'rpm', dec: 0, zones: [{ from: 6500, to: 8000, cls: 'red' }] });
    gSpd = G.radialGauge($('#g-speed'), { min: 0, max: 200, major: 20, minor: 10, tick: v => v, label: t('g.speed'), unit: 'km/h', dec: 0, zones: [] });
    chart = G.lineChart($('#chart-box'));
    lampCard = h('button', { class: 'card mini-lamp', type: 'button', 'data-act': 'diag', style: 'text-align:left;cursor:pointer;width:100%' },
      h('div', { class: 'lamp', id: 'mini-lamp' }, ic('engine', 'fill')),
      h('div', {}, h('b', { id: 'mini-lamp-t' }), h('span', { id: 'mini-lamp-s' })));
    $('#center-col').append(lampCard);
    TUI = TILES.map(buildTile);
    TUI.forEach(u => (u.def.where === 'center' ? $('#center-col') : $('#tiles')).append(u.root));
  }

  // ----------------------------------------------------------------- history
  function collect(snap) {
    if (S.gen !== snap.gen) { S.gen = snap.gen; S.hist = {}; S.lastT = {}; }
    for (const [k, o] of Object.entries(snap.live || {})) {
      if (typeof o.v !== 'number' || S.lastT[k] === o.t) continue;
      S.lastT[k] = o.t;
      (S.hist[k] = S.hist[k] || []).push([o.t, o.v]);
    }
    const cut = snap.now - WIN - 30;
    for (const k in S.hist) { const a = S.hist[k]; while (a.length && a[0][0] < cut) a.shift(); }
  }
  const series = (k, win, now) => (S.hist[k] || []).filter(p => p[0] >= now - win);
  const liveVal = (snap, k) => { const o = snap.live[k]; return o && typeof o.v === 'number' && snap.now - o.t < 10 ? o.v : null; };

  // --------------------------------------------------------------------- top
  function renderTop(snap) {
    const map = { online: 'online', disconnected: 'off', opening: 'busy', initializing: 'busy', searching: 'busy', reconnecting: 'warn', car_offline: 'warn', error: 'error' };
    const chip = $('#status-chip');
    chip.dataset.s = map[snap.status] || 'off';
    let text = snap.status === 'online' && snap.demo ? t('chip.demo') : L(snap.detail);
    if (snap.busy) text += ' · ' + L(snap.busy);
    $('#status-text').textContent = text;
    chip.title = snap.port ? t('chip.port', { port: snap.port }) : '';
    const v = snap.adapter && snap.adapter.voltage;
    $('#volt-chip').hidden = !(v != null && snap.status !== 'disconnected');
    if (v != null) $('#volt-text').textContent = fmt(v, 1) + ' V';
    const veh = snap.vehicle || {};
    const line = [snap.car, veh.vin ? 'VIN ' + veh.vin : '', snap.status === 'online' ? veh.protocol : ''].filter(Boolean).join(' · ');
    $('#vehicle-line').textContent = line || t('app.subtitle');
    const idle = snap.status === 'disconnected' || snap.status === 'error';
    const btn = $('#conn-btn');
    btn.className = 'btn' + (idle ? ' primary' : '');
    btn.replaceChildren(ic('plug'), h('span', { text: t(idle ? 'hdr.connect' : 'hdr.disconnect') }));
    btn.dataset.mode = idle ? 'connect' : 'disconnect';

    const sig = [snap.status, JSON.stringify(snap.detail), S.linkOk, I.getLang()].join('|');
    if (sig !== S.bannerSig) {
      S.bannerSig = sig;
      const w = $('#banner-wrap'); w.replaceChildren();
      const mk = (cls, icon, msg) => w.append(h('div', { class: 'banner ' + cls, role: 'alert' }, ic(icon), h('div', { text: msg })));
      if (!S.linkOk) mk('error', 'alert', t('banner.no_link'));
      else if (snap.status === 'car_offline' || snap.status === 'reconnecting') mk('warn', 'warn', L(snap.detail));
      else if (snap.status === 'error') mk('error', 'alert', t('banner.error_help', { detail: L(snap.detail) }));
    }
  }

  // ---------------------------------------------------------------- diagnosis
  const sevChip = (g, text) => h('span', { class: 'sev', 'data-s': g }, ic(SEV_ICON[g]), text);
  const listOf = (tag, items, cls) => h(tag, { class: cls }, items.map(x => h('li', { text: x })));
  const section = (title, ...kids) => h('section', {}, h('h4', {}, title), ...kids);
  const kbText = c => c.kb.t[I.getLang()] || c.kb.t.en;

  function codeCard(c, kind, open0, freezeDtc) {
    const k = c.kb, tx = kbText(c), id = kind + '-' + c.code;
    const open = S.toggled[id] !== undefined ? S.toggled[id] : open0;
    const head = h('button', { class: 'cc-head', type: 'button', 'aria-expanded': String(open) },
      h('span', { class: 'code-badge', text: c.code }),
      h('div', { class: 'cc-title' }, h('h3', { text: tx.title }), h('p', { text: tx.system + (c.ecu ? ' · ' + t('card.ecu', { ecu: c.ecu }) : '') })),
      sevChip(k.severity, t('card.severity', { text: tx.severity_text })), ic('chev', 'chev'));
    const body = h('div', { class: 'cc-body', hidden: !open });
    body.append(h('div', { class: 'drive', 'data-s': k.severity }, ic(SEV_ICON[k.severity]), h('div', {}, h('b', { text: t('card.drive') }), h('span', { text: tx.can_drive }))));
    if (tx.note) body.append(h('div', { class: 'en-note', text: tx.note }));
    body.append(section(t('card.what'), h('p', { text: tx.what })));
    if (tx.causes.length) body.append(section(h('span', {}, t('card.causes') + ' ', h('small', { text: t('card.causes_hint') })), listOf('ol', tx.causes)));
    if (tx.actions.length) body.append(section(t('card.actions'), listOf('ol', tx.actions, 'steps')));
    if (tx.brand_tip) body.append(h('div', { class: 'honda' }, h('h4', { text: t('card.brand_tip', { brand: k.brand }) }), h('p', { text: tx.brand_tip })));
    if (freezeDtc === c.code) body.append(h('p', { class: 'small-note', style: 'margin:0', text: t('card.freeze_note') }));
    const copy = h('button', { class: 'btn sm ghost', type: 'button', onclick: async () => toast((await copyText(`${c.code} — ${tx.title}`)) ? t('toast.code_copied') : t('toast.copy_failed'), false) }, ic('copy'), t('card.copy'));
    body.append(h('div', { class: 'cc-foot' }, h('span', { class: 'pill', text: tx.scope.split(' (')[0] }), h('span', { text: tx.area_text }),
      h('a', { href: tx.search_url, target: '_blank', rel: 'noopener noreferrer' }, ic('ext'), t('card.search')), copy));
    head.addEventListener('click', () => { const o = body.hidden; body.hidden = !o; head.setAttribute('aria-expanded', String(o)); S.toggled[id] = o; });
    return h('article', { class: 'card code-card', 'data-sev': k.severity }, head, body);
  }

  function verdictFor(d) {
    const all = d.stored.concat(d.pending, d.permanent || []);
    const mil = d.mil && d.mil.on;
    if (!all.length && !mil) return { s: '0', icon: 'check', title: t('verdict.ok_title'), text: t('verdict.ok_text') };
    if (!all.length) return { s: '2', icon: 'warn', title: t('verdict.noread_title'), text: t('verdict.noread_text') };
    const g = Math.max(...all.map(c => c.kb.severity));
    const [icon, title, text] = VERDICT[g];
    return { s: String(g), icon, title: t(title), text: t(text) + (mil ? t('verdict.flashing') : '') };
  }

  function renderDiag(snap) {
    const d = snap.dtc;
    const showMain = !!d || snap.status === 'online';
    $('#diag-main').hidden = !showMain;
    const empty = $('#diag-empty');
    empty.hidden = showMain;
    if (!showMain) {
      const idle = snap.status === 'disconnected' || snap.status === 'error';
      $('h2', empty).textContent = t(idle ? 'empty.title_idle' : snap.status === 'car_offline' ? 'empty.title_offline' : 'empty.title_connecting');
      $('p', empty).textContent = idle ? t('empty.p_idle') : L(snap.detail);
      $('.steps-mini', empty).hidden = !idle;
      $('.btns', empty).hidden = !idle;
    }
    const badge = $('#diag-badge');
    const ns = d ? d.stored.length : 0;
    badge.hidden = !ns; badge.textContent = ns;
    const milOn = d && d.mil && d.mil.on;
    $('#mini-lamp').classList.toggle('on', !!milOn);
    $('#mini-lamp-t').textContent = t(!d ? 'lamp.title' : milOn ? 'lamp.on' : 'lamp.off');
    $('#mini-lamp-s').textContent = !d ? t('lamp.none') : ns ? tn('lamp.codes', ns) : t('lamp.no_codes');
    if (!showMain) return;

    const lamp = $('#hero-lamp');
    lamp.className = 'lamp';
    $('#btn-clear').disabled = !(snap.status === 'online' && !!d && !snap.busy);
    $('#btn-read').disabled = snap.status !== 'online' || !!snap.busy;
    $('#btn-copy').disabled = !d;
    if (!d) {
      $('#hero-title').textContent = t('hero.reading');
      $('#hero-sub').textContent = snap.busy ? L(snap.busy) : t('hero.wait');
      $('#hero-meta').textContent = '';
      $('#hero-n').textContent = '…'; $('#hero-n-l').textContent = '';
      $('#verdict').replaceChildren(); return;
    }
    const np = d.pending.length;
    if (d.mil && d.mil.on) { lamp.classList.add('on'); $('#hero-title').textContent = t('hero.on'); }
    else if (d.mil && ns) $('#hero-title').textContent = t('hero.off_codes');
    else if (d.mil) { lamp.classList.add('ok'); $('#hero-title').textContent = t('hero.off'); }
    else $('#hero-title').textContent = t('hero.unknown');
    $('#hero-sub').textContent = ns ? tn('hero.sub_stored', ns) + (np ? tn('hero.sub_pending', np) : '')
      : np ? tn('hero.sub_only_pending', np) : (d.mil && d.mil.on ? t('hero.sub_mil_no_codes') : t('hero.sub_none'));
    $('#hero-meta').textContent = t('hero.meta', { when: new Date(d.ts * 1000).toLocaleString(I.locale()) }) + (snap.demo ? t('hero.meta_demo') : '');
    $('#hero-n').textContent = ns; $('#hero-n-l').textContent = tn('hero.count', ns, {}).replace(/^\d+\s*/, '');

    const v = verdictFor(d);
    $('#verdict').replaceChildren(h('div', { class: 'verdict', 'data-s': v.s }, ic(v.icon), h('div', {}, h('b', { text: v.title }), h('span', { text: v.text }))));
    $('#tips').replaceChildren(...(d.tips || []).map(x => h('div', { class: 'tip' }, ic('bulb'), h('div', { text: L(x) }))));
    $('#warnings').replaceChildren(...(d.warnings || []).map(x => h('div', { class: 'tip' }, ic('info'), h('div', { text: L(x) }))));
    $('#n-stored').textContent = ns ? `(${ns})` : '';
    const fz = d.freeze && d.freeze.dtc;
    const many = ns > 3;
    $('#codes-stored').replaceChildren(...(ns ? d.stored.map((c, i) => codeCard(c, 's', many ? i === 0 : true, fz))
      : [h('div', { class: 'card card-pad muted', text: t('sec.none_stored') })]));
    $('#pending-wrap').hidden = !np;
    $('#codes-pending').replaceChildren(...d.pending.map(c => codeCard(c, 'p', true, fz)));
    const pm = d.permanent || [];
    $('#perm-wrap').hidden = !pm.length;
    $('#codes-perm').replaceChildren(...pm.map(c => codeCard(c, 'm', true, fz)));

    const fr = $('#freeze'); fr.replaceChildren();
    if (d.freeze && d.freeze.values.length) {
      fr.append(h('p', { class: 'small-note', style: 'margin:0 0 8px', text: t('sec.freeze_caption', { code: d.freeze.dtc }) }));
      fr.append(h('table', { class: 't' }, h('tbody', {}, d.freeze.values.map(x => h('tr', {}, h('td', { text: L(x.label) }), h('td', { class: 'num', text: fmtVal(x.value, x.dec, x.unit) }))))));
    } else fr.append(h('p', { class: 'small-note', style: 'margin:0', text: t('sec.freeze_none') }));
    const mon = $('#monitors'); mon.replaceChildren();
    if (d.monitors && d.monitors.length) d.monitors.forEach(m => mon.append(h('li', { class: m.ready ? 'ready' : 'notready' }, ic(m.ready ? 'check' : 'clock'), h('span', { text: L(m.name) + ': ' + t(m.ready ? 'mon.ready' : 'mon.not_ready') }))));
    else mon.append(h('li', { class: 'muted', text: t('mon.none') }));

    const stale = $('#stale'); stale.replaceChildren();
    if (snap.dtc_stale) stale.append(h('div', { class: 'banner warn', style: 'margin:0' }, ic('warn'), h('div', { text: t('stale.msg') })));
  }

  // ----------------------------------------------------------- live dashboard
  function renderCluster(snap) {
    const online = snap.status === 'online';
    const rpm = liveVal(snap, 'rpm'), spd = liveVal(snap, 'speed');
    gRpm.set(online ? rpm : null, rpm != null && rpm >= 6500);
    gSpd.set(online ? spd : null, false);
    for (const u of TUI) {
      const def = u.def, m = metaOf(def.key);
      const label = L(m.label);
      if (label !== u.lblText) { u.lblText = label; u.lbl.textContent = label; if (u.m) u.m.setAttribute('aria-label', label); }
      const o = snap.live[def.key];
      let v = o && (typeof o.v === 'number' || (o.v && typeof o.v === 'object') || typeof o.v === 'string') ? o.v : null, age = o ? snap.now - o.t : Infinity;
      if (def.key === 'volt' && v == null && snap.adapter && snap.adapter.voltage != null) { v = snap.adapter.voltage; age = 0; }
      const known = (snap.supported || []).includes(def.key) || v != null;
      u.root.classList.toggle('na-hide', online ? !known : !def.core);
      const limit = Math.max(10, 2.5 * (m.interval || 1));      // slow sensors (e.g. read every 15 s) must not look "stale"
      const stale = v == null || age > limit || (!online && def.key !== 'volt');
      u.root.classList.toggle('stale', stale);
      if (v == null || (!online && def.key !== 'volt')) { u.num.textContent = '—'; u.st.replaceChildren(); u.root.dataset.st = ''; if (u.f) u.f.style.width = '0'; if (u.spark) u.spark.draw([], snap.now, 60, '', 0); continue; }
      u.num.textContent = typeof v === 'number' ? fmt(v, m.dec) : L(v);
      const st = def.state && typeof v === 'number' ? def.state(v, snap) : null;
      u.root.dataset.st = st ? st[0] : '';
      u.st.replaceChildren(...(st && st[1] ? [ic(STATE_ICON[st[0]]), h('span', { text: t(st[1]) })] : []));
      if (u.m) {
        u.m.dataset.s = st && (st[0] === 'warn' || st[0] === 'bad') ? st[0] : 'ok';
        u.m.setAttribute('aria-valuenow', v);
        if (def.kind === 'meter') u.f.style.width = (clamp((v - def.min) / (def.max - def.min), 0, 1) * 100).toFixed(1) + '%';
        else {
          const half = clamp(Math.abs(v - def.center) / (def.max - def.center), 0, 1) * 50;
          u.f.className = 'f' + (v < def.center ? ' neg' : '');
          u.f.style.left = (v >= def.center ? 50 : 50 - half).toFixed(1) + '%'; u.f.style.width = half.toFixed(1) + '%';
        }
      }
      if (u.spark) u.spark.draw(series(def.key, 60, snap.now), snap.now, 60, m.unit, m.dec);
    }
    const note = $('#dash-note');
    note.hidden = online;
    if (!online) { note.className = 'banner warn'; note.replaceChildren(ic('info'), h('div', { text: t('dash.no_data') })); }
  }

  function renderChart(snap) {
    if (S.tab !== 'dash') return;
    const keys = Object.keys(S.hist).filter(k => (S.hist[k] || []).length > 1);
    const pri = ['rpm', 'speed', 'ect', 'throttle', 'load', 'map', 'volt', 'stft1', 'ltft1', 'iat', 'timing'];
    keys.sort((a, b) => (pri.indexOf(a) < 0 ? 99 : pri.indexOf(a)) - (pri.indexOf(b) < 0 ? 99 : pri.indexOf(b)));
    const sig = keys.join(',') + '|' + I.getLang();
    const sel = $('#chart-sel');
    if (sig !== S.chartSig) {
      S.chartSig = sig;
      sel.replaceChildren(...keys.map(k => h('option', { value: k, text: L(metaOf(k).label) })));
      if (!keys.includes(S.chartKey)) S.chartKey = keys[0] || 'rpm';
      sel.value = S.chartKey;
    }
    const m = metaOf(S.chartKey);
    $('#chart-title').textContent = t('chart.title', { label: L(m.label), min: WIN / 60 });
    const pts = series(S.chartKey, WIN, snap.now);
    chart.render(pts, { now: snap.now, win: WIN, unit: m.unit, dec: m.dec, zero: ZERO.has(S.chartKey) });
    const tbl = $('#chart-table');
    if (S.chartTable) {
      const rows = pts.slice(-20).reverse();
      tbl.replaceChildren(h('table', { class: 't' }, h('thead', {}, h('tr', {}, h('th', { text: t('chart.when') }), h('th', { class: 'num', text: L(m.label) + (m.unit ? ' (' + m.unit + ')' : '') }))),
        h('tbody', {}, rows.map(p => h('tr', {}, h('td', { text: snap.now - p[0] < 1.5 ? t('chart.now') : t('chart.ago', { s: Math.round(snap.now - p[0]) }) }), h('td', { class: 'num', text: fmt(p[1], m.dec) }))))));
    }
  }

  // ----------------------------------------------------------------- car data
  function renderData(snap) {
    if (S.tab !== 'data') return;
    const ad = snap.adapter || {}, ve = snap.vehicle || {};
    const rows = [[t('kv.state'), L(snap.detail)], [t('kv.car'), snap.car || '—'], [t('kv.port'), snap.port || '—'], [t('kv.adapter'), ad.version || '—'],
      [t('kv.voltage'), ad.voltage != null ? fmt(ad.voltage, 1) + ' V' : '—'], [t('kv.protocol'), ve.protocol || '—'], [t('kv.vin'), ve.vin || '—'],
      [t('kv.cal'), ve.cal_id || '—'], [t('kv.std'), L(ve.obd_standard) || '—'],
      [t('kv.since'), snap.connected_at ? new Date(snap.connected_at * 1000).toLocaleTimeString(I.locale()) : '—']];
    $('#vehicle-kv').replaceChildren(...rows.flatMap(([a, b]) => [h('dt', { text: a }), h('dd', { text: b })]));
    const sup = snap.supported || [];
    $('#n-sup').textContent = sup.length ? `(${sup.length})` : '';
    $('#sup-list').textContent = sup.length ? sup.map(k => L(metaOf(k).label)).join(' · ') : t('data.sensors_none');
    const tb = $('#live-table tbody');
    const keys = Object.keys(snap.live).sort((a, b) => L(metaOf(a).label).localeCompare(L(metaOf(b).label), I.locale()));
    tb.replaceChildren(...keys.map(k => {
      const o = snap.live[k], m = metaOf(k);
      return h('tr', {}, h('td', { text: L(m.label) }), h('td', { class: 'num', text: fmtVal(o.v, m.dec, m.unit) }),
        h('td', { class: 'num muted', text: Math.max(0, Math.round(snap.now - o.t)) + ' s' }));
    }));
    if (!keys.length) tb.append(h('tr', {}, h('td', { colspan: 3, class: 'muted', text: t('data.live_none') })));
  }

  function appendEvents(evs) {
    if (!evs || !evs.length) return;
    for (const e of evs) { if (e.seq > S.evSeq) { S.evSeq = e.seq; S.events.push(e); } }
    while (S.events.length > 400) S.events.shift();
    if (S.tab === 'data') renderLog(false);
  }
  function renderLog(force) {
    const log = $('#log');
    const near = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    if (force || log.childElementCount !== S.events.length) {
      log.replaceChildren(...S.events.map(e => h('div', { class: e.level, text: logTime(e.t) + '  ' + L(e.msg) })));
      if (near || force) log.scrollTop = log.scrollHeight;
    }
  }

  // ----------------------------------------------------------------- pipeline
  function onSnap(snap) {
    S.snap = snap;
    collect(snap);
    renderTop(snap);
    const sig = (snap.dtc ? snap.dtc.ts + '|' + snap.dtc_stale : 'none') + '|' + snap.status + '|' + JSON.stringify(snap.busy) + '|' + snap.demo + '|' + I.getLang();
    if (sig !== S.dtcSig) { S.dtcSig = sig; renderDiag(snap); }
    renderCluster(snap);
    renderChart(snap);
    renderData(snap);
    appendEvents(snap.events);
    updateConnectModal(snap);
  }

  function rerender() {                                   // after a language change
    S.dtcSig = ''; S.bannerSig = ''; S.chartSig = '';
    if (gRpm) { gRpm.setLabel(t('g.rpm')); gSpd.setLabel(t('g.speed')); }
    $('#chart-table-btn').textContent = t(S.chartTable ? 'chart.graph_btn' : 'chart.table_btn');
    $('#hist-hint').textContent = t('hist.select', { folder: 'data/history' });
    if (S.snap) onSnap(S.snap);
    if (S.tab === 'data') renderLog(true);
    if (S.tab === 'hist') loadHistory();
  }

  // --------------------------------------------------------------------- tabs
  function showTab(name) {
    S.tab = name;
    for (const b of $$('.tab')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
    for (const s of $$('main > section')) s.hidden = s.id !== 'tab-' + name;
    G.hideTip();
    try { history.replaceState(null, '', location.search + '#' + name); } catch (e) { /* ignore */ }
    if (S.snap) { S.chartSig = ''; renderChart(S.snap); renderData(S.snap); }
    if (name === 'data') renderLog(true);
    if (name === 'hist') loadHistory();
  }
  $$('.tab').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

  // ------------------------------------------------------------------ actions
  async function readCodes() {
    const btn = $('#btn-read'); btn.disabled = true; btn.firstChild.classList.add('spin');
    try { await api('/api/dtc/read', {}); toast(t('toast.read_done')); } catch (e) { toast(e.message, true); } finally { btn.firstChild.classList.remove('spin'); }
  }
  async function copyReport() {
    try {
      const r = await fetch('/api/report?lang=' + I.getLang());
      toast((await copyText(await r.text())) ? t('toast.report_copied') : t('toast.report_copy_failed'), false);
    } catch (e) { toast(t('toast.report_failed'), true); }
  }

  function openClear() {
    const d = S.snap && S.snap.dtc; const codes = d ? d.stored.map(c => c.code) : [];
    modal((box, m) => {
      const chk = h('input', { type: 'checkbox', id: 'clr-ok' });
      const go = h('button', { class: 'btn danger solid', type: 'button', disabled: true }, ic('erase'), t('clear.go'));
      const cancel = h('button', { class: 'btn', type: 'button', text: t('clear.cancel'), onclick: () => m.close() });
      const closeBtn = () => h('div', { class: 'modal-actions' }, h('button', { class: 'btn primary', type: 'button', text: t('common.close'), onclick: () => m.close() }));
      chk.addEventListener('change', () => { go.disabled = !chk.checked; });
      box.append(h('h2', { text: t('clear.title') }),
        h('p', { text: codes.length ? t('clear.codes_now', { codes: codes.join(', ') }) : t('clear.copy_saved') }),
        h('ul', {}, ['clear.b1', 'clear.b2', 'clear.b3', 'clear.b4'].map(k => h('li', { text: t(k) }))),
        h('label', { class: 'check', for: 'clr-ok' }, chk, h('span', { text: t('clear.ack') })),
        h('div', { class: 'modal-actions' }, cancel, go));
      go.addEventListener('click', async () => {
        box.replaceChildren(h('h2', { text: t('clear.working') }), h('div', { class: 'progress' }, h('div', { class: 'line' }, ic('refresh', 'spin'), h('span', { text: t('clear.working_p') }))));
        try {
          const r = (await api('/api/dtc/clear', { confirm: true })).result;
          box.replaceChildren();
          if (!r.ok) box.append(h('h2', { text: t('clear.refused_title') }), h('p', { text: L(r.reason) }), closeBtn());
          else if (r.light_off) box.append(h('div', { class: 'result-ok' }, ic('check'), h('div', {}, h('h2', { text: t('clear.ok_title') }),
            h('p', { text: r.before.length ? t('clear.ok_codes', { codes: r.before.join(', ') }) : t('clear.ok_none') }),
            h('p', { class: 'small-note', text: t('clear.ok_note') }))), closeBtn());
          else box.append(h('h2', { text: t('clear.still_title') }),
            h('p', { text: r.remaining.length ? t('clear.still_codes', { codes: r.remaining.join(', ') }) : t('clear.still_nocodes') }), closeBtn());
        } catch (e) {
          box.replaceChildren(h('h2', { text: t('clear.fail_title') }), h('p', { text: e.message }), closeBtn());
        }
      });
    });
  }

  // --------------------------------------------------------------- connection
  const lsGet = k => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } };
  const portLabel = p => {
    const name = p.bt_name || (p.description && p.description !== 'n/a' ? p.description : '');
    return p.device + (name ? ' — ' + name : '') + (p.bluetooth ? ' (' + t('conn.kind_bt') + (p.incoming ? ', ' + t('conn.kind_incoming') : '') + ')' : '');
  };

  async function openConnect(demoFirst) {
    let ports = [], scenarios = {}, serverCar = '';
    const m = modal(box => { box.append(h('h2', { text: t('conn.title') }), h('p', { class: 'muted', text: t('conn.loading') })); });
    try { const r = await api('/api/ports'); ports = r.ports; scenarios = r.scenarios; serverCar = r.car || ''; } catch (e) { toast(e.message, true); m.close(); return; }
    draw();
    function draw() {
      const box = m.el; box.replaceChildren();
      const usable = ports.filter(p => (p.bluetooth && !p.incoming) || p.kind === 'usb');
      const opts = [h('label', { class: 'opt' }, h('input', { type: 'radio', name: 'port', value: 'auto', checked: true }), h('div', {}, h('b', { text: t('conn.auto_title') }),
        h('span', { text: usable.length ? t('conn.auto_found', { n: usable.length }) : t('conn.auto_none') })))];
      ports.forEach(p => opts.push(h('label', { class: 'opt' }, h('input', { type: 'radio', name: 'port', value: p.device }), h('div', {}, h('b', { text: portLabel(p) }), h('span', { text: p.hwid || '' })))));
      const sc = h('select', { id: 'cn-sc' }, Object.entries(scenarios).map(([k, v]) => h('option', { value: k, text: L(v) })));
      sc.style.cssText = 'height:34px;border-radius:8px;border:1px solid var(--border);background:var(--surface);padding:0 8px;width:100%;min-width:0';
      const car = h('input', { type: 'text', id: 'cn-car', value: lsGet('obd-car') || serverCar, maxlength: 60, autocomplete: 'off' });
      car.style.cssText = 'height:34px;border-radius:8px;border:1px solid var(--border);background:var(--surface);padding:0 10px;width:100%';
      const connect = h('button', { class: 'btn primary', type: 'button' }, ic('plug'), t('hdr.connect'));
      const demo = h('button', { class: 'btn', type: 'button' }, ic('play'), t('conn.demo_btn'));
      box.append(h('h2', { text: t('conn.title') }), h('p', { class: 'muted', text: t('conn.intro') }),
        h('div', { class: 'opts' }, opts),
        h('div', {}, h('label', { for: 'cn-car', style: 'font-weight:600;font-size:14px', text: t('conn.car_label') }), car, h('p', { class: 'small-note', style: 'margin:4px 0 0', text: t('conn.car_hint') })),
        h('div', { class: 'modal-actions', style: 'justify-content:space-between' },
          h('button', { class: 'btn ghost sm', type: 'button', onclick: async e => { e.currentTarget.textContent = t('conn.refreshing'); try { const r = await api('/api/ports'); ports = r.ports; } catch (x) { toast(x.message, true); } draw(); } }, ic('refresh'), t('conn.refresh')),
          connect),
        h('hr', { style: 'border:0;border-top:1px solid var(--border);width:100%;margin:4px 0' }),
        h('div', {}, h('b', { text: t('conn.demo_title') }), h('p', { class: 'small-note', style: 'margin:4px 0 8px', text: t('conn.demo_p') }), h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' }, sc, demo)));
      connect.addEventListener('click', () => start({ port: $('input[name=port]:checked', box).value, car: car.value.trim() }));
      demo.addEventListener('click', () => start({ demo: true, scenario: sc.value, car: car.value.trim() }));
      if (demoFirst) demo.focus();
    }
    function start(body) {
      lsSet('obd-car', body.car || '');
      S.conn = { el: m.el, m };
      m.onclose = () => { S.conn = null; };
      m.el.replaceChildren(h('h2', { text: t(body.demo ? 'conn.demo_starting' : 'conn.connecting') }), h('div', { class: 'progress', id: 'cn-prog' }, h('div', { class: 'line' }, ic('refresh', 'spin'), h('span', { id: 'cn-msg', text: t('conn.starting') }))));
      api('/api/connect', body).catch(e => showConnError(e.message));
    }
  }
  function showConnError(msg) {
    if (!S.conn) { toast(msg, true); return; }
    const box = S.conn.el, m = S.conn.m;
    box.replaceChildren(h('h2', { text: t('conn.failed') }), h('p', { style: 'color:var(--critical-text)', text: msg }),
      h('p', { class: 'small-note', text: t('conn.failed_tips') }),
      h('div', { class: 'modal-actions' }, h('button', { class: 'btn', type: 'button', text: t('conn.see_help'), onclick: () => { m.close(); showTab('help'); } }),
        h('button', { class: 'btn primary', type: 'button', text: t('conn.retry'), onclick: () => { m.close(); openConnect(); } })));
  }
  function updateConnectModal(snap) {
    if (!S.conn) return;
    const msg = $('#cn-msg', S.conn.el);
    if ((CONNECTING.includes(snap.status) || snap.status === 'car_offline') && msg) msg.textContent = L(snap.detail);
    else if (snap.status === 'online') { const m = S.conn.m; S.conn = null; m.close(); toast(snap.demo ? t('toast.demo_started') : t('toast.connected')); }
    else if (snap.status === 'error') showConnError(L(snap.detail));
  }
  async function toggleConnection() {
    if ($('#conn-btn').dataset.mode === 'connect') openConnect();
    else { try { await api('/api/disconnect', {}); toast(t('toast.disconnected')); } catch (e) { toast(e.message, true); } }
  }

  // ------------------------------------------------------------------ history
  async function loadHistory() {
    $('#hist-hint').textContent = t('hist.select', { folder: 'data/history' });
    try {
      const items = (await api('/api/history')).items;
      const list = $('#hist-list'); list.replaceChildren();
      if (!items.length) list.append(h('div', { class: 'card-pad muted', text: t('hist.empty_list') }));
      items.forEach(it => {
        const when = it.ts ? new Date(it.ts * 1000).toLocaleString(I.locale()) : it.ts_text;
        const b = h('button', { class: 'hist-item', type: 'button', 'aria-current': String(S.selHist === it.id) },
          h('div', { class: 'when' }, `${when} · `, h('b', { text: t(HIST_EVENTS[it.event] || 'hist.ev_reading') }), it.demo ? h('span', { class: 'pill', style: 'margin-left:8px', text: t('hist.demo') }) : null),
          h('div', { class: 'codes' }, it.stored.length ? it.stored.map(c => h('span', { class: 'code-badge', style: 'font-size:13px;padding:4px 7px', text: c })) : [h('span', { class: 'muted', text: t('hist.no_codes') })],
            it.pending.map(c => h('span', { class: 'pill', text: t('hist.pend_tag', { code: c }) }))));
        b.addEventListener('click', () => showHistory(it.id));
        list.append(b);
      });
    } catch (e) { toast(e.message, true); }
  }
  async function showHistory(id) {
    S.selHist = id;
    $$('.hist-item').forEach(b => b.setAttribute('aria-current', 'false'));
    try {
      const doc = (await api('/api/history/' + id)).doc, d = doc.data;
      const out = $('#hist-detail'); out.replaceChildren();
      const when = new Date(doc.ts * 1000).toLocaleString(I.locale());
      const light = d.mil ? t(d.mil.on ? 'hist.light_on' : 'hist.light_off') : t('hist.light_unread');
      out.append(h('div', { class: 'card card-pad' }, h('b', { text: `${t(HIST_EVENTS[doc.event] || 'hist.ev_reading')} · ${when}${doc.demo ? ' · ' + t('hist.demo') : ''}` }),
        h('p', { class: 'small-note', text: t('hist.detail_light', { state: light, s: d.stored.length, p: d.pending.length }) })));
      d.stored.forEach(c => out.append(codeCard(c, 'h' + id, false, null)));
      d.pending.forEach(c => out.append(codeCard(c, 'hp' + id, false, null)));
      if (d.freeze && d.freeze.values.length) out.append(h('div', { class: 'card card-pad' }, h('b', { text: t('hist.freeze_title', { code: d.freeze.dtc }) }),
        h('table', { class: 't' }, h('tbody', {}, d.freeze.values.map(x => h('tr', {}, h('td', { text: L(x.label) }), h('td', { class: 'num', text: fmtVal(x.value, x.dec, x.unit) })))))));
    } catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------ terminal, log, help
  $('#term-form').addEventListener('submit', async e => {
    e.preventDefault();
    const inp = $('#term-in'), cmd = inp.value.trim(); if (!cmd) return;
    const out = $('#term-out'); out.hidden = false;
    try {
      const r = (await api('/api/terminal', { cmd })).result;
      out.append(h('div', { text: '> ' + r.cmd }), ...(r.lines.length ? r.lines.map(l => h('div', { text: l })) : [h('div', { class: 'debug', text: r.error ? L(r.error) : t('data.term_noresp') })]));
    } catch (err) { out.append(h('div', { class: 'error', text: err.message })); }
    out.scrollTop = out.scrollHeight; inp.select();
  });
  $('#log-copy').addEventListener('click', async () => {
    toast((await copyText($$('#log div').map(d => d.textContent).join('\n'))) ? t('toast.log_copied') : t('toast.copy_failed'), false);
  });
  let rawTimer = null;
  $('#raw-on').addEventListener('change', e => {
    clearInterval(rawTimer); const old = $('#raw-box'); if (old) old.remove();
    if (!e.target.checked) return;
    const box = h('div', { class: 'log', id: 'raw-box', style: 'margin-top:8px;max-height:200px' }); $('#log').after(box);
    const pull = async () => { try { const r = (await api('/api/raw')).raw; box.replaceChildren(...r.slice(-150).map(x => h('div', { class: 'debug', text: `${x.d} ${x.x}` }))); box.scrollTop = box.scrollHeight; } catch (er) { /* ignore */ } };
    pull(); rawTimer = setInterval(pull, 1500);
  });
  $('#btn-diag').addEventListener('click', async e => {
    const btn = e.currentTarget, out = $('#diag-out');
    out.hidden = false; out.className = 'diag-out'; out.textContent = t('diag.running'); btn.disabled = true;
    try {
      const r = await api('/api/diag/bluetooth');
      out.replaceChildren();
      if (r.error || !r.sections.length) out.textContent = t('diag.failed');
      for (const sec of r.sections) {
        const lines = sec.lines.filter(Boolean);
        out.append(h('div', { class: 'diag-title', text: t(DIAG_TITLES[sec.key] || 'diag.serial') }), ...(lines.length ? lines.map(l => h('div', { text: l })) : [h('div', { class: 'muted', text: t('diag.none') })]));
      }
      if (r.sections.some(s => s.key === 'nearby')) out.append(h('p', { class: 'small-note', text: t('diag.hint_nearby') }));
    } catch (er) { out.textContent = er.message; }
    btn.disabled = false;
  });

  // ------------------------------------------------------------ general events
  $('#conn-btn').addEventListener('click', toggleConnection);
  $('#btn-read').addEventListener('click', readCodes);
  $('#btn-clear').addEventListener('click', openClear);
  $('#btn-copy').addEventListener('click', copyReport);
  $('#chart-sel').addEventListener('change', e => { S.chartKey = e.target.value; if (S.snap) renderChart(S.snap); });
  $('#chart-table-btn').addEventListener('click', e => {
    S.chartTable = !S.chartTable; e.currentTarget.setAttribute('aria-pressed', String(S.chartTable));
    e.currentTarget.textContent = t(S.chartTable ? 'chart.graph_btn' : 'chart.table_btn');
    $('#chart-box').hidden = S.chartTable; $('#chart-table').hidden = !S.chartTable; if (S.snap) renderChart(S.snap);
  });
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-act]'); if (!a) return;
    const act = a.dataset.act;
    if (act === 'connect') openConnect(); else if (act === 'demo') openConnect(true); else if (act === 'help') showTab('help'); else if (act === 'diag') showTab('diag');
  });
  $$('.lang-btn').forEach(b => b.addEventListener('click', async () => { await I.setLang(b.dataset.lang); rerender(); }));

  // -------------------------------------------------------------------- theme
  function setTheme(mode) {
    document.documentElement.dataset.theme = mode;
    $('#theme-btn').replaceChildren(ic(mode === 'dark' ? 'sun' : 'moon'));
    try { localStorage.setItem('obd-theme', mode); } catch (e) { /* no storage */ }
  }
  let theme = new URLSearchParams(location.search).get('theme');        // ?theme=dark|light overrides the saved choice (handy for screenshots)
  if (theme !== 'dark' && theme !== 'light') { try { theme = localStorage.getItem('obd-theme'); } catch (e) { theme = null; } }
  setTheme(theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  $('#theme-btn').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

  // ------------------------------------------------------------------- start
  async function init() {
    await I.init();
    try { S.meta = (await api('/api/pids')).pids; } catch (e) { /* labels fall back to the keys */ }
    buildCluster();
    $('#hist-hint').textContent = t('hist.select', { folder: 'data/history' });
    document.body.classList.remove('loading');
    const start = (location.hash || '').slice(1);
    showTab(['diag', 'dash', 'data', 'hist', 'help'].includes(start) ? start : 'diag');
    const es = new EventSource('/api/stream');
    es.onopen = () => { S.linkOk = true; if (S.snap) renderTop(S.snap); };
    es.onmessage = ev => { try { S.linkOk = true; onSnap(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
    es.onerror = () => { S.linkOk = false; if (S.snap) renderTop(S.snap); else $('#status-text').textContent = t('chip.no_link'); };
  }
  init();
})();
