"""vibeboard の「ハード」タブの画面（HTML の body・CSS・inline script）。仕様は docs/specs/dashboard.md §14。

⚠ **値は JSON で埋め込み、描くのは script の 1 経路だけ**（最初の表示も 5 秒ごとの更新も同じ関数が描く。
Python と JS に同じ表を 2 回書かない）。⚠ **文字は必ず `textContent` で入れる**（プロセスの cmdline は
他人が決められる文字列。`innerHTML` に連結しない）。

  - `now`: タイルとメーター（1 つの値に折れ線は要らない）。スレッド別だけ細い縦棒
  - `history`: 1 系列 1 枚の小さな折れ線 × 6（⚠ **縦軸を 2 本にしない**。単位が違うものは枚を分ける）
  - 色は系列 1 色（青）＋ 状態の警告色（ディスクの残り）だけ。明暗は `prefers-color-scheme` で選び直す
"""

from __future__ import annotations

import json

ITEMS = (("now", "いまの状態"), ("history", "この 1 時間"))
DISK_WARN_PCT = 90                # ⚠ 【推測】の目安。spec §14 にそう書く

CSS = """
 body { --series: #2a78d6; --track: #cde2fb; --grid: #e1e0d9; --axis: #c3c2b7;
        --muted: #898781; --warnfill: #fab219; }
 @media (prefers-color-scheme: dark) {
   body { --series: #3987e5; --track: #0d366b; --grid: #2c2c2a; --axis: #383835; }
 }
 .tiles { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
 .tile { min-width: 132px; flex: 0 1 172px; padding: 8px 10px; border-radius: 6px;
         border: 1px solid color-mix(in srgb, currentColor 18%, transparent); }
 .tile .lb { font-size: 12px; opacity: .7; }
 .tile .val { font-size: 20px; font-weight: 600; line-height: 1.3; }
 .tile .val small { font-size: 12px; font-weight: 400; opacity: .7; margin-left: 2px; }
 .tile .sub { font-size: 12px; opacity: .7; }
 .meter { height: 6px; border-radius: 3px; background: var(--track); margin-top: 6px; overflow: hidden; }
 .meter .fill { height: 100%; background: var(--series); border-radius: 3px; }
 .meter.warn .fill { background: var(--warnfill); }
 td .meter { width: 160px; margin: 0; }
 .threads { display: flex; align-items: flex-end; gap: 2px; height: 56px; margin: 8px 0 2px;
            border-bottom: 1px solid var(--axis); max-width: 640px; }
 .threads .col { flex: 1 1 0; max-width: 24px; height: 100%; display: flex; align-items: flex-end; }
 .threads .col i { display: block; width: 100%; min-height: 1px; background: var(--series);
                   border-radius: 3px 3px 0 0; }
 .threads .col:hover i { filter: brightness(1.25); }
 .stale { color: #b8860b; }
 td.cmd { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; }
 .charts { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
 .chart { border: 1px solid color-mix(in srgb, currentColor 18%, transparent); border-radius: 6px;
          padding: 8px 10px 4px; }
 .chart .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
 .chart .head .v { font-size: 16px; font-weight: 600; }
 .chart .head .t { font-size: 12px; opacity: .7; margin-left: 6px; }
 .chart svg { display: block; width: 100%; height: auto; outline: none; touch-action: none; }
 .chart svg:focus-visible { outline: 1px solid var(--series); }
 .chart .grid { stroke: var(--grid); stroke-width: 1; vector-effect: non-scaling-stroke; }
 .chart .base { stroke: var(--axis); stroke-width: 1; vector-effect: non-scaling-stroke; }
 .chart .line { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round;
                stroke-linecap: round; vector-effect: non-scaling-stroke; }
 .chart .area { fill: var(--series); opacity: .1; }
 .chart .dot { fill: var(--series); stroke: Canvas; stroke-width: 2; }
 .chart .cross { stroke: var(--muted); stroke-width: 1; vector-effect: non-scaling-stroke; }
 .chart text { fill: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }
"""

