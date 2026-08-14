/**
 * topology_monitor.js
 * Editor de topologia interativo com Zabbix + ZOOM/PAN completo.
 *
 * Zoom: scroll do mouse (centrado no cursor)
 * Pan:  botão do meio OU Ctrl+arrastar
 * Reset: duplo clique no fundo do SVG OU botão "⟲"
 */
'use strict';

function _csrfToken() {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? m[1] : '';
}
async function _post(url, data) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': _csrfToken(), 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return r.json();
}
async function _postForm(url, fd) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': _csrfToken() },
    body: fd,
  });
  return r.json();
}
async function _get(url) {
  const r = await fetch(url);
  return r.json();
}

// ─────────────────────────────────────────────────────────────
const MON = (() => {

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

  const COR_TIPO = {
    router:   '#00ff88', switch:   '#00d9ff', firewall: '#ff6b35',
    server:   '#a78bfa', ap:       '#fbbf24', cloud:    '#60a5fa',
    endpoint: '#94a3b8',
  };
  const COR_STATUS = {
    up: '#00ff88', down: '#ef4444', problem: '#fbbf24',
    unknown: '#555', unconfigured: '#2d3748',
  };

  // ── Estado da aplicação ───────────────────────────────────
  let clienteId        = null;
  let topoId           = null;
  let nodes            = [];
  let links            = [];
  let nextId           = 1;
  let modoEdicao       = false;
  let modoConectar     = false;
  let conectarFrom     = null;
  let dragging         = null;      // drag de nó
  let selected         = null;
  let monitorInterval  = null;
  let statusCache      = {};
  let hostsCache       = [];
  let ifacesCache      = {};
  let _hostTimer       = null;
  let _editingNode     = null;
  let _editingLink     = null;

  // ── Estado do Zoom / Pan ──────────────────────────────────
  let zoomLevel   = 1;
  let panX        = 0;
  let panY        = 0;
  let isPanning   = false;
  let panStartX   = 0;   // posição SVG onde o pan começou (relativa ao viewport)
  let panStartY   = 0;
  const MIN_ZOOM  = 0.15;
  const MAX_ZOOM  = 6;

  // ── Referências ao DOM ────────────────────────────────────
  const svgEl       = () => document.getElementById('mon-svg');
  const nodesGroup  = () => document.getElementById('mon-nodes-group');
  const linksGroup  = () => document.getElementById('mon-links-group');
  const viewportEl  = () => document.getElementById('mon-viewport');

  // ─────────────────────────────────────────────────────────
  // ZOOM / PAN
  // ─────────────────────────────────────────────────────────

  /** Aplica transform no grupo #mon-viewport e atualiza label */
  function _updateViewport() {
    const vp = viewportEl();
    if (vp) vp.setAttribute('transform',
      `translate(${panX.toFixed(2)},${panY.toFixed(2)}) scale(${zoomLevel.toFixed(4)})`);
    const lbl = document.getElementById('mon-zoom-label');
    if (lbl) lbl.textContent = `${Math.round(zoomLevel * 100)}%`;
  }

  /** Converte posição de cliente (px) → coordenadas do mundo (sem zoom/pan) */
  function _clientToWorld(clientX, clientY) {
    const rect = svgEl().getBoundingClientRect();
    const vb   = svgEl().viewBox.baseVal;
    const svgX = (clientX - rect.left) * (vb.width  / rect.width);
    const svgY = (clientY - rect.top)  * (vb.height / rect.height);
    return {
      x: (svgX - panX) / zoomLevel,
      y: (svgY - panY) / zoomLevel,
    };
  }

  /** Converte posição de cliente → coordenadas SVG (ainda com zoom/pan) */
  function _clientToSvg(clientX, clientY) {
    const rect = svgEl().getBoundingClientRect();
    const vb   = svgEl().viewBox.baseVal;
    return {
      x: (clientX - rect.left) * (vb.width  / rect.width),
      y: (clientY - rect.top)  * (vb.height / rect.height),
    };
  }

  // ── API pública de zoom ───────────────────────────────────
  function zoomIn() {
    const vb = svgEl().viewBox.baseVal;
    const cx = vb.width / 2, cy = vb.height / 2;
    _zoomAround(cx, cy, 1.25);
  }
  function zoomOut() {
    const vb = svgEl().viewBox.baseVal;
    const cx = vb.width / 2, cy = vb.height / 2;
    _zoomAround(cx, cy, 0.8);
  }
  function zoomReset() {
    zoomLevel = 1; panX = 0; panY = 0;
    _updateViewport();
  }

  /** Zoom centrado num ponto SVG (svgX, svgY) */
  function _zoomAround(svgX, svgY, factor) {
    const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoomLevel * factor));
    const ratio   = newZoom / zoomLevel;
    panX  = svgX - ratio * (svgX - panX);
    panY  = svgY - ratio * (svgY - panY);
    zoomLevel = newZoom;
    _updateViewport();
  }

  // ── Evento scroll (zoom centrado no cursor) ───────────────
  function svgWheel(e) {
    e.preventDefault();
    const pos    = _clientToSvg(e.clientX, e.clientY);
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    _zoomAround(pos.x, pos.y, factor);
  }

  // ── Evento mousedown (pan OU drag de nó) ─────────────────
  function svgMouseDown(e) {
    // Botão do meio OU Ctrl+botão esquerdo = iniciar pan
    if (e.button === 1 || (e.button === 0 && e.ctrlKey)) {
      e.preventDefault();
      isPanning = true;
      const pos = _clientToSvg(e.clientX, e.clientY);
      panStartX = pos.x - panX;
      panStartY = pos.y - panY;
      svgEl().classList.add('panning');
      return;
    }
  }

  function svgMouseMove(e) {
    if (isPanning) {
      const pos = _clientToSvg(e.clientX, e.clientY);
      panX = pos.x - panStartX;
      panY = pos.y - panStartY;
      _updateViewport();
      return;
    }
    if (!dragging) return;
    const world = _clientToWorld(e.clientX, e.clientY);
    const n     = nodes.find(nd => nd.id === dragging.nodeId);
    if (!n) return;
    n.x = world.x - dragging.offsetX;
    n.y = world.y - dragging.offsetY;
    _atualizarPosicaoNode(n);
  }

  function svgMouseUp(e) {
    if (isPanning) {
      isPanning = false;
      svgEl().classList.remove('panning');
      return;
    }
    if (dragging) {
      const el = document.getElementById(`mon-node-${dragging.nodeId}`);
      if (el) el.style.cursor = modoConectar ? 'crosshair' : 'grab';
      dragging = null;
    }
  }

  function svgClick(e) {
    if (modoConectar) return;
    // Clicou direto no SVG/grade = fecha painel de props
    const tag = e.target.tagName.toLowerCase();
    if (tag === 'svg' || tag === 'rect' || tag === 'pattern') fecharProps();
  }

  function svgDblClick(e) {
    // Duplo clique no fundo = reset zoom
    const tag = e.target.tagName.toLowerCase();
    if (tag === 'svg' || tag === 'rect') {
      zoomReset();
    }
  }

  // ─────────────────────────────────────────────────────────
  // INIT
  // ─────────────────────────────────────────────────────────
  function init() {
    clienteId = window.CLIENTE_ID || null;
    if (!clienteId) return;
    _updateViewport();   // aplica transform inicial (identidade)
    _carregarListaTopologias();
  }

  // ─────────────────────────────────────────────────────────
  // LISTA / CRUD DE TOPOLOGIAS
  // ─────────────────────────────────────────────────────────
  async function _carregarListaTopologias() {
    const data = await _get(`${URL.topoListar}?id=${clienteId}`);
    const sel  = document.getElementById('mon-topo-select');
    if (!sel) return;
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
    zoomReset();          // reseta zoom ao carregar nova topologia
    renderizarCanvas();
  }

  function _resetCanvas() {
    nodes = []; links = []; topoId = null; selected = null;
    const el = document.getElementById('mon-topo-nome');
    if (el) el.textContent = '';
    document.getElementById('mon-empty-state').style.display = 'flex';
    if (nodesGroup()) nodesGroup().innerHTML = '';
    if (linksGroup()) linksGroup().innerHTML = '';
    _setButtonsEnabled(false);
    _pararMonitor();
    const pp = document.getElementById('mon-props-panel');
    if (pp) pp.style.display = 'none';
    zoomReset();
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
    const pal  = document.getElementById('mon-palette');
    const bCon = document.getElementById('mon-btn-conectar');
    if (pal)  pal.style.display  = 'none';
    if (bCon) { bCon.style.display = 'none'; bCon.classList.remove('active'); }
    _atualizarLabelBtnConectar();
  }

  function _atualizarLabelBtnConectar() {
    const btn = document.getElementById('mon-btn-conectar');
    if (!btn) return;
    if (modoConectar && conectarFrom !== null) {
      btn.innerHTML = _iconeConectar() + ' Selecione o destino…';
    } else if (modoConectar) {
      btn.innerHTML = _iconeConectar() + ' Selecione a origem…';
    } else {
      btn.innerHTML = _iconeConectar() + ' Conectar';
    }
  }
  function _iconeConectar() {
    return '<svg viewBox="0 0 24 24" width="13" height="13"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  }

  // ─────────────────────────────────────────────────────────
  // RENDER
  // ─────────────────────────────────────────────────────────
  function renderizarCanvas() {
    const hasEl = nodes.length > 0;
    const es    = document.getElementById('mon-empty-state');
    if (es) es.style.display = hasEl ? 'none' : 'flex';
    if (linksGroup()) linksGroup().innerHTML = '';
    if (nodesGroup()) nodesGroup().innerHTML = '';
    links.forEach(_renderLink);
    nodes.forEach(_renderNode);
  }

  function _corStatus(nodeId) {
    const s = (statusCache.nodes || {})[nodeId];
    if (!s) return COR_STATUS.unconfigured;
    return COR_STATUS[s.status] || COR_STATUS.unknown;
  }

  function _renderNode(n) {
    const cor     = COR_TIPO[n.tipo] || '#888';
    const statusC = _corStatus(n.id);
    const g       = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('id',        `mon-node-${n.id}`);
    g.setAttribute('transform', `translate(${n.x},${n.y})`);
    g.style.cursor = modoEdicao ? (modoConectar ? 'crosshair' : 'grab') : 'pointer';

    // Anel de status
    const ring = _svgEl('circle');
    ring.setAttribute('r', '24');
    ring.setAttribute('fill', 'none');
    ring.setAttribute('stroke', modoConectar && conectarFrom === n.id ? '#7c3aed' : statusC);
    ring.setAttribute('stroke-width', '2.5');
    ring.setAttribute('opacity', '.7');
    g.appendChild(ring);

    // Fundo colorido
    const hex = cor.replace('#','');
    const r = parseInt(hex.substr(0,2),16);
    const gv= parseInt(hex.substr(2,2),16);
    const b = parseInt(hex.substr(4,2),16);
    const bg = _svgEl('circle');
    bg.setAttribute('r',            '18');
    bg.setAttribute('fill',         `rgba(${r},${gv},${b},0.13)`);
    bg.setAttribute('stroke',       cor);
    bg.setAttribute('stroke-width', '1.5');
    g.appendChild(bg);

    // Ícone
    const icon = _svgEl('g');
    icon.innerHTML = _iconePath(n.tipo, cor);
    g.appendChild(icon);

    // Label principal
    const txt = _svgEl('text');
    txt.setAttribute('y',           '38');
    txt.setAttribute('text-anchor', 'middle');
    txt.setAttribute('fill',        '#e2e8f0');
    txt.setAttribute('font-size',   '11');
    txt.setAttribute('font-family', 'system-ui,sans-serif');
    txt.textContent = n.label;
    g.appendChild(txt);

    // Sub-label (hostname Zabbix)
    if (n.zabbix_hostname) {
      const sub = _svgEl('text');
      sub.setAttribute('y',           '50');
      sub.setAttribute('text-anchor', 'middle');
      sub.setAttribute('fill',        '#64748b');
      sub.setAttribute('font-size',   '9');
      sub.setAttribute('font-family', 'system-ui,sans-serif');
      sub.textContent = n.zabbix_hostname;
      g.appendChild(sub);
    }

    // Hit-box transparente (facilita clique)
    const hit = _svgEl('circle');
    hit.setAttribute('r',    '28');
    hit.setAttribute('fill', 'transparent');
    g.appendChild(hit);

    // ── Eventos do nó ──
    g.addEventListener('mousedown', e => {
      e.stopPropagation();
      if (isPanning) return;
      if (modoConectar) { handleConectarClick(n.id); return; }
      if (modoEdicao) {
        // Inicia drag — calcula offset em coordenadas mundo
        const world     = _clientToWorld(e.clientX, e.clientY);
        dragging = { nodeId: n.id, offsetX: world.x - n.x, offsetY: world.y - n.y };
        g.style.cursor  = 'grabbing';
        return;
      }
      selecionarElemento('node', n.id);
    });
    g.addEventListener('dblclick', e => {
      e.stopPropagation();
      if (!modoConectar) abrirModalNode(n.id);
    });

    nodesGroup().appendChild(g);
  }

  function _renderLink(lk) {
    const src = nodes.find(nd => nd.id === lk.source);
    const dst = nodes.find(nd => nd.id === lk.target);
    if (!src || !dst) return;

    const ls     = (statusCache.links || {})[lk.id];
    const cor    = ls ? (COR_STATUS[ls.status] || '#444') : '#444';
    const marker = ls?.status === 'up'   ? 'arr-green'
                 : ls?.status === 'down' ? 'arr-red' : 'arr-gray';

    const g  = _svgEl('g');
    g.setAttribute('id', `mon-link-${lk.id}`);

    // Hit-box larga
    const hb = _svgEl('line');
    hb.setAttribute('x1', src.x); hb.setAttribute('y1', src.y);
    hb.setAttribute('x2', dst.x); hb.setAttribute('y2', dst.y);
    hb.setAttribute('stroke', 'transparent'); hb.setAttribute('stroke-width', '14');
    hb.style.cursor = 'pointer';
    g.appendChild(hb);

    // Linha visível
    const line = _svgEl('line');
    line.setAttribute('x1', src.x); line.setAttribute('y1', src.y);
    line.setAttribute('x2', dst.x); line.setAttribute('y2', dst.y);
    line.setAttribute('stroke',       cor);
    line.setAttribute('stroke-width', '2');
    line.setAttribute('marker-end',   `url(#${marker})`);
    g.appendChild(line);

    const mx = (src.x + dst.x) / 2;
    const my = (src.y + dst.y) / 2;

    // Label do enlace
    if (lk.label) {
      const bg = _svgEl('rect');
      bg.setAttribute('x', mx - 32); bg.setAttribute('y', my - 12);
      bg.setAttribute('width', 64);  bg.setAttribute('height', 16);
      bg.setAttribute('rx', 4);      bg.setAttribute('fill', '#1e293b');
      g.appendChild(bg);
      const tl = _svgEl('text');
      tl.setAttribute('x', mx); tl.setAttribute('y', my - 1);
      tl.setAttribute('text-anchor', 'middle'); tl.setAttribute('fill', '#94a3b8');
      tl.setAttribute('font-size', '9'); tl.setAttribute('font-family', 'system-ui,sans-serif');
      tl.textContent = lk.label;
      g.appendChild(tl);
    }

    // Tráfego em tempo real
    if (ls?.traffic_in) {
      const t = _svgEl('text');
      t.setAttribute('x', mx + 4); t.setAttribute('y', my + 14);
      t.setAttribute('fill','#00ff88'); t.setAttribute('font-size','8');
      t.setAttribute('font-family','system-ui,sans-serif');
      t.textContent = `↓ ${ls.traffic_in}`;
      g.appendChild(t);
    }
    if (ls?.traffic_out) {
      const t = _svgEl('text');
      t.setAttribute('x', mx + 4); t.setAttribute('y', my + 24);
      t.setAttribute('fill','#00d9ff'); t.setAttribute('font-size','8');
      t.setAttribute('font-family','system-ui,sans-serif');
      t.textContent = `↑ ${ls.traffic_out}`;
      g.appendChild(t);
    }

    // Badge DOWN
    if (ls?.status === 'down') {
      const bg = _svgEl('rect');
      bg.setAttribute('x', mx - 18); bg.setAttribute('y', my - 9);
      bg.setAttribute('width', 36);  bg.setAttribute('height', 14);
      bg.setAttribute('rx', 3);      bg.setAttribute('fill', '#ef4444');
      g.appendChild(bg);
      const dt = _svgEl('text');
      dt.setAttribute('x', mx); dt.setAttribute('y', my + 1);
      dt.setAttribute('text-anchor','middle'); dt.setAttribute('fill','#fff');
      dt.setAttribute('font-size','8'); dt.setAttribute('font-weight','bold');
      dt.setAttribute('font-family','system-ui,sans-serif');
      dt.textContent = 'DOWN';
      g.appendChild(dt);
    }

    g.addEventListener('click', e => {
      e.stopPropagation();
      if (!modoConectar) selecionarElemento('link', lk.id);
    });
    g.addEventListener('dblclick', e => {
      e.stopPropagation();
      if (!modoConectar) abrirModalLink(lk.id);
    });

    linksGroup().appendChild(g);
  }

  function _svgEl(tag) { return document.createElementNS('http://www.w3.org/2000/svg', tag); }

  function _atualizarPosicaoNode(n) {
    const el = document.getElementById(`mon-node-${n.id}`);
    if (el) el.setAttribute('transform', `translate(${n.x},${n.y})`);
    links.filter(l => l.source === n.id || l.target === n.id).forEach(l => {
      const le = document.getElementById(`mon-link-${l.id}`);
      if (le) le.remove();
      _renderLink(l);
    });
  }

  // ─────────────────────────────────────────────────────────
  // ARRASTAR (paleta → canvas)
  // ─────────────────────────────────────────────────────────
  function paletteDragStart(e) {
    e.dataTransfer.setData('mon-tipo', e.currentTarget.dataset.tipo);
  }

  function svgDrop(e) {
    e.preventDefault();
    const tipo = e.dataTransfer.getData('mon-tipo');
    if (!tipo || !modoEdicao) return;
    const pos  = _clientToWorld(e.clientX, e.clientY);
    const id   = nextId++;
    nodes.push({ id, tipo, label: tipo, x: pos.x, y: pos.y,
                 zabbix_hostid: null, zabbix_hostname: null });
    _renderNode(nodes[nodes.length - 1]);
    document.getElementById('mon-empty-state').style.display = 'none';
  }

  // ─────────────────────────────────────────────────────────
  // MODO CONECTAR
  // ─────────────────────────────────────────────────────────
  function toggleConectar() {
    modoConectar = !modoConectar;
    conectarFrom = null;
    const btn = document.getElementById('mon-btn-conectar');
    btn.classList.toggle('active', modoConectar);
    _atualizarLabelBtnConectar();
    nodes.forEach(n => {
      const el = document.getElementById(`mon-node-${n.id}`);
      if (el) el.style.cursor = modoConectar ? 'crosshair' : 'grab';
    });
    if (!modoConectar) renderizarCanvas();
  }

  function handleConectarClick(nodeId) {
    if (!conectarFrom) {
      conectarFrom = nodeId;
      const el = document.getElementById(`mon-node-${nodeId}`);
      el?.querySelector('circle')?.setAttribute('stroke', '#7c3aed');
      _atualizarLabelBtnConectar();
      return;
    }
    if (conectarFrom === nodeId) {
      const el = document.getElementById(`mon-node-${nodeId}`);
      el?.querySelector('circle')?.setAttribute('stroke', _corStatus(nodeId));
      conectarFrom = null;
      _atualizarLabelBtnConectar();
      return;
    }
    const from   = conectarFrom;
    const existe = links.find(l =>
      (l.source===from&&l.target===nodeId)||(l.source===nodeId&&l.target===from));
    if (!existe) {
      links.push({ id: nextId++, source: from, target: nodeId,
                   label:'', itemid_in:null, itemid_out:null, itemid_status:null });
      _renderLink(links[links.length-1]);
      _toast('Link criado!', 'success');
    } else {
      _toast('Já existe um link entre esses nós.', 'error');
    }
    const elFrom = document.getElementById(`mon-node-${from}`);
    elFrom?.querySelector('circle')?.setAttribute('stroke', _corStatus(from));
    conectarFrom = null;
    _atualizarLabelBtnConectar();
  }

  // ─────────────────────────────────────────────────────────
  // SELEÇÃO / PROPS PANEL
  // ─────────────────────────────────────────────────────────
  function selecionarElemento(type, id) {
    selected = { type, id };
    const panel = document.getElementById('mon-props-panel');
    const title = document.getElementById('mon-props-title');
    const body  = document.getElementById('mon-props-body');
    if (!panel) return;
    panel.style.display = 'flex';

    if (type === 'node') {
      const n  = nodes.find(nd => nd.id === id);
      const ls = (statusCache.nodes || {})[id];
      title.textContent = n.label;
      body.innerHTML = `
        <div style="margin-bottom:8px;">
          <span style="color:${COR_TIPO[n.tipo]||'#888'};font-weight:600;">${n.tipo}</span>
        </div>
        ${n.zabbix_hostname
          ? `<div style="margin-bottom:4px;">Host: <b style="color:#e2e8f0">${n.zabbix_hostname}</b></div>`
          : '<div style="color:#64748b;">Host não configurado</div>'}
        ${ls ? `<div style="margin-top:8px;">Status: <b style="color:${COR_STATUS[ls.status]||'#888'}">${ls.status}</b></div>` : ''}
        <button class="mon-btn mon-btn-primary"
                style="margin-top:12px;width:100%;"
                onclick="MON.abrirModalNode(${id})">Editar nó</button>`;
    } else {
      const l  = links.find(lk => lk.id === id);
      const ls = (statusCache.links || {})[id];
      title.textContent = l.label || 'Enlace';
      body.innerHTML = `
        ${ls ? `
          <div style="margin-bottom:8px;">Status: <b style="color:${COR_STATUS[ls.status]||'#888'}">${ls.status}</b></div>
          ${ls.traffic_in  ? `<div style="color:#00ff88">↓ In: ${ls.traffic_in}</div>`  : ''}
          ${ls.traffic_out ? `<div style="color:#00d9ff">↑ Out: ${ls.traffic_out}</div>` : ''}
        ` : '<div style="color:#64748b;">Items não configurados</div>'}
        <button class="mon-btn mon-btn-primary"
                style="margin-top:12px;width:100%;"
                onclick="MON.abrirModalLink(${id})">Editar enlace</button>`;
    }
  }

  function fecharProps() {
    selected = null;
    const pp = document.getElementById('mon-props-panel');
    if (pp) pp.style.display = 'none';
  }

  // ─────────────────────────────────────────────────────────
  // MODOS EDIÇÃO / MONITORAÇÃO
  // ─────────────────────────────────────────────────────────
  function entrarModoEdicao() {
    if (!topoId) return;
    _pararMonitor();
    modoEdicao = true; modoConectar = false; conectarFrom = null;
    const pal  = document.getElementById('mon-palette');
    const bCon = document.getElementById('mon-btn-conectar');
    if (pal)  { pal.style.display = 'flex'; pal.style.flexDirection = 'column'; }
    if (bCon) { bCon.style.display = 'inline-flex'; bCon.classList.remove('active'); }
    document.getElementById('mon-live-indicator').style.display = 'none';
    _atualizarLabelBtnConectar();
    renderizarCanvas();
  }

  function entrarModoMonitor() {
    if (!topoId) return;
    _resetModoEdicao();
    _iniciarMonitor();
  }

  function _iniciarMonitor() {
    _pararMonitor();
    buscarStatusAgora();
    monitorInterval = setInterval(buscarStatusAgora, 10000);
    document.getElementById('mon-live-indicator').style.display = 'inline-flex';
  }

  function _pararMonitor() {
    clearInterval(monitorInterval);
    monitorInterval = null;
    const li = document.getElementById('mon-live-indicator');
    if (li) li.style.display = 'none';
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
    nodes.forEach(n => {
      const ring = document.querySelector(`#mon-node-${n.id} circle`);
      if (ring) ring.setAttribute('stroke', _corStatus(n.id));
    });
    linksGroup().innerHTML = '';
    links.forEach(_renderLink);
  }

  // ─────────────────────────────────────────────────────────
  // SALVAR / CRIAR / DELETAR TOPOLOGIA
  // ─────────────────────────────────────────────────────────
  async function salvarTopologia() {
    if (!topoId) return;
    const btn = document.getElementById('mon-btn-salvar');
    if (btn) { btn.disabled = true; btn.textContent = 'Salvando…'; }
    try {
      const data = await _post(URL.topoSalvar, {
        topo_id: topoId, cliente_id: clienteId,
        nodes: nodes.map(n => ({ id:n.id, tipo:n.tipo, label:n.label, x:n.x, y:n.y,
                                  zabbix_hostid:n.zabbix_hostid, zabbix_hostname:n.zabbix_hostname })),
        links: links.map(l => ({ id:l.id, source:l.source, target:l.target, label:l.label,
                                  itemid_in:l.itemid_in, itemid_out:l.itemid_out, itemid_status:l.itemid_status })),
      });
      if (data.error) alert(`Erro: ${data.error}`);
      else { _toast('Topologia salva!', 'success'); await carregarTopologiaSelecionada(); }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Salvar'; }
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
      cliente_id: clienteId, nome,
      descricao: document.getElementById('mon-nova-desc').value,
    });
    if (data.error) { alert(data.error); return; }
    fecharModal('mon-modal-nova');
    await _carregarListaTopologias();
    topoId = data.id;
    document.getElementById('mon-topo-select').value = topoId;
    nodes = []; links = [];
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

  // ─────────────────────────────────────────────────────────
  // MODAL NÓ
  // ─────────────────────────────────────────────────────────
  function abrirModalNode(nodeId) {
    const n = nodes.find(nd => nd.id === nodeId);
    if (!n) return;
    _editingNode = nodeId;
    document.getElementById('mon-nd-label').value    = n.label;
    document.getElementById('mon-nd-tipo').value     = n.tipo;
    document.getElementById('mon-nd-search').value   = n.zabbix_hostname || '';
    document.getElementById('mon-nd-hostid').value   = n.zabbix_hostid   || '';
    document.getElementById('mon-nd-hostname').value = n.zabbix_hostname || '';
    document.getElementById('mon-nd-results').style.display = 'none';
    const badge = document.getElementById('mon-nd-host-selecionado');
    if (n.zabbix_hostname) {
      badge.textContent   = `✓ ${n.zabbix_hostname} (${n.zabbix_hostid})`;
      badge.style.display = 'block';
    } else { badge.style.display = 'none'; }
    _abrirModal('mon-modal-node');
  }

  function buscarHostDebounce(query) {
    clearTimeout(_hostTimer);
    _hostTimer = setTimeout(() => _buscarHosts(query, 'mon-nd-results',
      (id, name, host) => selecionarHost(id, name, host)), 300);
  }

  async function _buscarHosts(query, resultBoxId, onSelect) {
    const box = document.getElementById(resultBoxId);
    if (!query || query.length < 2) { if(box) box.style.display='none'; return; }
    const data = await _get(`${URL.zbxHosts}?id=${clienteId}&q=${encodeURIComponent(query)}`);
    if (!box) return;
    if (data.error || !data.hosts?.length) {
      box.innerHTML = '<div class="mon-search-item"><span style="color:#64748b;">Nenhum host encontrado</span></div>';
    } else {
      hostsCache    = data.hosts;
      box.innerHTML = data.hosts.map(h => `
        <div class="mon-search-item"
             onclick="MON.selecionarHost('${h.hostid}','${(h.name||h.host).replace(/'/g,"\\'")}','${(h.host||'').replace(/'/g,"\\'")}')">
          <span>${h.name || h.host}</span>
          <small>${h.host} — ${h.ip} — <b style="color:${h.available==='up'?'#00ff88':'#ef4444'}">${h.available}</b></small>
        </div>`).join('');
    }
    box.style.display = 'block';
  }

  function selecionarHost(hostid, name, host) {
    document.getElementById('mon-nd-hostid').value   = hostid;
    document.getElementById('mon-nd-hostname').value = name || host;
    document.getElementById('mon-nd-search').value   = name || host;
    document.getElementById('mon-nd-results').style.display = 'none';
    const badge = document.getElementById('mon-nd-host-selecionado');
    badge.textContent   = `✓ ${name||host} (${hostid})`;
    badge.style.display = 'block';
  }

  function salvarEdicaoNode() {
    const n = nodes.find(nd => nd.id === _editingNode);
    if (!n) return;
    n.label           = document.getElementById('mon-nd-label').value.trim()    || n.label;
    n.tipo            = document.getElementById('mon-nd-tipo').value;
    n.zabbix_hostid   = document.getElementById('mon-nd-hostid').value           || null;
    n.zabbix_hostname = document.getElementById('mon-nd-hostname').value         || null;
    fecharModal('mon-modal-node');
    renderizarCanvas();
  }

  function deletarNodeSelecionado() {
    if (!_editingNode) return;
    nodes = nodes.filter(n => n.id !== _editingNode);
    links = links.filter(l => l.source !== _editingNode && l.target !== _editingNode);
    fecharModal('mon-modal-node');
    renderizarCanvas();
  }

  // ─────────────────────────────────────────────────────────
  // MODAL LINK
  // ─────────────────────────────────────────────────────────
  async function abrirModalLink(linkId) {
    const l = links.find(lk => lk.id === linkId);
    if (!l) return;
    _editingLink = linkId;
    document.getElementById('mon-lk-label').value       = l.label         || '';
    document.getElementById('mon-lk-item-in').value     = l.itemid_in     || '';
    document.getElementById('mon-lk-item-out').value    = l.itemid_out    || '';
    document.getElementById('mon-lk-item-status').value = l.itemid_status || '';
    document.getElementById('mon-lk-items-preview').style.display = 'none';

    const hostNodes = nodes.filter(n => n.zabbix_hostid);
    ['mon-lk-src-host','mon-lk-dst-host'].forEach((selId, idx) => {
      const sel   = document.getElementById(selId);
      const currId = idx === 0
        ? nodes.find(n => n.id === l.source)?.zabbix_hostid
        : nodes.find(n => n.id === l.target)?.zabbix_hostid;
      sel.innerHTML = '<option value="">— nenhum —</option>';
      hostNodes.forEach(n => {
        const opt      = document.createElement('option');
        opt.value       = n.zabbix_hostid;
        opt.textContent = `${n.label} (${n.zabbix_hostname})`;
        if (n.zabbix_hostid === currId) opt.selected = true;
        sel.appendChild(opt);
      });
    });

    const sel = document.getElementById('mon-lk-iface');
    sel.innerHTML = '<option value="">Carregando…</option>';
    sel.disabled  = true;
    _abrirModal('mon-modal-link');

    const srcId = nodes.find(n => n.id === l.source)?.zabbix_hostid;
    if (srcId) {
      document.getElementById('mon-lk-src-host').value = srcId;
      await carregarInterfacesLink('src');
    } else {
      sel.innerHTML = '<option value="">— Selecione um host —</option>';
      sel.disabled  = false;
    }
  }

  async function carregarInterfacesLink(side) {
    const hostId = document.getElementById(
      side==='src' ? 'mon-lk-src-host' : 'mon-lk-dst-host'
    ).value;
    const sel = document.getElementById('mon-lk-iface');
    if (!hostId) { sel.innerHTML='<option value="">— Selecione um host —</option>'; return; }
    if (ifacesCache[hostId]) { _preencherSelectIfaces(sel, hostId, ifacesCache[hostId]); return; }
    sel.innerHTML = '<option value="">Buscando…</option>'; sel.disabled = true;
    try {
      const data = await _get(`${URL.zbxIfaces}?cliente_id=${clienteId}&host_id=${hostId}`);
      if (data.error || !data.interfaces?.length) {
        sel.innerHTML = `<option value="">${data.error||'Nenhuma interface encontrada'}</option>`;
      } else {
        ifacesCache[hostId] = data.interfaces;
        _preencherSelectIfaces(sel, hostId, data.interfaces);
      }
    } catch { sel.innerHTML='<option value="">Erro ao buscar</option>'; }
    sel.disabled = false;
  }

  function _preencherSelectIfaces(sel, hostId, ifaces) {
    sel.innerHTML = '<option value="">— Selecione a interface —</option>';
    ifaces.forEach(i => {
      const opt       = document.createElement('option');
      opt.value       = JSON.stringify({ hostId, iface: i.name });
      opt.textContent = i.name;
      sel.appendChild(opt);
    });
    sel.disabled = false;
  }

  function preencherItemsLink() {
    const raw = document.getElementById('mon-lk-iface').value;
    if (!raw) return;
    const { hostId, iface } = JSON.parse(raw);
    const ifaceData = ifacesCache[hostId]?.find(i => i.name === iface);
    if (!ifaceData) return;
    document.getElementById('mon-lk-item-in').value     = ifaceData.in_id     || '';
    document.getElementById('mon-lk-item-out').value    = ifaceData.out_id    || '';
    document.getElementById('mon-lk-item-status').value = ifaceData.status_id || '';
    const prev = document.getElementById('mon-lk-items-preview');
    prev.style.display = 'block';
    prev.innerHTML = `
      <b>Items detectados:</b><br>
      In: ${ifaceData.in_id     ? `<b style="color:#00ff88">${ifaceData.in_id}</b>`     : '<span style="color:#64748b">—</span>'}<br>
      Out: ${ifaceData.out_id   ? `<b style="color:#00d9ff">${ifaceData.out_id}</b>`    : '<span style="color:#64748b">—</span>'}<br>
      Status: ${ifaceData.status_id ? `<b style="color:#fbbf24">${ifaceData.status_id}</b>` : '<span style="color:#64748b">—</span>'}`;
  }

  function salvarEdicaoLink() {
    const l = links.find(lk => lk.id === _editingLink);
    if (!l) return;
    l.label         = document.getElementById('mon-lk-label').value.trim();
    l.itemid_in     = document.getElementById('mon-lk-item-in').value.trim()     || null;
    l.itemid_out    = document.getElementById('mon-lk-item-out').value.trim()    || null;
    l.itemid_status = document.getElementById('mon-lk-item-status').value.trim() || null;
    fecharModal('mon-modal-link');
    renderizarCanvas();
  }

  function deletarLinkSelecionado() {
    if (!_editingLink) return;
    links = links.filter(l => l.id !== _editingLink);
    fecharModal('mon-modal-link');
    renderizarCanvas();
  }

  // ─────────────────────────────────────────────────────────
  // ZABBIX CONFIG
  // ─────────────────────────────────────────────────────────
  async function abrirModalZabbix() {
    document.getElementById('mon-zbx-resultado').style.display = 'none';
    const data = await _get(`${URL.zbxBuscar}?id=${clienteId}`);
    if (data.existe) {
      document.getElementById('mon-zbx-url').value  = data.url     || '';
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
    res.textContent = 'Testando…'; res.className='mon-alert'; res.style.display='block';
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

  // ─────────────────────────────────────────────────────────
  // HELPERS
  // ─────────────────────────────────────────────────────────
  function _iconePath(tipo, c) {
    switch (tipo) {
      case 'router':   return `<path d="M-8 0h16M0 -8v16M-6-6l12 12M6-6L-6 6" stroke="${c}" stroke-width="2" stroke-linecap="round" fill="none"/>`;
      case 'switch':   return `<rect x="-10" y="-5" width="20" height="10" rx="2" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="-5" cy="0" r="2" fill="${c}"/><circle cx="0" cy="0" r="2" fill="${c}"/><circle cx="5" cy="0" r="2" fill="${c}"/>`;
      case 'firewall': return `<path d="M0,-12 L10,-6 V4 C10,10 5,14 0,16 C-5,14-10,10-10,4 V-6 Z" fill="none" stroke="${c}" stroke-width="1.5"/><path d="M-3,2 L0,5 L4,-1" stroke="${c}" stroke-width="2" stroke-linecap="round" fill="none"/>`;
      case 'server':   return `<rect x="-10" y="-8" width="20" height="6" rx="1" fill="none" stroke="${c}" stroke-width="1.5"/><rect x="-10" y="2" width="20" height="6" rx="1" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="-6" cy="-5" r="1.5" fill="${c}"/><circle cx="-6" cy="5" r="1.5" fill="${c}"/>`;
      case 'ap':       return `<path d="M-10,4 Q0,-6 10,4" stroke="${c}" stroke-width="2" fill="none"/><path d="M-6,8 Q0,1 6,8" stroke="${c}" stroke-width="2" fill="none"/><circle cx="0" cy="11" r="2.5" fill="${c}"/>`;
      case 'cloud':    return `<path d="M-10,5 a6,6 0 0,1 0-12 a5,5 0 0,1 9-2 a6,6 0 1 1 1,14z" fill="none" stroke="${c}" stroke-width="1.5"/>`;
      case 'endpoint': return `<rect x="-10" y="-8" width="20" height="12" rx="1" fill="none" stroke="${c}" stroke-width="1.5"/><path d="M-5,4 h10 M0,4 v4" stroke="${c}" stroke-width="1.5" stroke-linecap="round"/>`;
      default:         return `<circle r="8" fill="none" stroke="${c}" stroke-width="1.5"/>`;
    }
  }

  function _abrirModal(id) { const el=document.getElementById(id); if(el) el.style.display='flex'; }
  function fecharModal(id) { const el=document.getElementById(id); if(el) el.style.display='none'; }

  function _toast(msg, type='success') {
    const el = document.createElement('div');
    el.className = `mon-alert ${type}`;
    el.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;min-width:220px;box-shadow:0 8px 24px rgba(0,0,0,.4);';
    el.textContent   = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  // ─────────────────────────────────────────────────────────
  // API PÚBLICA
  // ─────────────────────────────────────────────────────────
  return {
    // lifecycle
    init,
    renderizarCanvas,
    // SVG events
    svgMouseDown, svgMouseMove, svgMouseUp,
    svgClick, svgDblClick, svgWheel,
    svgDrop, paletteDragStart,
    // zoom API (chamada pelos botões da toolbar)
    zoomIn, zoomOut, zoomReset,
    // topologias
    carregarTopologiaSelecionada,
    abrirModalNova, criarTopologia, salvarTopologia, deletarTopologia,
    // modos
    entrarModoEdicao, entrarModoMonitor, toggleConectar,
    buscarStatusAgora,
    // nós
    abrirModalNode, buscarHostDebounce, selecionarHost,
    salvarEdicaoNode, deletarNodeSelecionado,
    // links
    abrirModalLink, carregarInterfacesLink, preencherItemsLink,
    salvarEdicaoLink, deletarLinkSelecionado,
    // zabbix
    abrirModalZabbix, testarZabbix, salvarZabbixConfig,
    // utils
    fecharModal, fecharProps,
  };
})();

window.MON = MON;
document.addEventListener('DOMContentLoaded', () => { MON.init(); });