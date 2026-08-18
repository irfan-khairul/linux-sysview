// sysview/static/app.js
'use strict';

var VIEWS = ['resources', 'processes', 'docker', 'files'];
var ROUTES = {
  '#/resources': 'resources',
  '#/processes': 'processes',
  '#/docker': 'docker',
  '#/files': 'files'
};

var state = {
  view: 'resources',
  interval: 2,
  timer: null,
  filesPath: '/',
  filesParent: null,
  procSort: { key: 'cpu_percent', desc: true },
  procFilter: ''
};

// ---- helpers ----------------------------------------------------------

function el(id) { return document.getElementById(id); }

function bytes(n) {
  if (n === null || n === undefined) { return '-'; }
  var units = ['B', 'K', 'M', 'G', 'T', 'P'];
  var i = 0;
  var v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return (i === 0 ? v : v.toFixed(1)) + units[i];
}

function rate(n) { return bytes(n) + '/s'; }

function duration(seconds) {
  var s = Math.floor(seconds);
  var d = Math.floor(s / 86400);
  var h = Math.floor((s % 86400) / 3600);
  var m = Math.floor((s % 3600) / 60);
  if (d > 0) { return d + 'd ' + h + 'h ' + m + 'm'; }
  if (h > 0) { return h + 'h ' + m + 'm'; }
  return m + 'm';
}

function stamp(mtime) {
  if (!mtime) { return '-'; }
  var d = new Date(mtime * 1000);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}

function severity(pct) {
  if (pct >= 90) { return ' crit'; }
  if (pct >= 70) { return ' warn'; }
  return '';
}

function bar(pct) {
  var p = Math.max(0, Math.min(100, pct || 0));
  return '<div class="bar' + severity(p) + '"><span style="width:' + p + '%"></span></div>';
}

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setStatus(text) { el('status').textContent = text; }

function get(url) {
  return fetch(url, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) { throw new Error('HTTP ' + r.status); }
    return r.json();
  });
}

// ---- views -----------------------------------------------------------

function renderResources(d) {
  var cards = [];

  var cores = (d.cpu.per_core || []).map(function (v, i) {
    return '<div class="core">cpu' + i + ' ' + v.toFixed(0) + '%' + bar(v) + '</div>';
  }).join('');
  cards.push(
    '<div class="card"><h2>CPU</h2>' +
    '<div class="metric"><span class="label">Total (' + d.cpu.count + ' cores)</span><span>' +
    d.cpu.percent.toFixed(1) + '%</span></div>' + bar(d.cpu.percent) +
    '<div class="cores">' + cores + '</div></div>'
  );

  cards.push(
    '<div class="card"><h2>Memory</h2>' +
    '<div class="metric"><span class="label">Used</span><span>' +
    bytes(d.memory.used) + ' / ' + bytes(d.memory.total) + '</span></div>' +
    bar(d.memory.percent) +
    '<div class="metric"><span class="label">Available</span><span>' +
    bytes(d.memory.available) + '</span></div>' +
    '<div class="metric"><span class="label">Swap</span><span>' +
    bytes(d.swap.used) + ' / ' + bytes(d.swap.total) + '</span></div>' +
    bar(d.swap.percent) + '</div>'
  );

  var la = d.load_average;
  cards.push(
    '<div class="card"><h2>System</h2>' +
    '<div class="metric"><span class="label">Uptime</span><span>' +
    duration(d.uptime_seconds) + '</span></div>' +
    '<div class="metric"><span class="label">Load avg</span><span>' +
    la[0] + ' ' + la[1] + ' ' + la[2] + '</span></div>' +
    '<div class="metric"><span class="label">Disk read</span><span>' +
    rate(d.disk_io.read_rate) + '</span></div>' +
    '<div class="metric"><span class="label">Disk write</span><span>' +
    rate(d.disk_io.write_rate) + '</span></div></div>'
  );

  var disks = (d.disks || []).map(function (k) {
    return '<div class="metric"><span class="label">' + esc(k.mountpoint) +
      ' <small>' + esc(k.fstype) + '</small></span><span>' +
      bytes(k.used) + ' / ' + bytes(k.total) + '</span></div>' + bar(k.percent);
  }).join('') || '<div class="empty">No disks reported</div>';
  cards.push('<div class="card"><h2>Disks</h2>' + disks + '</div>');

  var nets = Object.keys(d.network || {}).sort().map(function (name) {
    var n = d.network[name];
    return '<div class="metric"><span class="label">' + esc(name) + '</span><span>&uarr; ' +
      rate(n.sent_rate) + ' &darr; ' + rate(n.recv_rate) + '</span></div>';
  }).join('') || '<div class="empty">No interfaces</div>';
  cards.push('<div class="card"><h2>Network</h2>' + nets + '</div>');

  el('view-resources').innerHTML = '<div class="cards">' + cards.join('') + '</div>';
}

