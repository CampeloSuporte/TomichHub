/**
 * topology_monitor.js
 * Lógica completa do editor de topologia interativo com integração Zabbix.
 *
 * Depende de window.CLIENTE_ID (definido na página pai).
 * URLs do app monitoramento via namespace 'monitoramento:*'.
 */

'use strict';

// ─────────────────────────────────────────────────────────────────
//  UTILITÁRIOS CSRF
// ─────────────────────────────────────────────────────────────────
function _csrfToken() {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? m[1] : '';
}

async function _post(url, data) {
  const r = await fetch(url, {
    method:  'POST',
    headers: { 'X-CSRFToken': _csrfToken(), 'Content-Type': 'application/json' },
    body:    JSON.stringify(data),
  });
  return r.json();
}

async function _postForm(url, formData) {
  const r = await fetch(url, {
    method:  'POST',
    headers: { 'X-CSRFToken': _csrfToken() },
    body:    formData,
  });
  return r.json();
}

async function _get(url) {
  const r = await fetch(url);
  return r.json();
}

// ─────────────────────────────────────────────────────────────────
//  ESTADO GLOBAL
// ─────────────────────────────────────────────────────────────────
const MON = (() => {

  // ── URLs (devem corresponder ao namespace do app) ──────────────
  const URL = {
    zbxSalvar:    '/monitoramento/zabbix/salvar/',
    zbxBuscar:    '/monitoramento/zabbix/buscar/',
    zbxTestar:    '/monitoramento/zabbix/testar/',
    zbxHosts:     '/monitoramento/zabbix/hosts/',
    zbxIfaces:    '/monitoramento/zabbix/interfaces/',
    topoListar:   '/monitoramento/topologias/listar/',
    topoCriar:    '/monitoramento/topologias/criar/',
    topoSalvar:   '/monitoramento/topologias/salvar/',
    topoCarregar: '/monitoramento/topologias/carregar/',
    topoDeletar:  '/monitoramento/topologias/deletar/',
    status:       '/monitoramento/status/',
  };

  // ── Cores por tipo de nó ───────────────────────────────────────
  const COR_TIPO = {
    router:   '#00ff88',
    switch:   '#00d9ff',
    firewall: '#ff6b35',
    server:   '#a78bfa',
    ap:       '#fbbf24',
    cloud:    '#60a5fa',
    endpoint: '#94a3b8',
  };

  // ── Cores de status ────────────────────────────────────────────
  const COR_STATUS = {
    up:           '#00ff88',
    down:         '#ef4444',
    problem:      '#fbbf24',
    unknown:      '#555',
    unconfigured: '#2d3748',
  };

  // ── Estado interno ─────────────────────────────────────────────
  let clienteId       = null;
  let topoId          = null;
  let nodes           = [];   // { id, tipo, label, x, y, zabbix_hostid, zabbix_hostname }
  let links           = [];   // { id, source, target, label, itemid_in, itemid_out, itemid_status }
  let nextId          = 1;
  let modoEdicao      = false;
  let modoConectar    = false;
  let conectarFrom    = null;
  let dragging        = null; // { nodeId, offsetX, offsetY }
  let selected        = null; // { type: 'node'|'link', id }
  let monitorInterval = null;
  let statusCache     = {};
  let hostsCache      = [];
  let ifacesCache     = {};
  let _hostSearchTimer = null;
  let _editingNode     = null;
  let _editingLink     = null;

  // ── Elementos SVG ─────────────────────────────────────────────
  function svgEl()       { return document.getElementById('mon-svg'); }
  function nodesGroup()  { return document.getElementById('mon-nodes-group'); }
  function linksGroup()  { return document.getElementById('mon-links-group'); }

  // ─────────────────────────────────────────────────────────────
  //  INICIALIZAÇÃO
  // ─────────────────────────────────────────────────────────────
  function init() {
    clienteId = window.CLIENTE_ID || null;
    if (!clienteId) return;
    _carregarListaTopologias();
  }

  // ─────────────────────────────────────────────────────────────
  //  LISTA DE TOPOLOGIAS
  // ─────────────────────────────────────────────────────────────
  async function _carregarListaTopologias() {
    const data = await _get(`${URL.topoListar}?id=${clienteId}`);
    const sel  = document.getElementById('mon-topo-select');
    sel.innerHTML = '<option value="">— Selecione a topologia —</option>';
    (data.topologias || []).forEach(t => {
      const opt = document.createElement('option');
      opt.value       = t.id;
      opt.textContent = `${t.nome} (${t.total_nodes} nós)`;
      sel.appendChild(opt);
    });
    if (topoId) sel.value = topoId;
  }

  async function carregarTopologiaSelecionada() {
    const sel = document.getElementById('mon-topo-select');
    topoId    = sel.value;
    if (!topoId) { _resetCanvas(); return; }

    const data = await _get(`${URL.topoCarregar}?topo_id=${topoId}&cliente_id=${clienteId}`);
    if (data.error) { alert(data.error); return; }

    // Preenche estado local
    nextId = 1;
    nodes  = data.nodes.map(n => {
      if (n.id >= nextId) nextId = n.id + 1;
      return { id: n.id, tipo: n.tipo, label: n.label, x: n.x, y: n.y,
               zabbix_hostid: n.zabbix_hostid, zabbix_hostname: n.zabbix_hostname };
    });
    links = data.links.map(l => {
      if (l.id >= nextId) nextId = l.id + 1;
      return { id: l.id, source: l.source, target: l.target, label: l.label,
               itemid_in: l.itemid_in, itemid_out: l.itemid_out, itemid_status: l.itemid_status };
    });

    document.getElementById('mon-topo-nome').textContent = data.nome;
    _setButtonsEnabled(true);
    _resetModoEdicao();
    renderizarCanvas();
  }

  // ─────────────────────────────────────────────────────────────
  //  RESET / UTILS
  // ─────────────────────────────────────────────────────────────
  function _resetCanvas() {
    nodes  = [];
    links  = [];
    topoId = null;
    selected = null;
    document.getElementById('mon-topo-nome').textContent = '';
    document.getElementById('mon-empty-state').style.display = 'flex';
    nodesGroup().innerHTML = '';
    linksGroup().innerHTML = '';
    _setButtonsEnabled(false);
    _pararMonitor();
    document.getElementById('mon-props-panel').style.display = 'none';
  }

  function _setButtonsEnabled(on) {
    ['mon-btn-salvar','mon-btn-deletar','mon-btn-editar','mon-btn-monitorar'].forEach(id => {
      const b = document.getElementById(id);
      if (b) b.disabled = !on;
    });
  }

  function _resetModoEdicao() {
    modoEdicao   = false;
    modoConectar = false;
    conectarFrom = null;
    document.getElementById('mon-palette').style.display     = 'none';
    document.getElementById('mon-btn-conectar').style.display = 'none';
    document.getElementById('mon-btn-conectar').classList.remove('active');
  }

  // ─────────────────────────────────────────────────────────────
  //  RENDER
  // ─────────────────────────────────────────────────────────────
  function renderizarCanvas() {
    const hasElements = nodes.length > 0;
    document.getElementById('mon-empty-state').style.display = hasElements ? 'none' : 'flex';
    linksGroup().innerHTML = '';
    nodesGroup().innerHTML = '';
    links.forEach(_renderLink);
    nodes.forEach(_renderNode);
  }

  function _corStatus(nodeId) {
    const s = (statusCache.nodes || {})[nodeId];
    if (!s) return COR_STATUS.unconfigured;
    return COR_STATUS[s.status] || COR_STATUS.unknown;
  }

  function _renderNode(n) {
    const cor      = COR_TIPO[n.tipo] || '#888';
    const statusC  = _corStatus(n.id);
    const g        = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('id', `mon-node-${n.id}`);
    g.setAttribute('transform', `translate(${n.x},${n.y})`);
    g.style.cursor = modoEdicao ? 'grab' : 'pointer';

    // Status ring
    const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    ring.setAttribute('r', '24');
    ring.setAttribute('fill', 'none');
    ring.setAttribute('stroke', statusC);
    ring.setAttribute('stroke-width', '2.5');
    ring.setAttribute('opacity', '.7');
    g.appendChild(ring);

    // Background circle
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    bg.setAttribute('r', '18');
    bg.setAttribute('fill', cor.replace(')', ',0.15)').replace('rgb', 'rgba').replace('#', 'rgba(').replace(
      /rgba\((.*)\)/, (_, h) => `rgba(${parseInt(h.substring(0,2),16)},${parseInt(h.substring(2,4),16)},${parseInt(h.substring(4,6),16)},0.15)`
    ));
    bg.setAttribute('stroke', cor);
    bg.setAttribute('stroke-width', '1.5');
    g.appendChild(bg);

    // Ícone SVG inline
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    icon.innerHTML = _iconePath(n.tipo, cor);
    g.appendChild(icon);

    // Label
    const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txt.setAttribute('y', '38');
    txt.setAttribute('text-anchor', 'middle');
    txt.setAttribute('fill', '#e2e8f0');
    txt.setAttribute('font-size', '11');
    txt.setAttribute('font-family', 'system-ui, sans-serif');
    txt.textContent = n.label;
    g.appendChild(txt);

    // Hostname sub-label
    if (n.zabbix_hostname) {
      const sub = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      sub.setAttribute('y', '50');
      sub.setAttribute('text-anchor', 'middle');
      sub.setAttribute('fill', '#64748b');
      sub.setAttribute('font-size', '9');
      sub.setAttribute('font-family', 'system-ui, sans-serif');
      sub.textContent = n.zabbix_hostname;
      g.appendChild(sub);
    }

    // Hit-box transparente para arrastar
    const hit = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    hit.setAttribute('r', '24');
    hit.setAttribute('fill', 'transparent');
    g.appendChild(hit);

    // Eventos
    g.addEventListener('mousedown', e => {
      e.stopPropagation();
      if (modoConectar) { handleConectarClick(n.id); return; }
      if (modoEdicao)   { iniciarDragNode(e, n.id); return; }
      selecionarElemento('node', n.id);
    });
    g.addEventListener('dblclick', e => {
      e.stopPropagation();
      abrirModalNode(n.id);
    });

    nodesGroup().appendChild(g);
  }

  function _renderLink(lk) {
    const src = nodes.find(n => n.id === lk.source);
    const dst = nodes.find(n => n.id === lk.target);
    if (!src || !dst) return;

    const ls   = (statusCache.links || {})[lk.id];
    const cor  = ls ? (COR_STATUS[ls.status] || '#444') : '#444';
    const marker = ls?.status === 'up' ? 'arr-green' : ls?.status === 'down' ? 'arr-red' : 'arr-gray';

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('id', `mon-link-${lk.id}`);

    // Hit-box
    const hb = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    hb.setAttribute('x1', src.x); hb.setAttribute('y1', src.y);
    hb.setAttribute('x2', dst.x); hb.setAttribute('y2', dst.y);
    hb.setAttribute('stroke', 'transparent');
    hb.setAttribute('stroke-width', '12');
    hb.style.cursor = 'pointer';
    g.appendChild(hb);

    // Linha visível
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', src.x); line.setAttribute('y1', src.y);
    line.setAttribute('x2', dst.x); line.setAttribute('y2', dst.y);
    line.setAttribute('stroke', cor);
    line.setAttribute('stroke-width', '2');
    line.setAttribute('marker-end', `url(#${marker})`);
    g.appendChild(line);

    // Label central
    const mx = (src.x + dst.x) / 2;
    const my = (src.y + dst.y) / 2;

    if (lk.label) {
      const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      bg.setAttribute('x', mx - 30); bg.setAttribute('y', my - 12);
      bg.setAttribute('width', 60); bg.setAttribute('height', 16);
      bg.setAttribute('rx', 4); bg.setAttribute('fill', '#1e293b');
      g.appendChild(bg);

      const tl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      tl.setAttribute('x', mx); tl.setAttribute('y', my - 1);
      tl.setAttribute('text-anchor', 'middle');
      tl.setAttribute('fill', '#94a3b8');
      tl.setAttribute('font-size', '9');
      tl.setAttribute('font-family', 'system-ui, sans-serif');
      tl.textContent = lk.label;
      g.appendChild(tl);
    }

    // Tráfego in/out
    if (ls?.traffic_in) {
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', mx + 4); t.setAttribute('y', my + 14);
      t.setAttribute('fill', '#00ff88'); t.setAttribute('font-size', '8');
      t.setAttribute('font-family', 'system-ui, sans-serif');
      t.textContent = `↓ ${ls.traffic_in}`;
      g.appendChild(t);
    }
    if (ls?.traffic_out) {
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', mx + 4); t.setAttribute('y', my + 24);
      t.setAttribute('fill', '#00d9ff'); t.setAttribute('font-size', '8');
      t.setAttribute('font-family', 'system-ui, sans-serif');
      t.textContent = `↑ ${ls.traffic_out}`;
      g.appendChild(t);
    }

    // Eventos
    g.addEventListener('click', e => {
      e.stopPropagation();
      selecionarElemento('link', lk.id);
    });
    g.addEventListener('dblclick', e => {
      e.stopPropagation();
      abrirModalLink(lk.id);
    });

    linksGroup().appendChild(g);
  }

  // Ícones por tipo
  function _iconePath(tipo, cor) {
    const c = cor;
    switch (tipo) {
      case 'router':   return `<path d="M-8 0h16M0 -8v16M-6-6l12 12M6-6L-6 6" stroke="${c}" stroke-width="2" stroke-linecap="round" fill="none"/>`;
      case 'switch':   return `<rect x="-10" y="-5" width="20" height="10" rx="2" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="-5" cy="0" r="2" fill="${c}"/><circle cx="0" cy="0" r="2" fill="${c}"/><circle cx="5" cy="0" r="2" fill="${c}"/>`;
      case 'firewall': return `<path d="M0,-12 L10,-6 V4 C10,10 5,14 0,16 C-5,14-10,10-10,4 V-6 Z" fill="none" stroke="${c}" stroke-width="1.5"/><path d="M-3,2 L0,5 L4,-1" stroke="${c}" stroke-width="2" stroke-linecap="round" fill="none"/>`;
      case 'server':   return `<rect x="-10" y="-8" width="20" height="6" rx="1" fill="none" stroke="${c}" stroke-width="1.5"/><rect x="-10" y="2" width="20" height="6" rx="1" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="-6" cy="-5" r="1.5" fill="${c}"/><circle cx="-6" cy="5" r="1.5" fill="${c}"/>`;
      case 'ap':       return `<path d="-10,4 Q0,-6 10,4" stroke="${c}" stroke-width="2" fill="none"/><path d="-6,8 Q0,1 6,8" stroke="${c}" stroke-width="2" fill="none"/><circle cx="0" cy="11" r="2.5" fill="${c}"/>`;
      case 'cloud':    return `<path d="M-10,5 a6,6 0 0,1 0-12 a5,5 0 0,1 9-2 a6,6 0 1 1 1,14z" fill="none" stroke="${c}" stroke-width="1.5"/>`;
      case 'endpoint': return `<rect x="-10" y="-8" width="20" height="12" rx="1" fill="none" stroke="${c}" stroke-width="1.5"/><path d="M-5,4 h10 M0,4 v4" stroke="${c}" stroke-width="1.5" stroke-linecap="round"/>`;
      default:         return `<circle r="8" fill="none" stroke="${c}" stroke-width="1.5"/>`;
    }
  }

  // ─────────────────────────────────────────────────────────────
  //  INTERAÇÕES — ARRASTAR
  // ─────────────────────────────────────────────────────────────
  function paletteDragStart(e) {
    e.dataTransfer.setData('mon-tipo', e.currentTarget.dataset.tipo);
  }

  function svgDrop(e) {
    e.preventDefault();
    const tipo = e.dataTransfer.getData('mon-tipo');
    if (!tipo || !modoEdicao) return;

    const rect = e.currentTarget.getBoundingClientRect();
    const svgEl_ = svgEl();
    const vb    = svgEl_.viewBox.baseVal;
    const scaleX = vb.width  / rect.width;
    const scaleY = vb.height / rect.height;

    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top)  * scaleY;

    const id = nextId++;
    nodes.push({ id, tipo, label: tipo, x, y, zabbix_hostid: null, zabbix_hostname: null });
    _renderNode(nodes[nodes.length - 1]);
    document.getElementById('mon-empty-state').style.display = 'none';
  }

  function iniciarDragNode(e, nodeId) {
    if (!modoEdicao) return;
    const rect = svgEl().getBoundingClientRect();
    const vb   = svgEl().viewBox.baseVal;
    const scaleX = vb.width  / rect.width;
    const scaleY = vb.height / rect.height;
    const n    = nodes.find(nd => nd.id === nodeId);
    const mx   = (e.clientX - rect.left) * scaleX;
    const my   = (e.clientY - rect.top)  * scaleY;
    dragging = { nodeId, offsetX: mx - n.x, offsetY: my - n.y };
    document.getElementById(`mon-node-${nodeId}`).style.cursor = 'grabbing';
  }

  function svgMouseMove(e) {
    if (!dragging) return;
    const rect = svgEl().getBoundingClientRect();
    const vb   = svgEl().viewBox.baseVal;
    const scaleX = vb.width  / rect.width;
    const scaleY = vb.height / rect.height;
    const nx   = (e.clientX - rect.left) * scaleX - dragging.offsetX;
    const ny   = (e.clientY - rect.top)  * scaleY - dragging.offsetY;
    const n    = nodes.find(nd => nd.id === dragging.nodeId);
    if (!n) return;
    n.x = nx;
    n.y = ny;
    _atualizarPosicaoNode(n);
  }

  function svgMouseUp() {
    if (dragging) {
      const el = document.getElementById(`mon-node-${dragging.nodeId}`);
      if (el) el.style.cursor = 'grab';
      dragging = null;
    }
  }

  function _atualizarPosicaoNode(n) {
    const el = document.getElementById(`mon-node-${n.id}`);
    if (el) el.setAttribute('transform', `translate(${n.x},${n.y})`);

    // Re-renderiza links conectados a este nó
    links.filter(l => l.source === n.id || l.target === n.id).forEach(l => {
      const el = document.getElementById(`mon-link-${l.id}`);
      if (el) el.remove();
      _renderLink(l);
      // Mover o grupo do link antes dos nós
      linksGroup().appendChild(document.getElementById(`mon-link-${l.id}`));
    });
  }

  // ─────────────────────────────────────────────────────────────
  //  MODO CONECTAR
  // ─────────────────────────────────────────────────────────────
  function toggleConectar() {
    modoConectar = !modoConectar;
    conectarFrom = null;
    const btn = document.getElementById('mon-btn-conectar');
    btn.classList.toggle('active', modoConectar);
    btn.textContent = modoConectar ? '✕ Cancelar conexão' : '⟷ Conectar';
  }

  function handleConectarClick(nodeId) {
    if (!conectarFrom) {
      conectarFrom = nodeId;
      const el = document.getElementById(`mon-node-${nodeId}`);
      el?.querySelector('circle:first-child')?.setAttribute('stroke', '#7c3aed');
      return;
    }
    if (conectarFrom === nodeId) { conectarFrom = null; return; }

    // Evita duplicata
    const existe = links.find(l =>
      (l.source === conectarFrom && l.target === nodeId) ||
      (l.source === nodeId && l.target === conectarFrom)
    );
    if (!existe) {
      const id = nextId++;
      links.push({ id, source: conectarFrom, target: nodeId,
                   label: '', itemid_in: null, itemid_out: null, itemid_status: null });
      _renderLink(links[links.length - 1]);
    }

    // Reset do anel colorido
    const el = document.getElementById(`mon-node-${conectarFrom}`);
    el?.querySelector('circle:first-child')?.setAttribute('stroke', _corStatus(conectarFrom));
    conectarFrom = null;
    toggleConectar(); // desativa modo conectar
  }

  // ─────────────────────────────────────────────────────────────
  //  SELEÇÃO
  // ─────────────────────────────────────────────────────────────
  function svgClick() {
    if (!modoConectar) fecharProps();
  }

  function selecionarElemento(type, id) {
    selected = { type, id };
    const panel = document.getElementById('mon-props-panel');
    const title = document.getElementById('mon-props-title');
    const body  = document.getElementById('mon-props-body');
    panel.style.display = 'flex';

    if (type === 'node') {
      const n  = nodes.find(nd => nd.id === id);
      const ls = (statusCache.nodes || {})[id];
      title.textContent = n.label;
      body.innerHTML = `
        <div style="margin-bottom:8px;">
          <span style="color:${COR_TIPO[n.tipo] || '#888'};font-weight:600;">${n.tipo}</span>
        </div>
        ${n.zabbix_hostname ? `<div style="margin-bottom:4px;">Host: <b style="color:#e2e8f0;">${n.zabbix_hostname}</b></div>` : '<div style="color:#64748b;">Host não configurado</div>'}
        ${ls ? `<div style="margin-top:8px;">Status: <b style="color:${COR_STATUS[ls.status] || '#888'};">${ls.status}</b></div>` : ''}
        <button class="mon-btn mon-btn-primary" style="margin-top:12px;width:100%;" onclick="MON.abrirModalNode(${id})">Editar nó</button>
      `;
    } else {
      const l  = links.find(lk => lk.id === id);
      const ls = (statusCache.links || {})[id];
      title.textContent = l.label || 'Enlace';
      body.innerHTML = `
        ${ls ? `
          <div style="margin-bottom:8px;">Status: <b style="color:${COR_STATUS[ls.status] || '#888'};">${ls.status}</b></div>
          ${ls.traffic_in  ? `<div style="color:#00ff88;">↓ In:  ${ls.traffic_in}</div>`  : ''}
          ${ls.traffic_out ? `<div style="color:#00d9ff;">↑ Out: ${ls.traffic_out}</div>` : ''}
        ` : '<div style="color:#64748b;">Items não configurados</div>'}
        <button class="mon-btn mon-btn-primary" style="margin-top:12px;width:100%;" onclick="MON.abrirModalLink(${id})">Editar enlace</button>
      `;
    }
  }

  function fecharProps() {
    selected = null;
    document.getElementById('mon-props-panel').style.display = 'none';
  }

  // ─────────────────────────────────────────────────────────────
  //  MODOS EDIÇÃO / MONITORAMENTO
  // ─────────────────────────────────────────────────────────────
  function entrarModoEdicao() {
    if (!topoId) return;
    _pararMonitor();
    modoEdicao = true;
    document.getElementById('mon-palette').style.display       = 'flex';
    document.getElementById('mon-palette').style.flexDirection = 'column';
    document.getElementById('mon-btn-conectar').style.display  = 'inline-flex';
    document.getElementById('mon-live-indicator').style.display = 'none';
    renderizarCanvas();
  }

  function entrarModoMonitor() {
    if (!topoId) return;
    _resetModoEdicao();
    _iniciarMonitor();
  }

  // ─────────────────────────────────────────────────────────────
  //  MONITORAMENTO TEMPO REAL
  // ─────────────────────────────────────────────────────────────
  function _iniciarMonitor() {
    _pararMonitor();
    buscarStatusAgora();
    monitorInterval = setInterval(buscarStatusAgora, 30000);
    document.getElementById('mon-live-indicator').style.display = 'inline-flex';
  }

  function _pararMonitor() {
    clearInterval(monitorInterval);
    monitorInterval = null;
    document.getElementById('mon-live-indicator').style.display = 'none';
  }

  async function buscarStatusAgora() {
    if (!topoId) return;
    const data = await _get(`${URL.status}?topo_id=${topoId}&cliente_id=${clienteId}`);
    if (data.success) {
      statusCache = { nodes: data.nodes || {}, links: data.links || {} };
      _atualizarStatusVisual();
    }
  }

  function _atualizarStatusVisual() {
    // Atualiza anéis dos nós
    nodes.forEach(n => {
      const ring = document.querySelector(`#mon-node-${n.id} circle:first-child`);
      if (ring) ring.setAttribute('stroke', _corStatus(n.id));
    });
    // Re-renderiza todos os links (tráfego + cores)
    linksGroup().innerHTML = '';
    links.forEach(_renderLink);
  }

  // ─────────────────────────────────────────────────────────────
  //  SALVAR / CRIAR / DELETAR TOPOLOGIA
  // ─────────────────────────────────────────────────────────────
  async function salvarTopologia() {
    if (!topoId) return;
    const btn = document.getElementById('mon-btn-salvar');
    btn.disabled = true; btn.textContent = 'Salvando…';
    try {
      const data = await _post(URL.topoSalvar, {
        topo_id:   topoId,
        cliente_id: clienteId,
        nodes: nodes.map(n => ({
          id:             n.id,
          tipo:           n.tipo,
          label:          n.label,
          x:              n.x,
          y:              n.y,
          zabbix_hostid:  n.zabbix_hostid,
          zabbix_hostname: n.zabbix_hostname,
        })),
        links: links.map(l => ({
          id:             l.id,
          source:         l.source,
          target:         l.target,
          label:          l.label,
          itemid_in:      l.itemid_in,
          itemid_out:     l.itemid_out,
          itemid_status:  l.itemid_status,
        })),
      });
      if (data.error) alert(`Erro: ${data.error}`);
      else {
        _toast('Topologia salva com sucesso!', 'success');
        // Recarregar IDs do backend
        await carregarTopologiaSelecionada();
      }
    } finally {
      btn.disabled = false; btn.textContent = 'Salvar';
    }
  }

  function abrirModalNova() {
    document.getElementById('mon-nova-nome').value = '';
    document.getElementById('mon-nova-desc').value = '';
    _abrirModal('mon-modal-nova');
    setTimeout(() => document.getElementById('mon-nova-nome').focus(), 100);
  }

  async function criarTopologia() {
    const nome = document.getElementById('mon-nova-nome').value.trim();
    if (!nome) { alert('Informe o nome da topologia'); return; }

    const data = await _post(URL.topoCriar, {
      cliente_id: clienteId,
      nome,
      descricao: document.getElementById('mon-nova-desc').value,
    });
    if (data.error) { alert(data.error); return; }

    fecharModal('mon-modal-nova');
    await _carregarListaTopologias();
    topoId = data.id;
    document.getElementById('mon-topo-select').value = topoId;
    nodes  = [];
    links  = [];
    document.getElementById('mon-topo-nome').textContent = data.nome;
    _setButtonsEnabled(true);
    renderizarCanvas();
    entrarModoEdicao();
  }

  async function deletarTopologia() {
    if (!topoId) return;
    if (!confirm('Tem certeza que deseja excluir esta topologia?')) return;

    const data = await _post(`${URL.topoDeletar}${topoId}/`, {});
    if (data.error) { alert(data.error); return; }

    await _carregarListaTopologias();
    _resetCanvas();
  }

  // ─────────────────────────────────────────────────────────────
  //  MODAL NÓ
  // ─────────────────────────────────────────────────────────────
  function abrirModalNode(nodeId) {
    const n = nodes.find(nd => nd.id === nodeId);
    if (!n) return;
    _editingNode = nodeId;

    document.getElementById('mon-nd-label').value     = n.label;
    document.getElementById('mon-nd-tipo').value      = n.tipo;
    document.getElementById('mon-nd-search').value    = n.zabbix_hostname || '';
    document.getElementById('mon-nd-hostid').value    = n.zabbix_hostid   || '';
    document.getElementById('mon-nd-hostname').value  = n.zabbix_hostname || '';
    document.getElementById('mon-nd-results').style.display = 'none';

    const badge = document.getElementById('mon-nd-host-selecionado');
    if (n.zabbix_hostname) {
      badge.textContent   = `✓ ${n.zabbix_hostname} (${n.zabbix_hostid})`;
      badge.style.display = 'block';
    } else {
      badge.style.display = 'none';
    }

    _abrirModal('mon-modal-node');
  }

  function buscarHostDebounce(query) {
    clearTimeout(_hostSearchTimer);
    _hostSearchTimer = setTimeout(() => _buscarHosts(query), 300);
  }

  async function _buscarHosts(query) {
    if (!query || query.length < 2) {
      document.getElementById('mon-nd-results').style.display = 'none';
      return;
    }
    const data = await _get(`${URL.zbxHosts}?id=${clienteId}&q=${encodeURIComponent(query)}`);
    const box  = document.getElementById('mon-nd-results');
    if (data.error || !data.hosts?.length) {
      box.innerHTML = '<div class="mon-search-item"><span style="color:#64748b;">Nenhum host encontrado</span></div>';
    } else {
      hostsCache = data.hosts;
      box.innerHTML = data.hosts.map(h => `
        <div class="mon-search-item" onclick="MON.selecionarHost('${h.hostid}','${h.name}','${h.host}')">
          <span>${h.name}</span>
          <small>${h.host} — ${h.ip} — <b style="color:${h.available==='up'?'#00ff88':'#ef4444'}">${h.available}</b></small>
        </div>
      `).join('');
    }
    box.style.display = 'block';
  }

  function selecionarHost(hostid, name, host) {
    document.getElementById('mon-nd-hostid').value   = hostid;
    document.getElementById('mon-nd-hostname').value = name || host;
    document.getElementById('mon-nd-search').value   = name || host;
    document.getElementById('mon-nd-results').style.display = 'none';
    const badge = document.getElementById('mon-nd-host-selecionado');
    badge.textContent   = `✓ ${name || host} (${hostid})`;
    badge.style.display = 'block';
  }

  function salvarEdicaoNode() {
    const n = nodes.find(nd => nd.id === _editingNode);
    if (!n) return;
    n.label           = document.getElementById('mon-nd-label').value.trim() || n.label;
    n.tipo            = document.getElementById('mon-nd-tipo').value;
    n.zabbix_hostid   = document.getElementById('mon-nd-hostid').value   || null;
    n.zabbix_hostname = document.getElementById('mon-nd-hostname').value || null;

    fecharModal('mon-modal-node');
    renderizarCanvas();
  }

  function deletarNodeSelecionado() {
    if (!_editingNode) return;
    nodes  = nodes.filter(n => n.id !== _editingNode);
    links  = links.filter(l => l.source !== _editingNode && l.target !== _editingNode);
    fecharModal('mon-modal-node');
    renderizarCanvas();
  }

  // ─────────────────────────────────────────────────────────────
  //  MODAL LINK
  // ─────────────────────────────────────────────────────────────
  function abrirModalLink(linkId) {
    const l = links.find(lk => lk.id === linkId);
    if (!l) return;
    _editingLink = linkId;

    document.getElementById('mon-lk-label').value    = l.label    || '';
    document.getElementById('mon-lk-item-in').value  = l.itemid_in    || '';
    document.getElementById('mon-lk-item-out').value = l.itemid_out   || '';
    document.getElementById('mon-lk-item-status').value = l.itemid_status || '';
    document.getElementById('mon-lk-items-preview').style.display = 'none';

    // Preenche selects de host com nós que têm Zabbix configurado
    const hostNodes = nodes.filter(n => n.zabbix_hostid);
    ['mon-lk-src-host', 'mon-lk-dst-host'].forEach((selId, idx) => {
      const sel    = document.getElementById(selId);
      const currId = idx === 0 ? nodes.find(n => n.id === l.source)?.zabbix_hostid
                                : nodes.find(n => n.id === l.target)?.zabbix_hostid;
      sel.innerHTML = '<option value="">— nenhum —</option>';
      hostNodes.forEach(n => {
        const opt = document.createElement('option');
        opt.value       = n.zabbix_hostid;
        opt.textContent = `${n.label} (${n.zabbix_hostname})`;
        if (n.zabbix_hostid === currId) opt.selected = true;
        sel.appendChild(opt);
      });
    });

    document.getElementById('mon-lk-iface').innerHTML = '<option value="">— Selecione a interface —</option>';
    _abrirModal('mon-modal-link');
  }

  async function carregarInterfacesLink(side) {
    const selId  = side === 'src' ? 'mon-lk-src-host' : 'mon-lk-dst-host';
    const hostId = document.getElementById(selId).value;
    if (!hostId) return;

    const data = await _get(`${URL.zbxIfaces}?cliente_id=${clienteId}&host_id=${hostId}`);
    if (data.error || !data.interfaces?.length) return;

    ifacesCache[hostId] = data.interfaces;
    const sel = document.getElementById('mon-lk-iface');
    sel.innerHTML = '<option value="">— Selecione a interface —</option>';
    data.interfaces.forEach(iface => {
      const opt = document.createElement('option');
      opt.value       = JSON.stringify({ hostId, iface: iface.name });
      opt.textContent = iface.name;
      sel.appendChild(opt);
    });
  }

  function preencherItemsLink() {
    const raw = document.getElementById('mon-lk-iface').value;
    if (!raw) return;
    const { hostId, iface } = JSON.parse(raw);
    const ifaceData = ifacesCache[hostId]?.find(i => i.name === iface);
    if (!ifaceData) return;

    let inId = '', outId = '', statusId = '';
    ifaceData.items.forEach(it => {
      if (it.key.startsWith('net.if.in'))     inId     = it.itemid;
      if (it.key.startsWith('net.if.out'))    outId    = it.itemid;
      if (it.key.startsWith('net.if.status')) statusId = it.itemid;
    });

    document.getElementById('mon-lk-item-in').value     = inId;
    document.getElementById('mon-lk-item-out').value    = outId;
    document.getElementById('mon-lk-item-status').value = statusId;

    const prev = document.getElementById('mon-lk-items-preview');
    prev.style.display = 'block';
    prev.innerHTML = `
      <b>Items detectados:</b><br>
      In:     ${inId     ? `<b style="color:#00ff88;">${inId}</b>`     : '<span style="color:#64748b;">não encontrado</span>'}<br>
      Out:    ${outId    ? `<b style="color:#00d9ff;">${outId}</b>`    : '<span style="color:#64748b;">não encontrado</span>'}<br>
      Status: ${statusId ? `<b style="color:#fbbf24;">${statusId}</b>` : '<span style="color:#64748b;">não encontrado</span>'}
    `;
  }

  function salvarEdicaoLink() {
    const l = links.find(lk => lk.id === _editingLink);
    if (!l) return;
    l.label          = document.getElementById('mon-lk-label').value.trim();
    l.itemid_in      = document.getElementById('mon-lk-item-in').value.trim()     || null;
    l.itemid_out     = document.getElementById('mon-lk-item-out').value.trim()    || null;
    l.itemid_status  = document.getElementById('mon-lk-item-status').value.trim() || null;
    fecharModal('mon-modal-link');
    renderizarCanvas();
  }

  function deletarLinkSelecionado() {
    if (!_editingLink) return;
    links = links.filter(l => l.id !== _editingLink);
    fecharModal('mon-modal-link');
    renderizarCanvas();
  }

  // ─────────────────────────────────────────────────────────────
  //  CONFIGURAÇÃO ZABBIX
  // ─────────────────────────────────────────────────────────────
  async function abrirModalZabbix() {
    document.getElementById('mon-zbx-resultado').style.display = 'none';
    const data = await _get(`${URL.zbxBuscar}?id=${clienteId}`);
    if (data.existe) {
      document.getElementById('mon-zbx-url').value  = data.url || '';
      document.getElementById('mon-zbx-user').value = data.usuario || '';
    } else {
      document.getElementById('mon-zbx-url').value  = '';
      document.getElementById('mon-zbx-user').value = '';
    }
    document.getElementById('mon-zbx-pass').value  = '';
    document.getElementById('mon-zbx-token').value = '';
    _abrirModal('mon-modal-zabbix');
  }

  async function testarZabbix() {
    const res = document.getElementById('mon-zbx-resultado');
    res.textContent   = 'Testando…';
    res.className     = 'mon-alert';
    res.style.display = 'block';
    const data = await _get(`${URL.zbxTestar}?id=${clienteId}`);
    res.textContent = data.message;
    res.className   = `mon-alert ${data.success ? 'success' : 'error'}`;
  }

  async function salvarZabbixConfig() {
    const fd = new FormData();
    fd.append('cliente',   clienteId);
    fd.append('url',       document.getElementById('mon-zbx-url').value.trim());
    fd.append('usuario',   document.getElementById('mon-zbx-user').value.trim());
    fd.append('senha',     document.getElementById('mon-zbx-pass').value);
    fd.append('api_token', document.getElementById('mon-zbx-token').value.trim());

    const res  = document.getElementById('mon-zbx-resultado');
    const data = await _postForm(URL.zbxSalvar, fd);
    res.textContent   = data.message || (data.success ? 'Salvo!' : 'Erro ao salvar');
    res.className     = `mon-alert ${data.success ? 'success' : 'error'}`;
    res.style.display = 'block';
  }

  // ─────────────────────────────────────────────────────────────
  //  MODAIS HELPERS
  // ─────────────────────────────────────────────────────────────
  function _abrirModal(id) {
    document.getElementById(id).style.display = 'flex';
  }

  function fecharModal(id) {
    document.getElementById(id).style.display = 'none';
  }

  // ─────────────────────────────────────────────────────────────
  //  TOAST
  // ─────────────────────────────────────────────────────────────
  function _toast(msg, type = 'success') {
    const el = document.createElement('div');
    el.className = `mon-alert ${type}`;
    el.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;min-width:220px;box-shadow:0 8px 24px rgba(0,0,0,.4)';
    el.textContent   = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  // ─────────────────────────────────────────────────────────────
  //  API PÚBLICA
  // ─────────────────────────────────────────────────────────────
  return {
    // Init
    init,
    // Canvas
    renderizarCanvas,
    svgMouseMove,
    svgMouseUp,
    svgClick,
    svgDrop,
    paletteDragStart,
    // Topologias
    carregarTopologiaSelecionada,
    abrirModalNova,
    criarTopologia,
    salvarTopologia,
    deletarTopologia,
    // Modos
    entrarModoEdicao,
    entrarModoMonitor,
    toggleConectar,
    // Monitoramento
    buscarStatusAgora,
    // Nó
    abrirModalNode,
    buscarHostDebounce,
    selecionarHost,
    salvarEdicaoNode,
    deletarNodeSelecionado,
    // Link
    abrirModalLink,
    carregarInterfacesLink,
    preencherItemsLink,
    salvarEdicaoLink,
    deletarLinkSelecionado,
    // Zabbix
    abrirModalZabbix,
    testarZabbix,
    salvarZabbixConfig,
    // Modais
    fecharModal,
    fecharProps,
  };
})();

// Auto-inicializa quando a aba for exibida
document.addEventListener('DOMContentLoaded', () => {
  MON.init();

  // Suporte ao sistema de abas do projeto (função trocarAba)
  // A aba chama show() e esconde as outras, mas o objeto tab pode
  // ter display:none no início — o init() trata isso corretamente.
});
