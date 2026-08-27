// Ordem das faixas na importação de hosts do CRM: hierarquia da rede lida de
// cima pra baixo (trânsito → core → distribuição → acesso → cliente). Tipo que
// não estiver aqui entra depois de todos, na ordem em que apareceu.
const TOPO_IMPORT_TIERS = [
  'internet', 'ix', 'cloud',
  'router', 'firewall', 'cgnat', 'dwdm',
  'switch_l3', 'switch_l2',
  'olt', 'splitter', 'onu', 'cpe',
  'radio', 'ap',
  'server', 'vm', 'host',
];

// Ordem crescente de "peso" das interfaces — usada só ao agrupar hosts, para
// escolher qual dos enlaces do vizinho vira o enlace único que liga ele ao
// ícone do grupo (fica o mais rápido; os demais viram o rótulo "N enlaces").
const TOPO_ORDEM_IFACE = [
  'other', '100m', 'wifi', 'mw', '1g', 'sfp', 'gpon', 'xpon',
  '10g', 'sfp+', '20g', '30g', '40g', '50g', '100g',
];

class TopoEditor {
  constructor(cfg) {
    this.clienteId = cfg.clienteId;
    this.diagramaId = cfg.diagramaId || null;
    this.nodes = [];
    this.links = [];
    this.history = [];
    this.histIdx = -1;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.snap = true;
    this.showGrid = true;
    this.effectsOn = true; // fluxo animado nos links + pulso nos nodes do CRM
    this.connectMode = false;
    this.connectSrc = null;
    this.dragging = null;
    this.panning = null;
    this.selected = null;
    this.dirty = false;
    this.wpDrag = null;   // {linkId, wpIdx} — arrastar waypoint
    this.areaResizing = null; // {id, corner, fx, fy} — arrastar canto de uma Área
    this._propsGen = 0;   // invalida fetch de interfaces em voo ao trocar seleção
    this._ifaceCache = {}; // acesso_id -> [interfaces] (extraídas do backup)
    this._l2vpn = null;    // estado do modal de VSI/VPLS/VPWS/L2VC quando aberto
    this.selectedNodes = new Set(); // multi-seleção de nodes (seleção em área / shift+clique)
    this.rubberBand = null;         // {x0,y0,x1,y1} durante o arraste do laço de seleção
    this.groupDragging = null;      // {startX,startY,positions:{id:{x,y}}} arrasto em grupo
    this.areaSelectMode = false;    // toggle do botão "Área" — troca pan por laço de seleção
    // ── Estado da navegação (pan/zoom/arraste) ──────────────────────────────
    // O mapa é um SVG só: qualquer movimento rasteriza a cena inteira de novo,
    // com todos os drop-shadows, blurs e fluxos animados junto. Estes campos
    // seguram o desenho em 1x por frame (_moveRaf/_vpRaf) e desligam os
    // efeitos decorativos enquanto a mão está no mapa (_navBusy).
    this._moveEvt = null;   // último mousemove recebido, aplicado no próximo frame
    this._moveRaf = null;
    this._vpRaf = null;     // frame agendado para o transform do viewport
    this._navOn = false;    // body.nav-busy ligado
    this._navTimer = null;
    this._navSujo = false;  // links desenhados em modo leve, redesenhar ao parar
    this._rect = null;      // getBoundingClientRect do SVG, válido por 1 frame
    this.svg = document.getElementById('canvas-svg');
    this.vp = document.getElementById('viewport');
    this.nodesLayer = document.getElementById('nodes-layer');
    this.linksLayer = document.getElementById('links-layer');
    this.preview = document.getElementById('connect-preview');
    this.rubberEl = document.getElementById('rubber-band');

    // Camada para handles de waypoints (acima dos links, abaixo dos nós)
    this.handlesLayer = document.createElementNS('http://www.w3.org/2000/svg','g');
    this.handlesLayer.id = 'handles-layer';
    this.vp.insertBefore(this.handlesLayer, this.nodesLayer);

    // Camada das Áreas — zonas de documentação desenhadas ATRÁS de tudo (fica
    // antes de links-layer no viewport, então links e nodes são pintados por
    // cima da área que os agrupa).
    this.areasLayer = document.createElementNS('http://www.w3.org/2000/svg','g');
    this.areasLayer.id = 'areas-layer';
    this.vp.insertBefore(this.areasLayer, this.linksLayer);

    // Retoca a animação de fade do painel de propriedades (`#props-fade` no CSS)
    // toda vez que o conteúdo troca — só setar innerHTML de novo no mesmo nó
    // não reinicia uma CSS animation já concluída, então sem isso o fade só
    // tocava uma vez (no load da página) e nunca mais ao trocar de seleção.
    const propsBody = document.getElementById('props-body');
    if (propsBody) {
      new MutationObserver(() => {
        propsBody.style.animation = 'none';
        void propsBody.offsetWidth; // força reflow antes de restaurar
        propsBody.style.animation = '';
      }).observe(propsBody, {childList: true});
    }

    this._bindEvents();
    this._buildPalette();
  }

  _id() { return 'n' + Date.now() + Math.random().toString(36).slice(2,6); }

  _snap(v) { return this.snap ? Math.round(v/20)*20 : v; }

  /** Modificador de seleção múltipla: **Ctrl** (o que a equipe usa), com Shift
   *  e Cmd valendo como alias — Shift porque era o único jeito até 2026-08-25
   *  e virou dedo de quem já usava, Cmd (metaKey) pro mesmo gesto no Mac. */
  _ehAditivo(e) { return e.ctrlKey || e.metaKey || e.shiftKey; }

  /** true se o id aponta para uma Área (zona de documentação, não device). */
  _ehArea(id) { const n = this.nodes.find(x => x.id === id); return !!n && n.type === 'area'; }

  /** Retângulo do canvas, cacheado por um frame: o mousemove pedia
   *  getBoundingClientRect a cada evento, e cada chamada força o navegador a
   *  recalcular layout no meio do arraste. */
  _svgRect() {
    if (!this._rect) {
      this._rect = this.svg.getBoundingClientRect();
      requestAnimationFrame(() => { this._rect = null; });
    }
    return this._rect;
  }

  _svgPoint(e) {
    const r = this._svgRect();
    return {
      x: (e.clientX - r.left - this.panX) / this.zoom,
      y: (e.clientY - r.top  - this.panY) / this.zoom
    };
  }

  _buildPalette() {
    const pal = document.getElementById('palette');
    // Agrupa por TOPO_DEVICES[type].group (metadado só de exibição — não
    // afeta o modelo de dados do node nem o mapeamento automático função→tipo).
    // Preserva a ordem de definição em topo_engine.js dentro de cada grupo.
    const grupos = new Map();
    Object.entries(TOPO_DEVICES).forEach(([type, def]) => {
      // `grupo` não entra na paleta: só existe como resultado da ação
      // "Agrupar" (precisa de membros e de um sub-mapa pra fazer sentido).
      if (type === 'grupo') return;
      const g = def.group || 'Outros';
      if (!grupos.has(g)) grupos.set(g, []);
      grupos.get(g).push([type, def]);
    });

    grupos.forEach((itens, grupo) => {
      const header = document.createElement('div');
      header.className = 'pal-group-title';
      header.dataset.grupo = grupo;
      header.textContent = grupo;
      pal.appendChild(header);

      itens.forEach(([type, def]) => {
        const item = document.createElement('div');
        item.className = 'pal-item';
        item.draggable = true;
        item.dataset.type = type;
        // Chave de busca da paleta: rótulo + tipo interno + grupo, tudo sem
        // acento, pra "radio" achar "Rádio PTP" e "ftth" achar OLT/ONU/Splitter.
        item.dataset.busca = this._semAcento(`${def.label} ${type} ${grupo}`);
        item.title = `Arraste para o canvas — ${def.label}`;
        item.innerHTML = `
          <div class="pal-icon" style="background:${def.color}1f;color:${def.color}">
            <svg viewBox="0 0 48 48">${TOPO_ICONS[def.icon]||''}</svg>
          </div>
          <div><div class="pal-label">${def.label}</div></div>`;
        item.addEventListener('dragstart', e => {
          e.dataTransfer.setData('device-type', type);
        });
        pal.appendChild(item);
      });
    });

    const vazio = document.createElement('div');
    vazio.className = 'pal-vazio';
    vazio.id = 'pal-vazio';
    vazio.textContent = 'Nenhum dispositivo com esse nome';
    vazio.style.display = 'none';
    pal.appendChild(vazio);

    const busca = document.getElementById('pal-search');
    if (busca) busca.addEventListener('input', () => this._filtrarPaleta(busca.value));
  }