var PROC_COLS = [
  { key: 'pid', label: 'PID', num: true },
  { key: 'user', label: 'User' },
  { key: 'cpu_percent', label: 'CPU%', num: true },
  { key: 'memory_percent', label: 'MEM%', num: true },
  { key: 'rss', label: 'RSS', num: true, fmt: bytes },
  { key: 'status', label: 'State' },
  { key: 'name', label: 'Name' },
  { key: 'cmdline', label: 'Command', cls: 'cmd' }
];

function renderProcesses(d) {
  var rows = d.processes || [];
  var q = state.procFilter.trim().toLowerCase();
  if (q) {
    rows = rows.filter(function (p) {
      return String(p.pid).indexOf(q) === 0 ||
        (p.name || '').toLowerCase().indexOf(q) !== -1;
    });
  }

  var s = state.procSort;
  rows = rows.slice().sort(function (a, b) {
    var x = a[s.key];
    var y = b[s.key];
    if (typeof x === 'string' || typeof y === 'string') {
      x = String(x).toLowerCase();
      y = String(y).toLowerCase();
    }
    if (x < y) { return s.desc ? 1 : -1; }
    if (x > y) { return s.desc ? -1 : 1; }
    return 0;
  });

  var head = PROC_COLS.map(function (c) {
    var mark = s.key === c.key ? (s.desc ? ' ▾' : ' ▴') : '';
    return '<th data-key="' + c.key + '"' + (c.num ? ' class="num"' : '') + '>' +
      c.label + mark + '</th>';
  }).join('');

  var body = rows.map(function (p) {
    return '<tr>' + PROC_COLS.map(function (c) {
      var v = c.fmt ? c.fmt(p[c.key]) : p[c.key];
      var cls = c.cls || (c.num ? 'num' : '');
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + esc(v) + '</td>';
    }).join('') + '</tr>';
  }).join('');

  el('proc-count').textContent = rows.length + ' shown of ' + d.total + ' total';
  el('proc-table').innerHTML = '<thead><tr>' + head + '</tr></thead><tbody>' +
    (body || '<tr><td colspan="8" class="empty">No processes match</td></tr>') +
    '</tbody>';
}

function renderDocker(d) {
  if (!d.available) {
    el('docker-table').innerHTML =
      '<tbody><tr><td class="empty">Docker not available &mdash; ' +
      esc(d.error) + '</td></tr></tbody>';
    return;
  }
  if (!d.containers.length) {
    el('docker-table').innerHTML =
      '<tbody><tr><td class="empty">No containers</td></tr></tbody>';
    return;
  }

  var head = '<thead><tr><th>Name</th><th>Image</th><th>State</th><th>Status</th>' +
    '<th class="num">CPU</th><th class="num">Memory</th><th>Ports</th><th>Actions</th></tr></thead>';

  var body = d.containers.map(function (c) {
    var running = c.state === 'running';
    var btn = function (action, label) {
      return '<button type="button" data-id="' + esc(c.id) + '" data-action="' +
        action + '">' + label + '</button>';
    };
    var actions = running
      ? btn('stop', 'Stop') + ' ' + btn('restart', 'Restart')
      : btn('start', 'Start');
    return '<tr><td>' + esc(c.name) + '</td><td>' + esc(c.image) + '</td><td>' +
      esc(c.state) + '</td><td>' + esc(c.status) + '</td><td class="num">' +
      esc(c.cpu_percent) + '</td><td class="num">' + esc(c.memory) + '</td><td>' +
      esc(c.ports) + '</td><td>' + actions + '</td></tr>';
  }).join('');

  el('docker-table').innerHTML = head + '<tbody>' + body + '</tbody>';
}