_COMMON_JS = r"""
const $ = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const num = (v, d = 0) => v == null ? '—'
  : Number(v).toLocaleString('ja-JP', { maximumFractionDigits: d });
const hms = ts => new Date(ts * 1000).toLocaleTimeString('ja-JP', { hour12: false });
function poll(url, intervalS, onData, onStale) {
  let timer = null;
  const tick = async () => {
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      onData(await r.json());
      onStale(false);
    } catch (e) { onStale(true); }
  };
  const arm = () => {
    clearInterval(timer);
    if (document.hidden) return;          // 見えていない間は取りに行かない
    tick();
    timer = setInterval(tick, Math.max(1, intervalS) * 1000);
  };
  document.addEventListener('visibilitychange', arm);
  timer = setInterval(tick, Math.max(1, intervalS) * 1000);
}
"""

NOW_JS = _COMMON_JS + r"""
const root = document.getElementById('hw');
const WARN = Number(root.dataset.diskWarn);
const bytes = b => {
  if (b == null) return '—';
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return num(b, b >= 100 ? 0 : 1) + ' ' + u[i];
};
const elapsed = s => {
  if (s == null) return '—';
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h} 時間 ${m} 分` : m ? `${m} 分` : `${s} 秒`;
};
function meter(pct, warn) {
  const m = $('div', 'meter' + (warn ? ' warn' : ''));
  const f = $('div', 'fill');
  f.style.width = (pct == null ? 0 : Math.max(0, Math.min(100, pct))) + '%';
  m.appendChild(f);
  return m;
}
function tile(label, value, unit, sub, pct) {
  const t = $('div', 'tile');
  t.appendChild($('div', 'lb', label));
  const v = $('div', 'val', value);
  if (unit) v.appendChild($('small', '', unit));
  t.appendChild(v);
  if (sub) t.appendChild($('div', 'sub', sub));
  if (pct !== undefined) t.appendChild(meter(pct));
  return t;
}
function table(headers, rows) {
  const tb = $('table');
  const hr = $('tr');
  headers.forEach(h => hr.appendChild($('th', h.num ? 'num' : '', h.t)));
  tb.appendChild(hr);
  rows.forEach(r => {
    const tr = $('tr');
    r.forEach((c, i) => {
      const td = $('td', headers[i].cls || (headers[i].num ? 'num' : ''));
      if (c instanceof Node) td.appendChild(c); else td.textContent = c;
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  return tb;
}
function render(s) {
  const open = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.k));
  const out = [];
  const h1 = $('h1', '', 'ハードの利用状況');
  out.push(h1);
  const meta = $('p', 'meta', `取得 ${hms(s.ts)} ・ ${num(s.interval_s, 1)} 秒おきに更新`);
  meta.id = 'meta';
  out.push(meta);

  out.push($('h2', '', 'GPU'));
  if (!s.gpu.ok) {
    out.push($('p', 'warn', `⚠ GPU を読めない: ${s.gpu.error}`));
  } else {
    s.gpu.gpus.forEach(g => {
      out.push($('p', 'meta', `GPU ${g.index}: ${g.name}`));
      const row = $('div', 'tiles');
      row.appendChild(tile('使用率', num(g['utilization.gpu']), '%', null, g['utilization.gpu']));
      row.appendChild(tile('メモリ', num(g['memory.pct'], 1), '%',
        `${num(g['memory.used'])} / ${num(g['memory.total'])} MiB`, g['memory.pct']));
      row.appendChild(tile('温度', num(g['temperature.gpu']), '℃'));
      const pw = g['power.draw'], lim = g['power.limit'];
      row.appendChild(tile('電力', num(pw), 'W', lim == null ? null : `上限 ${num(lim)} W`,
        pw == null || !lim ? null : 100 * pw / lim));
      row.appendChild(tile('ファン', num(g['fan.speed']), '%'));
      row.appendChild(tile('P-state', g.pstate || '—', null,
        g.throttle.length ? '⚠ 絞り: ' + g.throttle.join('・') : '絞りなし'));
      out.push(row);
    });
    out.push($('p', 'meta', '⚠ メモリの合計には Windows 側の使用分も混ざる（WSL2 ではプロセス別の内訳が出ない）'));
    out.push($('h2', '', 'GPU を使っているプロセス（WSL 側）'));
    if (s.gpu.procs_error) out.push($('p', 'warn', `⚠ 一覧を読めない: ${s.gpu.procs_error}`));
    else if (!s.gpu.procs.length) out.push($('p', 'meta', '無い'));
    else out.push(table(
      [{ t: 'PID', num: 1 }, { t: '経過', num: 1 }, { t: 'RSS', num: 1 }, { t: 'コマンド', cls: 'cmd' }],
      s.gpu.procs.map(p => [String(p.pid), elapsed(p.elapsed_s),
        p.rss_kb == null ? '—' : bytes(p.rss_kb * 1024), p.cmdline || '—（もう居ない）'])));
  }

  out.push($('h2', '', 'CPU'));
  const c = s.cpu, cr = $('div', 'tiles');
  cr.appendChild(tile('使用率（全体）', num(c.total_pct, 1), '%',
    c.total_pct == null ? '次の更新から出る（2 時点の差）' : `${c.threads} スレッド`, c.total_pct));
  const la = c.loadavg || [null, null, null];
  cr.appendChild(tile('load average', num(la[0], 2), null, `5 分 ${num(la[1], 2)} ・ 15 分 ${num(la[2], 2)}`));
  out.push(cr);
  if (c.per_thread_pct) {
    const bars = $('div', 'threads');
    c.per_thread_pct.forEach((p, i) => {
      const col = $('div', 'col');
      col.title = `cpu${i}: ${num(p, 1)}%`;
      const b = $('i');
      b.style.height = (p == null ? 0 : p) + '%';
      col.appendChild(b);
      bars.appendChild(col);
    });
    out.push(bars);
    out.push($('p', 'meta', `スレッド別の使用率（左から cpu0。縦は 0〜100%）`));
    const d = $('details');
    d.dataset.k = 'threads';
    d.appendChild($('summary', '', '表で見る'));
    d.appendChild(table([{ t: 'スレッド' }, { t: '使用率 %', num: 1 }],
      c.per_thread_pct.map((p, i) => [`cpu${i}`, num(p, 1)])));
    out.push(d);
  }

  out.push($('h2', '', 'メモリ'));
  const m = s.mem, mr = $('div', 'tiles');
  const kb = v => v == null ? '—' : bytes(v * 1024);
  mr.appendChild(tile('使用', num(m.used_pct, 1), '%', `${kb(m.used_kb)} / ${kb(m.total_kb)}`, m.used_pct));
  mr.appendChild(tile('空き（available）', kb(m.available_kb)));
  const sp = m.swap_total_kb ? 100 * m.swap_used_kb / m.swap_total_kb : null;
  mr.appendChild(tile('swap', m.swap_total_kb ? num(sp, 1) : '—', m.swap_total_kb ? '%' : null,
    m.swap_total_kb ? `${kb(m.swap_used_kb)} / ${kb(m.swap_total_kb)}` : 'swap なし',
    m.swap_total_kb ? sp : undefined));
  out.push(mr);

  out.push($('h2', '', 'ディスク'));
  if (!s.disks.length) out.push($('p', 'meta', '読めるマウントが無い'));
  else out.push(table(
    [{ t: 'マウント' }, { t: '使用 %', num: 1 }, { t: '' }, { t: '使用', num: 1 }, { t: '空き', num: 1 },
     { t: '全体', num: 1 }, { t: '' }],
    s.disks.map(d => {
      const w = d.used_pct != null && d.used_pct >= WARN;
      return [d.mount, num(d.used_pct, 1), meter(d.used_pct, w), bytes(d.used), bytes(d.free),
        bytes(d.total), w ? `⚠ 残りわずか（${WARN}% 以上）` : ''];
    })));

  out.push($('h2', '', 'この画面で読めないもの'));
  const ul = $('ul');
  ['CPU の温度（WSL2 に sensors が無い）',
   'プロセス別の GPU メモリ（WSL2 では [N/A]）',
   'Windows 側で GPU を使っているプロセス（WSL からは見えない）',
  ].forEach(t => ul.appendChild($('li', '', t)));
  out.push(ul);

  root.replaceChildren(...out);
  root.querySelectorAll('details').forEach(d => { if (open.has(d.dataset.k)) d.open = true; });
}
const first = JSON.parse(document.getElementById('hw-data').textContent);
render(first);
poll('api/snapshot', first.interval_s || 5, render, stale => {
  const m = document.getElementById('meta');
  if (m) m.classList.toggle('stale', stale);
  if (m && stale && !m.textContent.includes('止まっている')) m.textContent += ' ・ ⚠ 更新が止まっている';
});
"""

