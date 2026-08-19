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
  procFilter: '',
  history: [],
  netOpen: false,
  dockerOpen: {}
};

// ---- helpers ----------------------------------------------------------

function el(id) { return document.getElementById(id); }

function bytes(n) {
  if (n === null || n === undefined) { return '-'; }
  var units = ['B', 'K', 'M', 'G', 'T', 'P'];
  var i = 0;
  var v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  // Raw byte counts are whole numbers; scaled units keep one decimal. Without
  // rounding here a rate like 359.51867316465695 would print in full.
  return (i === 0 ? Math.round(v) : v.toFixed(1)) + units[i];
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

// A sparkline is just a polyline over a normalised series. Scaling to the
// series' own max (with a floor) keeps a quiet line flat instead of amplifying
// noise into a dramatic-looking graph.
function sparkline(values, opts) {
  var o = opts || {};
  if (!values || values.length < 2) { return ''; }
  var w = o.width || 160;
  var h = o.height || 28;
  var max = o.max;
  if (max === undefined) {
    max = Math.max.apply(null, values);
    if (!isFinite(max) || max <= 0) { max = 1; }
  }
  var step = w / (values.length - 1);
  var points = values.map(function (v, i) {
    var y = h - Math.max(0, Math.min(1, v / max)) * (h - 2) - 1;
    return (i * step).toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');
  return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '" ' +
    'preserveAspectRatio="none" aria-hidden="true">' +
    '<polyline points="' + points + '"/></svg>';
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
  var hist = state.history;
  cards.push(
    '<div class="card"><h2>CPU</h2>' +
    '<div class="metric"><span class="label">Total (' + d.cpu.count + ' cores)</span><span>' +
    d.cpu.percent.toFixed(1) + '%</span></div>' + bar(d.cpu.percent) +
    // Percentages get a fixed 0-100 scale so the line height always means the
    // same thing between refreshes.
    sparkline(hist.map(function (h) { return h.cpu; }), { max: 100 }) +
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
    bar(d.swap.percent) +
    sparkline(hist.map(function (h) { return h.mem; }), { max: 100 }) + '</div>'
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

  // A busy Docker host has dozens of veth interfaces that are almost always
  // idle, so lead with the graph and the totals and put the per-interface
  // breakdown behind a toggle.
  var ifaces = Object.keys(d.network || {}).sort();
  var active = ifaces.filter(function (name) {
    var n = d.network[name];
    return n.sent_rate > 0 || n.recv_rate > 0;
  });

  var totalSent = ifaces.reduce(function (a, n) { return a + d.network[n].sent_rate; }, 0);
  var totalRecv = ifaces.reduce(function (a, n) { return a + d.network[n].recv_rate; }, 0);

  var recv = hist.map(function (h) { return h.net_recv; });
  var sent = hist.map(function (h) { return h.net_sent; });
  var peak = Math.max(1, Math.max.apply(null, recv.concat(sent).concat([0])));

  var rows = ifaces.map(function (name) {
    var n = d.network[name];
    var idle = n.sent_rate === 0 && n.recv_rate === 0;
    return '<div class="metric' + (idle ? ' idle' : '') + '">' +
      '<span class="label">' + esc(name) + '</span><span>&uarr; ' +
      rate(n.sent_rate) + ' &darr; ' + rate(n.recv_rate) + '</span></div>';
  }).join('') || '<div class="empty">No interfaces</div>';

  var netCard =
    '<div class="metric"><span class="label">&darr; Down</span><span>' +
    rate(totalRecv) + '</span></div>' +
    (hist.length > 1 ? sparkline(recv, { max: peak }) : '') +
    '<div class="metric"><span class="label">&uarr; Up</span><span>' +
    rate(totalSent) + '</span></div>' +
    (hist.length > 1 ? sparkline(sent, { max: peak }) : '') +
    (hist.length > 1 ? '<div class="spark-label">peak ' + rate(peak) + '</div>' : '') +
    '<details class="iface-details"' + (state.netOpen ? ' open' : '') + '>' +
    '<summary>' + ifaces.length + ' interfaces, ' + active.length +
    ' active</summary>' + rows + '</details>';

  cards.push('<div class="card"><h2>Network</h2>' + netCard + '</div>');

  el('view-resources').innerHTML = '<div class="cards">' + cards.join('') + '</div>';
}

var PROC_COLS = [
  { key: 'pid', label: 'PID', num: true },
  { key: 'user', label: 'User' },
  { key: 'cpu_percent', label: 'CPU%', num: true },
  { key: 'memory_percent', label: 'MEM%', num: true },
  { key: 'rss', label: 'RSS', num: true, fmt: bytes },
  { key: 'threads', label: 'Thr', num: true },
  { key: 'status', label: 'State' },
  { key: 'name', label: 'Name' },
  { key: 'cmdline', label: 'Command', cls: 'cmd' }
];

function renderProcesses(d) {
  // Sorting and filtering are done by the server across ALL processes; doing
  // them here would only ever see the truncated slice, so sorting by memory
  // would show the largest memory user among the top CPU consumers rather
  // than the largest overall.
  var rows = d.processes || [];
  var s = state.procSort;

  var head = PROC_COLS.map(function (c) {
    var mark = s.key === c.key ? (s.desc ? ' \u25be' : ' \u25b4') : '';
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

  var matched = d.matched === undefined ? rows.length : d.matched;
  var label = rows.length + ' shown of ' + matched;
  if (matched !== d.total) { label += ' matching (' + d.total + ' total)'; }
  else { label += ' total'; }
  el('proc-count').textContent = label;

  el('proc-table').innerHTML = '<thead><tr>' + head + '</tr></thead><tbody>' +
    (body || '<tr><td colspan="' + PROC_COLS.length +
      '" class="empty">No processes match</td></tr>') +
    '</tbody>';
}

// Compose stamps a project label on every container it creates, so a stack
// like supabase (a dozen containers) collapses into one row you can act on as
// a unit — the same grouping Docker Desktop shows.
function dockerGroups(containers) {
  var order = [];
  var byProject = {};
  containers.forEach(function (c) {
    var key = c.project || '';
    if (!byProject[key]) {
      byProject[key] = [];
      order.push(key);
    }
    byProject[key].push(c);
  });
  // Real projects first, then loose `docker run` containers.
  order.sort(function (a, b) {
    if (!a !== !b) { return a ? -1 : 1; }
    return a.localeCompare(b);
  });
  return order.map(function (key) {
    return { project: key, containers: byProject[key] };
  });
}

function sumPercent(containers) {
  var total = containers.reduce(function (acc, c) {
    var v = parseFloat(String(c.cpu_percent).replace('%', ''));
    return acc + (isFinite(v) ? v : 0);
  }, 0);
  return total.toFixed(2) + '%';
}

function containerRow(c) {
  var running = c.state === 'running';
  var btn = function (action, label) {
    return '<button type="button" data-id="' + esc(c.id) + '" data-action="' +
      action + '">' + label + '</button>';
  };
  var actions = running
    ? btn('stop', 'Stop') + ' ' + btn('restart', 'Restart')
    : btn('start', 'Start');
  // Within a group the service name is the useful label; the container name
  // just repeats the project prefix.
  var label = c.service || c.name;
  return '<tr class="' + (running ? '' : 'stopped') + '">' +
    '<td class="c-name">' + esc(label) + '</td>' +
    '<td>' + esc(c.image) + '</td>' +
    '<td>' + esc(c.state) + '</td>' +
    '<td>' + esc(c.status) + '</td>' +
    '<td class="num">' + esc(c.cpu_percent) + '</td>' +
    '<td class="num">' + esc(c.memory) + '</td>' +
    '<td>' + esc(c.ports) + '</td>' +
    '<td>' + actions + '</td></tr>';
}

function groupBlock(g) {
  var running = g.containers.filter(function (c) { return c.state === 'running'; });
  var name = g.project || 'Ungrouped';
  var ids = g.containers.map(function (c) { return c.id; }).join(' ');
  var open = state.dockerOpen[name] ? ' open' : '';

  var groupBtn = function (action, label) {
    return '<button type="button" class="group-btn" data-group-ids="' + esc(ids) +
      '" data-action="' + action + '">' + label + '</button>';
  };
  // A group is actionable as a whole: start what is stopped, stop what runs.
  var actions = g.project
    ? (running.length ? groupBtn('stop', 'Stop all') + ' ' + groupBtn('restart', 'Restart all') : '') +
      (running.length < g.containers.length ? ' ' + groupBtn('start', 'Start all') : '')
    : '';

  var rows = g.containers.map(containerRow).join('');

  return '<details class="dgroup"' + open + ' data-group="' + esc(name) + '">' +
    '<summary>' +
      '<span class="dgroup-name">' + esc(name) + '</span>' +
      '<span class="dgroup-meta">' + running.length + ' / ' + g.containers.length +
        ' running &middot; ' + sumPercent(running) + ' CPU</span>' +
      '<span class="dgroup-actions">' + actions + '</span>' +
    '</summary>' +
    '<div class="table-wrap"><table><thead><tr>' +
      '<th>Service</th><th>Image</th><th>State</th><th>Status</th>' +
      '<th class="num">CPU</th><th class="num">Memory</th><th>Ports</th><th>Actions</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '</details>';
}

function renderDocker(d) {
  var host = el('docker-view');
  if (!d.available) {
    host.innerHTML = '<div class="empty">Docker not available &mdash; ' +
      esc(d.error) + '</div>';
    return;
  }
  if (!d.containers.length) {
    host.innerHTML = '<div class="empty">No containers. Note that ' +
      '<code>docker compose down</code> removes containers entirely, so a ' +
      'project torn down that way has nothing left to show.</div>';
    return;
  }
  host.innerHTML = dockerGroups(d.containers).map(groupBlock).join('');
}

function renderFiles(d) {
  el('files-error').textContent = d.error || '';
  if (d.error) { return; }

  state.filesPath = d.path;
  state.filesParent = d.parent;
  // Do not clobber the path while the user is typing into it.
  if (document.activeElement !== el('files-path')) {
    el('files-path').value = d.path;
  }
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
  resources: function () {
    // History and current values are fetched together so the sparklines and
    // the numbers beside them always describe the same moment.
    return Promise.all([get('/api/resources'), get('/api/history')])
      .then(function (both) {
        state.history = (both[1] && both[1].points) || [];
        renderResources(both[0]);
      });
  },
  processes: function () {
    var s = state.procSort;
    var url = '/api/processes?sort=' + encodeURIComponent(s.key) +
      '&desc=' + (s.desc ? '1' : '0') +
      '&q=' + encodeURIComponent(state.procFilter.trim());
    return get(url).then(renderProcesses);
  },
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

// ---- startup config ----------------------------------------------------

// Picks the <select> option numerically closest to a requested interval, so
// a --interval value that isn't one of the fixed dropdown choices still ends
// up on a sane, visible, selected option instead of an empty dropdown.
function closestIntervalOption(value) {
  var options = Array.prototype.map.call(
    el('interval').options, function (o) { return parseFloat(o.value); }
  );
  var best = options[0];
  options.forEach(function (o) {
    if (Math.abs(o - value) < Math.abs(best - value)) { best = o; }
  });
  return best;
}

function applyInterval(value) {
  var chosen = closestIntervalOption(value);
  state.interval = chosen;
  el('interval').value = String(chosen);
}

function loadConfig() {
  return get('/api/config').then(function (cfg) {
    applyInterval(cfg.interval);
  }).catch(function () {
    // Config fetch failed for any reason: fall back to the hardcoded default
    // rather than leaving the page unpolled.
    applyInterval(state.interval);
  });
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

// The card is re-rendered on every poll, so remember whether the interface
// list was expanded or it would slam shut a moment after being opened.
el('view-resources').addEventListener('toggle', function (e) {
  if (e.target.classList.contains('iface-details')) {
    state.netOpen = e.target.open;
  }
}, true);

el('proc-table').addEventListener('click', function (e) {
  var th = e.target.closest('th');
  if (!th || !th.dataset.key) { return; }
  var key = th.dataset.key;
  if (state.procSort.key === key) { state.procSort.desc = !state.procSort.desc; }
  else { state.procSort = { key: key, desc: true }; }
  refresh();
});

el('docker-view').addEventListener('click', function (e) {
  var btn = e.target.closest('button');
  if (!btn) { return; }
  // Buttons live inside <summary>; without this the click would also toggle
  // the group open or shut.
  e.preventDefault();
  e.stopPropagation();

  var host = el('docker-view');
  var buttons = host.querySelectorAll('button');
  Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });

  var group = btn.dataset.groupIds;
  var request;
  if (group) {
    var ids = group.split(' ').filter(Boolean);
    setStatus(btn.dataset.action + 'ing ' + ids.length + ' containers...');
    request = fetch('/api/docker/group/' + btn.dataset.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: ids })
    });
  } else {
    setStatus(btn.dataset.action + 'ing...');
    request = fetch('/api/docker/' + encodeURIComponent(btn.dataset.id) + '/' +
      btn.dataset.action, { method: 'POST' });
  }

  request
    .then(function (r) { return r.json(); })
    .then(function (res) {
      setStatus(res.ok ? '' : res.error);
      return refresh();
    })
    .catch(function (err) { setStatus(err.message); })
    .then(function () {
      var again = el('docker-view').querySelectorAll('button');
      Array.prototype.forEach.call(again, function (b) { b.disabled = false; });
    });
});

// Remember which groups are expanded, or every poll would collapse them.
el('docker-view').addEventListener('toggle', function (e) {
  if (e.target.classList.contains('dgroup')) {
    state.dockerOpen[e.target.dataset.group] = e.target.open;
  }
}, true);

el('files-table').addEventListener('click', function (e) {
  var tr = e.target.closest('tr');
  if (!tr || tr.dataset.dir !== '1') { return; }
  var base = state.filesPath === '/' ? '' : state.filesPath;
  state.filesPath = base + '/' + tr.dataset.name;
  refresh();
});

el('files-path').addEventListener('keydown', function (e) {
  if (e.key !== 'Enter') { return; }
  var typed = e.target.value.trim();
  if (!typed) { return; }
  state.filesPath = typed;
  e.target.blur();
  refresh();
});

el('files-back').addEventListener('click', function () {
  if (!state.filesParent) { return; }
  state.filesPath = state.filesParent;
  refresh();
});

loadConfig().then(onHashChange);