function renderFiles(d) {
  el('files-error').textContent = d.error || '';
  if (d.error) { return; }

  state.filesPath = d.path;
  state.filesParent = d.parent;
  el('files-path').textContent = d.path;
  el('files-back').disabled = !d.parent;

  var head = '<thead><tr><th>Name</th><th class="num">Size</th>' +
    '<th>Modified</th><th>Mode</th></tr></thead>';

  var body = (d.entries || []).map(function (e) {
    var cls = e.is_dir ? 'dir' : 'file';
    var name = (e.is_dir ? '▸ ' : '  ') + esc(e.name);
    return '<tr class="' + cls + '" data-name="' + esc(e.name) +
      '" data-dir="' + (e.is_dir ? '1' : '0') + '"><td>' + name +
      '</td><td class="num">' + (e.is_dir ? '-' : bytes(e.size)) +
      '</td><td>' + stamp(e.mtime) + '</td><td>' + esc(e.mode) + '</td></tr>';
  }).join('');

  el('files-table').innerHTML = head + '<tbody>' +
    (body || '<tr><td colspan="4" class="empty">Empty directory</td></tr>') + '</tbody>';
}

// ---- polling ---------------------------------------------------------

var LOADERS = {
  resources: function () { return get('/api/resources').then(renderResources); },
  processes: function () { return get('/api/processes').then(renderProcesses); },
  docker: function () { return get('/api/docker').then(renderDocker); },
  files: function () {
    return get('/api/files?path=' + encodeURIComponent(state.filesPath)).then(renderFiles);
  }
};

function refresh() {
  return LOADERS[state.view]().then(function () {
    setStatus('');
  }).catch(function (err) {
    // Keep the last good values on screen; just flag them as stale.
    setStatus('stale (' + err.message + ')');
  });
}

function schedule() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  if (state.interval > 0) {
    state.timer = setInterval(refresh, state.interval * 1000);
  }
}

// The file explorer is navigated, not monitored; re-polling it every 2s would
// fight the user's clicks for no benefit.
function isPolled(view) { return view !== 'files'; }

function showView(name) {
  state.view = name;
  VIEWS.forEach(function (v) {
    el('view-' + v).classList.toggle('active', v === name);
  });
  var links = document.querySelectorAll('nav a');
  Array.prototype.forEach.call(links, function (a) {
    a.classList.toggle('active', a.getAttribute('data-view') === name);
  });
  setStatus('');
  refresh();
  if (isPolled(name)) { schedule(); }
  else if (state.timer) { clearInterval(state.timer); state.timer = null; }
}

function onHashChange() {
  showView(ROUTES[window.location.hash] || 'resources');
}

// ---- events ----------------------------------------------------------

window.addEventListener('hashchange', onHashChange);

el('interval').addEventListener('change', function (e) {
  state.interval = parseFloat(e.target.value);
  if (isPolled(state.view)) { schedule(); }
});

el('proc-filter').addEventListener('input', function (e) {
  state.procFilter = e.target.value;
  refresh();
});

el('proc-table').addEventListener('click', function (e) {
  var th = e.target.closest('th');
  if (!th || !th.dataset.key) { return; }
  var key = th.dataset.key;
  if (state.procSort.key === key) { state.procSort.desc = !state.procSort.desc; }
  else { state.procSort = { key: key, desc: true }; }
  refresh();
});

el('docker-table').addEventListener('click', function (e) {
  var btn = e.target.closest('button');
  if (!btn) { return; }
  var buttons = el('docker-table').querySelectorAll('button');
  Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
  setStatus(btn.dataset.action + 'ing...');
  fetch('/api/docker/' + encodeURIComponent(btn.dataset.id) + '/' + btn.dataset.action,
        { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      setStatus(res.ok ? '' : res.error);
      return refresh();
    })
    .catch(function (err) { setStatus(err.message); })
    .then(function () {
      var again = el('docker-table').querySelectorAll('button');
      Array.prototype.forEach.call(again, function (b) { b.disabled = false; });
    });
});

el('files-table').addEventListener('dblclick', function (e) {
  var tr = e.target.closest('tr');
  if (!tr || tr.dataset.dir !== '1') { return; }
  var base = state.filesPath === '/' ? '' : state.filesPath;
  state.filesPath = base + '/' + tr.dataset.name;
  refresh();
});

el('files-back').addEventListener('click', function () {
  if (!state.filesParent) { return; }
  state.filesPath = state.filesParent;
  refresh();
});

onHashChange();