HISTORY_JS = _COMMON_JS + r"""
const root = document.getElementById('hw');
const NS = 'http://www.w3.org/2000/svg';
const W = 360, H = 132, L = 30, R = 10, T = 8, B = 20;
const svg = (tag, attrs, cls) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (cls) e.setAttribute('class', cls);
  return e;
};
const niceMax = v => {
  if (!(v > 0)) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return [1, 2, 2.5, 5, 10].map(k => k * p).find(k => k >= v);
};
let data = null, hover = -1, charts = [];

// 乗せた時刻の値を 6 枚ぶん出す。⚠ 描き直さない（十字線・点・見出しの値だけ動かす）
function setHover(i) {
  hover = i;
  const pts = data.points;
  charts.forEach(c => {
    const k = i >= 0 ? i : pts.length - 1;
    const p = pts[k], v = p ? p[c.s.key] : null;
    c.vEl.textContent = p ? `${num(v, 1)} ${c.s.unit}` : '—';
    c.tEl.textContent = p ? (i >= 0 ? hms(p.ts) : '最新') : '';
    c.cross.style.display = i >= 0 ? '' : 'none';
    if (p) { c.cross.setAttribute('x1', c.x(p.ts)); c.cross.setAttribute('x2', c.x(p.ts)); }
    c.dot.style.display = p && v != null ? '' : 'none';
    if (p && v != null) { c.dot.setAttribute('cx', c.x(p.ts)); c.dot.setAttribute('cy', c.y(v)); }
  });
}

function draw(h) {
  data = h;
  const open = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.k));
  const pts = h.points;
  // 横軸は「貯まったぶん」。⚠ 起こした直後に 1 時間の枠へ数点を押し込まない（下限 5 分・上限は輪の長さ）
  const t1 = pts.length ? pts[pts.length - 1].ts : Date.now() / 1000;
  const span = Math.max(300, Math.min(h.capacity * h.interval_s, pts.length ? t1 - pts[0].ts : 0));
  const t0 = t1 - span;
  const x = ts => L + (W - L - R) * (ts - t0) / span;
  const out = [$('h1', '', 'この 1 時間')];
  const meta = $('p', 'meta', pts.length
    ? `${hms(pts[0].ts)} 〜 ${hms(t1)} ・ ${pts.length} 点（${num(h.interval_s, 1)} 秒おき・最大 ${num(h.capacity)} 点）`
    : 'まだ 1 点も無い');
  meta.id = 'meta';
  out.push(meta);
  out.push($('p', 'meta', '⚠ メモリ上だけに持つ。vibetab.py を入れ直すと消える。グラフに乗せるか、選んで ← → で時刻を動かすと、6 枚とも同じ時刻の値を出す'));

  charts = [];
  const grid = $('div', 'charts');
  h.series.forEach(s => {
    const vals = pts.map(p => p[s.key]);
    const ymax = s.max || niceMax(Math.max(0, ...vals.filter(v => v != null)));
    const y = v => T + (H - T - B) * (1 - Math.min(v, ymax) / ymax);
    const card = $('div', 'chart');
    const head = $('div', 'head');
    head.appendChild($('span', '', s.label));
    const rd = $('span'), vEl = $('span', 'v'), tEl = $('span', 't');
    rd.appendChild(vEl);
    rd.appendChild(tEl);
    head.appendChild(rd);
    card.appendChild(head);

    const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, tabindex: 0, role: 'img',
      'aria-label': `${s.label}の推移。値は下の表にもある` });
    [0, 0.5, 1].forEach(f => {
      const yy = y(ymax * f);
      g.appendChild(svg('line', { x1: L, x2: W - R, y1: yy, y2: yy }, f === 0 ? 'base' : 'grid'));
      const tx = svg('text', { x: L - 4, y: yy + 3, 'text-anchor': 'end' });
      tx.textContent = num(ymax * f);
      g.appendChild(tx);
    });
    for (let k = 0; k <= 4; k++) {
      const ts = t0 + span * k / 4;
      const tx = svg('text', { x: x(ts), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === 4 ? 'end' : 'middle' });
      tx.textContent = new Date(ts * 1000).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', hour12: false });
      g.appendChild(tx);
    }
    // 値の無い点（GPU が読めなかった回）で線を切る。間を直線で埋めない
    let seg = [];
    const segs = [];
    pts.forEach((p, i) => {
      if (vals[i] == null) { if (seg.length) segs.push(seg); seg = []; }
      else seg.push([x(p.ts), y(vals[i])]);
    });
    if (seg.length) segs.push(seg);
    segs.forEach(sg => {
      const d = sg.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join(' ');
      g.appendChild(svg('path', { d: `${d} L${sg[sg.length - 1][0].toFixed(1)} ${y(0)} L${sg[0][0].toFixed(1)} ${y(0)} Z` }, 'area'));
      g.appendChild(svg('path', { d }, 'line'));
    });
    const cross = svg('line', { x1: 0, x2: 0, y1: T, y2: H - B }, 'cross');
    const dot = svg('circle', { cx: 0, cy: 0, r: 4 }, 'dot');
    g.appendChild(cross);
    g.appendChild(dot);

    // 線に当てなくてよい。いちばん近い時刻を拾う
    g.addEventListener('pointermove', ev => {
      if (!pts.length) return;
      const box = g.getBoundingClientRect();
      const ts = t0 + span * ((ev.clientX - box.left) * W / box.width - L) / (W - L - R);
      let best = Infinity, bi = -1;
      pts.forEach((p, i) => { const d = Math.abs(p.ts - ts); if (d < best) { best = d; bi = i; } });
      setHover(bi);
    });
    g.addEventListener('pointerleave', () => { setHover(-1); if (pending) { const p = pending; pending = null; draw(p); } });
    g.addEventListener('keydown', ev => {
      if (!['ArrowLeft', 'ArrowRight', 'Escape'].includes(ev.key) || !pts.length) return;
      ev.preventDefault();
      if (ev.key === 'Escape') return setHover(-1);
      const cur = hover >= 0 ? hover : pts.length - 1;
      setHover(Math.max(0, Math.min(pts.length - 1, cur + (ev.key === 'ArrowLeft' ? -1 : 1))));
    });
    g.addEventListener('blur', () => setHover(-1));
    card.appendChild(g);
    grid.appendChild(card);
    charts.push({ s, x, y, vEl, tEl, cross, dot });
  });
  out.push(grid);

  const d = $('details');
  d.dataset.k = 'table';
  d.appendChild($('summary', '', '表で見る（最新・最小・平均・最大）'));
  const tb = $('table'), hr = $('tr');
  ['系列', '単位', '最新', '最小', '平均', '最大', '点'].forEach((t, i) => hr.appendChild($('th', i > 1 ? 'num' : '', t)));
  tb.appendChild(hr);
  h.series.forEach(s => {
    const v = pts.map(p => p[s.key]).filter(q => q != null);
    const tr = $('tr');
    const last = pts.length ? pts[pts.length - 1][s.key] : null;
    [s.label, s.unit, num(last, 1), v.length ? num(Math.min(...v), 1) : '—',
     v.length ? num(v.reduce((a, b) => a + b, 0) / v.length, 1) : '—',
     v.length ? num(Math.max(...v), 1) : '—', num(v.length)]
      .forEach((c, i) => tr.appendChild($('td', i > 1 ? 'num' : '', c)));
    tb.appendChild(tr);
  });
  d.appendChild(tb);
  out.push(d);

  root.replaceChildren(...out);
  root.querySelectorAll('details').forEach(e => { if (open.has(e.dataset.k)) e.open = true; });
  setHover(-1);
}
// 時刻を選んでいる間に届いた分は、離したときに描く（選んでいる点が足元で動かないように）
let pending = null;
const first = JSON.parse(document.getElementById('hw-data').textContent);
draw(first);
poll('api/history', first.interval_s || 5, h => { if (hover < 0) draw(h); else pending = h; }, stale => {
  const m = document.getElementById('meta');
  if (m) m.classList.toggle('stale', stale);
});
"""


def embed_json(obj) -> str:
    """`<script type="application/json">` に入れる形。⚠ `<` を逃がして `</script>` で切られないようにする。"""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


def _body(payload, script: str) -> str:
    return (f'<div id="hw" data-disk-warn="{DISK_WARN_PCT}"></div>\n'
            '<noscript><p class="warn">この画面は script で描く。JavaScript を有効にする。</p></noscript>\n'
            f'<script type="application/json" id="hw-data">{embed_json(payload)}</script>\n'
            f"<script>(function () {{{script}}})();</script>")


def now_body(snapshot: dict) -> str:
    return _body(snapshot, NOW_JS)


def history_body(history: dict) -> str:
    return _body(history, HISTORY_JS)
