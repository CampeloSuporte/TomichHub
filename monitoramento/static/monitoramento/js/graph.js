'use strict';

/**
 * GRAPH.js — Monitor de Tráfego Zabbix
 * Visual rico: sparklines, status badges, tooltips, fullscreen, tema escuro.
 */
const GRAPH = (() => {

  // ── Estado ────────────────────────────────────────────────
  let clienteId     = null;
  let charts        = [];
  let nextChartId   = 1;
  let ifacesCache   = {};
  let _hostTimer    = null;
  let _editingChart = null;
  let _fullscreenId = null;

  // ── Paleta ────────────────────────────────────────────────
  const C = {
    in:      '#00ff88',
    out:     '#00d9ff',
    inFill:  'rgba(0,255,136,0.08)',
    outFill: 'rgba(0,217,255,0.08)',
    grid:    'rgba(255,255,255,0.04)',
    text:    '#475569',
    border:  '#1e293b',
    card:    '#111827',
    cardHov: '#131f30',
  };

  const URLS = {
    zbxHosts:  '/monitoramento/zabbix/hosts/',
    zbxIfaces: '/monitoramento/zabbix/interfaces/',
    history:   '/monitoramento/zabbix/history/',
  };

  async function _get(url) {
    const r = await fetch(url);
    return r.json();
  }

  // ─────────────────────────────────────────────────────────
  // INIT
  // ─────────────────────────────────────────────────────────
  function init() {
    clienteId = window.CLIENTE_ID || null;
    _renderGrid();
    _bindKeyboard();
  }

  function _bindKeyboard() {
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && _fullscreenId !== null) fecharFullscreen();
    });
  }

  // ─────────────────────────────────────────────────────────
  // GRID
  // ─────────────────────────────────────────────────────────
  function _renderGrid() {
    const grid = document.getElementById('grph-grid');
    if (!grid) return;
    grid.innerHTML = '';

    if (charts.length === 0) {
      grid.innerHTML = _emptyState();
      return;
    }

    charts.forEach(c => {
      const card = document.createElement('div');
      card.className = 'grph-card';
      card.id = `grph-card-${c.id}`;
      card.innerHTML = _cardHTML(c);
      grid.appendChild(card);
      _initChart(c);
    });
  }

  function _emptyState() {
    return `
      <div class="grph-empty">
        <div class="grph-empty-icon">
          <svg viewBox="0 0 120 80" width="120" height="80">
            <defs>
              <linearGradient id="emptyGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stop-color="#00ff88" stop-opacity="0.4"/>
                <stop offset="100%" stop-color="#00d9ff" stop-opacity="0.4"/>
              </linearGradient>
            </defs>
            <rect x="4"  y="50" width="16" height="26" rx="3" fill="url(#emptyGrad)" opacity=".5"/>
            <rect x="26" y="32" width="16" height="44" rx="3" fill="url(#emptyGrad)" opacity=".6"/>
            <rect x="48" y="40" width="16" height="36" rx="3" fill="url(#emptyGrad)" opacity=".7"/>
            <rect x="70" y="20" width="16" height="56" rx="3" fill="url(#emptyGrad)" opacity=".8"/>
            <rect x="92" y="10" width="16" height="66" rx="3" fill="url(#emptyGrad)"/>
            <polyline points="12,45 34,28 56,36 78,16 100,6"
                      stroke="#3b82f6" stroke-width="2" fill="none"
                      stroke-dasharray="4,3" stroke-linecap="round"/>
          </svg>
        </div>
        <h3 class="grph-empty-title">Nenhum gráfico configurado</h3>
        <p class="grph-empty-sub">Adicione interfaces Zabbix para monitorar tráfego em tempo real.</p>
        <button class="grph-btn grph-btn-primary" onclick="GRAPH_CALL('abrirModalAdd')">
          <svg viewBox="0 0 24 24" width="14" height="14"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
          Adicionar Monitor
        </button>
      </div>`;
  }

  function _cardHTML(c) {
    return `
      <div class="grph-card-glow"></div>
      <div class="grph-card-header">
        <div class="grph-card-info">
          <div class="grph-card-title-row">
            <span class="grph-poll-dot" id="grph-dot-${c.id}"></span>
            <span class="grph-host">${c.hostname || '—'}</span>
          </div>
          <span class="grph-iface">
            <svg viewBox="0 0 24 24" width="10" height="10" style="margin-right:3px;opacity:.5">
              <path d="M5 12h14M12 5l7 7-7 7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>
            </svg>
            ${c.ifaceName || '—'}
          </span>
        </div>
        <div class="grph-card-acts">
          <button class="grph-icon-btn" onclick="GRAPH.toggleFullscreen(${c.id})" title="Expandir">
            <svg viewBox="0 0 24 24" width="13" height="13">
              <path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"
                    stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="grph-icon-btn" onclick="GRAPH.abrirModalEdit(${c.id})" title="Editar">
            <svg viewBox="0 0 24 24" width="13" height="13">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"
                    stroke="currentColor" stroke-width="2" fill="none"/>
              <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"
                    stroke="currentColor" stroke-width="2" fill="none"/>
            </svg>
          </button>
          <button class="grph-icon-btn grph-danger" onclick="GRAPH.removerChart(${c.id})" title="Remover">
            <svg viewBox="0 0 24 24" width="13" height="13">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
      </div>

      <div class="grph-stats-row">
        <div class="grph-stat-block grph-stat-in">
          <span class="grph-stat-arrow">↓</span>
          <div>
            <div class="grph-stat-val" id="grph-in-${c.id}">—</div>
            <div class="grph-stat-label">Download</div>
          </div>
        </div>
        <div class="grph-stat-divider"></div>
        <div class="grph-stat-block grph-stat-out">
          <span class="grph-stat-arrow">↑</span>
          <div>
            <div class="grph-stat-val" id="grph-out-${c.id}">—</div>
            <div class="grph-stat-label">Upload</div>
          </div>
        </div>
        <div class="grph-stat-divider"></div>
        <div class="grph-stat-block grph-stat-peak">
          <svg viewBox="0 0 24 24" width="12" height="12" style="opacity:.5"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
          <div>
            <div class="grph-stat-val" id="grph-peak-${c.id}">—</div>
            <div class="grph-stat-label">Pico (1h)</div>
          </div>
        </div>
      </div>

      <div class="grph-canvas-wrap">
        <canvas id="grph-cv-${c.id}"></canvas>
      </div>

      <div class="grph-footer">
        <span class="grph-ts" id="grph-ts-${c.id}">Aguardando dados...</span>
        <span class="grph-interval">
          <span class="grph-interval-dot"></span>
          15s
        </span>
      </div>`;
  }

  // ─────────────────────────────────────────────────────────
  // CHART.JS
  // ─────────────────────────────────────────────────────────
  function _chartConfig(mini = false) {
    return {
      type: 'line',
      data: { labels: [], datasets: [
        {
          label: 'Download ↓', data: [],
          borderColor: C.in, backgroundColor: C.inFill,
          borderWidth: mini ? 1.5 : 2, pointRadius: 0,
          fill: true, tension: 0.4,
        },
        {
          label: 'Upload ↑', data: [],
          borderColor: C.out, backgroundColor: C.outFill,
          borderWidth: mini ? 1.5 : 2, pointRadius: 0,
          fill: true, tension: 0.4,
        },
      ]},
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: !mini,
            labels: { color: '#64748b', font: { size: 10 }, boxWidth: 10, padding: 12 },
          },
          tooltip: {
            backgroundColor: '#0f172a',
            borderColor: '#334155',
            borderWidth: 1,
            titleColor: '#e2e8f0',
            bodyColor: '#94a3b8',
            padding: 12,
            cornerRadius: 8,
            callbacks: { label: ctx => `  ${ctx.dataset.label}: ${_fmtBps(ctx.raw)}` },
          },
        },
        scales: {
          x: {
            display: !mini,
            ticks: { color: C.text, font: { size: 9 }, maxRotation: 0, maxTicksLimit: 8 },
            grid: { color: C.grid },
            border: { color: 'transparent' },
          },
          y: {
            min: 0,
            display: !mini,
            ticks: { color: C.text, font: { size: 9 }, callback: v => _fmtBps(v) },
            grid: { color: C.grid },
            border: { color: 'transparent' },
          },
        },
      },
    };
  }

  function _initChart(c) {
    const canvas = document.getElementById(`grph-cv-${c.id}`);
    if (!canvas) return;

    if (typeof Chart === 'undefined') {
      canvas.parentElement.innerHTML =
        '<p style="color:#475569;text-align:center;padding:24px;font-size:.78rem;">Chart.js não encontrado.</p>';
      return;
    }

    c.chartInstance = new Chart(canvas, _chartConfig(false));
    _startPolling(c);
  }

  // ─────────────────────────────────────────────────────────
  // FULLSCREEN
  // ─────────────────────────────────────────────────────────
  function toggleFullscreen(id) {
    if (_fullscreenId === id) { fecharFullscreen(); return; }
    _fullscreenId = id;
    const c = charts.find(x => x.id === id);
    if (!c) return;

    let overlay = document.getElementById('grph-fullscreen-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'grph-fullscreen-overlay';
      overlay.className = 'grph-fullscreen-overlay';
      overlay.onclick = e => { if (e.target === overlay) fecharFullscreen(); };
      document.body.appendChild(overlay);
    }

    overlay.innerHTML = `
      <div class="grph-fullscreen-modal">
        <div class="grph-fullscreen-header">
          <div>
            <h2 class="grph-fullscreen-title">${c.hostname || '—'}</h2>
            <p class="grph-fullscreen-sub">${c.ifaceName || '—'}</p>
          </div>
          <div class="grph-fullscreen-actions">
            <div class="grph-fs-stats">
              <span class="grph-fs-stat-in" id="grph-fs-in">↓ —</span>
              <span class="grph-fs-stat-out" id="grph-fs-out">↑ —</span>
            </div>
            <button class="grph-icon-btn" onclick="GRAPH.fecharFullscreen()" title="Fechar">
              <svg viewBox="0 0 24 24" width="16" height="16">
                <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </div>
        <div class="grph-fullscreen-canvas-wrap">
          <canvas id="grph-fs-canvas"></canvas>
        </div>
        <div class="grph-fullscreen-footer" id="grph-fs-footer">
          <span id="grph-fs-ts">—</span>
        </div>
      </div>`;

    overlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    if (typeof Chart !== 'undefined') {
      const fsCanvas = document.getElementById('grph-fs-canvas');
      c.fsChartInstance = new Chart(fsCanvas, _chartConfig(false));

      // Copiar dados existentes
      if (c.chartInstance) {
        c.fsChartInstance.data = JSON.parse(JSON.stringify(c.chartInstance.data));
        c.fsChartInstance.update('none');
      }

      // Sincronizar futuras atualizações via hook
      c._fsSync = true;
    }

    _updateFsStats(c);
  }

  function fecharFullscreen() {
    const c = charts.find(x => x.id === _fullscreenId);
    if (c) {
      c._fsSync = false;
      if (c.fsChartInstance) { c.fsChartInstance.destroy(); c.fsChartInstance = null; }
    }
    const overlay = document.getElementById('grph-fullscreen-overlay');
    if (overlay) overlay.style.display = 'none';
    document.body.style.overflow = '';
    _fullscreenId = null;
  }

  function _updateFsStats(c) {
    const elIn  = document.getElementById('grph-fs-in');
    const elOut = document.getElementById('grph-fs-out');
    const elTs  = document.getElementById('grph-fs-ts');
    const srcIn  = document.getElementById(`grph-in-${c.id}`);
    const srcOut = document.getElementById(`grph-out-${c.id}`);
    const srcTs  = document.getElementById(`grph-ts-${c.id}`);
    if (elIn  && srcIn)  elIn.textContent  = srcIn.textContent;
    if (elOut && srcOut) elOut.textContent = srcOut.textContent;
    if (elTs  && srcTs)  elTs.textContent  = srcTs.textContent;
  }

  // ─────────────────────────────────────────────────────────
  // POLLING
  // ─────────────────────────────────────────────────────────
  function _startPolling(c) {
    if (c._interval) clearInterval(c._interval);
    if (!c.inId && !c.outId) return;
    _fetchHistory(c);
    c._interval = setInterval(() => _fetchHistory(c), 15000);
  }

  function atualizarTodos() { charts.forEach(c => _fetchHistory(c)); }

  async function _fetchHistory(c) {
    const dot = document.getElementById(`grph-dot-${c.id}`);
    if (dot) { dot.classList.add('pulsing'); }

    try {
      const ids     = [c.inId, c.outId].filter(Boolean);
      const results = await Promise.all(
        ids.map(id => _get(`${URLS.history}?cliente_id=${clienteId}&item_id=${id}&limit=60`))
      );
      const histIn  = c.inId  ? (results[0]?.history || []) : [];
      const histOut = c.outId ? (results[c.inId ? 1 : 0]?.history || []) : [];

      const allTs = [...new Set([...histIn.map(p => p.t), ...histOut.map(p => p.t)])].sort((a, b) => a - b);
      if (!allTs.length) return;

      const mapIn   = Object.fromEntries(histIn.map(p  => [p.t, p.v]));
      const mapOut  = Object.fromEntries(histOut.map(p => [p.t, p.v]));
      const labels  = allTs.map(_fmtTime);
      const dataIn  = allTs.map(t => mapIn[t]  !== undefined ? parseFloat(mapIn[t])  : null);
      const dataOut = allTs.map(t => mapOut[t] !== undefined ? parseFloat(mapOut[t]) : null);

      // Atualizar chart principal
      if (c.chartInstance) {
        c.chartInstance.data.labels           = labels;
        c.chartInstance.data.datasets[0].data = dataIn;
        c.chartInstance.data.datasets[1].data = dataOut;
        c.chartInstance.update('none');
      }

      // Sincronizar fullscreen
      if (c._fsSync && c.fsChartInstance) {
        c.fsChartInstance.data.labels           = labels;
        c.fsChartInstance.data.datasets[0].data = dataIn;
        c.fsChartInstance.data.datasets[1].data = dataOut;
        c.fsChartInstance.update('none');
        _updateFsStats(c);
      }

      // Stats
      const lastIn   = dataIn.filter(v => v !== null).at(-1);
      const lastOut  = dataOut.filter(v => v !== null).at(-1);
      const peakIn   = Math.max(...dataIn.filter(v => v !== null), 0);
      const peakOut  = Math.max(...dataOut.filter(v => v !== null), 0);
      const lastTs   = allTs[allTs.length - 1];

      _setText(`grph-in-${c.id}`,   _fmtBps(lastIn));
      _setText(`grph-out-${c.id}`,  _fmtBps(lastOut));
      _setText(`grph-peak-${c.id}`, _fmtBps(Math.max(peakIn, peakOut)));
      _setText(`grph-ts-${c.id}`,   `Atualizado às ${_fmtTime(lastTs)}`);

      // Colorir dot baseado em atividade
      if (dot) {
        dot.classList.toggle('grph-dot-active', (lastIn || 0) > 0 || (lastOut || 0) > 0);
      }

    } catch (e) {
      console.error('[GRAPH] poll error:', e);
    } finally {
      setTimeout(() => {
        const d = document.getElementById(`grph-dot-${c.id}`);
        if (d) d.classList.remove('pulsing');
      }, 700);
    }
  }

  function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  // ─────────────────────────────────────────────────────────
  // MODAIS
  // ─────────────────────────────────────────────────────────
  function abrirModalAdd() {
    _editingChart = null;
    _resetModal();
    document.getElementById('grph-modal-title').textContent = 'Adicionar Monitor';
    document.getElementById('grph-modal').style.display = 'flex';
  }

  function abrirModalEdit(id) {
    const c = charts.find(x => x.id === id);
    if (!c) return;
    _editingChart = id;
    _resetModal();
    document.getElementById('grph-modal-title').textContent = 'Editar Monitor';
    if (c.hostname) {
      document.getElementById('grph-m-search').value   = c.hostname;
      document.getElementById('grph-m-hostid').value   = c.hostid;
      document.getElementById('grph-m-hostname').value = c.hostname;
      const badge = document.getElementById('grph-m-badge');
      badge.textContent = `✓ ${c.hostname}`; badge.style.display = 'block';
      _carregarInterfaces(c.hostid, c.ifaceName);
    }
    document.getElementById('grph-modal').style.display = 'flex';
  }

  function fecharModal() { document.getElementById('grph-modal').style.display = 'none'; }

  function _resetModal() {
    ['grph-m-search','grph-m-hostid','grph-m-hostname'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const badge = document.getElementById('grph-m-badge');
    if (badge) badge.style.display = 'none';
    const results = document.getElementById('grph-m-results');
    if (results) results.style.display = 'none';
    const preview = document.getElementById('grph-m-preview');
    if (preview) preview.style.display = 'none';
    const sel = document.getElementById('grph-m-iface');
    if (sel) {
      sel.innerHTML = '<option value="">— Selecione um host primeiro —</option>';
      sel.disabled  = true;
    }
  }

  // ─────────────────────────────────────────────────────────
  // HOST SEARCH
  // ─────────────────────────────────────────────────────────
  function hostSearchDebounce(q) {
    clearTimeout(_hostTimer);
    _hostTimer = setTimeout(() => _buscarHosts(q), 300);
  }

  async function _buscarHosts(q) {
    const box = document.getElementById('grph-m-results');
    if (!q || q.length < 2) { box.style.display = 'none'; return; }
    const data = await _get(`${URLS.zbxHosts}?id=${clienteId}&q=${encodeURIComponent(q)}`);
    if (data.error || !data.hosts?.length) {
      box.innerHTML = '<div class="grph-ri"><span style="color:#64748b">Nenhum host encontrado</span></div>';
    } else {
      box.innerHTML = data.hosts.map(h => `
        <div class="grph-ri"
             onclick="GRAPH.selecionarHost('${h.hostid}','${(h.name||h.host).replace(/'/g,"\\'")}')">
          <div class="grph-ri-main">
            <span class="grph-ri-dot" style="background:${h.available==='up'?'#00ff88':'#ef4444'}"></span>
            <span>${h.name || h.host}</span>
          </div>
          <small>${h.ip} — <b style="color:${h.available==='up'?'#00ff88':'#ef4444'}">${h.available}</b></small>
        </div>`).join('');
    }
    box.style.display = 'block';
  }

  function selecionarHost(hostid, name) {
    document.getElementById('grph-m-hostid').value   = hostid;
    document.getElementById('grph-m-hostname').value = name;
    document.getElementById('grph-m-search').value   = name;
    document.getElementById('grph-m-results').style.display = 'none';
    document.getElementById('grph-m-preview').style.display = 'none';
    const badge = document.getElementById('grph-m-badge');
    badge.textContent = `✓ ${name}`; badge.style.display = 'block';
    _carregarInterfaces(hostid, null);
  }

  async function _carregarInterfaces(hostid, preselect) {
    const sel = document.getElementById('grph-m-iface');
    sel.innerHTML = '<option value="">Buscando interfaces…</option>';
    sel.disabled  = true;
    if (ifacesCache[hostid]) { _preencherIfaces(sel, ifacesCache[hostid], preselect); return; }
    try {
      const data = await _get(`${URLS.zbxIfaces}?cliente_id=${clienteId}&host_id=${hostid}`);
      if (data.error || !data.interfaces?.length) {
        sel.innerHTML = '<option value="">Nenhuma interface encontrada</option>';
        sel.disabled  = false; return;
      }
      ifacesCache[hostid] = data.interfaces;
      _preencherIfaces(sel, data.interfaces, preselect);
    } catch {
      sel.innerHTML = '<option value="">Erro ao buscar</option>';
      sel.disabled  = false;
    }
  }

  function _preencherIfaces(sel, ifaces, preselect) {
    sel.innerHTML = '<option value="">— Selecione a interface —</option>';
    ifaces.forEach(i => {
      const opt = document.createElement('option');
      opt.value       = JSON.stringify({ iface: i.name, inId: i.in_id, outId: i.out_id });
      opt.textContent = i.name;
      if (preselect && i.name === preselect) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.disabled = false;
    if (preselect) {
      const found = ifaces.find(i => i.name === preselect);
      if (found) _exibirPreviewIds(found);
    }
  }

  function ifaceChange() {
    const raw  = document.getElementById('grph-m-iface').value;
    const prev = document.getElementById('grph-m-preview');
    if (!raw) { prev.style.display = 'none'; return; }
    try {
      const { iface } = JSON.parse(raw);
      const hostid    = document.getElementById('grph-m-hostid').value;
      const found     = ifacesCache[hostid]?.find(i => i.name === iface);
      if (found) _exibirPreviewIds(found);
      else prev.style.display = 'none';
    } catch { prev.style.display = 'none'; }
  }

  function _exibirPreviewIds(iface) {
    const prev = document.getElementById('grph-m-preview');
    if (!prev) return;
    prev.style.display = 'block';
    prev.innerHTML = `
      <div class="grph-preview-row">
        <span>In Item ID</span>
        <b style="color:#00ff88">${iface.in_id  || '<span style="color:#64748b">—</span>'}</b>
      </div>
      <div class="grph-preview-row">
        <span>Out Item ID</span>
        <b style="color:#00d9ff">${iface.out_id || '<span style="color:#64748b">—</span>'}</b>
      </div>`;
  }

  // ─────────────────────────────────────────────────────────
  // SALVAR / REMOVER
  // ─────────────────────────────────────────────────────────
  function salvarChart() {
    const hostid   = document.getElementById('grph-m-hostid').value;
    const hostname = document.getElementById('grph-m-hostname').value;
    const raw      = document.getElementById('grph-m-iface').value;
    if (!hostid || !raw) { alert('Selecione um host e uma interface.'); return; }
    let d;
    try { d = JSON.parse(raw); } catch { alert('Interface inválida.'); return; }

    if (_editingChart !== null) {
      const c = charts.find(x => x.id === _editingChart);
      if (c) {
        if (c._interval)     clearInterval(c._interval);
        if (c.chartInstance) c.chartInstance.destroy();
        Object.assign(c, {
          hostid, hostname, ifaceName: d.iface,
          inId: d.inId||null, outId: d.outId||null,
          chartInstance: null, _interval: null,
        });
      }
    } else {
      charts.push({
        id: nextChartId++, hostid, hostname, ifaceName: d.iface,
        inId: d.inId||null, outId: d.outId||null,
        chartInstance: null, fsChartInstance: null, _interval: null, _fsSync: false,
      });
    }
    fecharModal();
    _renderGrid();
  }

  function removerChart(id) {
    const idx = charts.findIndex(c => c.id === id);
    if (idx === -1) return;
    const c = charts[idx];
    if (c._interval)      clearInterval(c._interval);
    if (c.chartInstance)  c.chartInstance.destroy();
    if (_fullscreenId === id) fecharFullscreen();
    charts.splice(idx, 1);
    _renderGrid();
  }

  // ─────────────────────────────────────────────────────────
  // UTILS
  // ─────────────────────────────────────────────────────────
  function _fmtBps(v) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    v = parseFloat(v);
    if (v >= 1e9) return `${(v/1e9).toFixed(2)} Gbps`;
    if (v >= 1e6) return `${(v/1e6).toFixed(2)} Mbps`;
    if (v >= 1e3) return `${(v/1e3).toFixed(1)} Kbps`;
    return `${v.toFixed(0)} bps`;
  }

  function _fmtTime(ts) {
    const d = new Date(ts * 1000);
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  }

  // ─────────────────────────────────────────────────────────
  // API PÚBLICA
  // ─────────────────────────────────────────────────────────
  return {
    init, abrirModalAdd, abrirModalEdit, fecharModal,
    hostSearchDebounce, selecionarHost, ifaceChange,
    salvarChart, removerChart, atualizarTodos,
    toggleFullscreen, fecharFullscreen,
  };
})();

console.log('[graph.js] Executou até o fim, definindo window.GRAPH');
window.GRAPH = GRAPH;
console.log('[graph.js] window.GRAPH definido:', typeof window.GRAPH, Object.keys(window.GRAPH));
/* init controlado pelo tab_monitoramento.html */