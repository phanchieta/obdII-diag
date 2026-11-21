/* Visual primitives of the dashboard: radial gauge, sparkline and line chart. No dependencies. */
(function (global) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const T = (k, p) => (global.I18N ? global.I18N.t(k, p) : k);
  const fmt = (v, dec = 0) => Number(v).toLocaleString(global.I18N ? global.I18N.locale() : 'en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const ago = s => (s < 1.5 ? T('chart.now') : T('chart.ago', { s: Math.round(s) }));

  // ------------------------------------------------------------ shared tooltip
  const tip = document.getElementById('tooltip');
  function showTip(x, y, value, sub) {
    tip.replaceChildren();
    const b = document.createElement('b'); b.textContent = value;
    const s = document.createElement('span'); s.textContent = sub;
    tip.append(b, s);
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y - r.height - 12;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top < 8) top = y + 18;
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  }
  function hideTip() { tip.hidden = true; }

  // -------------------------------------------------------------- radial gauge
  function polar(cx, cy, r, deg) { const a = deg * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
  function arc(cx, cy, r, a0, a1) {
    const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a1);
    return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }

  /* o: {min,max,major,minor,tick(v),zones:[{from,to,cls}],label,unit,dec} */
  function radialGauge(host, o) {
    const C = 150, R = 118, A0 = 135, SW = 270;
    const ang = v => A0 + SW * clamp((v - o.min) / (o.max - o.min), 0, 1);
    let s = '<svg viewBox="0 0 300 300" role="img">';
    s += `<path class="g-track" d="${arc(C, C, R, A0, A0 + SW)}"/>`;
    for (const z of o.zones || []) s += `<path class="g-zone ${z.cls}" d="${arc(C, C, R + 11, ang(z.from), ang(z.to))}"/>`;
    for (let v = o.min; v <= o.max + 1e-9; v += o.minor) {
      const k = v / o.major, major = Math.abs(k - Math.round(k)) < 1e-9;
      const a = ang(v), [x0, y0] = polar(C, C, R - 14, a), [x1, y1] = polar(C, C, R - (major ? 28 : 21), a);
      s += `<line class="g-tick${major ? ' major' : ''}" x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}"/>`;
      if (major) { const [tx, ty] = polar(C, C, R - 42, a); s += `<text class="g-tl" x="${tx.toFixed(1)}" y="${ty.toFixed(1)}">${o.tick(v)}</text>`; }
    }
    s += `<path class="g-val" d="${arc(C, C, R, A0, A0 + SW)}" pathLength="100" style="stroke-dasharray:0 100;opacity:0"/>`;
    s += `<g class="g-dot" style="transform:rotate(${A0}deg);opacity:0"><circle cx="${C + R}" cy="${C}" r="6"/></g>`;
    s += '<text class="g-num na" x="150" y="172" style="font-size:46px">—</text>';
    s += `<text class="g-unit" x="150" y="196">${o.unit}</text>`;
    s += '<text class="g-label" x="150" y="244"></text></svg>';
    host.innerHTML = s;
    const svg = host.querySelector('svg');
    const num = host.querySelector('.g-num'), val = host.querySelector('.g-val'), dot = host.querySelector('.g-dot'), label = host.querySelector('.g-label');
    const setLabel = text => { label.textContent = text; svg.setAttribute('aria-label', text); };
    setLabel(o.label || '');
    return {
      setLabel,
      set(v, hot) {
        if (v == null || Number.isNaN(v)) {
          num.textContent = '—'; num.classList.add('na');
          val.style.opacity = 0; dot.style.opacity = 0; val.style.strokeDasharray = '0 100';
          return;
        }
        num.classList.remove('na'); num.textContent = fmt(v, o.dec || 0);
        const p = clamp((v - o.min) / (o.max - o.min), 0, 1);
        val.style.strokeDasharray = (p * 100).toFixed(2) + ' 100';
        val.style.opacity = p < 0.004 ? 0 : 1;
        dot.style.opacity = 1;
        dot.style.transform = `rotate(${ang(v).toFixed(2)}deg)`;
        val.classList.toggle('hot', !!hot); dot.classList.toggle('hot', !!hot);
      },
    };
  }

  // ---------------------------------------------------------------- sparkline
  function sparkline(host) {
    host.innerHTML = '<svg viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true"><path class="ar"/><path class="ln"/></svg><i class="end" hidden></i>';
    const ar = host.querySelector('.ar'), ln = host.querySelector('.ln'), end = host.querySelector('.end');
    let ctx = null;
    host.addEventListener('pointermove', e => {
      if (!ctx || ctx.points.length < 2) return;
      const r = host.getBoundingClientRect();
      const t = (ctx.now - ctx.win) + ((e.clientX - r.left) / r.width) * ctx.win;
      let best = ctx.points[0];
      for (const p of ctx.points) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      showTip(e.clientX, e.clientY, fmt(best[1], ctx.dec) + (ctx.unit ? ' ' + ctx.unit : ''), ago(ctx.now - best[0]));
    });
    host.addEventListener('pointerleave', hideTip);
    return {
      draw(points, now, win, unit, dec) {
        ctx = { points, now, win, unit, dec };
        if (points.length < 2) { ln.setAttribute('d', ''); ar.setAttribute('d', ''); end.hidden = true; return; }
        let lo = Infinity, hi = -Infinity;
        for (const p of points) { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; }
        if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
        const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
        const X = t => 100 * clamp((t - (now - win)) / win, 0, 1), Y = v => 32 - 28 * (v - lo) / (hi - lo);
        let d = '';
        points.forEach((p, i) => { d += (i ? 'L' : 'M') + X(p[0]).toFixed(2) + ' ' + Y(p[1]).toFixed(2); });
        const last = points[points.length - 1];
        ln.setAttribute('d', d);
        ar.setAttribute('d', d + `L${X(last[0]).toFixed(2)} 34L${X(points[0][0]).toFixed(2)} 34Z`);
        end.hidden = false; end.style.left = X(last[0]).toFixed(2) + '%'; end.style.top = (Y(last[1]) / 34 * 100).toFixed(2) + '%';
      },
    };
  }

  // --------------------------------------------------------------- line chart
  function niceTicks(lo, hi, n) {
    if (hi - lo < 1e-9) { hi = lo + 1; }
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function lineChart(host) {
    host.replaceChildren();
    const svg = document.createElementNS(NS, 'svg');
    const empty = document.createElement('div'); empty.className = 'chart-empty';
    host.append(svg, empty);
    let state = null;
    const el = (tag, attrs, parent) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); (parent || svg).append(n); return n; };

    /* o: {now, win, unit, dec, zero} */
    function render(points, o) {
      state = { points, o };
      svg.replaceChildren();
      empty.textContent = T('chart.waiting');
      const W = host.clientWidth, H = host.clientHeight;
      if (!W || !H) return;
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      if (points.length < 2) { empty.hidden = false; return; }
      empty.hidden = true;
      const L = 50, R = 16, T0 = 10, B = 26, pw = W - L - R, ph = H - T0 - B;
      let lo = Infinity, hi = -Infinity;
      for (const p of points) { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; }
      if (o.zero) lo = Math.min(0, lo);
      const ticks = niceTicks(lo, hi, 4), y0 = ticks[0], y1 = ticks[ticks.length - 1];
      const X = t => L + pw * clamp((t - (o.now - o.win)) / o.win, 0, 1), Y = v => T0 + ph * (1 - (v - y0) / ((y1 - y0) || 1));
      for (const tv of ticks) {
        el('line', { class: 'gl', x1: L, x2: W - R, y1: Y(tv), y2: Y(tv) });
        const t = el('text', { class: 'ax', x: L - 8, y: Y(tv), 'text-anchor': 'end', 'dominant-baseline': 'central' }); t.textContent = fmt(tv, o.dec > 0 && Math.abs(tv) < 100 ? Math.min(o.dec, 1) : 0);
      }
      [0, .25, .5, .75, 1].forEach(f => {
        const t = el('text', { class: 'ax', x: L + pw * f, y: H - 7, 'text-anchor': f === 0 ? 'start' : f === 1 ? 'end' : 'middle' });
        t.textContent = f === 1 ? T('chart.now') : '−' + Math.round(o.win * (1 - f)) + ' s';
      });
      let d = '';
      points.forEach((p, i) => { d += (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1); });
      const last = points[points.length - 1];
      el('path', { class: 'ar', d: d + `L${X(last[0]).toFixed(1)} ${T0 + ph}L${X(points[0][0]).toFixed(1)} ${T0 + ph}Z` });
      el('path', { class: 'ln', d });
      el('circle', { class: 'pt', cx: X(last[0]), cy: Y(last[1]), r: 4 });
      const cross = el('line', { class: 'cross', y1: T0, y2: T0 + ph, visibility: 'hidden' });
      const mark = el('circle', { class: 'pt', r: 4, visibility: 'hidden' });
      const hit = el('rect', { x: L, y: T0, width: pw, height: ph, fill: 'transparent' });
      hit.addEventListener('pointermove', e => {
        const r = svg.getBoundingClientRect();
        const t = (o.now - o.win) + ((e.clientX - r.left - L) / pw) * o.win;
        let best = points[0];
        for (const p of points) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
        cross.setAttribute('x1', X(best[0])); cross.setAttribute('x2', X(best[0])); cross.setAttribute('visibility', 'visible');
        mark.setAttribute('cx', X(best[0])); mark.setAttribute('cy', Y(best[1])); mark.setAttribute('visibility', 'visible');
        showTip(e.clientX, e.clientY, fmt(best[1], o.dec) + (o.unit ? ' ' + o.unit : ''), ago(o.now - best[0]));
      });
      hit.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); mark.setAttribute('visibility', 'hidden'); hideTip(); });
    }
    new ResizeObserver(() => { if (state) render(state.points, state.o); }).observe(host);
    return { render };
  }

  global.Gauges = { radialGauge, sparkline, lineChart, fmt, clamp, hideTip };
})(window);