  _semAcento(s) {
    return (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }

  /** Filtra a paleta pelo texto digitado, escondendo também o título de um
   *  grupo que ficou sem nenhum item visível (senão sobra cabeçalho órfão). */
  _filtrarPaleta(termo) {
    const q = this._semAcento(termo).trim();
    const pal = document.getElementById('palette');
    let visiveis = 0;
    let grupoAtual = null, grupoTem = false;
    const fecharGrupo = () => { if (grupoAtual) grupoAtual.style.display = grupoTem ? '' : 'none'; };

    pal.querySelectorAll('.pal-group-title,.pal-item').forEach(el => {
      if (el.classList.contains('pal-group-title')) {
        fecharGrupo();
        grupoAtual = el; grupoTem = false;
        return;
      }
      const ok = !q || el.dataset.busca.includes(q);
      el.style.display = ok ? '' : 'none';
      if (ok) { grupoTem = true; visiveis++; }
    });
    fecharGrupo();
    const vazio = document.getElementById('pal-vazio');
    if (vazio) vazio.style.display = visiveis ? 'none' : '';
  }

  _bindEvents() {
    const svg = this.svg;
    const wrap = document.getElementById('canvas-wrap');

    wrap.addEventListener('dragover', e => e.preventDefault());
    wrap.addEventListener('drop', e => {
      e.preventDefault();
      const type = e.dataTransfer.getData('device-type');
      if (!type) return;
      const pt = this._svgPoint(e);
      if (type === 'area') {
        // Área nasce grande (é um contêiner) e já selecionada, pra pessoa
        // digitar o nome na hora.
        const a = this.addNode('area', this._snap(pt.x), this._snap(pt.y),
          {w: 280, h: 190, label: 'Nova área'});
        this._select('node', a.id);
        return;
      }
      this.addNode(type, this._snap(pt.x), this._snap(pt.y));
    });

    svg.addEventListener('mousedown', e => this._onDown(e));
    svg.addEventListener('mousemove', e => this._onMove(e));
    svg.addEventListener('mouseup',   e => this._onUp(e));
    svg.addEventListener('dblclick',  e => this._onDblClick(e));
    svg.addEventListener('wheel', e => {
      e.preventDefault();
      // Um giro de scroll manda dezenas de eventos; o transform só precisa ser
      // escrito uma vez por frame (ver _agendarVP).
      this._navBusy();
      const r = this._svgRect();
      const cx = e.clientX - r.left, cy = e.clientY - r.top;
      const dz = e.deltaY < 0 ? 1.1 : 0.9;
      // Mesmo limite dos botões +/− (10%–400%). O fator do pan sai do zoom já
      // limitado, senão o mapa continuaria escorregando no batente.
      const novoZoom = Math.max(.1, Math.min(4, this.zoom * dz));
      const fator = novoZoom / this.zoom;
      this.panX = cx - (cx - this.panX) * fator;
      this.panY = cy - (cy - this.panY) * fator;
      this.zoom = novoZoom;
      this._agendarVP();
    }, {passive:false});

    document.addEventListener('keydown', e => {
      // Com o modal de L2VPN aberto, Esc fecha ele (mesmo com o foco no campo
      // de busca) e os atalhos do canvas ficam suspensos — senão um Delete ou
      // um "c" digitado editaria o diagrama escondido atrás do modal.
      if (this._l2vpn) {
        if (e.key === 'Escape') this.fecharL2vpn();
        return;
      }
      if (this._pon) {
        if (e.key === 'Escape') this.fecharPon();
        return;
      }
      if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
      if (e.ctrlKey && e.key === 's') { e.preventDefault(); this.save(); }
      if (e.ctrlKey && e.key === 'z') { this.undo(); }
      if (e.ctrlKey && e.key === 'y') { this.redo(); }
      if (e.key === 'Delete' || e.key === 'Backspace') { this._deleteSelected(); }
      if (e.key === 'c' || e.key === 'C') { this.toggleConnectMode(); }
      if (e.key === 'g' || e.key === 'G') { this.agruparSelecionados(); }
      if (e.key === 'f' || e.key === 'F') { this.toggleFullscreen(); }
      if (e.key === 'Escape') { this._cancelConnect(); this._deselect(); this._clearMultiSelect(); }
    });

    // Quando é o <iframe> que entra em tela cheia (ver _fsAlvo), o evento sai
    // no documento pai — sem escutar lá, o botão nunca virava "sair".
    const fsDoc = this._fsAlvo().doc;
    ['fullscreenchange','webkitfullscreenchange'].forEach(ev => {
      document.addEventListener(ev, () => this._aoTrocarFullscreen());
      if (fsDoc !== document) fsDoc.addEventListener(ev, () => this._aoTrocarFullscreen());
    });

    document.getElementById('nome-diagrama').addEventListener('input', () => this._setDirty());
  }

  // ── Path generation ───────────────────────────────────────────────────────

  _linkPath(link) {
    const src = this.nodes.find(n => n.id === link.src);
    const tgt = this.nodes.find(n => n.id === link.tgt);
    if (!src || !tgt) return '';

    const shape = link.shape || 'straight';
    const wps   = link.waypoints || [];
    const allPts = [{x:src.x, y:src.y}, ...wps, {x:tgt.x, y:tgt.y}];

    if (shape === 'wavy') {
      // Segmentos ondulados entre cada par de pontos
      return allPts.slice(0,-1).map((p, i) =>
        this._wavySeg(p.x, p.y, allPts[i+1].x, allPts[i+1].y, i === 0)
      ).join(' ');
    }

    if (shape === 'curved') {
      return this._catmullRom(allPts);
    }

    // straight (padrão)
    return 'M' + allPts.map(p => `${p.x},${p.y}`).join(' L');
  }

  _wavySeg(x1, y1, x2, y2, isFirst) {
    const dx = x2-x1, dy = y2-y1;
    const len = Math.sqrt(dx*dx+dy*dy);
    if (len < 2) return (isFirst ? `M${x1},${y1} ` : '') + `L${x2},${y2}`;
    const nx = -dy/len, ny = dx/len;
    const amp = 10, wl = 28;
    const N = Math.max(20, Math.floor(len/5));
    let d = isFirst ? `M${x1},${y1}` : '';
    for (let i = 1; i <= N; i++) {
      const t = i/N;
      const px = x1+dx*t, py = y1+dy*t;
      const w = Math.sin(t * Math.PI * 2 * (len/wl)) * amp;
      d += ` L${(px+nx*w).toFixed(1)},${(py+ny*w).toFixed(1)}`;
    }
    return d;
  }

  _catmullRom(pts) {
    if (pts.length < 2) return '';
    if (pts.length === 2) {
      const cp = this._cp(pts[0].x, pts[0].y, pts[1].x, pts[1].y);
      return `M${pts[0].x},${pts[0].y} C${cp.c1x},${cp.c1y} ${cp.c2x},${cp.c2y} ${pts[1].x},${pts[1].y}`;
    }
    let d = `M${pts[0].x},${pts[0].y}`;
    for (let i = 0; i < pts.length-1; i++) {
      const p0 = pts[Math.max(0,i-1)], p1 = pts[i];
      const p2 = pts[i+1], p3 = pts[Math.min(pts.length-1,i+2)];
      const c1x = p1.x+(p2.x-p0.x)/6, c1y = p1.y+(p2.y-p0.y)/6;
      const c2x = p2.x-(p3.x-p1.x)/6, c2y = p2.y-(p3.y-p1.y)/6;
      d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2.x},${p2.y}`;
    }
    return d;
  }

  _cp(x1,y1,x2,y2) {
    const dx=x2-x1, dy=y2-y1, d=Math.sqrt(dx*dx+dy*dy);
    const ctrl=Math.min(d*.4,100);
    const s = dx > 0 ? 0.8 : -0.8;
    return {c1x:x1+ctrl*s, c1y:y1, c2x:x2-ctrl*s, c2y:y2};
  }

  // ── Waypoint handles ──────────────────────────────────────────────────────

  _renderLinkHandles(link) {
    this.handlesLayer.innerHTML = '';
    if (!link) return;

    const src = this.nodes.find(n => n.id === link.src);
    const tgt = this.nodes.find(n => n.id === link.tgt);
    if (!src || !tgt) return;

    const wps = link.waypoints || [];
    const allPts = [{x:src.x,y:src.y}, ...wps, {x:tgt.x,y:tgt.y}];

    const mk = (cx,cy,r,fill,stroke,cls,data={}) => {
      const c = document.createElementNS('http://www.w3.org/2000/svg','circle');
      c.setAttribute('cx', cx); c.setAttribute('cy', cy); c.setAttribute('r', r);
      c.setAttribute('fill', fill); c.setAttribute('stroke', stroke);
      c.setAttribute('stroke-width', '2');
      c.setAttribute('class', cls);
      Object.entries(data).forEach(([k,v]) => c.dataset[k] = v);
      this.handlesLayer.appendChild(c);
      return c;
    };

    // Midpoint handles (inserir waypoint) — círculo vazio
    allPts.slice(0,-1).forEach((p,i) => {
      const mx = (p.x + allPts[i+1].x)/2, my = (p.y + allPts[i+1].y)/2;
      mk(mx,my,5,'#161b22','#58a6ff','wp-handle wp-mid',
        {linkId:link.id, insertAfter:i});
    });

    // Waypoint handles (mover) — círculo preenchido
    wps.forEach((wp,i) => {
      mk(wp.x,wp.y,7,'#58a6ff','white','wp-handle wp-pt',
        {linkId:link.id, wpIdx:i});
    });
  }

  // ── Events ────────────────────────────────────────────────────────────────

  _onDown(e) {
    if (e.button !== 0) return;
    const target = e.target;

    // Waypoint handle drag
    if (target.classList.contains('wp-handle')) {
      const linkId = target.dataset.linkId;
      const link = this.links.find(l => l.id === linkId);
      if (!link) return;

      if (target.classList.contains('wp-mid')) {
        // Inserir novo waypoint
        const insertAfter = parseInt(target.dataset.insertAfter);
        const pt = this._svgPoint(e);
        if (!link.waypoints) link.waypoints = [];
        link.waypoints.splice(insertAfter, 0, {x:pt.x, y:pt.y});
        this.wpDrag = {linkId, wpIdx: insertAfter};
      } else {
        // Mover waypoint existente
        this.wpDrag = {linkId, wpIdx: parseInt(target.dataset.wpIdx)};
      }
      e.stopPropagation();
      return;
    }

    // Canto de uma Área: redimensiona em vez de mover. O canto oposto (fx,fy)
    // fica ancorado enquanto o outro segue o cursor.
    if (target.classList.contains('area-handle')) {
      const id = target.dataset.id;
      const node = this.nodes.find(n => n.id === id);
      if (node) {
        this._select('node', id);
        const corner = target.dataset.corner;
        this._saveHistory();
        this.areaResizing = {
          id, corner,
          fx: node.x + (corner.includes('w') ?  node.w/2 : -node.w/2),
          fy: node.y + (corner.includes('n') ?  node.h/2 : -node.h/2),
        };
        e.stopPropagation();
      }
      return;
    }

    const nodeEl = target.closest('.node');
    const linkEl = target.closest('.link-hit');
    const isAnchor = target.classList.contains('anchor');

    // Áreas não participam de conexões (não são equipamentos).
    if (this.connectMode && nodeEl && !this._ehArea(nodeEl.dataset.id)) {
      this.connectSrc = nodeEl.dataset.id;
      this.anchorDrag = true;
      nodeEl.classList.add('connecting');
      e.stopPropagation();
      return;
    }

    if (isAnchor && nodeEl) {
      this.connectSrc = nodeEl.dataset.id;
      this.anchorDrag = true;
      nodeEl.classList.add('connecting');
      e.stopPropagation();
      return;
    }

    if (nodeEl) {
      const id = nodeEl.dataset.id;

      // Ctrl+clique (ou Shift/Cmd): alterna o nó na multi-seleção sem abrir o
      // painel de propriedades — mesmo padrão de editores gráficos.
      if (this._ehAditivo(e)) {
        this._toggleMultiSelect(id);
        e.stopPropagation();
        return;
      }

      // Clicar (sem shift) num nó que já faz parte de um grupo selecionado
      // arrasta o grupo inteiro, preservando a posição relativa entre eles.
      if (this.selectedNodes.size > 1 && this.selectedNodes.has(id)) {
        const pt = this._svgPoint(e);
        const positions = {};
        this.selectedNodes.forEach(nid => {
          const n = this.nodes.find(n => n.id === nid);
          if (n) positions[nid] = {x: n.x, y: n.y};
        });
        this.groupDragging = {startX: pt.x, startY: pt.y, positions};
        e.stopPropagation();
        return;
      }

      this._clearMultiSelect();
      this._select('node', id);
      const pt = this._svgPoint(e);
      const node = this.nodes.find(n => n.id === id);
      this.dragging = {id, offX: pt.x - node.x, offY: pt.y - node.y};
      e.stopPropagation();
      return;
    }

    if (linkEl) {
      this._clearMultiSelect();
      this._select('link', linkEl.dataset.id);
      e.stopPropagation();
      return;
    }

    // Área vazia do canvas: com o modo "Área" ativo (botão da toolbar) ou
    // segurando Ctrl/Shift, arrastar desenha um laço de seleção em vez de
    // fazer pan — captura todos os nodes cujo centro cair no retângulo.
    if (this.areaSelectMode || this._ehAditivo(e)) {
      this._deselect();
      this._clearMultiSelect();
      const pt = this._svgPoint(e);
      this.rubberBand = {x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y};
      this._renderRubberBand();
      e.stopPropagation();
      return;
    }

    this._deselect();
    this._clearMultiSelect();
    this.panning = {startX: e.clientX, startY: e.clientY, px: this.panX, py: this.panY};
  }

  /** Handler cru do mousemove: só guarda o evento. Mouses de 500–1000Hz
   *  entregam 4–8 movimentos por frame — antes cada um redesenhava node e
   *  enlaces na hora, ou seja o mapa era reconstruído várias vezes para ser
   *  pintado uma só. Agora o desenho roda uma vez por frame. */
  _onMove(e) {
    this._moveEvt = e;
    if (this._moveRaf) return;
    this._moveRaf = requestAnimationFrame(() => {
      this._moveRaf = null;
      if (this._moveEvt) this._aplicarMove(this._moveEvt);
    });
  }

  /** Aplica o último movimento no mouseup pendente, para o gesto não terminar
   *  um frame atrás da posição real do cursor. */
  _flushMove() {
    if (!this._moveRaf) return;
    cancelAnimationFrame(this._moveRaf);
    this._moveRaf = null;
    if (this._moveEvt) this._aplicarMove(this._moveEvt);
  }

  _aplicarMove(e) {
    if (this.wpDrag) {
      const pt = this._svgPoint(e);
      const link = this.links.find(l => l.id === this.wpDrag.linkId);
      if (link && link.waypoints) {
        link.waypoints[this.wpDrag.wpIdx] = {x:pt.x, y:pt.y};
        this._renderLink(link);
        this._renderLinkHandles(link);
        this._setDirty();
      }
      return;
    }
    if (this.areaResizing) {
      this._navBusy();
      const s = this.areaResizing;
      const node = this.nodes.find(n => n.id === s.id);
      if (node) {
        const pt = this._svgPoint(e);
        let nx = pt.x, ny = pt.y;
        if (this.snap) { nx = Math.round(nx/20)*20; ny = Math.round(ny/20)*20; }
        const w = Math.max(80, Math.abs(nx - s.fx));
        const h = Math.max(50, Math.abs(ny - s.fy));
        const left = nx < s.fx ? s.fx - w : s.fx;
        const top  = ny < s.fy ? s.fy - h : s.fy;
        node.w = w; node.h = h;
        node.x = left + w/2; node.y = top + h/2;
        this._renderNode(node);
        this._setDirty();
      }
      return;
    }
    if (this.rubberBand) {
      this._navBusy();
      const pt = this._svgPoint(e);
      this.rubberBand.x1 = pt.x;
      this.rubberBand.y1 = pt.y;
      this._renderRubberBand();
      return;
    }
    if (this.groupDragging) {
      this._navBusy();
      const pt = this._svgPoint(e);
      let dx = pt.x - this.groupDragging.startX;
      let dy = pt.y - this.groupDragging.startY;
      if (this.snap) { dx = Math.round(dx/20)*20; dy = Math.round(dy/20)*20; }
      this.selectedNodes.forEach(id => {
        const node = this.nodes.find(n => n.id === id);
        const start = this.groupDragging.positions[id];
        if (node && start) {
          node.x = start.x + dx;
          node.y = start.y + dy;
          this._moverNode(node);
        }
      });
      this._renderLinksDe(this.selectedNodes);
      this._setDirty();
      return;
    }
    if (this.dragging) {
      this._navBusy();
      const pt = this._svgPoint(e);
      const node = this.nodes.find(n => n.id === this.dragging.id);
      if (node) {
        node.x = this._snap(pt.x - this.dragging.offX);
        node.y = this._snap(pt.y - this.dragging.offY);
        this._moverNode(node);
        this._renderLinksDe([node.id]);
        // Atualiza handles se um link do nó arrastado estiver selecionado
        if (this.selected && this.selected.type === 'link') {
          const selLink = this.links.find(l => l.id === this.selected.id);
          if (selLink && (selLink.src === node.id || selLink.tgt === node.id)) {
            this._renderLinkHandles(selLink);
          }
        }
        this._setDirty();
      }
    } else if (this.panning) {
      this._navBusy();
      this.panX = this.panning.px + (e.clientX - this.panning.startX);
      this.panY = this.panning.py + (e.clientY - this.panning.startY);
      this._agendarVP();
    } else if (this.connectSrc) {
      const pt = this._svgPoint(e);
      const src = this.nodes.find(n => n.id === this.connectSrc);
      if (src) {
        this.preview.setAttribute('d', `M${src.x},${src.y} L${pt.x},${pt.y}`);
        this.preview.style.display = '';
      }
    }
  }

  _onUp(e) {
    this._flushMove();
    if (this.wpDrag) { this._saveHistory(); this.wpDrag = null; return; }
    if (this.areaResizing) { this.areaResizing = null; return; }
    if (this.rubberBand) { this._finishRubberBand(); this.rubberBand = null; return; }
    if (this.groupDragging) { this._saveHistory(); this.groupDragging = null; return; }
    if (this.dragging) { this._saveHistory(); this.dragging = null; }
    if (this.panning)  { this.panning = null; }

    if (this.anchorDrag && this.connectSrc) {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const nodeEl = el ? el.closest('.node') : null;
      if (nodeEl && nodeEl.dataset.id !== this.connectSrc) {
        this.addLink(this.connectSrc, nodeEl.dataset.id);
      }
      this.anchorDrag = false;
      this._cancelConnect();
    }
  }

  _onDblClick(e) {
    // Dois Ctrl+cliques seguidos no mesmo host (marcar/desmarcar da seleção)
    // chegam aqui como duplo-clique. Sem esse guard, fazer isso num ícone de
    // grupo abriria o sub-mapa no meio da seleção e tiraria a pessoa da tela.
    if (this._ehAditivo(e)) return;
    // Remover waypoint com duplo-clique
    const target = e.target;
    if (target.classList.contains('wp-pt')) {
      const linkId = target.dataset.linkId;
      const wpIdx  = parseInt(target.dataset.wpIdx);
      const link   = this.links.find(l => l.id === linkId);
      if (link && link.waypoints) {
        this._saveHistory();
        link.waypoints.splice(wpIdx, 1);
        this._renderLink(link);
        this._renderLinkHandles(link);
        this._setDirty();
        e.stopPropagation();
      }
      return;
    }
    // Duplo-clique num node: abre o sub-mapa vinculado (se já existir).
    const nodeEl = target.closest('.node');
    if (nodeEl) {
      const node = this.nodes.find(n => n.id === nodeEl.dataset.id);
      if (node && node.submap_id) {
        e.stopPropagation();
        this._abrirOuCriarSubmapa(node.id);
      }
    }
  }

  // ── Sub-mapas ────────────────────────────────────────────────────────────

  _abrirOuCriarSubmapa(id) {
    const node = this.nodes.find(n => n.id === id);
    if (!node) return;
    if (node.submap_id) {
      window.location.href = `/clientes/${this.clienteId}/topologia/editor/?diagrama=${node.submap_id}`;
      return;
    }
    const nome = prompt('Nome do sub-mapa:', node.label ? `${node.label} — detalhe` : 'Novo sub-mapa');
    if (nome === null) return;
    this._criarSubmapa(id, nome || 'Novo sub-mapa');
  }

  async _criarSubmapa(nodeId, nome) {
    if (!this.diagramaId) {
      await this.save();
    }
    if (!this.diagramaId) {
      this._toast('Salve o mapa antes de criar um sub-mapa', 'error');
      return;
    }
    try {
      const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
      const r = await fetch(`/clientes/${this.clienteId}/topologia/${this.diagramaId}/submapa/`, {
        method: 'POST',
        headers: {'Content-Type':'application/json','X-CSRFToken':csrf},
        body: JSON.stringify({node_id: nodeId, nome}),
      });
      const d = await r.json();
      if (!d.ok) { this._toast(d.error || 'Erro ao criar sub-mapa', 'error'); return; }
      window.location.href = `/clientes/${this.clienteId}/topologia/editor/?diagrama=${d.submap_id}`;
    } catch (e) {
      this._toast('Erro ao criar sub-mapa: ' + e, 'error');
    }
  }

  // ── Agrupar hosts num ícone só ────────────────────────────────────────────
  //
  // Pega os nodes da multi-seleção, tira eles do mapa e põe um único node do
  // tipo `grupo` no lugar, ligado a um sub-mapa que contém os mesmos hosts
  // MAIS os vizinhos deles (o switch de onde saem os enlaces), marcados com
  // `grupo_borda` — assim o sub-mapa abre já mostrando "switch + OLTs" em vez
  // de OLTs soltas sem uplink.
  //
  // No mapa pai, todos os enlaces que cruzavam a fronteira do grupo viram
  // **um enlace por vizinho** (o mais rápido deles; se havia mais de um, o
  // rótulo vira "N enlaces"). Os campos de interface/IP do lado que aponta
  // para o grupo são limpos: eles pertenciam a um host específico, e o ícone
  // do grupo não é um host.

  _prefixoComum(nomes) {
    if (!nomes.length) return '';
    let p = nomes[0];
    nomes.slice(1).forEach(n => {
      let i = 0;
      while (i < p.length && i < n.length && p[i].toUpperCase() === n[i].toUpperCase()) i++;
      p = p.slice(0, i);
    });
    // Tira o resto de numeração que sobrou do prefixo ("OLT-ALCOBACA-0" dos
    // hosts 02..06 vira "OLT-ALCOBACA") e o separador solto no fim.
    return p.replace(/\d+$/, '').replace(/[-_.\s]+$/, '').trim();
  }

  _pesoIface(k) {
    const i = TOPO_ORDEM_IFACE.indexOf(k);
    return i < 0 ? 0 : i;
  }

  /** Nome sugerido para o grupo: prefixo comum dos hosts ("OLT-ALCOBACA-07",
   *  "OLT-ALCOBACA-02" → "OLT-ALCOBACA") ou, na falta dele, o tipo dominante. */
  _nomeSugeridoGrupo(membros) {
    const prefixo = this._prefixoComum(membros.map(n => n.label || ''));
    if (prefixo.length >= 3) return `${prefixo} (${membros.length})`;
    const contagem = {};
    membros.forEach(n => { contagem[n.type] = (contagem[n.type] || 0) + 1; });
    const dom = Object.entries(contagem).sort((a, b) => b[1] - a[1])[0][0];
    const def = TOPO_DEVICES[dom] || TOPO_DEVICES.host;
    return `${def.label} (${membros.length})`;
  }

  async agruparSelecionados() {
    if (this.selectedNodes.size < 2) {
      this._toast('Segure Ctrl e clique nos dispositivos para selecionar (2 ou mais)', 'error');
      return;
    }
    const membros = this.nodes.filter(n => this.selectedNodes.has(n.id));
    const ids = new Set(membros.map(n => n.id));

    const internos = this.links.filter(l => ids.has(l.src) && ids.has(l.tgt));
    const externos = this.links.filter(l => ids.has(l.src) !== ids.has(l.tgt));
    const vizinhos = [...new Set(externos.map(l => (ids.has(l.src) ? l.tgt : l.src)))]
      .map(id => this.nodes.find(n => n.id === id)).filter(Boolean);

    const nome = prompt('Nome do grupo:', this._nomeSugeridoGrupo(membros));
    if (nome === null) return;

    // O sub-mapa nasce com os membros nas mesmas coordenadas que tinham aqui
    // (o desenho que a pessoa já arrumou continua valendo lá dentro) + uma
    // cópia de cada vizinho, marcada como borda.
    const submapDados = {
      nodes: [
        ...membros.map(n => ({...n})),
        ...vizinhos.map(n => ({...n, grupo_borda: true})),
      ],
      links: [...internos, ...externos].map(l => ({...l})),
    };

    this._saveHistory();

    const cx = this._snap(Math.round(membros.reduce((a, n) => a + n.x, 0) / membros.length));
    const cy = this._snap(Math.round(membros.reduce((a, n) => a + n.y, 0) / membros.length));
    const corDom = membros[0].color || (TOPO_DEVICES[membros[0].type] || TOPO_DEVICES.host).color;

    this.nodes = this.nodes.filter(n => !ids.has(n.id));
    this.links = this.links.filter(l => !ids.has(l.src) && !ids.has(l.tgt));

    const grupo = {
      id: this._id(), type: 'grupo', x: cx, y: cy, w: 72, h: 72,
      label: nome || 'Grupo', ip: '', color: corDom,
      grupo: true,
      grupo_membros: membros.map(n => ({id: n.id, label: n.label || ''})),
    };
    this.nodes.push(grupo);

    // Um enlace por vizinho, no lugar dos N que existiam.
    vizinhos.forEach(v => {
      const doVizinho = externos.filter(l => l.src === v.id || l.tgt === v.id);
      const base = doVizinho.slice()
        .sort((a, b) => this._pesoIface(b.iface) - this._pesoIface(a.iface))[0];
      const vizinhoEhOrigem = base.src === v.id;
      this.links.push({
        ...base,
        id: this._id(),
        src: vizinhoEhOrigem ? v.id : grupo.id,
        tgt: vizinhoEhOrigem ? grupo.id : v.id,
        waypoints: [],
        label: doVizinho.length > 1 ? `${doVizinho.length} enlaces` : (base.label || ''),
        iface_a:   vizinhoEhOrigem ? (base.iface_a || '') : '',
        iface_b:   vizinhoEhOrigem ? '' : (base.iface_b || ''),
        ip_local:  vizinhoEhOrigem ? (base.ip_local || '') : '',
        ip_remote: vizinhoEhOrigem ? '' : (base.ip_remote || ''),
      });
    });

    this._clearMultiSelect();
    this._deselect();
    this._renderAll();
    this._setDirty();

    // O backend grava o `submap_id` no nó dentro do `dados_json` já salvo do
    // mapa pai — então o pai precisa estar salvo COM o node do grupo antes.
    await this.save();
    if (!this.diagramaId) { this._toast('Salve o mapa antes de agrupar', 'error'); return; }

    try {
      const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
      const r = await fetch(`/clientes/${this.clienteId}/topologia/${this.diagramaId}/submapa/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
        body: JSON.stringify({node_id: grupo.id, nome: grupo.label, dados_json: submapDados}),
      });
      const d = await r.json();
      if (!d.ok) { this._toast(d.error || 'Erro ao criar o mapa do grupo', 'error'); return; }
      grupo.submap_id = d.submap_id;
      this._renderNode(grupo);
      await this.save();
      this._toast(`${membros.length} dispositivos agrupados — duplo-clique no ícone abre o mapa`);
    } catch (e) {
      this._toast('Erro ao criar o mapa do grupo: ' + e, 'error');
    }
  }

  /** Traz os hosts do sub-mapa de volta para este mapa e apaga o ícone do
   *  grupo. Os nós de borda (o vizinho copiado pra dar contexto lá dentro) não
   *  voltam — eles nunca saíram daqui. */
  async desagrupar(id) {
    const grupo = this.nodes.find(n => n.id === id);
    if (!grupo || !grupo.grupo) return;

    let dados = {nodes: [], links: []};
    if (grupo.submap_id) {
      try {
        const r = await fetch(`/clientes/${this.clienteId}/topologia/dados/?diagrama=${grupo.submap_id}`);
        if (!r.ok) { this._toast(`Erro ${r.status} ao ler o mapa do grupo`, 'error'); return; }
        dados = await r.json();
      } catch (e) {
        this._toast('Erro ao ler o mapa do grupo: ' + e, 'error');
        return;
      }
    }
    const existentes = new Set(this.nodes.map(n => n.id));
    const voltando = (dados.nodes || []).filter(n => !n.grupo_borda && !existentes.has(n.id));

    if (!confirm(`Desagrupar "${grupo.label}"?\n\n${voltando.length} dispositivo(s) voltam para este mapa `
               + `e o mapa do grupo é excluído.`)) return;

    this._saveHistory();
    voltando.forEach(n => this.nodes.push(n));

    const validos = new Set(this.nodes.map(n => n.id));
    const jaTem = new Set(this.links.map(l => l.id));
    (dados.links || []).forEach(l => {
      if (!validos.has(l.src) || !validos.has(l.tgt) || jaTem.has(l.id)) return;
      this.links.push({waypoints: [], shape: 'straight', iface_a: '', iface_b: '', ...l});
    });

    this.nodes = this.nodes.filter(n => n.id !== id);
    this.links = this.links.filter(l => l.src !== id && l.tgt !== id);

    this._deselect();
    this._clearMultiSelect();
    this._renderAll();
    this._setDirty();
    await this.save();
    if (grupo.submap_id) await this._excluirSubmapa(grupo.submap_id);
    this._toast(`Grupo desfeito — ${voltando.length} dispositivos de volta no mapa`);
  }

  async _excluirSubmapa(submapId) {
    try {
      const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
      await fetch(`/clientes/${this.clienteId}/topologia/${submapId}/submapa/excluir/`, {
        method: 'POST', headers: {'X-CSRFToken': csrf},
      });
    } catch (e) { console.warn('sub-mapa não excluído:', e); }
  }

  _updateVP() {
    this.vp.setAttribute('transform', `translate(${this.panX},${this.panY}) scale(${this.zoom})`);
    const lbl = this._zoomLabel || (this._zoomLabel = document.getElementById('zoom-label'));
    const txt = Math.round(this.zoom*100) + '%';
    if (lbl && lbl.textContent !== txt) lbl.textContent = txt;
  }

  /** Escreve o transform do viewport no máximo uma vez por frame. Pan e zoom
   *  chegam muito mais rápido que isso e o navegador só pinta uma vez mesmo. */
  _agendarVP() {
    if (this._vpRaf) return;
    this._vpRaf = requestAnimationFrame(() => { this._vpRaf = null; this._updateVP(); });
  }

  /** Modo navegação: enquanto o mapa está sendo arrastado, ampliado ou tem um
   *  host sendo movido, os enfeites saem de cena (fluxo animado nos enlaces,
   *  pacotes, pulso dos hosts do CRM, drop-shadow dos ícones, blur dos
   *  painéis) — ver `body.nav-busy` no <style> do editor. Cada um deles é
   *  recalculado a cada frame do movimento, e com ~35 hosts e ~40 enlaces
   *  somados derrubavam o arraste a poucos quadros por segundo. Voltam 200ms
   *  depois que o mapa para. */
  _navBusy() {
    if (!this._navOn) { this._navOn = true; document.body.classList.add('nav-busy'); }
    clearTimeout(this._navTimer);
    this._navTimer = setTimeout(() => {
      this._navOn = false;
      document.body.classList.remove('nav-busy');
      // Enlaces desenhados sem os "pacotes" durante o movimento: agora que
      // parou, redesenha com a animação de volta.
      if (this._navSujo) { this._navSujo = false; this._renderLinks(); }
    }, 200);
  }

  /** Redesenha só os enlaces que tocam estes hosts. Arrastar um único ícone
   *  reconstruía os 40 enlaces do mapa (com `<animateMotion>` e tudo) a cada
   *  frame — quase todos parados no mesmo lugar. */
  _renderLinksDe(ids) {
    const alvo = ids instanceof Set ? ids : new Set(ids);
    this.links.forEach(l => {
      if (alvo.has(l.src) || alvo.has(l.tgt)) this._renderLink(l);
    });
  }

  _cancelConnect() {
    this.connectSrc = null;
    this.preview.style.display = 'none';
    document.querySelectorAll('.node.connecting').forEach(n => n.classList.remove('connecting'));
  }

  _select(type, id) {
    this._deselect();
    this.selected = {type, id};
    // O painel só existe quando há algo selecionado — abre aqui e fecha no
    // _deselect(), em vez de ficar ocupando a lateral com "selecione algo".
    document.body.classList.remove('props-off');
    if (type === 'node') {
      const el = this.nodesLayer.querySelector(`[data-id="${id}"]`)
              || this.areasLayer.querySelector(`[data-id="${id}"]`);
      if (el) el.classList.add('selected');
      this._showNodeProps(id);
    } else {
      const el = this.linksLayer.querySelector(`.link-hit[data-id="${id}"]`);
      if (el) el.closest('g').classList.add('selected');
      this._showLinkProps(id);
      const link = this.links.find(l => l.id === id);
      this._renderLinkHandles(link);
    }
  }

  _deselect() {
    this.selected = null;
    document.body.classList.add('props-off');
    this._propsGen++; // invalida qualquer fetch de interfaces (datalist) em voo
    this.handlesLayer.innerHTML = '';
    document.querySelectorAll('.node.selected, .link-group.selected').forEach(el => el.classList.remove('selected'));
    document.getElementById('props-body').innerHTML = `<div class="prop-empty"><i class="fas fa-arrow-pointer"></i>Selecione um dispositivo<br>ou conexão no canvas</div>`;
  }

  /** Nodes de grupo entre os que estão sendo removidos: pede confirmação (os
   *  hosts lá dentro somem deste mapa junto) e apaga o sub-mapa de cada um. */
  _confirmarRemoverGrupos(ids) {
    const grupos = this.nodes.filter(n => ids.has(n.id) && n.grupo);
    if (!grupos.length) return true;
    const qtd = grupos.reduce((a, g) => a + (g.grupo_membros || []).length, 0);
    if (!confirm(`Remover ${grupos.length === 1 ? `o grupo "${grupos[0].label}"` : `${grupos.length} grupos`}?\n\n`
               + `${qtd} dispositivo(s) dentro dele(s) e o mapa do grupo serão apagados. `
               + `Use "Desagrupar" se quiser trazer os dispositivos de volta.`)) return false;
    grupos.forEach(g => { if (g.submap_id) this._excluirSubmapa(g.submap_id); });
    return true;
  }

  _deleteSelected() {
    if (this.selectedNodes.size > 1) {
      const ids = new Set(this.selectedNodes);
      if (!this._confirmarRemoverGrupos(ids)) return;
      this._saveHistory();
      this.nodes = this.nodes.filter(n => !ids.has(n.id));
      this.links = this.links.filter(l => !ids.has(l.src) && !ids.has(l.tgt));
      this._clearMultiSelect();
      this._deselect();
      this._renderAll();
      this._setDirty();
      this._toast(`${ids.size} dispositivos removidos`);
      return;
    }
    if (!this.selected) return;
    if (this.selected.type === 'node'
        && !this._confirmarRemoverGrupos(new Set([this.selected.id]))) return;
    this._saveHistory();
    if (this.selected.type === 'node') {
      const id = this.selected.id;
      this.nodes = this.nodes.filter(n => n.id !== id);
      this.links = this.links.filter(l => l.src !== id && l.tgt !== id);
    } else {
      this.links = this.links.filter(l => l.id !== this.selected.id);
    }
    this._deselect();
    this._renderAll();
    this._setDirty();
  }

  // ── Multi-seleção / seleção em área ─────────────────────────────────────

  _renderRubberBand() {
    if (!this.rubberEl || !this.rubberBand) return;
    const {x0, y0, x1, y1} = this.rubberBand;
    this.rubberEl.setAttribute('x', Math.min(x0, x1));
    this.rubberEl.setAttribute('y', Math.min(y0, y1));
    this.rubberEl.setAttribute('width', Math.abs(x1 - x0));
    this.rubberEl.setAttribute('height', Math.abs(y1 - y0));
    this.rubberEl.style.display = '';
  }

  _finishRubberBand() {
    if (this.rubberEl) this.rubberEl.style.display = 'none';
    if (!this.rubberBand) return;
    const {x0, y0, x1, y1} = this.rubberBand;
    const minX = Math.min(x0, x1), maxX = Math.max(x0, x1);
    const minY = Math.min(y0, y1), maxY = Math.max(y0, y1);
    // Arraste mínimo — um clique parado na área vazia (sem mover o mouse)
    // não deve contar como um laço de seleção de 0x0.
    if (maxX - minX < 4 && maxY - minY < 4) return;

    const found = this.nodes.filter(n => n.type !== 'area'
      && n.x >= minX && n.x <= maxX && n.y >= minY && n.y <= maxY);
    if (!found.length) return;

    if (found.length === 1) {
      // Só um nó capturado — comporta-se como seleção normal (abre o painel).
      this._select('node', found[0].id);
      return;
    }

    this.selectedNodes = new Set(found.map(n => n.id));
    found.forEach(n => {
      const el = this.nodesLayer.querySelector(`[data-id="${n.id}"]`);
      if (el) el.classList.add('multi-selected');
    });
    this._updateMultiSelectStatus();
  }

  _toggleMultiSelect(id) {
    this._deselect(); // fecha o painel de propriedades de seleção única, se aberto
    const el = this.nodesLayer.querySelector(`[data-id="${id}"]`);
    if (this.selectedNodes.has(id)) {
      this.selectedNodes.delete(id);
      if (el) el.classList.remove('multi-selected');
    } else {
      this.selectedNodes.add(id);
      if (el) el.classList.add('multi-selected');
    }
    this._updateMultiSelectStatus();
  }

  _clearMultiSelect() {
    if (!this.selectedNodes.size) return;
    this.selectedNodes.forEach(id => {
      const el = this.nodesLayer.querySelector(`[data-id="${id}"]`);
      if (el) el.classList.remove('multi-selected');
    });
    this.selectedNodes.clear();
    this._updateMultiSelectStatus();
  }

  _updateMultiSelectStatus() {
    const btn = document.getElementById('btn-agrupar');
    if (btn) btn.classList.toggle('dim', this.selectedNodes.size < 2);
    const el = document.getElementById('st-mode');
    if (!el) return;
    if (this.selectedNodes.size > 1) {
      el.textContent = `${this.selectedNodes.size} selecionados (Ctrl+clique adiciona) — arraste para mover ou "Agrupar" (G)`;
    } else {
      el.textContent = 'Modo: ' + (this.connectMode ? 'Conexão' : this.areaSelectMode ? 'Seleção de área' : 'Seleção');
    }
  }

  addNode(type, x, y, extra={}) {
    this._saveHistory();
    const def = TOPO_DEVICES[type] || TOPO_DEVICES.host;
    const node = {
      id: extra.id || this._id(),
      type, x, y,
      label: extra.label || def.label,
      ip: extra.ip || '',
      color: extra.color || def.color,
      w: 64, h: 64,
      ...extra
    };
    this.nodes.push(node);
    this._renderNode(node);
    this._updateStatus();
    this._setDirty();
    return node;
  }

  addLink(srcId, tgtId, extra={}) {
    this._saveHistory();
    const link = {
      id:        extra.id        || this._id(),
      src: srcId, tgt: tgtId,
      iface:     extra.iface     || '1g',
      label:     extra.label     || '',
      ip_local:  extra.ip_local  || '',
      ip_remote: extra.ip_remote || '',
      vlan:      extra.vlan      || '',
      color:     extra.color     || null,
      style:     extra.style     || 'solid',    // traço: solid/dashed/dotted
      shape:     extra.shape     || 'straight', // forma: straight/curved/wavy
      waypoints: extra.waypoints || [],          // [{x,y}, ...]
      iface_a:   extra.iface_a   || '',          // interface lado A (ex: eth0, ge0/0/1)
      iface_b:   extra.iface_b   || '',          // interface lado B
    };
    this.links.push(link);
    this._renderLink(link);
    this._updateStatus();
    this._setDirty();
    return link;
  }

  /** Arrastar não muda o desenho do ícone, só onde ele está: reposiciona pelo
   *  `transform` em vez de passar pelo _renderNode, que reconstrói ~15
   *  elementos SVG via innerHTML — a 60fps, caro à toa. */
  _moverNode(node) {
    const el = this.nodesLayer.querySelector(`[data-id="${node.id}"]`)
            || this.areasLayer.querySelector(`[data-id="${node.id}"]`);
    if (!el) { this._renderNode(node); return; }
    el.setAttribute('transform', `translate(${node.x},${node.y})`);
  }

  _renderNode(node) {
    const layer = node.type === 'area' ? this.areasLayer : this.nodesLayer;
    let el = layer.querySelector(`[data-id="${node.id}"]`);
    if (!el) {
      el = document.createElementNS('http://www.w3.org/2000/svg','g');
      el.classList.add('node');
      el.dataset.id = node.id;
      layer.appendChild(el);
    }
    const def = TOPO_DEVICES[node.type] || TOPO_DEVICES.host;
    const c = node.color || def.color;
    el.setAttribute('transform', `translate(${node.x},${node.y})`);
    // Anel pulsante (CSS) só em nodes vinculados a um Acesso real do CRM —
    // ver `.node[data-live="1"] .node-ring` no <style> do template.
    if (node.acesso_id) el.dataset.live = '1'; else delete el.dataset.live;

    if (node.type === 'area') {
      el.classList.add('area-node');
      el.style.color = c; // currentColor do drop-shadow de seleção
      const hw = Math.max(40, node.w/2), hh = Math.max(25, node.h/2);
      const label = node.label || '';
      const titleW = Math.max(28, label.length * 6.7 + 20);
      const corner = (k) => {
        const hx = k.includes('w') ? -hw : hw;
        const hy = k.includes('n') ? -hh : hh;
        return `<rect class="area-handle" data-corner="${k}" data-id="${node.id}"
          x="${hx-5}" y="${hy-5}" width="10" height="10" rx="2"
          fill="#0d1117" stroke="${c}" stroke-width="2" style="cursor:${k}-resize"/>`;
      };
      el.innerHTML = `
        <rect class="area-rect" x="${-hw}" y="${-hh}" width="${hw*2}" height="${hh*2}" rx="10"
          fill="${c}14" stroke="${c}" stroke-width="1.6"/>
        <rect class="area-border-hit" x="${-hw}" y="${-hh}" width="${hw*2}" height="${hh*2}" rx="10"/>
        ${label ? `<g class="area-label" transform="translate(${-hw},${-hh})">
          <rect x="0" y="-10" width="${titleW}" height="20" rx="6" fill="${c}" fill-opacity=".95"/>
          <text x="10" y="4.5" font-size="11.5" font-weight="700" fill="#0d1117"
            font-family="'Segoe UI',sans-serif">${this._esc(label)}</text>
        </g>` : ''}
        ${['nw','ne','sw','se'].map(corner).join('')}`;
      return;
    }
    el.classList.remove('area-node');

    if (node.type === 'text_box') {
      el.classList.add('text-box-node');
      const lines = (node.label || '').split('\n');
      const lh = 16, pad = 12;
      const bw = Math.max(...lines.map(l => l.length * 7), 80) + pad*2;
      const bh = lines.length * lh + pad*2;
      el.innerHTML = `
        <rect x="${-bw/2}" y="${-bh/2}" width="${bw}" height="${bh}" rx="6"
          fill="${c}11" stroke="${c}" stroke-width="1.5" stroke-dasharray="6,3"/>
        ${lines.map((l,i) => `<text x="0" y="${-bh/2 + pad + lh*i + 11}"
          text-anchor="middle" font-size="12" fill="${c}"
          font-family="'Segoe UI',sans-serif">${this._esc(l)}</text>`).join('')}
        <circle class="anchor" cx="0" cy="${-bh/2}" r="7" fill="${c}" stroke="white" stroke-width="1.5"/>
        <circle class="anchor" cx="0" cy="${bh/2}"  r="7" fill="${c}" stroke="white" stroke-width="1.5"/>
        <circle class="anchor" cx="${-bw/2}" cy="0"  r="7" fill="${c}" stroke="white" stroke-width="1.5"/>
        <circle class="anchor" cx="${bw/2}"  cy="0"  r="7" fill="${c}" stroke="white" stroke-width="1.5"/>`;
      return;
    }

    el.classList.remove('text-box-node');
    const hw = node.w/2, hh = node.h/2;
    // Backdrop atrás do nome/IP — sem isso o texto claro fica pouco legível
    // sobre os pontos do grid quando o node está fora do fundo sólido do canvas.
    const maxLen = Math.max((node.label||'').length, (node.ip||'').length, 1);
    const lblBgW = Math.max(34, maxLen * 6.4 + 14); // 6.4 (não 6): IP em negrito é um pouco mais largo
    const lblBgH = node.ip ? 31 : 18;
    // Chassi "de vidro": fundo bem translúcido na cor do device + borda a ~35%,
    // brilho no topo e sombra interna embaixo. O contorno cheio de antes (cor
    // pura, 1.5px) competia com o ícone e deixava a tela pesada com 20+ nodes.
    el.innerHTML = `
      <rect class="node-ring" x="${-hw-5}" y="${-hh-5}" width="${node.w+10}" height="${node.h+10}" rx="14" stroke="${c}" stroke-width="2"/>
      <rect class="node-body" x="${-hw}" y="${-hh}" width="${node.w}" height="${node.h}" rx="12" fill="${c}1c" stroke="${c}59" stroke-width="1.5"/>
      <rect x="${-hw}" y="${-hh}" width="${node.w}" height="${node.h}" rx="12" fill="url(#node-gloss)" pointer-events="none"/>
      <rect x="${-hw}" y="${-hh}" width="${node.w}" height="${node.h}" rx="12" fill="url(#node-shade)" pointer-events="none"/>
      <path d="M${-hw+9},${-hh+1.2} H${hw-9}" stroke="#fff" stroke-opacity=".18" stroke-width="1.2" fill="none" pointer-events="none"/>
      <g class="node-icon" style="color:${c}" transform="translate(${-hw+8},${-hh+8}) scale(${(node.w-16)/48})">
        ${TOPO_ICONS[def.icon]||''}
      </g>
      ${node.acesso_id ? `<circle class="node-led" cx="${hw-7}" cy="${-hh+7}" r="2.6" fill="#3fb950"/>` : ''}
      ${node.grupo ? `<g class="node-grupo-badge" transform="translate(${hw-11},${-hh+11})" data-tip="Dispositivos dentro do grupo">
          <circle r="10" fill="#0d1117" stroke="${c}" stroke-width="1.5"/>
          <text text-anchor="middle" y="3.6" font-size="10.5" font-weight="800" fill="${c}"
            font-family="'Segoe UI',sans-serif">${(node.grupo_membros||[]).length || node.grupo_qtd || 0}</text>
        </g>` : ''}
      ${node.submap_id && !node.grupo ? `<g class="node-submap-badge" transform="translate(${hw-13},${hh-13})" data-tip="Tem sub-mapa">
          <circle r="9" fill="#0d1117" stroke="${c}" stroke-width="1.5"/>
          <path d="M-3.5,-3.5 h5 v5 M1.5,-3.5 L-3.5,1.5 M-1,-3.5 h4.5 v4.5" fill="none" stroke="${c}" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </g>` : ''}
      <rect class="node-label-bg" x="${-lblBgW/2}" y="${hh+7}" width="${lblBgW}" height="${lblBgH}" rx="7"/>
      <text x="0" y="${hh+19}" text-anchor="middle" font-size="11" font-weight="600" fill="#e6edf3" font-family="'Segoe UI',sans-serif" letter-spacing=".1">${this._esc(node.label)}</text>
      ${node.ip ? `<text x="0" y="${hh+31}" text-anchor="middle" font-size="9" font-weight="700" fill="${c}" fill-opacity=".85" font-family="'Courier New',monospace">${this._esc(node.ip)}</text>` : ''}
      <circle class="anchor" data-dir="N" cx="0" cy="${-hh}" r="5.5" fill="#0d1117" stroke="${c}" stroke-width="2.5"/>
      <circle class="anchor" data-dir="S" cx="0" cy="${hh}"  r="5.5" fill="#0d1117" stroke="${c}" stroke-width="2.5"/>
      <circle class="anchor" data-dir="W" cx="${-hw}" cy="0" r="5.5" fill="#0d1117" stroke="${c}" stroke-width="2.5"/>
      <circle class="anchor" data-dir="E" cx="${hw}"  cy="0" r="5.5" fill="#0d1117" stroke="${c}" stroke-width="2.5"/>`;
  }

  _renderLink(link) {
    const linkGroup = this.linksLayer.querySelector(`g[data-link="${link.id}"]`) || (() => {
      const g = document.createElementNS('http://www.w3.org/2000/svg','g');
      g.classList.add('link-group');
      g.dataset.link = link.id;
      this.linksLayer.appendChild(g);
      return g;
    })();

    const src = this.nodes.find(n => n.id === link.src);
    const tgt = this.nodes.find(n => n.id === link.tgt);
    if (!src || !tgt) { linkGroup.remove(); return; }

    const ifaceDef = TOPO_IFACES[link.iface] || TOPO_IFACES['1g'];
    const color = link.color || ifaceDef.color;
    const w     = ifaceDef.w;
    const dash  = link.style === 'dashed' ? '8,4' : link.style === 'dotted' ? '2,4' : '';
    const d     = this._linkPath(link);

    // Ponto médio para rótulo (centro entre src e tgt)
    const wps = link.waypoints || [];
    let midPt;
    if (wps.length > 0) {
      // Pega o waypoint do meio ou o ponto entre src e primeiro wp
      const allPts = [{x:src.x,y:src.y}, ...wps, {x:tgt.x,y:tgt.y}];
      const mid = allPts[Math.floor(allPts.length/2)];
      midPt = mid;
    } else {
      midPt = {x:(src.x+tgt.x)/2, y:(src.y+tgt.y)/2};
    }
    const mx = midPt.x, my = midPt.y;

    const userLbl  = link.label || '';

    // Vetor unitário src→tgt, usado para afastar os rótulos de IP/Interface
    // da borda do node por uma DISTÂNCIA FIXA em pixels (não uma % do
    // comprimento do link). Com % fixa, links curtos entre nodes grandes
    // colocavam o rótulo por baixo do próprio node — que é desenhado por
    // cima (nodes-layer vem depois de links-layer no SVG) — e o texto
    // sumia atrás do ícone. O clamp por linkLen evita que o rótulo passe do
    // meio do link em conexões muito curtas.
    const dx = tgt.x - src.x, dy = tgt.y - src.y;
    const linkLen = Math.hypot(dx, dy) || 1;
    const ux = dx / linkLen, uy = dy / linkLen;
    const raioA = (src.w || 64) / 2 + 14;
    const raioB = (tgt.w || 64) / 2 + 14;

    // Vetor PERPENDICULAR à linha, sempre apontando "para cima" (ny <= 0). O IP
    // é afastado da linha por esse vetor e a interface pelo oposto — em vez de
    // um deslocamento fixo em Y. Com o Y fixo (IP -8, interface +14), um link
    // quase vertical jogava os dois rótulos praticamente na mesma coluna e o
    // nome da interface cobria o IP (bug visto em produção — ver captura da
    // sessão). Na perpendicular eles se separam em qualquer ângulo do enlace.
    let nx = -uy, ny = ux;
    if (ny > 0) { nx = -nx; ny = -ny; }
    const OFF_IP = 12;   // afastamento perpendicular do rótulo de IP (um lado)
    const OFF_IF = 12;   // ...e do rótulo de interface (lado oposto)

    // Largura de cada rótulo, calculada ANTES da posição — precisa entrar na
    // conta da distância até o node (ver comentário abaixo).
    const ipLocalW = Math.max(68, (link.ip_local||'').length * 6 + 8);
    const ipRemoteW = Math.max(68, (link.ip_remote||'').length * 6 + 8);
    const ifAW = Math.max(52, (link.iface_a||'').length * 5.5 + 8);
    const ifBW = Math.max(52, (link.iface_b||'').length * 5.5 + 8);

    // IPs P2P (acima da linha, logo depois do rótulo de interface). O texto
    // usa text-anchor:middle, ou seja a METADE da largura do rótulo fica do
    // lado do node — por isso a distância inclui `largura/2`: sem isso, um
    // rótulo largo (nome de interface comprido, ex. "Eth-Trunk10") ficava com
    // a metade de trás ainda em cima do node mesmo com a "distância fixa"
    // aplicada só ao ponto central (bug visto em produção — texto cortado
    // pela borda do node em links horizontais).
    const clearIpA = Math.min(raioA + 16 + ipLocalW/2, linkLen * 0.45);
    const clearIpB = Math.min(raioB + 16 + ipRemoteW/2, linkLen * 0.45);
    const ipLocalX  = src.x + ux * clearIpA + nx * OFF_IP;
    const ipLocalY  = src.y + uy * clearIpA + ny * OFF_IP;
    const ipRemoteX = tgt.x - ux * clearIpB + nx * OFF_IP;
    const ipRemoteY = tgt.y - uy * clearIpB + ny * OFF_IP;
    const ipLocalHtml = link.ip_local ? `
      <rect x="${ipLocalX-ipLocalW/2}" y="${ipLocalY-10}" width="${ipLocalW}" height="14" rx="5" fill="#0b0f15" fill-opacity=".92" stroke="${color}" stroke-opacity=".35"/>
      <text class="link-ip" x="${ipLocalX}" y="${ipLocalY}">${this._esc(link.ip_local)}</text>` : '';
    const ipRemoteHtml = link.ip_remote ? `
      <rect x="${ipRemoteX-ipRemoteW/2}" y="${ipRemoteY-10}" width="${ipRemoteW}" height="14" rx="5" fill="#0b0f15" fill-opacity=".92" stroke="${color}" stroke-opacity=".35"/>
      <text class="link-ip" x="${ipRemoteX}" y="${ipRemoteY}">${this._esc(link.ip_remote)}</text>` : '';

    // Interfaces lado A e lado B — logo fora da borda do node, abaixo da
    // linha. Mesmo ajuste de `largura/2` explicado acima.
    const clearIfA = Math.min(raioA + ifAW/2 + 6, linkLen * 0.4);
    const clearIfB = Math.min(raioB + ifBW/2 + 6, linkLen * 0.4);
    const ifAx = src.x + ux * clearIfA - nx * OFF_IF;
    const ifAy = src.y + uy * clearIfA - ny * OFF_IF;
    const ifBx = tgt.x - ux * clearIfB - nx * OFF_IF;
    const ifBy = tgt.y - uy * clearIfB - ny * OFF_IF;
    const ifAHtml = link.iface_a ? `
      <rect x="${ifAx-ifAW/2}" y="${ifAy-8.5}" width="${ifAW}" height="12" rx="4" fill="#0b0f15" fill-opacity=".92"/>
      <text class="link-iface" x="${ifAx}" y="${ifAy}" fill="${color}">${this._esc(link.iface_a)}</text>` : '';
    const ifBHtml = link.iface_b ? `
      <rect x="${ifBx-ifBW/2}" y="${ifBy-8.5}" width="${ifBW}" height="12" rx="4" fill="#0b0f15" fill-opacity=".92"/>
      <text class="link-iface" x="${ifBx}" y="${ifBy}" fill="${color}">${this._esc(link.iface_b)}</text>` : '';

    // Rótulo do meio do link: nome (se houver), banda e VLAN em linhas
    // separadas — "VLAN 100" embaixo da banda lê muito melhor que o antigo
    // sufixo "V100" grudado na mesma linha ("100 Gbps V100").
    const lblLines = [];
    if (userLbl) lblLines.push({text: userLbl, fill: color, weight: 600});
    lblLines.push({text: ifaceDef.label, fill: null, weight: 400});
    if (link.vlan) lblLines.push({text: `VLAN ${link.vlan}`, fill: null, weight: 400});

    const lblLineH = 12;
    const bgW = Math.max(56, ...lblLines.map(l => l.text.length * 5.7 + 14));
    const bgH = lblLines.length * lblLineH + 4;
    const bgY = my - bgH/2 - 2;
    const labelHtml = `
      <rect x="${mx-bgW/2}" y="${bgY}" width="${bgW}" height="${bgH}" rx="6" fill="#0b0f15" fill-opacity=".92" stroke="${color}" stroke-opacity=".3"/>
      ${lblLines.map((l,i) => {
        const y = bgY + lblLineH*(i+1) - 1;
        const attrs = l.fill ? ` fill="${l.fill}" font-size="10" font-weight="${l.weight}"` : '';
        return `<text class="link-label" x="${mx}" y="${y}"${attrs}>${this._esc(l.text)}</text>`;
      }).join('')}`;

    // "Pacotes" trafegando de A pra B — usa o mesmo `d` do link como trilho de
    // <animateMotion> (SVG anima o círculo ao longo do path automaticamente,
    // na direção em que o path foi desenhado: src→tgt). Duração proporcional
    // ao comprimento do link (px/s constante) pra todo link parecer andar na
    // mesma "velocidade", em vez de um link curto parecer mais lento/rápido
    // que um comprido com a mesma duração fixa.
    // Durante o movimento os pacotes estão escondidos por CSS (body.nav-busy):
    // recriar dois <animateMotion> por enlace a cada frame era puro custo. O
    // _navBusy() redesenha os enlaces por inteiro quando o mapa para.
    const flowDur = Math.max(.6, Math.min(4, linkLen / 220));
    if (this._navOn) this._navSujo = true;
    const packetsHtml = this._navOn ? '' : `
      <circle class="link-packet" r="${Math.max(2.2, w*.9)}" fill="${color}" style="color:${color}">
        <animateMotion dur="${flowDur.toFixed(2)}s" repeatCount="indefinite" path="${d}"/>
      </circle>
      <circle class="link-packet" r="${Math.max(2.2, w*.9)}" fill="${color}" style="color:${color}">
        <animateMotion dur="${flowDur.toFixed(2)}s" repeatCount="indefinite" path="${d}" begin="${(flowDur/2).toFixed(2)}s"/>
      </circle>`;

    linkGroup.innerHTML = `
      <path class="link" d="${d}" stroke="${color}" stroke-width="${w}" stroke-dasharray="${dash}" stroke-linecap="round"/>
      <path class="link-glow" d="${d}" stroke="${color}" stroke-width="${w + 4}" style="color:${color}"/>
      <path class="link-flow" d="${d}" stroke-width="${Math.max(1.4, w - .6)}" style="color:${color}"/>
      ${packetsHtml}
      <path class="link-hit" d="${d}" data-id="${link.id}"/>
      ${ipLocalHtml}${ipRemoteHtml}
      ${ifAHtml}${ifBHtml}
      ${labelHtml}`;

    linkGroup.querySelector('.link-hit').addEventListener('click', e => {
      e.stopPropagation();
      this._select('link', link.id);
    });

    // Manter seleção visual se este link estiver selecionado
    if (this.selected && this.selected.type === 'link' && this.selected.id === link.id) {
      linkGroup.classList.add('selected');
    }
  }

  _renderLinks() {
    this.links.forEach(l => this._renderLink(l));
  }

  _renderAll() {
    this.nodesLayer.innerHTML = '';
    this.linksLayer.innerHTML = '';
    this.handlesLayer.innerHTML = '';
    this.areasLayer.innerHTML = '';
    // Áreas primeiro: ficam atrás e não devem cobrir o clique de nada.
    this.nodes.filter(n => n.type === 'area').forEach(n => this._renderNode(n));
    this.nodes.filter(n => n.type !== 'area').forEach(n => this._renderNode(n));
    this.links.forEach(l => this._renderLink(l));
    // Undo/redo pode trazer de volta (ou remover) nodes que estavam na
    // multi-seleção — descarta ids que não existem mais e reaplica o
    // destaque visual nos que sobreviveram.
    const validIds = new Set(this.nodes.map(n => n.id));
    this.selectedNodes.forEach(id => { if (!validIds.has(id)) this.selectedNodes.delete(id); });
    this.selectedNodes.forEach(id => {
      const el = this.nodesLayer.querySelector(`[data-id="${id}"]`);
      if (el) el.classList.add('multi-selected');
    });
    this._updateStatus();
  }

  // ── Properties panels ─────────────────────────────────────────────────────

  _showNodeProps(id) {
    const node = this.nodes.find(n => n.id === id);
    if (!node) return;

    if (node.type === 'area') {
      document.getElementById('props-body').innerHTML = `
        <div class="prop-title" style="color:${node.color}"><i class="fas fa-vector-square"></i> Área</div>
        <div class="prop-group">
          <label class="prop-label">Nome (rótulo no topo)</label>
          <input class="prop-input" id="pn-label" value="${this._esc(node.label)}" placeholder="Ex.: POP Central, Sala de servidores…">
        </div>
        <div class="prop-group">
          <label class="prop-label">Cor</label>
          <input type="color" class="prop-input" id="pn-color" value="${node.color}" style="height:36px;padding:2px">
        </div>
        <div class="prop-row">
          <div class="prop-group" style="flex:1;margin-bottom:0">
            <label class="prop-label">Largura</label>
            <input class="prop-input" id="pn-w" type="number" min="80" value="${Math.round(node.w)}">
          </div>
          <div class="prop-group" style="flex:1;margin-bottom:0">
            <label class="prop-label">Altura</label>
            <input class="prop-input" id="pn-h" type="number" min="50" value="${Math.round(node.h)}">
          </div>
        </div>
        <div class="prop-group" style="font-size:.68rem;color:var(--muted);margin-top:10px">
          <i class="fas fa-circle-info"></i> Arraste a borda para mover, os cantos para redimensionar.
        </div>
        <button class="prop-btn primary" onclick="topo._applyNodeProps('${id}')"><i class="fas fa-check"></i> Aplicar</button>
        <button class="prop-btn danger" onclick="topo._deleteSelected()"><i class="fas fa-trash"></i> Remover</button>`;
      return;
    }

    if (node.type === 'text_box') {
      document.getElementById('props-body').innerHTML = `
        <div class="prop-title" style="color:${node.color}"><i class="fas fa-font"></i> Texto / Legenda</div>
        <div class="prop-group">
          <label class="prop-label">Conteúdo (Enter = nova linha)</label>
          <textarea class="prop-input" id="pn-label" rows="4"
            style="resize:vertical;font-family:'Segoe UI',sans-serif;font-size:.82rem">${this._esc(node.label)}</textarea>
        </div>
        <div class="prop-group">
          <label class="prop-label">Cor</label>
          <input type="color" class="prop-input" id="pn-color" value="${node.color}" style="height:36px;padding:2px">
        </div>
        <button class="prop-btn primary" onclick="topo._applyNodeProps('${id}')"><i class="fas fa-check"></i> Aplicar</button>
        <button class="prop-btn danger" onclick="topo._deleteSelected()"><i class="fas fa-trash"></i> Remover</button>`;
      return;
    }

    // Node de grupo tem painel próprio: não é um equipamento (não tem IP de
    // gerência, nem ícone trocável, nem acesso), é a porta de entrada de um
    // sub-mapa — o que interessa aqui é abrir, renomear ou desfazer.
    if (node.grupo) { this._showGrupoProps(node); return; }

    const def = TOPO_DEVICES[node.type] || TOPO_DEVICES.host;

    let accessHtml = '';
    if (node.acesso_id) {
      const proto = (node.protocolo || '').toUpperCase();
      const protoIcons = {SSH:'terminal',TELNET:'terminal',HTTP:'globe',HTTPS:'lock',WINBOX:'network-wired',FTP:'file',FTPS:'file'};
      const icon = protoIcons[proto] || 'plug';
      accessHtml = `
        <div class="prop-group" style="border-top:1px solid var(--border);padding-top:10px;margin-top:4px">
          <label class="prop-label" style="color:var(--cyan)">Acessar Host</label>
          <button class="prop-btn" style="background:rgba(63,185,80,.12);border-color:var(--green);color:var(--green)"
            onclick="topo._acessarHost('${id}')">
            <i class="fas fa-${icon}"></i> ${proto} — ${this._esc(node.ip)}${node.porta ? ':'+node.porta : ''}
          </button>
        </div>`;
    }

    const tipoOpts = Object.entries(TOPO_DEVICES)
      .filter(([k]) => k !== 'text_box' && k !== 'grupo' && k !== 'area')
      .map(([k,v]) => `<option value="${k}" ${node.type===k?'selected':''}>${v.label}</option>`).join('');

    const autoNote = node.acesso_id ? `
      <div class="prop-group" style="font-size:.68rem;color:var(--muted)">
        ${node.type_manual
          ? `<i class="fas fa-lock"></i> Ícone fixado manualmente — não muda mais sozinho ao reimportar hosts.
             <button class="prop-btn" onclick="topo._resetNodeTypeAuto('${id}')" style="margin-top:6px"><i class="fas fa-rotate"></i> Voltar a ícone automático</button>`
          : `<i class="fas fa-wand-magic-sparkles"></i> Ícone automático (pela função do CRM). Trocar abaixo fixa o ícone escolhido.`}
      </div>` : '';

    // Fica no topo do painel, antes dos campos de edição: é ação de consulta
    // (o que esse host tem configurado), não propriedade do desenho — e no fim
    // do painel exigia rolar pra descobrir que existe.
    const submapHtml = node.submap_id ? `
      <button class="prop-btn" id="btn-submap"
        style="background:rgba(0,217,255,.12);border-color:var(--cyan);color:var(--cyan);margin:0 0 12px"
        onclick="topo._abrirOuCriarSubmapa('${id}')"
        title="Abre o sub-mapa vinculado a este nó">
        <i class="fas fa-diagram-project"></i> Abrir sub-mapa →
      </button>` : `
      <button class="prop-btn" id="btn-submap"
        onclick="topo._abrirOuCriarSubmapa('${id}')"
        title="Cria um sub-mapa vazio vinculado a este nó (duplo-clique no nó também abre)">
        <i class="fas fa-diagram-project"></i> Criar sub-mapa
      </button>`;

    const l2vpnHtml = node.acesso_id ? `
      <button class="prop-btn" id="btn-l2vpn"
        style="background:rgba(188,140,255,.12);border-color:var(--purple);color:var(--purple);margin:0 0 12px"
        onclick="topo.mostrarL2vpn('${id}')"
        title="VSI/VPLS, VPWS e L2VC lidos do backup mais recente deste host">
        <i class="fas fa-network-wired"></i> Mostrar L2VPN
      </button>` : '';

    // Cabeçalho do painel com o próprio ícone do device (não um genérico
    // fa-server): é o mesmo desenho que está selecionado no canvas, então o
    // olho liga painel↔node sem precisar reler o nome.
    // Botão de portas PON: só em node que é OLT. A checagem olha o tipo do
    // ícone, a função cadastrada no CRM e o nome do host — uma OLT importada
    // sem função vira `host` no mapeamento automático e ficaria sem o botão.
    const ehOlt = node.acesso_id && (
      node.type === 'olt' ||
      /\bolt\b|ma5\d{3}/i.test(`${node.funcao || ''} ${node.label || ''}`));
    const ponHtml = ehOlt ? `
      <button class="prop-btn" id="btn-pon"
        style="background:rgba(0,217,255,.12);border-color:var(--cyan);color:var(--cyan);margin:0 0 12px"
        onclick="topo.mostrarPon('${id}')"
        title="Placas e portas PON lidas do backup — consulta de porta e liga/desliga do laser (OLT Huawei)">
        <i class="fas fa-diagram-successor"></i> Portas PON
      </button>` : '';

    document.getElementById('props-body').innerHTML = `
      <div class="prop-hero">
        <div class="prop-hero-icon" style="background:${node.color}1f;color:${node.color}">
          <svg viewBox="0 0 48 48">${TOPO_ICONS[def.icon]||''}</svg>
        </div>
        <div class="prop-hero-txt">
          <b>${this._esc(node.label || def.label)}</b>
          <span>${def.label}${node.acesso_id ? ' · host do CRM' : ''}</span>
        </div>
      </div>
      ${l2vpnHtml}
      ${ponHtml}
      ${submapHtml}
      <div class="prop-group">
        <label class="prop-label">Nome</label>
        <input class="prop-input" id="pn-label" value="${this._esc(node.label)}">
      </div>
      <div class="prop-group">
        <label class="prop-label">Ícone / Tipo</label>
        <select class="prop-select" id="pn-type">${tipoOpts}</select>
      </div>
      ${autoNote}
      <div class="prop-group">
        <label class="prop-label">IP de Gerência</label>
        <input class="prop-input" id="pn-ip" placeholder="192.168.1.1" value="${this._esc(node.ip||'')}">
      </div>
      <div class="prop-group">
        <label class="prop-label">Cor</label>
        <input type="color" class="prop-input" id="pn-color" value="${node.color}" style="height:36px;padding:2px">
      </div>
      ${accessHtml}
      <button class="prop-btn primary" onclick="topo._applyNodeProps('${id}')"><i class="fas fa-check"></i> Aplicar</button>
      <button class="prop-btn danger" onclick="topo._deleteSelected()"><i class="fas fa-trash"></i> Remover</button>`;
  }

  _showGrupoProps(node) {
    const def = TOPO_DEVICES.grupo;
    const membros = node.grupo_membros || [];
    const mostrar = membros.slice(0, 8);
    const lista = mostrar.map(m =>
      `<div class="grupo-membro"><i class="fas fa-circle-nodes"></i>${this._esc(m.label || m.id)}</div>`).join('')
      + (membros.length > mostrar.length
          ? `<div class="grupo-membro mais">e mais ${membros.length - mostrar.length}…</div>` : '');

    document.getElementById('props-body').innerHTML = `
      <div class="prop-hero">
        <div class="prop-hero-icon" style="background:${node.color}1f;color:${node.color}">
          <svg viewBox="0 0 48 48">${TOPO_ICONS[def.icon]||''}</svg>
        </div>
        <div class="prop-hero-txt">
          <b>${this._esc(node.label || def.label)}</b>
          <span>${membros.length} dispositivo${membros.length === 1 ? '' : 's'} agrupados</span>
        </div>
      </div>
      ${node.submap_id ? `
      <button class="prop-btn" style="background:rgba(0,217,255,.12);border-color:var(--cyan);color:var(--cyan);margin:0 0 12px"
        onclick="topo._abrirOuCriarSubmapa('${node.id}')"
        title="Abre o mapa só com estes dispositivos (duplo-clique no ícone faz o mesmo)">
        <i class="fas fa-diagram-project"></i> Abrir mapa do grupo →
      </button>` : `
      <div class="prop-group" style="font-size:.68rem;color:var(--red)">
        <i class="fas fa-triangle-exclamation"></i> Este grupo ficou sem mapa vinculado.
      </div>`}
      <div class="prop-group">
        <label class="prop-label">Nome do grupo</label>
        <input class="prop-input" id="pn-label" value="${this._esc(node.label)}">
      </div>
      <div class="prop-group">
        <label class="prop-label">Cor</label>
        <input type="color" class="prop-input" id="pn-color" value="${node.color}" style="height:36px;padding:2px">
      </div>
      ${membros.length ? `
      <div class="prop-group">
        <label class="prop-label">Dentro do grupo</label>
        <div class="grupo-lista">${lista}</div>
      </div>` : ''}
      <button class="prop-btn primary" onclick="topo._applyNodeProps('${node.id}')"><i class="fas fa-check"></i> Aplicar</button>
      <button class="prop-btn" onclick="topo.desagrupar('${node.id}')"
        title="Traz os dispositivos de volta para este mapa e exclui o mapa do grupo">
        <i class="fas fa-object-ungroup"></i> Desagrupar
      </button>
      <button class="prop-btn danger" onclick="topo._deleteSelected()"><i class="fas fa-trash"></i> Remover</button>`;
  }

  async _resetNodeTypeAuto(id) {
    const node = this.nodes.find(n => n.id === id);
    if (!node) return;
    this._saveHistory();
    delete node.type_manual;
    this._setDirty();
    await this._refreshCrmNodeTypes();
    this._toast('Ícone automático restaurado');
    if (this.selected && this.selected.type === 'node' && this.selected.id === id) {
      this._showNodeProps(id);
    }
  }

  _acessarHost(nodeId) {
    const node = this.nodes.find(n => n.id === nodeId);
    if (!node || !node.acesso_id) return;
    const proto = (node.protocolo || '').toUpperCase();
    if (proto === 'HTTP' || proto === 'HTTPS') {
      const porta = node.porta ? ':' + node.porta : '';
      window.open(`${proto.toLowerCase()}://${node.ip}${porta}`, '_blank');
      return;
    }
    const acesso = {
      id: node.acesso_id, tipo: node.label, host: node.ip,
      porta: node.porta, protocolo: node.protocolo,
      usuario: node.usuario || '', cliente_id: node.cliente_id || this.clienteId,
    };
    localStorage.setItem('acessoPendente', JSON.stringify(acesso));
    window.open(`/clientes/terminal/?cliente=${acesso.cliente_id}`, '_blank');
  }

  _applyNodeProps(id) {
    const node = this.nodes.find(n => n.id === id);
    if (!node) return;
    this._saveHistory();
    node.label = document.getElementById('pn-label').value;
    const ipEl = document.getElementById('pn-ip');
    if (ipEl) node.ip = ipEl.value;
    const wEl = document.getElementById('pn-w');
    if (wEl) node.w = Math.max(80, parseInt(wEl.value) || node.w);
    const hEl = document.getElementById('pn-h');
    if (hEl) node.h = Math.max(50, parseInt(hEl.value) || node.h);
    node.color = document.getElementById('pn-color').value;
    const typeEl = document.getElementById('pn-type');
    if (typeEl && typeEl.value !== node.type) {
      node.type = typeEl.value;
      node.type_manual = true; // usuário escolheu na mão — reimportar hosts não sobrescreve mais
    }
    this._renderNode(node);
    this._setDirty();
    this._toast('Aplicado');
    this._showNodeProps(id); // atualiza a nota de ícone automático/manual no painel
  }

  // ── L2VPN: VSI / VPLS / VPWS / L2VC documentados a partir do backup ────────
  //
  // O backend (`/clientes/acessos/<id>/l2vpn-backup/`) lê o backup mais recente
  // do host, extrai os serviços L2 e já resolve cada peer/neighbor do túnel
  // para o host do outro lado — aqui só falta pintar isso e ligar o clique do
  // peer ao nó correspondente no diagrama.

  mostrarL2vpn(nodeId) {
    const node = this.nodes.find(n => n.id === nodeId);
    if (!node || !node.acesso_id) return;
    this.abrirL2vpn(node.acesso_id, node.label);
  }

  async abrirL2vpn(acessoId, titulo) {
    this._l2vpn = {acessoId, titulo: titulo || '', filtro: 'todos', busca: '',
                   abertos: new Set(), dados: null, clone: null,
                   peersLista: null, ifacesLista: null, _ultimoErro: ''};
    this._renderL2vpnModal(`
      <div style="padding:40px;text-align:center;color:var(--muted)">
        <i class="fas fa-circle-notch fa-spin" style="font-size:1.6rem"></i>
        <div style="margin-top:12px;font-size:.82rem">Lendo o backup do equipamento…</div>
      </div>`);
    try {
      const r = await fetch(`/clientes/acessos/${acessoId}/l2vpn-backup/`, {
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      // Sessão expirada: o @login_required devolve um 302 pro /auth/login/ e o
      // .json() estouraria com um erro genérico — avisa o motivo de verdade.
      if (r.redirected || r.status === 401 || r.status === 403) {
        this.fecharL2vpn();
        this._toast('Sessão expirada — recarregue a página e faça login', 'error');
        return;
      }
      if (!r.ok) throw new Error(r.status);
      if (!this._l2vpn || this._l2vpn.acessoId !== acessoId) return; // modal já trocou
      const dados = await r.json();
      // Índice estável por serviço: é ele que vai nos onclick (expandir/ir pro
      // peer), nunca o nome — nome vem da config do equipamento e escapar HTML
      // não protege dentro de uma string JS de atributo.
      (dados.servicos || []).forEach((s, i) => { s.idx = i; });
      this._l2vpn.dados = dados;
      this._pintarL2vpn();
    } catch (e) {
      this._renderL2vpnModal(`
        <div style="padding:36px;text-align:center;color:var(--red);font-size:.85rem">
          <i class="fas fa-triangle-exclamation" style="font-size:1.5rem;display:block;margin-bottom:10px"></i>
          Não foi possível ler os serviços L2VPN deste host.
        </div>
        <div class="modal-footer"><button class="btn-cancel" onclick="topo.fecharL2vpn()">Fechar</button></div>`);
    }
  }

  fecharL2vpn() {
    const el = document.getElementById('l2vpn-modal');
    if (el) el.remove();
    this._l2vpn = null;
  }

  _renderL2vpnModal(html) {
    let el = document.getElementById('l2vpn-modal');
    if (!el) {
      el = document.createElement('div');
      el.id = 'l2vpn-modal';
      el.className = 'modal-overlay';
      el.addEventListener('mousedown', e => { if (e.target === el) this.fecharL2vpn(); });
      document.body.appendChild(el);
    }
    el.innerHTML = `<div class="modal-box l2vpn-box">${html}</div>`;
  }

  _l2vpnFiltrados() {
    const {dados, filtro, busca} = this._l2vpn;
    const termo = busca.trim().toLowerCase();
    return (dados.servicos || []).filter(s => {
      if (filtro !== 'todos' && s.tipo !== filtro) return false;
      if (!termo) return true;
      const alvo = [s.nome, s.id, s.grupo, s.descricao, s.vlan, s.tecnologia,
                    ...s.peers.map(p => `${p.ip} ${(p.destino || {}).nome || ''}`),
                    ...s.interfaces.map(i => `${i.nome} ${i.descricao}`)].join(' ').toLowerCase();
      return alvo.includes(termo);
    });
  }

  _pintarL2vpn() {
    const {dados, filtro, busca} = this._l2vpn;

    if (!dados.tem_backup) {
      this._renderL2vpnModal(`
        ${this._l2vpnCabecalho()}
        <div class="l2vpn-vazio">
          <i class="fas fa-inbox"></i>
          <div>Este host ainda não tem backup coletado.</div>
          <div class="l2vpn-dica">Os serviços L2VPN são lidos do backup — habilite o backup
            do equipamento no cadastro do acesso para documentá-los aqui.</div>
        </div>
        <div class="modal-footer"><button class="btn-cancel" onclick="topo.fecharL2vpn()">Fechar</button></div>`);
      return;
    }

    const total = (dados.servicos || []).length;
    if (!total) {
      this._renderL2vpnModal(`
        ${this._l2vpnCabecalho()}
        <div class="l2vpn-vazio">
          <i class="fas fa-diagram-project"></i>
          <div>Nenhum VSI, VPLS, VPWS ou L2VC encontrado no backup deste host.</div>
          <div class="l2vpn-dica">Backup de ${this._esc(dados.data_backup || '')}.</div>
        </div>
        <div class="modal-footer"><button class="btn-cancel" onclick="topo.fecharL2vpn()">Fechar</button></div>`);
      return;
    }

    const lista = this._l2vpnFiltrados();
    const chips = [['todos', 'Todos', total],
                   ['vpls', 'VPLS / VSI', dados.resumo.vpls],
                   ['vpws', 'VPWS', dados.resumo.vpws],
                   ['l2vc', 'L2VC', dados.resumo.l2vc]]
      .filter(([k, , n]) => k === 'todos' || n)
      .map(([k, rotulo, n]) => `
        <button class="l2vpn-chip ${filtro === k ? 'ativo' : ''} tipo-${k}"
          onclick="topo._l2vpnFiltrar('${k}')">${rotulo} <b>${n}</b></button>`).join('');

    this._renderL2vpnModal(`
      ${this._l2vpnCabecalho()}
      <div class="l2vpn-barra">
        <div class="l2vpn-chips">${chips}</div>
        <input class="l2vpn-busca" id="l2vpn-busca" placeholder="Filtrar por nome, id, VLAN, peer…"
               value="${this._esc(busca)}" autocomplete="off">
      </div>
      <div class="l2vpn-lista">
        ${lista.length
          ? lista.map(s => this._l2vpnLinha(s)).join('')
          : `<div class="l2vpn-vazio"><i class="fas fa-filter"></i><div>Nenhum serviço com esse filtro.</div></div>`}
      </div>
      <div class="modal-footer">
        <button class="btn-cancel" onclick="topo._l2vpnCopiar()"><i class="fas fa-copy"></i> Copiar tabela</button>
        <button class="btn-cancel" onclick="topo.fecharL2vpn()">Fechar</button>
      </div>`);

    const busca_el = document.getElementById('l2vpn-busca');
    if (busca_el) {
      busca_el.addEventListener('input', e => {
        this._l2vpn.busca = e.target.value;
        this._pintarL2vpn();
        const novo = document.getElementById('l2vpn-busca');
        if (novo) { novo.focus(); novo.setSelectionRange(novo.value.length, novo.value.length); }
      });
    }
  }

  _l2vpnCabecalho() {
    const {dados, titulo} = this._l2vpn;
    const identidade = Object.entries((dados && dados.host && dados.host.ips_identidade) || {});
    return `
      <div class="l2vpn-head">
        <div>
          <div class="l2vpn-titulo"><i class="fas fa-diagram-project"></i>
            Serviços L2VPN — ${this._esc(titulo || (dados.host && dados.host.nome) || '')}</div>
          <div class="l2vpn-sub">
            ${dados.host ? this._esc(dados.host.ip) : ''}
            ${dados.data_backup ? `· backup de ${this._esc(dados.data_backup)}` : ''}
            ${identidade.length
              ? `· conhecido pelos vizinhos como ${identidade.slice(0, 3).map(
                   ([ip, origem]) => `<b title="${this._esc(origem)}">${this._esc(ip)}</b>`).join(', ')}`
              : ''}
          </div>
        </div>
        <i class="fas fa-times l2vpn-fechar" onclick="topo.fecharL2vpn()" title="Fechar (Esc)"></i>
      </div>`;
  }

  _l2vpnLinha(s) {
    const aberto = this._l2vpn.abertos.has(s.idx);
    const peers = s.peers.length
      ? s.peers.map((p, i) => this._l2vpnPeer(p, s.idx, i)).join('')
      : `<span class="l2vpn-peer sem-peer" title="O serviço está configurado sem peer — túnel incompleto no equipamento"><i class="fas fa-unlink"></i> sem peer</span>`;

    const meta = [
      s.vlan ? `VLAN ${this._esc(s.vlan)}` : '',
      s.mtu ? `MTU ${this._esc(s.mtu)}` : '',
      s.grupo ? `grupo ${this._esc(s.grupo)}` : '',
      s.sinalizacao ? this._esc(s.sinalizacao.toUpperCase()) : '',
    ].filter(Boolean).join(' · ');

    return `
      <div class="l2vpn-item ${aberto ? 'aberto' : ''}">
        <div class="l2vpn-item-head" onclick="topo._l2vpnToggle(${s.idx})">
          <span class="l2vpn-badge tipo-${s.tipo}">${this._esc(s.tecnologia)}</span>
          <span class="l2vpn-id">${s.id ? '#' + this._esc(s.id) : '—'}</span>
          <span class="l2vpn-nome">${this._esc(s.nome)}
            ${s.descricao && s.descricao !== s.nome ? `<em>${this._esc(s.descricao)}</em>` : ''}</span>
          <span class="l2vpn-meta">${meta}</span>
          ${this._l2vpnPodeClonar(s) ? `<button class="l2vpn-clonar"
            onclick="event.stopPropagation();topo.clonarL2vpn(${s.idx})"
            title="Criar um serviço novo a partir deste, editando o que muda">
            <i class="fas fa-clone"></i> Clonar</button>` : ''}
          <i class="fas fa-chevron-${aberto ? 'up' : 'down'} l2vpn-seta"></i>
        </div>
        <div class="l2vpn-peers">${peers}</div>
        ${aberto ? this._l2vpnDetalhe(s) : ''}
      </div>`;
  }

  _l2vpnPeer(p, idxServico, idxPeer) {
    const destino = p.destino;
    const pwid = p.pw_id ? ` <em>pw ${this._esc(p.pw_id)}</em>` : '';
    if (!destino) {
      return `<span class="l2vpn-peer nao-ident"
        title="Nenhum host deste cliente tem esse IP como loopback/LSR-ID nos backups">
        <i class="fas fa-question-circle"></i> ${this._esc(p.ip)}${pwid}</span>`;
    }
    return `<button class="l2vpn-peer ident" onclick="topo._irParaPeer(${idxServico}, ${idxPeer})"
      title="${this._esc(destino.nome)} — ${this._esc(p.ip)} (${this._esc(destino.origem)}). Clique para ir até o host no diagrama.">
      <i class="fas fa-arrow-right-arrow-left"></i> ${this._esc(destino.nome)}
      <span class="l2vpn-peer-ip">${this._esc(p.ip)}</span>${pwid}</button>`;
  }

  _l2vpnDetalhe(s) {
    const ifaces = s.interfaces.length
      ? s.interfaces.map(i => `
          <li><code>${this._esc(i.nome)}</code>
            ${i.vlan ? `<span class="l2vpn-tag">dot1q ${this._esc(i.vlan)}</span>` : ''}
            ${i.descricao ? `<em>${this._esc(i.descricao)}</em>` : ''}</li>`).join('')
      : '<li class="l2vpn-nada">nenhuma interface de acesso encontrada no backup</li>';

    return `
      <div class="l2vpn-detalhe">
        <div class="l2vpn-col">
          <div class="l2vpn-col-titulo">Interfaces de acesso</div>
          <ul class="l2vpn-ifaces">${ifaces}</ul>
          ${s.pw_type ? `<div class="l2vpn-col-titulo" style="margin-top:8px">pw-type</div>
            <div class="l2vpn-nada">${this._esc(s.pw_type)}${s.encapsulamento ? ' · ' + this._esc(s.encapsulamento) : ''}</div>` : ''}
        </div>
        <div class="l2vpn-col">
          <div class="l2vpn-col-titulo">Config no equipamento
            ${s.linha ? `<span class="l2vpn-tag">linha ${s.linha}</span>` : ''}</div>
          <pre class="l2vpn-config">${this._esc(s.trecho || '—')}</pre>
        </div>
      </div>`;
  }

  // ── Clonar um serviço L2VPN ───────────────────────────────────────────────
  //
  // Fluxo em três passos, igual ao da automação BGP: formulario pre-preenchido
  // com a config de origem -> comandos gerados pelo backend num textarea
  // editavel -> confirmacao explicita antes de aplicar no equipamento.

  _l2vpnPodeClonar(s) {
    return ['huawei', 'datacom', 'mikrotik'].includes(s.vendor);
  }

  _l2vpnDicaVendor(s) {
    if (s.vendor === 'mikrotik') {
      return 'RouterOS: o nome é o da interface VPLS; cada "interface de acesso" vira uma VLAN criada sobre ela.';
    }
    if (s.vendor === 'datacom') {
      return 'DmOS: o serviço nasce dentro do grupo informado; a config é confirmada com <code>commit</code>.';
    }
    return s.tipo === 'l2vc'
      ? 'Huawei: o pseudowire é criado na sub-interface (interface.VLAN), com <code>mpls l2vc</code>.'
      : 'Huawei: cria a VLAN e o VSI, e faz o <code>l2 binding vsi</code> na <code>Vlanif</code> dela.';
  }

  clonarL2vpn(idx) {
    const s = (this._l2vpn.dados.servicos || [])[idx];
    if (!s) return;
    // Copia profunda: editar o formulário não pode sujar a listagem por trás.
    this._l2vpn.clone = {
      idx,
      origem: s,
      // `form` guarda o que está digitado. Todo re-render do painel (adicionar
      // peer, gerar comandos, confirmar) parte daqui — renderizar direto da
      // origem apagaria o que o operador já tinha editado.
      form: {
        nome: `${s.nome}-CLONE`,
        id: String(this._l2vpn.dados.id_sugerido || ''),
        vlan: s.vlan || '',
        mtu: s.mtu || '',
        grupo: s.grupo || '',
        descricao: s.descricao || '',
        flow_label: s.flow_label || '',
        // `pw-type vlan N` (DmOS): a tag que trafega DENTRO do túnel, que não
        // é obrigatoriamente a VLAN de acesso — por isso campo próprio.
        pw_vlan: s.pw_vlan || '',
        ldp_lsr_id: (this._l2vpn.dados.ldp || {}).lsr_id || '',
      },
      // Fechar a sessão LDP targeted junto: já vem marcado, porque o caso em
      // que ela falta é justamente o de clonar para um peer novo — e sem
      // sessão o pseudowire nasce down.
      ldp: true,
      peers: s.peers.map(p => p.ip),
      // VLAN por interface fica VAZIA quando é a mesma do serviço: assim ela
      // herda o campo "VLAN (dot1q)" e mudar a VLAN do clone num lugar só já
      // vale pra todas as interfaces. Só fica explícita quando a origem
      // realmente usa uma VLAN diferente naquela interface.
      interfaces: s.interfaces.length
        ? s.interfaces.map(i => ({nome: i.nome, vlan: String(i.vlan || '') === String(s.vlan || '') ? '' : i.vlan}))
        : [{nome: '', vlan: ''}],
      // Portas físicas onde a VLAN entra — só o VSI Huawei usa (ele é aplicado
      // na Vlanif). Começa vazia: a interface da origem é a Vlanif dela, que
      // não serve de palpite pra porta física do serviço novo.
      portas: [{nome: '', modo: 'tagged'}],
      comandos: null,
      resultado: null,
      confirmando: false,
      erro: '',
    };
    this._pintarL2vpnClone();
  }

  _l2vpnVoltarLista() {
    if (!this._l2vpn) return;
    this._l2vpn.clone = null;
    this._pintarL2vpn();
  }

  _pintarL2vpnClone() {
    const c = this._l2vpn.clone;
    const s = c.origem;
    const f = c.form;
    const ehDatacom = s.vendor === 'datacom';

    const linhasPeer = c.peers.map((ip, i) => `
      <div class="l2vpn-linha-lista l2vpn-combo">
        <input class="l2vpn-input" id="cl-peer-${i}" value="${this._esc(ip)}" autocomplete="off"
               placeholder="Digite o IP ou busque pelo nome do host">
        <div class="l2vpn-drop" id="cl-peer-drop-${i}"></div>
        <button class="l2vpn-mini danger" onclick="topo._l2vpnRemoverPeer(${i})" title="Remover peer">
          <i class="fas fa-times"></i></button>
      </div>`).join('');

    const vsiHuawei = s.vendor === 'huawei' && s.tipo !== 'l2vc';

    const linhasIface = c.interfaces.map((iface, i) => `
      <div class="l2vpn-linha-lista l2vpn-combo">
        <input class="l2vpn-input" id="cl-if-${i}" value="${this._esc(iface.nome)}" autocomplete="off"
               placeholder="${s.vendor === 'mikrotik' ? 'vlan200-CLIENTE (nome da VLAN nova)' : 'busque pela porta ou pela descrição'}">
        ${s.vendor === 'mikrotik' ? '' : `<div class="l2vpn-drop" id="cl-if-drop-${i}"></div>`}
        <input class="l2vpn-input curto" id="cl-ifvlan-${i}" value="${this._esc(iface.vlan || '')}"
               placeholder="VLAN" title="Vazio = usa a VLAN do serviço acima">
        <button class="l2vpn-mini danger" onclick="topo._l2vpnRemoverIface(${i})" title="Remover interface">
          <i class="fas fa-times"></i></button>
      </div>`).join('');

    // Huawei VSI: o acesso é sempre a Vlanif da VLAN designada, então em vez
    // de "interface de acesso" o que se escolhe são as PORTAS FÍSICAS por onde
    // a VLAN entra, cada uma tagged ou untagged.
    const linhasPorta = (c.portas || []).map((porta, i) => `
      <div class="l2vpn-linha-lista l2vpn-combo">
        <input class="l2vpn-input" id="cl-porta-${i}" value="${this._esc(porta.nome)}" autocomplete="off"
               placeholder="busque pela porta ou pela descrição">
        <div class="l2vpn-drop" id="cl-porta-drop-${i}"></div>
        <select class="l2vpn-select" id="cl-portamodo-${i}" title="tagged = port trunk allow-pass · untagged = port default vlan">
          <option value="tagged" ${porta.modo !== 'untagged' ? 'selected' : ''}>tagged</option>
          <option value="untagged" ${porta.modo === 'untagged' ? 'selected' : ''}>untagged</option>
        </select>
        <button class="l2vpn-mini danger" onclick="topo._l2vpnRemoverPorta(${i})" title="Remover porta">
          <i class="fas fa-times"></i></button>
      </div>`).join('');

    const blocoAcesso = vsiHuawei ? `
      <div class="l2vpn-form-grupo" style="grid-column:1/-1">
        <label class="l2vpn-label">Portas físicas onde a VLAN entra
          <button class="l2vpn-mini" onclick="topo._l2vpnAddPorta()"><i class="fas fa-plus"></i></button>
          <span class="l2vpn-tag">opcional</span></label>
        ${linhasPorta}
        <div class="l2vpn-aviso">
          O VSI é aplicado em <b>interface Vlanif${this._esc(c.form.vlan || '<VLAN>')}</b>, criada e
          vinculada automaticamente. Aqui você libera a VLAN nas portas físicas:
          <b>tagged</b> gera <code>port trunk allow-pass vlan</code>, <b>untagged</b> gera
          <code>port default vlan</code>. O <code>port link-type</code> da porta não é alterado —
          trocar o tipo de uma porta em produção derruba o que já passa por ela.
        </div>
      </div>` : `
      <div class="l2vpn-form-grupo">
        <label class="l2vpn-label">Interfaces de acesso
          <button class="l2vpn-mini" onclick="topo._l2vpnAddIface()"><i class="fas fa-plus"></i></button></label>
        ${linhasIface}
      </div>`;

    const erro = c.erro ? `
      <div class="l2vpn-form-grupo" style="grid-column:1/-1">
        <div class="l2vpn-erro"><i class="fas fa-circle-exclamation"></i> ${this._esc(c.erro)}</div>
      </div>` : '';

    // O preview fica sempre visível: os comandos que vão pro equipamento são
    // mostrados (e continuam editáveis) antes e durante a confirmação.
    const passoComandos = c.comandos ? `
      <div class="l2vpn-form-grupo" style="grid-column:1/-1">
        <label class="l2vpn-label" style="color:var(--cyan)">
          <i class="fas fa-terminal"></i> Comandos que serão enviados a
          ${this._esc(this._l2vpn.dados.host.nome)} — revise e edite se precisar
          <span class="l2vpn-tag">${c.comandos.length} linhas</span>
        </label>
        <textarea class="l2vpn-config editavel" id="cl-comandos" rows="${Math.min(c.comandos.length + 1, 18)}"
          spellcheck="false">${this._esc(c.comandos.join('\n'))}</textarea>
      </div>` : `
      <div class="l2vpn-form-grupo" style="grid-column:1/-1">
        <div class="l2vpn-aviso"><i class="fas fa-circle-info"></i>
          Clique em <b>Gerar comandos</b> para ver a config exata que será enviada ao equipamento.
          Nada é aplicado até você revisar e confirmar.</div>
      </div>`;

    const resultado = c.resultado ? `
      <div class="l2vpn-form-grupo" style="grid-column:1/-1">
        <label class="l2vpn-label" style="color:${c.resultado.status === 'sucesso' ? 'var(--green)' : 'var(--red)'}">
          ${c.resultado.status === 'sucesso'
            ? '<i class="fas fa-check-circle"></i> Aplicado no equipamento'
            : '<i class="fas fa-triangle-exclamation"></i> O equipamento recusou'}
        </label>
        <pre class="l2vpn-config">${this._esc(c.resultado.output || '(sem saída)')}</pre>
        ${c.resultado.status === 'sucesso' ? `<div class="l2vpn-aviso">
          O serviço novo só vai aparecer nesta lista depois do próximo backup do host — a listagem é
          lida do backup, não do equipamento ao vivo.</div>` : ''}
      </div>` : '';

    const acoes = c.resultado && c.resultado.status === 'sucesso'
      ? `<button class="btn-ok" onclick="topo._l2vpnVoltarLista()"><i class="fas fa-list"></i> Voltar aos serviços</button>`
      : (c.confirmando
        ? `<div class="l2vpn-confirma">
             <span><i class="fas fa-triangle-exclamation"></i>
               Aplicar em <b>${this._esc(this._l2vpn.dados.host.nome)}</b> (${this._esc(this._l2vpn.dados.host.ip)}) agora?</span>
             <button class="btn-ok" onclick="topo._l2vpnAplicar(true)">Sim, aplicar</button>
             <button class="btn-cancel" onclick="topo._l2vpnCancelarConfirma()">Cancelar</button>
           </div>`
        : `<button class="btn-cancel" onclick="topo._l2vpnVoltarLista()">Voltar</button>
           <button class="btn-ok" onclick="topo._l2vpnGerar()">
             <i class="fas fa-wand-magic-sparkles"></i> ${c.comandos ? 'Gerar de novo' : 'Gerar comandos'}</button>
           ${c.comandos ? `<button class="btn-ok perigo" onclick="topo._l2vpnAplicar(false)">
             <i class="fas fa-bolt"></i> Aplicar no equipamento</button>` : ''}`);

    this._renderL2vpnModal(`
      <div class="l2vpn-head">
        <div>
          <div class="l2vpn-titulo"><i class="fas fa-clone"></i> Clonar ${this._esc(s.tecnologia)}
            ${s.id ? '#' + this._esc(s.id) : ''} — ${this._esc(s.nome)}</div>
          <div class="l2vpn-sub">${this._l2vpnDicaVendor(s)}</div>
        </div>
        <i class="fas fa-times l2vpn-fechar" onclick="topo.fecharL2vpn()" title="Fechar (Esc)"></i>
      </div>
      <div class="l2vpn-lista">
        <div class="l2vpn-form">
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">Nome do serviço novo</label>
            <input class="l2vpn-input" id="cl-nome" value="${this._esc(f.nome)}">
          </div>
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">ID (${this._esc(s.tecnologia === 'VSI' ? 'vsi-id' : 'pw-id / vc-id')})</label>
            <input class="l2vpn-input" id="cl-id" value="${this._esc(f.id)}">
          </div>
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">VLAN (dot1q)</label>
            <input class="l2vpn-input" id="cl-vlan" value="${this._esc(f.vlan)}">
          </div>
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">MTU</label>
            <input class="l2vpn-input" id="cl-mtu" value="${this._esc(f.mtu)}">
          </div>
          ${ehDatacom ? `
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">Grupo (${s.tipo === 'vpls' ? 'vpls-group' : 'vpws-group'})</label>
            <input class="l2vpn-input" id="cl-grupo" value="${this._esc(f.grupo)}">
          </div>
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">VLAN do pseudowire
              <span class="l2vpn-tag">pw-type vlan N</span></label>
            <input class="l2vpn-input" id="cl-pwvlan" value="${this._esc(f.pw_vlan)}"
                   placeholder="igual à origem">
          </div>` : ''}
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">Descrição</label>
            <input class="l2vpn-input" id="cl-desc" value="${this._esc(f.descricao)}">
          </div>
          ${s.vendor === 'mikrotik' ? '' : `
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">flow-label
              <span class="l2vpn-tag">balanceamento do pseudowire</span></label>
            <select class="l2vpn-select" id="cl-flow" style="width:100%">
              ${['both', 'transmit', 'receive'].map(v => `
                <option value="${v}" ${f.flow_label === v ? 'selected' : ''}>${v}</option>`).join('')}
              <option value="" ${!f.flow_label ? 'selected' : ''}>não usar</option>
            </select>
          </div>`}
          <div class="l2vpn-form-grupo">
            <label class="l2vpn-label">Peers (outro lado do túnel)
              <button class="l2vpn-mini" onclick="topo._l2vpnAddPeer()"><i class="fas fa-plus"></i></button></label>
            ${linhasPeer}
          </div>
          ${this._l2vpnBlocoLdp()}
          ${blocoAcesso}
          ${erro}
          ${passoComandos}
          ${resultado}
        </div>
      </div>
      <div class="modal-footer">${acoes}</div>`);

    this._l2vpnLigarCombos();
  }

  /** Bloco "Sessão LDP" do formulário de clonagem.
   *
   *  O pseudowire só sobe se existir sessão LDP targeted com o peer, e ela
   *  mora FORA do bloco do serviço — clonar um VSI/VPWS para um peer novo não
   *  fechava o circuito sozinho. Aqui o operador vê, peer a peer, quem já tem
   *  sessão no backup e quem vai ser criado junto com o serviço.
   */
  _l2vpnBlocoLdp() {
    const c = this._l2vpn.clone;
    const s = c.origem;
    if (!['huawei', 'datacom'].includes(s.vendor)) return '';   // RouterOS: outro modelo de LDP

    const ldp = this._l2vpn.dados.ldp || {peers: {}, lsr_id: '', tem_bloco: false};
    const jaTem = ldp.peers || {};
    const peers = c.peers.filter(Boolean);
    const faltando = peers.filter(ip => !(ip in jaTem));

    const status = peers.length ? peers.map(ip => {
      const ok = ip in jaTem;
      return `<span class="l2vpn-peer ${ok ? 'ident' : 'nao-ident'}" style="cursor:default">
        <i class="fas fa-${ok ? 'check' : 'plus'}"></i>
        <span class="l2vpn-peer-ip">${this._esc(ip)}</span>
        <em>${ok ? 'sessão já existe' : 'será criada'}</em></span>`;
    }).join('') : '<span class="l2vpn-nada">Informe um peer acima.</span>';

    return `
      <div class="l2vpn-form-grupo" style="grid-column:1/-1">
        <label class="l2vpn-label">
          <label style="display:flex;align-items:center;gap:7px;cursor:pointer">
            <input type="checkbox" id="cl-ldp" ${c.ldp ? 'checked' : ''}
                   onchange="topo._l2vpnLerForm();topo._pintarL2vpnClone()"
                   style="accent-color:var(--cyan);width:14px;height:14px">
            Fechar também a sessão LDP com o peer
          </label>
          <span class="l2vpn-tag">${s.vendor === 'huawei'
            ? 'mpls ldp remote-peer' : 'mpls ldp · neighbor targeted'}</span>
        </label>
        <div class="l2vpn-peers" style="padding:2px 0 0">${status}</div>
        ${c.ldp && s.vendor === 'datacom' ? `
          <div class="l2vpn-linha-lista" style="margin-top:6px">
            <span class="l2vpn-tag" style="margin:0">lsr-id</span>
            <input class="l2vpn-input" id="cl-ldp-lsr" value="${this._esc(c.form.ldp_lsr_id)}"
                   placeholder="loopback-0">
          </div>
          <div class="l2vpn-aviso">O <code>neighbor targeted</code> fica dentro do
            <code>lsr-id</code>, e qual loopback está em uso muda por equipamento —
            ${ldp.lsr_id
              ? `este veio do backup deste host (<b>${this._esc(ldp.lsr_id)}</b>).`
              : 'não achei no backup, confira antes de aplicar.'}</div>` : ''}
        ${c.ldp && !faltando.length && peers.length ? `
          <div class="l2vpn-aviso">Todos os peers já têm sessão — nada de LDP será enviado.</div>` : ''}
        ${c.ldp && !ldp.tem_bloco ? `
          <div class="l2vpn-aviso">O backup deste host não mostra bloco de LDP; revise o
            preview antes de aplicar.</div>` : ''}
      </div>`;
  }

  // ── Combos de busca (peer, interface, porta) ──────────────────────────────
  //
  // Um combo só, com fontes diferentes: peers vêm de /l2vpn-peers/ (hosts do
  // cliente com identidade MPLS) e interfaces de /interfaces-backup/ (o que o
  // backup do próprio host mostra). Em ambos, digitar continua valendo — a
  // lista é atalho, não trava o campo.

  async _l2vpnCarregarListas() {
    const est = this._l2vpn;
    if (!est.peersLista) {
      est.peersLista = [];   // evita duas buscas simultâneas
      try {
        const r = await fetch(`/clientes/acessos/${est.acessoId}/l2vpn-peers/`,
                              {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        if (r.ok && !r.redirected) est.peersLista = (await r.json()).peers || [];
      } catch (e) { /* sem lista o campo continua aceitando IP digitado */ }
    }
    if (!est.ifacesLista) {
      est.ifacesLista = [];
      try {
        const r = await fetch(`/clientes/acessos/${est.acessoId}/interfaces-backup/`,
                              {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        if (r.ok && !r.redirected) {
          // Só portas físicas: num switch com 48 portas e 300 Vlanif, listar
          // as lógicas junto torna a busca inútil — e Vlanif nunca é a porta
          // onde a VLAN do cliente entra.
          est.ifacesLista = ((await r.json()).interfaces || [])
            .filter(i => !i.logica && !i.subinterface);
        }
      } catch (e) { /* idem */ }
    }
  }

  _l2vpnOpcoesPeer(termo) {
    // Busca por nome do host OU por IP — o operador raramente lembra o
    // loopback, mas sempre sabe o nome do equipamento do outro lado.
    return (this._l2vpn.peersLista || [])
      .filter(p => !termo || (p.nome || '').toLowerCase().includes(termo) || p.ip.includes(termo))
      .slice(0, 8)
      .map(p => ({
        valor: p.ip,
        principal: p.ip,
        secundario: p.nome,
        meta: p.origem + (p.servicos ? ` · ${p.servicos} L2VPN` : ''),
      }));
  }

  _l2vpnOpcoesIface(termo) {
    // Busca por nome da porta OU pela descrição — é pela descrição
    // ("CLIENTE-NETCENTER") que se sabe qual porta é a certa.
    return (this._l2vpn.ifacesLista || [])
      .filter(i => !termo || i.nome.toLowerCase().includes(termo)
                          || (i.descricao || '').toLowerCase().includes(termo))
      .slice(0, 10)
      .map(i => ({
        valor: i.nome,
        principal: i.nome,
        secundario: i.descricao || '',
        meta: i.ip || '',
      }));
  }

  _l2vpnLigarCombos() {
    const c = this._l2vpn.clone;
    if (!c) return;
    c.peers.forEach((_, i) => this._l2vpnLigarCombo(
      `cl-peer-${i}`, `cl-peer-drop-${i}`, termo => this._l2vpnOpcoesPeer(termo)));
    c.interfaces.forEach((_, i) => this._l2vpnLigarCombo(
      `cl-if-${i}`, `cl-if-drop-${i}`, termo => this._l2vpnOpcoesIface(termo)));
    (c.portas || []).forEach((_, i) => this._l2vpnLigarCombo(
      `cl-porta-${i}`, `cl-porta-drop-${i}`, termo => this._l2vpnOpcoesIface(termo)));

    // Rolar a lista move o campo, mas não o dropdown (que é `fixed`) — fecha.
    const lista = document.querySelector('#l2vpn-modal .l2vpn-lista');
    if (lista) {
      lista.addEventListener('scroll', () => {
        document.querySelectorAll('#l2vpn-modal .l2vpn-drop.show')
          .forEach(d => d.classList.remove('show'));
      });
    }
  }

  _l2vpnLigarCombo(inputId, dropId, fnOpcoes) {
    const input = document.getElementById(inputId);
    const drop = document.getElementById(dropId);
    if (!input || !drop) return;
    const abrir = async () => {
      await this._l2vpnCarregarListas();
      this._l2vpnPintarCombo(input, drop, fnOpcoes(input.value.trim().toLowerCase()));
    };
    input.addEventListener('focus', abrir);
    input.addEventListener('input', abrir);
    input.addEventListener('blur', () => setTimeout(() => drop.classList.remove('show'), 160));
  }

  _l2vpnPintarCombo(input, drop, itens) {
    if (!itens.length) {
      drop.classList.remove('show');
      drop.innerHTML = '';
      return;
    }
    drop.innerHTML = itens.map((it, k) => `
      <div class="l2vpn-drop-item" data-k="${k}">
        <span class="l2vpn-drop-ip">${this._esc(it.principal)}</span>
        <span class="l2vpn-drop-nome">${this._esc(it.secundario)}</span>
        <span class="l2vpn-drop-meta">${this._esc(it.meta)}</span>
      </div>`).join('');
    // Delegação em vez de onclick inline: nome de porta e descrição vêm da
    // config do equipamento e não podem ser interpolados numa string JS.
    // `preventDefault` no mousedown evita o blur que fecharia o dropdown antes.
    drop.onmousedown = e => {
      const alvo = e.target.closest('.l2vpn-drop-item');
      if (!alvo) return;
      e.preventDefault();
      input.value = itens[+alvo.dataset.k].valor;
      drop.classList.remove('show');
      this._l2vpnLerForm();
    };
    drop.classList.add('show');

    // Posiciona em coordenadas de viewport (CSS `position:fixed`) — dentro do
    // modal a lista rola com overflow, que cortaria um dropdown `absolute`.
    const r = input.getBoundingClientRect();
    const abaixo = window.innerHeight - r.bottom;
    drop.style.left = `${r.left}px`;
    drop.style.width = `${r.width}px`;
    if (abaixo < Math.min(230, drop.scrollHeight) + 12 && r.top > abaixo) {
      drop.style.top = 'auto';                                   // sem espaço embaixo: abre pra cima
      drop.style.bottom = `${window.innerHeight - r.top + 3}px`;
    } else {
      drop.style.bottom = 'auto';
      drop.style.top = `${r.bottom + 3}px`;
    }
  }

  _l2vpnLerForm() {
    // Lê o que está digitado e guarda no estado: o painel é re-renderizado
    // inteiro a cada ação (adicionar peer, gerar comandos, confirmar), então
    // o que não for salvo aqui se perde. Campo ausente do DOM (ex: a tela de
    // confirmação, que não mostra o formulário) mantém o valor atual.
    const c = this._l2vpn.clone;
    const campo = (id, atual) => {
      const el = document.getElementById(id);
      return el ? el.value.trim() : atual;
    };

    c.form = {
      nome: campo('cl-nome', c.form.nome),
      id: campo('cl-id', c.form.id),
      vlan: campo('cl-vlan', c.form.vlan),
      mtu: campo('cl-mtu', c.form.mtu),
      grupo: campo('cl-grupo', c.form.grupo),
      descricao: campo('cl-desc', c.form.descricao),
      flow_label: campo('cl-flow', c.form.flow_label),
      pw_vlan: campo('cl-pwvlan', c.form.pw_vlan),
      ldp_lsr_id: campo('cl-ldp-lsr', c.form.ldp_lsr_id),
    };
    const chkLdp = document.getElementById('cl-ldp');
    if (chkLdp) c.ldp = chkLdp.checked;
    c.peers = c.peers.map((atual, i) => campo(`cl-peer-${i}`, atual));
    c.interfaces = c.interfaces.map((atual, i) => ({
      nome: campo(`cl-if-${i}`, atual.nome), vlan: campo(`cl-ifvlan-${i}`, atual.vlan),
    }));
    c.portas = (c.portas || []).map((atual, i) => ({
      nome: campo(`cl-porta-${i}`, atual.nome),
      modo: campo(`cl-portamodo-${i}`, atual.modo) || 'tagged',
    }));
    if (!c.peers.length) c.peers = [''];
    if (!c.interfaces.length) c.interfaces = [{nome: '', vlan: ''}];
    if (!c.portas.length) c.portas = [{nome: '', modo: 'tagged'}];
    return {
      ...c.form,
      ldp: !!c.ldp,
      peers: c.peers.filter(Boolean),
      interfaces: c.interfaces.filter(i => i.nome),
      portas: c.portas.filter(p => p.nome),
    };
  }

  _l2vpnAddPeer()  { this._l2vpnLerForm(); this._l2vpn.clone.peers.push(''); this._pintarL2vpnClone(); }
  _l2vpnAddIface() { this._l2vpnLerForm(); this._l2vpn.clone.interfaces.push({nome: '', vlan: ''}); this._pintarL2vpnClone(); }

  _l2vpnRemoverPeer(i) {
    this._l2vpnLerForm();
    this._l2vpn.clone.peers.splice(i, 1);
    if (!this._l2vpn.clone.peers.length) this._l2vpn.clone.peers = [''];
    this._pintarL2vpnClone();
  }

  _l2vpnAddPorta() {
    this._l2vpnLerForm();
    this._l2vpn.clone.portas.push({nome: '', modo: 'tagged'});
    this._pintarL2vpnClone();
  }

  _l2vpnRemoverPorta(i) {
    this._l2vpnLerForm();
    this._l2vpn.clone.portas.splice(i, 1);
    if (!this._l2vpn.clone.portas.length) this._l2vpn.clone.portas = [{nome: '', modo: 'tagged'}];
    this._pintarL2vpnClone();
  }

  _l2vpnRemoverIface(i) {
    this._l2vpnLerForm();
    this._l2vpn.clone.interfaces.splice(i, 1);
    if (!this._l2vpn.clone.interfaces.length) this._l2vpn.clone.interfaces = [{nome: '', vlan: ''}];
    this._pintarL2vpnClone();
  }

  _l2vpnCancelarConfirma() {
    this._l2vpn.clone.confirmando = false;
    this._pintarL2vpnClone();
  }

  async _l2vpnPost(corpo) {
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]');
    const r = await fetch(`/clientes/acessos/${this._l2vpn.acessoId}/l2vpn-clonar/`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrf ? csrf.value : ''},
      body: JSON.stringify(corpo),
    });
    if (r.redirected || r.status === 401) {
      this._toast('Sessão expirada — recarregue a página e faça login', 'error');
      return null;
    }
    let dados = {};
    try { dados = await r.json(); } catch (e) { dados = {}; }
    if (!r.ok) {
      // Guarda a mensagem pro painel mostrar de forma persistente: um toast
      // de 2,5s some antes do operador ler por que a config foi recusada.
      this._l2vpn._ultimoErro = dados.error || `Erro ${r.status} ao falar com o servidor.`;
      this._toast(this._l2vpn._ultimoErro, 'error');
      return null;
    }
    this._l2vpn._ultimoErro = '';
    return dados;
  }

  async _l2vpnGerar() {
    const c = this._l2vpn.clone;
    const spec = this._l2vpnLerForm();
    const dados = await this._l2vpnPost({origem_idx: c.idx, spec, preview: true});
    if (!dados) {
      c.erro = this._l2vpn._ultimoErro;
      c.comandos = null;
      this._pintarL2vpnClone();
      return;
    }
    c.erro = '';
    c.comandos = dados.comandos || [];
    c.resultado = null;
    this._pintarL2vpnClone();
    // Traz o preview pra vista: em tela curta ele nasce abaixo da dobra e
    // parece que "nao gerou nada".
    const ta = document.getElementById('cl-comandos');
    if (ta) {
      ta.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      ta.classList.add('destacado');
      setTimeout(() => ta.classList.remove('destacado'), 1400);
    }
    this._toast(`${c.comandos.length} comandos gerados — revise antes de aplicar`);
  }

  async _l2vpnAplicar(confirmado) {
    const c = this._l2vpn.clone;
    if (!confirmado) {
      // Guarda o que estiver no textarea antes de trocar a tela pela pergunta
      // de confirmação, senão uma edição manual dos comandos se perderia.
      const ta = document.getElementById('cl-comandos');
      if (ta) c.comandos = ta.value.split('\n').map(l => l.trim()).filter(Boolean);
      c.spec = this._l2vpnLerForm();
      c.confirmando = true;
      this._pintarL2vpnClone();
      return;
    }
    c.confirmando = false;
    this._renderL2vpnModal(`
      <div style="padding:40px;text-align:center;color:var(--muted)">
        <i class="fas fa-circle-notch fa-spin" style="font-size:1.6rem"></i>
        <div style="margin-top:12px;font-size:.82rem">Conectando no equipamento e aplicando…</div>
      </div>`);
    const dados = await this._l2vpnPost({
      origem_idx: c.idx, spec: c.spec, preview: false, comandos: c.comandos,
    });
    if (!dados) {
      c.erro = this._l2vpn._ultimoErro;
      this._pintarL2vpnClone();
      return;
    }
    c.erro = '';
    c.resultado = dados;
    this._pintarL2vpnClone();
    this._toast(dados.status === 'sucesso' ? `${dados.nome} criado no equipamento`
                                           : 'O equipamento recusou — veja a saída',
                dados.status === 'sucesso' ? 'ok' : 'error');
  }

  _l2vpnFiltrar(tipo) {
    if (!this._l2vpn) return;
    this._l2vpn.filtro = tipo;
    this._pintarL2vpn();
  }

  _l2vpnToggle(idx) {
    if (!this._l2vpn) return;
    const abertos = this._l2vpn.abertos;
    if (abertos.has(idx)) abertos.delete(idx); else abertos.add(idx);
    this._pintarL2vpn();
  }

  _l2vpnCopiar() {
    const linhas = ['| Tipo | ID | Nome | VLAN | MTU | Peers | Interfaces |',
                    '|---|---|---|---|---|---|---|'];
    this._l2vpnFiltrados().forEach(s => {
      const peers = s.peers.map(p => p.destino ? `${p.ip} (${p.destino.nome})` : p.ip).join('<br>') || '—';
      const ifaces = s.interfaces.map(i => i.vlan ? `${i.nome}.${i.vlan}` : i.nome).join('<br>') || '—';
      linhas.push(`| ${s.tecnologia} | ${s.id || '—'} | ${s.nome} | ${s.vlan || '—'} | ${s.mtu || '—'} | ${peers} | ${ifaces} |`);
    });
    const texto = linhas.join('\n');
    navigator.clipboard.writeText(texto)
      .then(() => this._toast(`${linhas.length - 2} serviços copiados em Markdown`))
      .catch(() => this._toast('Não foi possível copiar', 'error'));
  }

  _irParaPeer(idxServico, idxPeer) {
    if (!this._l2vpn || !this._l2vpn.dados) return;
    const servico = (this._l2vpn.dados.servicos || [])[idxServico];
    const peer = servico && servico.peers[idxPeer];
    if (!peer || !peer.destino) return;
    const destino = peer.destino;

    const node = this.nodes.find(n => String(n.acesso_id) === String(destino.acesso_id));
    if (node) {
      this.fecharL2vpn();
      this._focarNode(node.id);
      this._toast(`${node.label} — outro lado do túnel (${peer.ip})`);
      return;
    }
    // Host existe no CRM mas ainda não está no diagrama: em vez de não fazer
    // nada, abre a documentação L2VPN dele (o "Importar Hosts" traz o ícone
    // pro canvas depois, se a pessoa quiser desenhar o enlace).
    this._toast('Host fora do diagrama — abrindo os serviços dele');
    this.abrirL2vpn(destino.acesso_id, destino.nome);
  }

  _focarNode(nodeId) {
    const node = this.nodes.find(n => n.id === nodeId);
    if (!node) return false;
    const r = this.svg.getBoundingClientRect();
    this.zoom = Math.max(this.zoom, 0.9);
    this.panX = r.width / 2 - node.x * this.zoom;
    this.panY = r.height / 2 - node.y * this.zoom;
    this._updateVP();
    this._select('node', nodeId);
    const el = this.nodesLayer.querySelector(`[data-id="${nodeId}"]`);
    if (el) {
      el.classList.remove('node-flash');
      void el.getBoundingClientRect(); // reinicia a animação se clicar de novo
      el.classList.add('node-flash');
      setTimeout(() => el.classList.remove('node-flash'), 1800);
    }
    return true;
  }

  _showLinkProps(id) {
    const link = this.links.find(l => l.id === id);
    if (!link) return;
    const ifaceOpts = Object.entries(TOPO_IFACES).map(([k,v]) =>
      `<option value="${k}" ${link.iface===k?'selected':''}>${v.label}</option>`).join('');

    const shape = link.shape || 'straight';
    const wps   = link.waypoints || [];
    const src = this.nodes.find(n => n.id === link.src);
    const tgt = this.nodes.find(n => n.id === link.tgt);
    const gen = this._propsGen;

    document.getElementById('props-body').innerHTML = `
      <div class="prop-title"><i class="fas fa-ethernet"></i> Conexão</div>
      <div class="prop-group">
        <label class="prop-label">Interface / Velocidade</label>
        <select class="prop-select" id="pl-iface">${ifaceOpts}</select>
      </div>
      <div class="prop-group">
        <label class="prop-label">Label</label>
        <input class="prop-input" id="pl-label" placeholder="Link name" value="${this._esc(link.label||'')}">
      </div>
      <div class="prop-group">
        <label class="prop-label">Interface Lado A${src&&src.label?' — '+this._esc(src.label):''}</label>
        <input class="prop-input" id="pl-ifa" list="dl-ifa" autocomplete="off"
               placeholder="ge0/0/1, eth0, sfp1…" value="${this._esc(link.iface_a||'')}">
        <datalist id="dl-ifa"></datalist>
      </div>
      <div class="prop-group">
        <label class="prop-label">Interface Lado B${tgt&&tgt.label?' — '+this._esc(tgt.label):''}</label>
        <input class="prop-input" id="pl-ifb" list="dl-ifb" autocomplete="off"
               placeholder="ge0/0/2, eth1, sfp2…" value="${this._esc(link.iface_b||'')}">
        <datalist id="dl-ifb"></datalist>
      </div>
      <div class="prop-group">
        <label class="prop-label">IP Local (P2P)${src&&src.label?' — '+this._esc(src.label):''}</label>
        <input class="prop-input" id="pl-ipl" list="dl-ipl" autocomplete="off"
               placeholder="10.0.0.1/30" value="${this._esc(link.ip_local||'')}">
        <datalist id="dl-ipl"></datalist>
      </div>
      <div class="prop-group">
        <label class="prop-label">IP Remoto (P2P)${tgt&&tgt.label?' — '+this._esc(tgt.label):''}</label>
        <input class="prop-input" id="pl-ipr" list="dl-ipr" autocomplete="off"
               placeholder="10.0.0.2/30" value="${this._esc(link.ip_remote||'')}">
        <datalist id="dl-ipr"></datalist>
      </div>
      <div class="prop-group">
        <label class="prop-label">VLAN</label>
        <input class="prop-input" id="pl-vlan" placeholder="100" value="${link.vlan||''}">
      </div>
      <div class="prop-group">
        <label class="prop-label">Traço</label>
        <select class="prop-select" id="pl-style">
          <option value="solid"  ${link.style==='solid' ?'selected':''}>Sólido</option>
          <option value="dashed" ${link.style==='dashed'?'selected':''}>Tracejado</option>
          <option value="dotted" ${link.style==='dotted'?'selected':''}>Pontilhado</option>
        </select>
      </div>
      <div class="prop-group">
        <label class="prop-label">Forma da linha</label>
        <select class="prop-select" id="pl-shape">
          <option value="straight" ${shape==='straight'?'selected':''}>Reta</option>
          <option value="curved"   ${shape==='curved'  ?'selected':''}>Curva</option>
          <option value="wavy"     ${shape==='wavy'    ?'selected':''}>Ondulada</option>
        </select>
      </div>
      <div class="prop-group" style="background:rgba(88,166,255,.07);border-radius:6px;padding:8px;font-size:.75rem;color:var(--muted)">
        <i class="fas fa-route" style="color:var(--cyan);margin-right:5px"></i>
        <strong style="color:var(--text)">Editar caminho:</strong><br>
        Arraste o <span style="color:#58a6ff">●</span> no meio da linha para dobrar.<br>
        <span style="color:#58a6ff">○</span> círculos criados = waypoints.<br>
        <strong>Duplo-clique</strong> em um waypoint para removê-lo.
        ${wps.length > 0 ? `<br><br><button class="prop-btn" onclick="topo._clearWaypoints('${id}')" style="margin-top:4px"><i class="fas fa-minus-circle"></i> Limpar waypoints (${wps.length})</button>` : ''}
      </div>
      <button class="prop-btn" onclick="topo._applyLinkProps('${id}')"><i class="fas fa-check"></i> Aplicar</button>
      <button class="prop-btn danger" onclick="topo._deleteSelected()"><i class="fas fa-trash"></i> Remover</button>`;

    this._populateIfaceDatalist('dl-ifa', src && src.acesso_id, gen);
    this._populateIfaceDatalist('dl-ifb', tgt && tgt.acesso_id, gen);
    this._populateIpDatalist('dl-ipl', src && src.acesso_id, gen);
    this._populateIpDatalist('dl-ipr', tgt && tgt.acesso_id, gen);

    // Ao escolher/digitar uma interface que bate com o nome exato de uma
    // interface do backup, sugere o IP P2P dela (só se a interface tiver
    // roteamento configurado e o campo de IP ainda estiver vazio).
    const ifaEl = document.getElementById('pl-ifa');
    const ifbEl = document.getElementById('pl-ifb');
    if (ifaEl) ifaEl.addEventListener('input', () => this._sugerirIpPorInterface('pl-ifa', 'pl-ipl', src && src.acesso_id));
    if (ifbEl) ifbEl.addEventListener('input', () => this._sugerirIpPorInterface('pl-ifb', 'pl-ipr', tgt && tgt.acesso_id));
  }

  async _sugerirIpPorInterface(ifInputId, ipInputId, acessoId) {
    if (!acessoId) return;
    const ifEl = document.getElementById(ifInputId);
    const ipEl = document.getElementById(ipInputId);
    if (!ifEl || !ipEl || ipEl.value.trim()) return; // nunca sobrescreve IP já preenchido
    const nome = ifEl.value.trim();
    if (!nome) return;
    const interfaces = await this._fetchInterfaces(acessoId);
    // Campo pode ter mudado enquanto buscava (cache já deve resolver na hora,
    // mas o guard cobre o primeiro acesso a um node ainda não cacheado).
    if (ifEl.value.trim() !== nome || ipEl.value.trim()) return;
    const item = interfaces.find(i => i.nome === nome);
    if (item && item.ip) {
      ipEl.value = item.ip;
      this._toast(`IP ${item.ip} sugerido a partir do backup (${nome})`);
    }
  }

  // ── Sugestão de interfaces a partir do backup ───────────────────────────────

  async _fetchInterfaces(acessoId) {
    if (!acessoId) return [];
    // Cacheia por acesso_id (mesmo lista vazia) — evita refazer a mesma consulta
    // toda vez que o painel de propriedades do link é reaberto na sessão.
    if (acessoId in this._ifaceCache) return this._ifaceCache[acessoId];
    try {
      const r = await fetch(`/clientes/acessos/${acessoId}/interfaces-backup/`);
      const lista = r.ok ? ((await r.json()).interfaces || []) : [];
      this._ifaceCache[acessoId] = lista;
      return lista;
    } catch (e) { return []; }
  }

  async _populateIfaceDatalist(datalistId, acessoId, gen) {
    if (!acessoId) return; // nó não veio do CRM (ou sem acesso vinculado) — datalist fica vazia, input livre
    const interfaces = await this._fetchInterfaces(acessoId);
    if (gen !== this._propsGen) return; // seleção mudou enquanto a busca corria — descarta
    const dl = document.getElementById(datalistId);
    if (!dl) return;
    // A descrição da interface no backup (ex. "P2P-SW-CORE-P6") vira a legenda
    // da sugestão no dropdown — ajuda a escolher a interface certa sem precisar
    // decorar o nome físico da porta. Preenche tanto o atributo `label` (usado
    // pelo Firefox) quanto o texto do <option> (usado pelo Chrome/Edge) para
    // cobrir os dois jeitos que os navegadores renderizam <datalist>.
    dl.innerHTML = interfaces.map(i => {
      const desc = this._esc(i.descricao || '');
      const labelAttr = desc ? ` label="${desc}"` : '';
      return `<option value="${this._esc(i.nome)}"${labelAttr}>${desc}</option>`;
    }).join('');
  }

  async _populateIpDatalist(datalistId, acessoId, gen) {
    if (!acessoId) return; // nó não veio do CRM (ou sem acesso vinculado) — datalist fica vazia, input livre
    const interfaces = await this._fetchInterfaces(acessoId);
    if (gen !== this._propsGen) return; // seleção mudou enquanto a busca corria — descarta
    const dl = document.getElementById(datalistId);
    if (!dl) return;
    // Reaproveita o mesmo cache/consulta das interfaces (nenhuma chamada de
    // rede extra) — só interfaces com IP roteado configurado no backup fazem
    // sentido aqui, a maioria das portas L2/trunk não tem endereço.
    dl.innerHTML = interfaces.filter(i => i.ip).map(i => {
      const legenda = this._esc([i.nome, i.descricao].filter(Boolean).join(' — '));
      const labelAttr = legenda ? ` label="${legenda}"` : '';
      return `<option value="${this._esc(i.ip)}"${labelAttr}>${legenda}</option>`;
    }).join('');
  }

  _applyLinkProps(id) {
    const link = this.links.find(l => l.id === id);
    if (!link) return;
    this._saveHistory();
    link.iface     = document.getElementById('pl-iface').value;
    link.label     = document.getElementById('pl-label').value;
    link.iface_a   = document.getElementById('pl-ifa').value;
    link.iface_b   = document.getElementById('pl-ifb').value;
    link.ip_local  = document.getElementById('pl-ipl').value;
    link.ip_remote = document.getElementById('pl-ipr').value;
    link.vlan      = document.getElementById('pl-vlan').value;
    link.style     = document.getElementById('pl-style').value;
    link.shape     = document.getElementById('pl-shape').value;
    this._renderLink(link);
    this._renderLinkHandles(link);
    this._setDirty();
    this._toast('Aplicado');
  }

  _clearWaypoints(id) {
    const link = this.links.find(l => l.id === id);
    if (!link) return;
    this._saveHistory();
    link.waypoints = [];
    this._renderLink(link);
    this._renderLinkHandles(link);
    this._showLinkProps(id);
    this._setDirty();
    this._toast('Waypoints removidos');
  }

  // ── Controls ─────────────────────────────────────────────────────────────

  toggleConnectMode() {
    this.connectMode = !this.connectMode;
    if (this.connectMode && this.areaSelectMode) {
      this.areaSelectMode = false;
      document.getElementById('btn-area-select').classList.remove('active');
    }
    this._cancelConnect();
    const btn = document.getElementById('btn-connect');
    btn.classList.toggle('active', this.connectMode);
    document.getElementById('canvas-svg').style.cursor = this.connectMode ? 'crosshair' : 'default';
    this._updateMultiSelectStatus();
  }

  toggleAreaSelect() {
    this.areaSelectMode = !this.areaSelectMode;
    if (this.areaSelectMode && this.connectMode) {
      this.connectMode = false;
      document.getElementById('btn-connect').classList.remove('active');
    }
    this._cancelConnect();
    const btn = document.getElementById('btn-area-select');
    btn.classList.toggle('active', this.areaSelectMode);
    document.getElementById('canvas-svg').style.cursor = this.areaSelectMode ? 'crosshair' : 'default';
    this._updateMultiSelectStatus();
  }

  /** Paleta de dispositivos: cartão flutuante, fechado ao abrir a topologia
   *  (o canvas é o que interessa). O botão da borda esquerda traz de volta. */
  togglePalette() {
    const fechada = document.body.classList.toggle('pal-off');
    if (!fechada) setTimeout(() => document.getElementById('pal-search')?.focus(), 260);
  }

  /** Fecha o painel de propriedades — junto some a seleção, senão fica um node
   *  aceso no canvas sem nenhum painel explicando o porquê. */
  fecharProps() {
    this._deselect();
    this._clearMultiSelect();
  }

  toggleGrid() {
    this.showGrid = !this.showGrid;
    document.getElementById('grid-bg').style.display = this.showGrid ? '' : 'none';
    document.getElementById('btn-grid').classList.toggle('active', this.showGrid);
  }

  toggleSnap() {
    this.snap = !this.snap;
    document.getElementById('btn-snap').classList.toggle('active', this.snap);
  }

  toggleEffects() {
    this.effectsOn = !this.effectsOn;
    document.body.classList.toggle('effects-off', !this.effectsOn);
    document.getElementById('btn-effects').classList.toggle('active', this.effectsOn);
  }

  // ── Tela cheia ────────────────────────────────────────────────────────────
  // Vale principalmente pro editor embutido no cadastro do cliente, onde o
  // iframe tem `calc(100vh - 200px)` e sobra pouca área pra desenhar.

  /** Quem vai pra tela cheia, e em qual documento.
   *
   *  Embutido num <iframe>, pedir fullscreen no <html> DE DENTRO do iframe dá
   *  o editor cortado: o navegador desenha a moldura no tamanho da tela, mas o
   *  VIEWPORT do iframe continua com o tamanho antigo — as alturas do editor
   *  (body em 100vh, painéis em 100%) seguem valendo o `calc(100vh - 200px)`
   *  do cadastro, então tudo fica espremido numa faixa no topo e o resto da
   *  tela fica preto.
   *
   *  Quem precisa ir pra tela cheia é o PRÓPRIO <iframe>, no documento pai: aí
   *  ele vai pra top layer em tela inteira e o viewport de dentro é
   *  redimensionado de verdade, com as medidas do editor batendo de novo.
   *  `window.frameElement` é null fora de iframe e estoura em cross-origin —
   *  nos dois casos cai no <html> local, que é o certo pra aba solta. */
  _fsAlvo() {
    try {
      const frame = window.frameElement;
      const abrir = frame && (frame.requestFullscreen || frame.webkitRequestFullscreen);
      if (abrir) return {el: frame, doc: frame.ownerDocument};
    } catch (e) { /* iframe cross-origin: usa o documento local */ }
    return {el: document.documentElement, doc: document};
  }

  _emFullscreen() {
    const docs = [document];
    const {doc} = this._fsAlvo();
    if (doc !== document) docs.push(doc);
    return docs.some(d => !!(d.fullscreenElement || d.webkitFullscreenElement));
  }

  async toggleFullscreen() {
    const {el, doc} = this._fsAlvo();
    try {
      if (this._emFullscreen()) {
        // Sair tem que ser pedido no documento que entrou (o pai, quando é o
        // iframe que está em tela cheia).
        const d = (doc.fullscreenElement || doc.webkitFullscreenElement) ? doc : document;
        const sair = d.exitFullscreen || d.webkitExitFullscreen;
        if (sair) await sair.call(d);
        return;
      }
      // `fullscreenEnabled` é false quando o editor está num <iframe> sem
      // `allowfullscreen` — melhor dizer o que fazer do que o botão não
      // reagir. (O iframe do cadastro do cliente já tem o atributo.)
      if (document.fullscreenEnabled === false) {
        this._toast('Esta página não libera tela cheia — abra o editor em nova aba', 'error');
        return;
      }
      const abrir = el.requestFullscreen || el.webkitRequestFullscreen;
      if (!abrir) { this._toast('O navegador não suporta tela cheia', 'error'); return; }
      await abrir.call(el);
    } catch (e) {
      this._toast('Tela cheia bloqueada nesta página — abra o editor em nova aba', 'error');
    }
  }

  /** O viewport muda de tamanho ao entrar/sair: o rect cacheado do canvas e o
   *  botão precisam acompanhar. */
  _aoTrocarFullscreen() {
    this._rect = null;
    this._syncFullscreenBtn();
  }

  _syncFullscreenBtn() {
    const btn = document.getElementById('btn-fullscreen');
    if (!btn) return;
    const on = this._emFullscreen();
    btn.classList.toggle('active', on);
    const i = btn.querySelector('i');
    if (i) i.className = on ? 'fas fa-compress' : 'fas fa-expand';
    btn.setAttribute('data-tip', on ? 'Sair da tela cheia (F ou Esc)' : 'Editar em tela cheia (F)');
  }

  zoomIn()  { this.zoom = Math.min(4, this.zoom*1.2); this._updateVP(); }
  zoomOut() { this.zoom = Math.max(.1, this.zoom/1.2); this._updateVP(); }
  zoomFit() {
    if (!this.nodes.length) { this.zoom=1;this.panX=0;this.panY=0;this._updateVP();return; }
    const xs = this.nodes.map(n=>n.x), ys = this.nodes.map(n=>n.y);
    const mx=Math.min(...xs)-80, my=Math.min(...ys)-80, Mx=Math.max(...xs)+80, My=Math.max(...ys)+80;
    const r = this.svg.getBoundingClientRect();
    const zx=r.width/(Mx-mx), zy=r.height/(My-my);
    this.zoom = Math.min(zx,zy,.8);
    this.panX = (r.width-(Mx-mx)*this.zoom)/2 - mx*this.zoom;
    this.panY = (r.height-(My-my)*this.zoom)/2 - my*this.zoom;
    this._updateVP();
  }

  // ── History ───────────────────────────────────────────────────────────────

  _saveHistory() {
    this.history = this.history.slice(0, this.histIdx+1);
    this.history.push(JSON.stringify({nodes:this.nodes, links:this.links}));
    if (this.history.length > 50) this.history.shift();
    else this.histIdx++;
  }
  undo() {
    if (this.histIdx <= 0) return;
    this.histIdx--;
    const s = JSON.parse(this.history[this.histIdx]);
    this.nodes=s.nodes; this.links=s.links;
    this._renderAll(); this._setDirty();
  }
  redo() {
    if (this.histIdx >= this.history.length-1) return;
    this.histIdx++;
    const s = JSON.parse(this.history[this.histIdx]);
    this.nodes=s.nodes; this.links=s.links;
    this._renderAll(); this._setDirty();
  }

  _setDirty() {
    // Chamado a cada frame de arraste — quando já está sujo não há nada novo a
    // escrever na barra de status nem no botão Salvar.
    if (this.dirty) return;
    this.dirty = true;
    document.getElementById('st-save').textContent = '● Não salvo';
    document.getElementById('st-save').style.color = 'var(--orange)';
    // Ponto no próprio botão Salvar: a barra de status fica no rodapé e passa
    // despercebida — o aviso de "tem coisa não salva" precisa estar onde a
    // pessoa vai clicar.
    document.getElementById('btn-salvar')?.classList.add('dirty');
  }

  // ── Save / Load ───────────────────────────────────────────────────────────

  async save() {
    const nome = document.getElementById('nome-diagrama').value || 'Nova Topologia';
    const payload = {nome, dados_json: JSON.stringify({nodes:this.nodes, links:this.links}), diagrama_id: this.diagramaId};
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    try {
      const r = await fetch(`/clientes/${this.clienteId}/topologia/salvar/`, {
        method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
        body: JSON.stringify(payload)
      });
      const d = await r.json();
      if (d.ok) {
        this.diagramaId = d.diagrama_id;
        this.dirty = false;
        document.getElementById('st-save').textContent = '✓ Salvo';
        document.getElementById('st-save').style.color = 'var(--green)';
        document.getElementById('btn-salvar')?.classList.remove('dirty');
        this._toast('Topologia salva!');
      }
    } catch(e) { this._toast('Erro ao salvar: '+e,'error'); }
  }

  async importHosts() {
    this._toast('Importando hosts...');
    try {
      const r = await fetch(`/clientes/${this.clienteId}/topologia/hosts/`);
      if (!r.ok) { this._toast(`Erro ${r.status}`, 'error'); return; }
      const d = await r.json();
      if (!d.hosts || !d.hosts.length) { this._toast('Nenhum host cadastrado', 'error'); return; }

      // Hosts que foram agrupados não estão mais entre os nodes deste mapa
      // (vivem no sub-mapa do grupo). Sem essa checagem, "Importar Hosts"
      // traria cada um deles de volta pro mapa pai como se fosse host novo.
      const agrupados = this._idsAgrupados();

      const novos = [];
      d.hosts.forEach(h => {
        const existing = this.nodes.find(n => n.id === 'crm_' + h.id);
        if (!existing) { if (!agrupados.has('crm_' + h.id)) novos.push(h); return; }
        existing.funcao = h.funcao;
        // Ícone trocado manualmente pelo usuário (ver _applyNodeProps) não é
        // sobrescrito por uma reimportação — só o mapeamento automático (função
        // do CRM → tipo) é sincronizado aqui.
        if (!existing.type_manual && existing.type !== h.tipo) {
          existing.type = h.tipo;
          this._renderNode(existing); this._setDirty();
        }
      });
      if (!novos.length) { this._toast('Nenhum host novo para importar'); return; }

      const added = this._layoutImportados(novos);
      if (added > 0) this.zoomFit();
      this._toast(`${added} hosts importados`);
    } catch(e) { this._toast('Erro: ' + e.message, 'error'); }
  }

  /** Ids de nodes que estão dentro de algum grupo deste mapa. */
  _idsAgrupados() {
    const s = new Set();
    this.nodes.forEach(n => {
      if (n.grupo) (n.grupo_membros || []).forEach(m => s.add(m.id));
    });
    return s;
  }

  // Coloca os hosts recém-importados em faixas horizontais, uma por função
  // (tipo de dispositivo), na ordem hierárquica de TOPO_IMPORT_TIERS — trânsito
  // em cima, cliente embaixo. Antes tudo caía num grid único de 5 colunas na
  // ordem em que o backend devolvia, e uma topologia nunca configurada abria
  // com switch, OLT e servidor embaralhados no mesmo bloco.
  _layoutImportados(hosts) {
    const COL_W = 170, ROW_H = 150, PER_ROW = 6;
    const LABEL_X = 130, X0 = 300, GAP = 64;

    // Agrupa mantendo a ordem de chegada dentro de cada faixa.
    const faixas = new Map();
    hosts.forEach(h => {
      const t = TOPO_DEVICES[h.tipo] ? h.tipo : 'host';
      if (!faixas.has(t)) faixas.set(t, []);
      faixas.get(t).push(h);
    });
    const ordenadas = [...faixas.keys()].sort((a, b) => {
      const ia = TOPO_IMPORT_TIERS.indexOf(a), ib = TOPO_IMPORT_TIERS.indexOf(b);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
    });

    const jaPosicionados = this.nodes.filter(n => n.type !== 'text_box' && n.type !== 'area');
    // Reimportação: faixa nova entra abaixo do que já está desenhado, em vez de
    // cair por cima do que a pessoa já posicionou à mão.
    let y = jaPosicionados.length
      ? Math.max(...jaPosicionados.map(n => n.y)) + ROW_H + GAP
      : 120;

    const criar = (h, x, yy) => this.addNode(h.tipo, x, yy, {
      label: h.label || h.ip, ip: h.ip, id: 'crm_' + h.id,
      funcao: h.funcao, acesso_id: h.id, protocolo: h.protocolo,
      porta: h.porta, usuario: h.usuario, cliente_id: h.cliente_id,
    });

    let added = 0;
    ordenadas.forEach(tipo => {
      const lista = faixas.get(tipo);

      // Já existe node dessa função no canvas: o host novo entra ao lado dos
      // irmãos (à direita do último), pra continuar a faixa mesmo que ela tenha
      // sido movida — e não abrir um bloco solto no fim do desenho.
      const irmaos = jaPosicionados.filter(n => n.type === tipo);
      if (irmaos.length) {
        const ancora = irmaos.reduce((a, b) => (b.x > a.x ? b : a));
        lista.forEach((h, i) => {
          criar(h, ancora.x + COL_W * (1 + (i % PER_ROW)),
                   ancora.y + ROW_H * Math.floor(i / PER_ROW));
          added++;
        });
        return;
      }

      const linhas = Math.ceil(lista.length / PER_ROW);
      const def = TOPO_DEVICES[tipo];

      // Rótulo da faixa (text_box): id fixo por tipo, então uma reimportação
      // não cria um segundo rótulo "Switch L3" ao lado do que já está lá.
      if (!this.nodes.find(n => n.id === 'grp_' + tipo)) {
        this.addNode('text_box', LABEL_X, y + (linhas - 1) * ROW_H / 2, {
          id: 'grp_' + tipo, label: def.label, color: def.color,
        });
      }

      lista.forEach((h, i) => {
        criar(h, X0 + COL_W * (i % PER_ROW), y + ROW_H * Math.floor(i / PER_ROW));
        added++;
      });

      y += linhas * ROW_H + GAP;
    });
    return added;
  }

  async _refreshCrmNodeTypes() {
    try {
      const r = await fetch(`/clientes/${this.clienteId}/topologia/hosts/`);
      if (!r.ok) return;
      const d = await r.json();
      let updated = false;
      (d.hosts || []).forEach(h => {
        const node = this.nodes.find(n => n.id === 'crm_' + h.id);
        if (!node) return;
        node.funcao = h.funcao;
        if (!node.type_manual && node.type !== h.tipo) {
          node.type = h.tipo;
          this._renderNode(node); updated = true;
        }
      });
      if (updated) { this._renderLinks(); this._setDirty(); }
    } catch(e) { /* silencioso */ }
  }

  exportPNG() {
    const svgData = new XMLSerializer().serializeToString(this.svg);
    const svgBlob = new Blob([svgData], {type: 'image/svg+xml;charset=utf-8'});
    const svgUrl = URL.createObjectURL(svgBlob);
    const r = this.svg.getBoundingClientRect();
    const scale = 2;
    const canvas = document.createElement('canvas');
    canvas.width  = r.width  * scale;
    canvas.height = r.height * scale;
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, r.width, r.height);
    const img = new Image();
    img.onload = () => {
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(svgUrl);
      canvas.toBlob(blob => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'topologia.png';
        a.click();
        this._toast('PNG exportado');
      }, 'image/png');
    };
    img.onerror = () => {
      URL.revokeObjectURL(svgUrl);
      this._toast('Erro ao exportar PNG', 'error');
    };
    img.src = svgUrl;
  }

  fromJSON(data) {
    try {
      const d = typeof data === 'string' ? JSON.parse(data) : data;
      this.nodes = d.nodes || [];
      this.links = (d.links || []).map(l => ({
        waypoints: [], shape: 'straight', iface_a: '', iface_b: '', ...l
      }));
      this.selectedNodes = new Set();
      this._renderAll();
      if (this.nodes.length) this.zoomFit();
    } catch(e) { console.warn(e); }
  }

  _updateStatus() {
    // Áreas são anotações de fundo, não contam como "dispositivos".
    const reais = this.nodes.filter(n => n.type !== 'area');
    document.getElementById('st-nodes').textContent = reais.length;
    document.getElementById('st-links').textContent = this.links.length;
    const hint = document.getElementById('canvas-hint');
    if (hint) hint.classList.toggle('show', !this.nodes.length);
  }

  // ── Legenda de tipos de interface ───────────────────────────────────────

  toggleLegend() {
    const panel = document.getElementById('legend-panel');
    const btn = document.getElementById('btn-legend');
    if (!panel) return;
    const show = !panel.classList.contains('show');
    if (show && !panel.dataset.built) {
      const itens = Object.values(TOPO_IFACES).map(v =>
        `<div class="legend-item"><span class="legend-swatch" style="background:${v.color}"></span>${v.label}</div>`
      ).join('');
      panel.innerHTML = `
        <div class="legend-title">
          <span><i class="fas fa-ethernet"></i> Interfaces</span>
          <i class="fas fa-times legend-close" onclick="topo.toggleLegend()"></i>
        </div>
        <div class="legend-grid">${itens}</div>`;
      panel.dataset.built = '1';
    }
    panel.classList.toggle('show', show);
    if (btn) btn.classList.toggle('active', show);
  }


  // ── Portas PON de OLT Huawei (MA5600T/MA5800) ──────────────────────────────
  // Inventário (placas, portas, ONTs) vem do backup; `display port info/state`
  // e o `laser-switch` vão ao equipamento na hora, com preview editável e
  // confirmação — mesmo contrato do clone de L2VPN e da automação BGP.

  async mostrarPon(nodeId) {
    const node = this.nodes.find(n => n.id === nodeId);
    if (!node || !node.acesso_id) return;
    this._pon = {
      acessoId: node.acesso_id, nodeNome: node.label || '', dados: null,
      slot: null, porta: null, acao: null, comandos: null, resultado: null,
      confirmando: false, erro: '', carregando: true,
    };
    this._renderPonModal(`
      <div style="padding:40px;text-align:center;color:var(--muted)">
        <i class="fas fa-circle-notch fa-spin" style="font-size:1.6rem"></i>
        <div style="margin-top:12px;font-size:.82rem">Lendo as placas PON do backup…</div>
      </div>`);
    try {
      const r = await fetch(`/clientes/acessos/${node.acesso_id}/olt-pon/`, {
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      // Sessão expirada vira 302 pro login e o .json() estouraria com um erro
      // genérico — mesmo tratamento do modal de L2VPN.
      if (r.redirected || r.status === 401 || r.status === 403) {
        this.fecharPon();
        this._toast('Sessão expirada — recarregue a página e faça login', 'error');
        return;
      }
      if (!r.ok) throw new Error(r.status);
      if (!this._pon || this._pon.acessoId !== node.acesso_id) return; // modal trocou
      const dados = await r.json();
      this._pon.dados = dados;
      this._pon.carregando = false;
      // Abre já na primeira placa: com uma placa só (caso comum) não faz
      // sentido exigir um clique antes de ver as portas.
      if ((dados.placas || []).length) this._pon.slot = dados.placas[0].slot;
      this._pintarPon();
    } catch (e) {
      this._renderPonModal(`
        <div style="padding:36px;text-align:center;color:var(--red);font-size:.85rem">
          <i class="fas fa-triangle-exclamation" style="font-size:1.5rem;display:block;margin-bottom:10px"></i>
          Não foi possível ler as placas PON deste host.
        </div>
        <div class="modal-footer"><button class="btn-cancel" onclick="topo.fecharPon()">Fechar</button></div>`);
    }
  }

  fecharPon() {
    const el = document.getElementById('pon-modal');
    if (el) el.remove();
    this._pon = null;
  }

  _renderPonModal(html) {
    let el = document.getElementById('pon-modal');
    if (!el) {
      el = document.createElement('div');
      el.id = 'pon-modal';
      el.className = 'modal-overlay';
      el.addEventListener('mousedown', e => { if (e.target === el) this.fecharPon(); });
      document.body.appendChild(el);
    }
    el.innerHTML = `<div class="modal-box pon-box">${html}</div>`;
  }

  _ponPlacaAtual() {
    const d = this._pon.dados || {};
    return (d.placas || []).find(p => p.slot === this._pon.slot) || null;
  }

  _ponPortaAtual() {
    const placa = this._ponPlacaAtual();
    if (!placa || this._pon.porta === null) return null;
    return placa.portas.find(p => p.porta === this._pon.porta) || null;
  }

  _ponCabecalho() {
    const d = this._pon.dados || {};
    const host = d.host || {};
    return `
      <div class="pon-head">
        <div>
          <div class="pon-titulo"><i class="fas fa-network-wired"></i>
            Portas PON — ${this._esc(d.modelo || 'OLT Huawei')}</div>
          <div class="pon-sub">
            <b>${this._esc(host.nome || this._pon.nodeNome)}</b> · ${this._esc(host.ip || '')}
            ${d.total_onts ? ` · ${d.total_onts} ONTs em ${(d.placas || []).length} placa(s)` : ''}
            <br>Inventário do backup de <b>${this._esc(d.data_backup || '—')}</b>
          </div>
        </div>
        <i class="fas fa-times l2vpn-fechar" onclick="topo.fecharPon()" title="Fechar (Esc)"></i>
      </div>`;
  }

  _pintarPon() {
    const c = this._pon;
    const d = c.dados || {};

    if (!d.tem_backup || d.suportado === false) {
      this._renderPonModal(`
        ${this._ponCabecalho()}
        <div class="l2vpn-vazio" style="padding:38px 24px">
          <i class="fas fa-plug-circle-xmark"></i>
          <div>${this._esc(d.mensagem || 'Este host não tem inventário PON.')}</div>
        </div>
        <div class="modal-footer"><button class="btn-cancel" onclick="topo.fecharPon()">Fechar</button></div>`);
      return;
    }

    const placa = this._ponPlacaAtual();
    const chips = (d.placas || []).map(p => `
      <button class="l2vpn-chip ${p.slot === c.slot ? 'ativo' : ''}"
        onclick="topo._ponSelecionarPlaca('${p.slot}')">
        ${this._esc(p.slot)} <b>${this._esc(p.tipo || '')}</b>
        <em style="font-style:normal;opacity:.7">${p.onts} ONT</em>
      </button>`).join('');

    this._renderPonModal(`
      ${this._ponCabecalho()}
      <div class="l2vpn-barra"><div class="l2vpn-chips">${chips}</div></div>
      <div class="pon-corpo">
        ${placa ? this._ponGrade(placa) : ''}
        ${this._ponDetalhe()}
      </div>
      <div class="modal-footer">
        <button class="btn-cancel" onclick="topo.fecharPon()">Fechar</button>
      </div>`);

    // Com 16 portas na grade, o preview e o retorno nascem fora da área
    // visível do modal — sem isso o clique na ação parece não ter feito nada.
    const foco = document.querySelector('.pon-resultado') || document.querySelector('.pon-preview');
    if (foco) foco.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }

  /** Grade de portas da placa. A cor é a ocupação (quantas ONTs), então dá pra
   *  ver de longe onde está a base — e onde desativar a porta doeria.
   *
   *  Cada porta traz o próprio botão de desativar (laser off): o laser É o
   *  liga/desliga da porta PON, então ele mora na porta, não escondido atrás
   *  de um clique de seleção. O botão não executa nada sozinho — abre o
   *  preview daquela porta, que ainda exige a confirmação explícita. */
  _ponGrade(placa) {
    const tiles = placa.portas.map(p => {
      const classes = ['pon-porta'];
      if (p.porta === this._pon.porta) classes.push('sel');
      if (!p.configurada) classes.push('vazia');
      else if (p.onts >= 50) classes.push('cheia');
      else if (p.onts > 0) classes.push('ocupada');
      const impacto = p.onts ? ` — derruba ${p.onts} ONT${p.onts === 1 ? '' : 's'}` : ' (sem ONT cadastrada)';
      // Wrapper por fora: <button> dentro de <button> é HTML inválido e o
      // clique interno nem sempre chega ao handler certo.
      return `
        <div class="pon-porta-wrap">
          <button class="${classes.join(' ')}" onclick="topo._ponSelecionarPorta(${p.porta})"
            title="Porta ${p.porta} — ${p.onts} ONT${p.onts === 1 ? '' : 's'}${p.configurada ? '' : ' (sem configuração no backup)'}">
            <b>${p.porta}</b><em>${p.onts || '—'}</em>
          </button>
          <button class="pon-laser" onclick="topo._ponDesativarPorta(${p.porta})"
            title="Desativar a porta ${placa.slot}/${p.porta} (laser off)${impacto}"
            aria-label="Desativar a porta ${p.porta}">
            <i class="fas fa-power-off"></i>
          </button>
        </div>`;
    }).join('');
    // Placa sem `board add` no backup: o total de portas saiu do que está
    // configurado. Dizer isso é obrigação — o operador precisa saber que uma
    // porta física pode não estar listada (o contrário, oferecer porta que não
    // existe, foi o bug que gerou "% Parameter error" na OLT-HU-LEAL).
    const nota = placa.portas_inferidas ? `
      <span class="pon-nota" title="Sem a linha 'board add ${this._esc(placa.slot)}' no backup, o total de portas veio do que está configurado nela">
        <i class="fas fa-circle-info"></i> portas vistas no backup</span>` : '';
    return `
      <div class="pon-grade-wrap">
        <div class="pon-grade-titulo">
          Placa ${this._esc(placa.slot)} · ${this._esc(placa.tipo || 'tipo desconhecido')} ·
          ${placa.portas_total} portas ${nota}
          <span class="pon-legenda">
            <i class="pon-dot vazia"></i> sem config
            <i class="pon-dot ocupada"></i> com ONT
            <i class="pon-dot cheia"></i> 50+
          </span>
        </div>
        <div class="pon-grade">${tiles}</div>
      </div>`;
  }

  _ponDetalhe() {
    const c = this._pon;
    const porta = this._ponPortaAtual();
    if (!porta) {
      return `<div class="pon-vazio">Escolha uma porta acima para consultar ou operar.</div>`;
    }

    const clientes = (porta.clientes || []).map(o => `
      <span class="pon-ont"><b>${this._esc(o.ont)}</b>
        ${this._esc(o.desc || o.sn || '')}</span>`).join('');
    const restantes = porta.onts - (porta.clientes || []).length;

    const acoes = (c.dados.acoes || []).map(a => `
      <button class="pon-acao ${a.escreve ? 'perigo' : ''} ${c.acao === a.chave ? 'sel' : ''}"
        onclick="topo._ponPreview('${a.chave}')">
        <i class="fas fa-${a.chave === 'info' ? 'circle-info'
                          : a.chave === 'state' ? 'wave-square'
                          : a.chave === 'laser_off' ? 'power-off' : 'bolt'}"></i>
        ${this._esc(a.label)}
      </button>`).join('');

    return `
      <div class="pon-detalhe">
        <div class="pon-detalhe-head">
          <div>
            <b>${this._esc(c.slot)}/${porta.porta}</b>
            <span>${porta.onts} ONT${porta.onts === 1 ? '' : 's'}${porta.auto_find ? ' · ont-auto-find' : ''}${porta.distancia ? ' · ' + this._esc(porta.distancia) : ''}</span>
          </div>
        </div>
        ${porta.onts ? `<div class="pon-onts">${clientes}
          ${restantes > 0 ? `<span class="pon-ont mais">+${restantes}</span>` : ''}</div>` : ''}
        <div class="pon-acoes">${acoes}</div>
        ${c.erro ? `<div class="l2vpn-erro"><i class="fas fa-triangle-exclamation"></i>
          <div>${this._esc(c.erro)}</div></div>` : ''}
        ${c.executando ? `<div class="pon-executando">
          <i class="fas fa-circle-notch fa-spin"></i>
          Conectando na OLT e executando…</div>` : ''}
        ${c.comandos ? this._ponPreviewBloco() : ''}
        ${c.resultado ? this._ponResultado() : ''}
      </div>`;
  }

  _ponPreviewBloco() {
    const c = this._pon;
    const escreve = !!c.escreve;
    const porta = this._ponPortaAtual() || {onts: 0};
    return `
      <div class="pon-preview">
        <div class="l2vpn-col-titulo">Comandos que serão enviados</div>
        <textarea class="l2vpn-config editavel" id="pon-comandos"
          rows="${Math.min(c.comandos.length + 1, 12)}"
          oninput="topo._pon.comandosEditados = this.value">${this._esc(c.comandos.join('\n'))}</textarea>
        ${escreve ? `
          <div class="pon-alerta">
            <i class="fas fa-triangle-exclamation"></i>
            <div><b>Isto muda o equipamento.</b>
              ${c.acao === 'laser_off'
                ? `Apagar o laser da porta <b>${this._esc(c.slot)}/${porta.porta}</b> derruba
                   <b>${porta.onts} ONT${porta.onts === 1 ? '' : 's'}</b> — a base inteira dessa porta fica sem sinal
                   até o laser voltar.`
                : `Religa o laser da porta <b>${this._esc(c.slot)}/${porta.porta}</b>.`}
            </div>
          </div>` : ''}
        <div class="pon-preview-acoes">
          ${c.confirmando
            ? `<div class="l2vpn-confirma">
                 <span><i class="fas fa-triangle-exclamation"></i>
                   Confirma enviar para <b>${this._esc((c.dados.host || {}).nome || '')}</b>?</span>
                 <button class="btn-ok perigo" onclick="topo._ponExecutar()">Sim, enviar</button>
                 <button class="btn-cancel" onclick="topo._ponCancelarConfirma()">Cancelar</button>
               </div>`
            : `<button class="btn-ok ${escreve ? 'perigo' : ''}" onclick="topo._ponConfirmar()">
                 <i class="fas fa-paper-plane"></i> ${escreve ? 'Aplicar no equipamento' : 'Executar consulta'}
               </button>
               <button class="btn-cancel" onclick="topo._ponLimpar()">Cancelar</button>`}
        </div>
      </div>`;
  }

  _ponResultado() {
    const c = this._pon;
    const r = c.resultado;
    const ok = r.status === 'sucesso';
    const rotulo = ((c.dados.acoes || []).find(a => a.chave === c.acao) || {}).label || '';
    return `
      <div class="pon-resultado">
        <div class="l2vpn-col-titulo" style="color:${ok ? 'var(--green)' : 'var(--red)'}">
          <i class="fas fa-${ok ? 'circle-check' : 'circle-xmark'}"></i>
          ${ok ? this._esc(rotulo) : 'O equipamento recusou o comando'}
          <span style="color:var(--faint);font-weight:500;text-transform:none;letter-spacing:0">
            · ${this._esc(c.slot)}/${c.porta}</span>
        </div>
        ${!ok ? `<div class="pon-recusa">
          <i class="fas fa-triangle-exclamation"></i>
          <div><b>${this._esc(r.recusa || 'A CLI rejeitou o comando.')}</b>
            <br>${c.escreve
              ? 'Nada foi alterado na porta — o comando não chegou a valer.'
              : 'A consulta não retornou dados.'}</div>
        </div>` : ''}
        <pre class="l2vpn-config" style="max-height:260px">${this._esc(r.output || '(sem saída)')}</pre>
      </div>`;
  }

  _ponSelecionarPlaca(slot) {
    Object.assign(this._pon, {slot, porta: null, acao: null, comandos: null,
                              resultado: null, confirmando: false, erro: ''});
    this._pintarPon();
  }

  /** Botão de desativar na própria porta: seleciona e já monta o preview do
   *  laser off — a confirmação continua sendo um segundo clique, no preview. */
  _ponDesativarPorta(porta) {
    Object.assign(this._pon, {porta, acao: null, comandos: null, comandosEditados: null,
                              resultado: null, confirmando: false, erro: ''});
    this._ponPreview('laser_off');
  }

  _ponSelecionarPorta(porta) {
    Object.assign(this._pon, {porta, acao: null, comandos: null,
                              resultado: null, confirmando: false, erro: ''});
    this._pintarPon();
  }

  _ponLimpar() {
    Object.assign(this._pon, {acao: null, comandos: null, comandosEditados: null,
                              confirmando: false, erro: ''});
    this._pintarPon();
  }

  _ponCancelarConfirma() {
    this._pon.confirmando = false;
    this._pintarPon();
  }

  _ponConfirmar() {
    // Guarda o texto do textarea antes de re-renderizar (o innerHTML novo
    // descarta o elemento e, com ele, qualquer edição não lida).
    this._ponLerTextarea();
    // Consulta é leitura pura: exigir um segundo clique de confirmação pra ver
    // o estado de uma porta só ensina a clicar em "sim" no automático — e é
    // justamente esse reflexo que não pode existir no laser.
    if (!this._pon.escreve) { this._ponExecutar(); return; }
    this._pon.confirmando = true;
    this._pintarPon();
  }

  _ponLerTextarea() {
    const ta = document.getElementById('pon-comandos');
    if (ta) this._pon.comandosEditados = ta.value;
  }

  /** Dispara uma ação na porta selecionada.
   *
   *  Consulta (`info`/`state`) vai DIRETO ao equipamento: o comando é fixo,
   *  não muda nada e não há o que revisar — mostrar um textarea de comandos
   *  antes de um `display` só coloca um passo entre a pergunta e a resposta.
   *  Escrita (laser) continua passando pelo preview editável + confirmação. */
  async _ponPreview(acao) {
    const c = this._pon;
    const def = (c.dados.acoes || []).find(a => a.chave === acao) || {};
    Object.assign(c, {acao, escreve: !!def.escreve, resultado: null, confirmando: false,
                      erro: '', comandos: null, comandosEditados: null});
    if (!def.escreve) { this._ponExecutar(); return; }
    this._pintarPon();
    try {
      const r = await fetch(`/clientes/acessos/${c.acessoId}/olt-pon/executar/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json',
                  'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value},
        body: JSON.stringify({acao, slot: c.slot, portas: [c.porta], preview: true}),
      });
      const d = await r.json();
      if (!r.ok) { c.erro = d.error || `Erro ${r.status}`; this._pintarPon(); return; }
      c.comandos = d.comandos;
      c.escreve = d.escreve;
      c.ontsAfetadas = d.onts_afetadas;
      this._pintarPon();
    } catch (e) {
      c.erro = 'Falha ao gerar os comandos: ' + e;
      this._pintarPon();
    }
  }

  async _ponExecutar() {
    const c = this._pon;
    this._ponLerTextarea();
    const comandos = (c.comandosEditados !== null && c.comandosEditados !== undefined)
      ? c.comandosEditados.split('\n').map(l => l.trim()).filter(Boolean)
      : c.comandos;

    c.confirmando = false;
    c.resultado = null;
    c.executando = true;
    this._pintarPon();
    this._toast('Conectando no equipamento…');

    try {
      const r = await fetch(`/clientes/acessos/${c.acessoId}/olt-pon/executar/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json',
                  'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value},
        body: JSON.stringify({acao: c.acao, slot: c.slot, portas: [c.porta],
                              preview: false, comandos}),
      });
      const d = await r.json();
      c.executando = false;
      if (!r.ok) { c.erro = d.error || `Erro ${r.status}`; this._pintarPon(); return; }
      c.resultado = d;
      this._pintarPon();
      this._toast(d.status === 'sucesso' ? 'Comando executado' : 'O equipamento recusou',
                  d.status === 'sucesso' ? 'ok' : 'error');
    } catch (e) {
      c.executando = false;
      c.erro = 'Falha ao executar: ' + e;
      this._pintarPon();
    }
  }

  _esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  _toast(msg, type='ok') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.borderColor = type==='error' ? 'var(--red)' : 'var(--green)';
    t.style.color       = type==='error' ? 'var(--red)' : 'var(--green)';
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
  }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  window.topo = new TopoEditor({
    clienteId: window.TOPO_CLIENTE_ID,
    diagramaId: window.TOPO_DIAGRAMA_ID,
  });
  topo._updateStatus(); // estado inicial da dica de canvas vazio, antes de qualquer fetch resolver

  const dados = window.TOPO_DADOS;
  const temNodes = dados && (typeof dados === 'string'
    ? dados.includes('"nodes"') && JSON.parse(dados)?.nodes?.length
    : dados.nodes?.length);

  if (temNodes) {
    topo.fromJSON(dados);
    topo._refreshCrmNodeTypes();
  } else {
    topo.importHosts();
  }

  document.getElementById('btn-grid').classList.add('active');
  document.getElementById('btn-snap').classList.add('active');
  // Efeitos (pulso nos nodes do CRM, fluxo animado e "pacotes" nos links) vêm
  // ligados por padrão, exceto para quem pediu menos movimento no SO/navegador
  // (prefers-reduced-motion) — nesse caso já inicia desligado, sem exigir que
  // a pessoa descubra e clique no botão "Efeitos" manualmente.
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    topo.effectsOn = false;
    document.body.classList.add('effects-off');
  } else {
    document.getElementById('btn-effects').classList.add('active');
  }
});
