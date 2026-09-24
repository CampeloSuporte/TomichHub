/* Tela de racks — montagem por U e conexões físicas a partir da topologia.
 *
 * O servidor é a fonte da verdade (racks/services.py): toda ação faz POST e
 * recebe o `estado` inteiro de volta, que substitui o local e redesenha tudo.
 * A checagem de colisão daqui é só para pintar a prévia do arraste de verde
 * ou vermelho; quem decide é o backend.
 *
 * Numeração EIA-310: U1 é o de baixo. Na tela o U mais alto fica em cima.
 * Ver docs/racks.md.
 */
'use strict';

const RB_ARRASTE_MIN = 4; // px até um clique virar arraste

class RackBuilder {
  constructor(cfg) {
    this.clienteId = cfg.clienteId;
    this.diagramaId = cfg.diagramaId || '';
    this.leitura = !!cfg.somenteLeitura;
    this.cat = cfg.catalogo;
    this.estado = {racks: [], conexoes: [], dispositivos: [], links: []};
    this.rackId = null;
    this.face = 'frente';
    this.sel = null;              // id do equipamento selecionado
    this.aPaleta = 'catalogo';
    this.aLateral = 'equipamento';
    this.filtroLinks = 'todos';
    this.busca = '';
    this.focoLink = null;
    this.drag = null;
    this._ifaceCache = {};
    this.U = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--u')) || 24;

    const qs = new URLSearchParams(location.search);
    this.embed = qs.get('embed') === '1';
    this._linkInicial = qs.get('link');
    this._rackInicial = qs.get('rack');
    const voltar = new URLSearchParams();
    if (this.diagramaId) voltar.set('diagrama', this.diagramaId);
    if (this.embed) voltar.set('embed', '1');
    document.getElementById('btn-voltar').href =
      `/clientes/${this.clienteId}/topologia/editor/` + (voltar.toString() ? '?' + voltar : '');

    window.addEventListener('pointermove', e => this._mover(e));
    window.addEventListener('pointerup', e => this._soltar(e));
    window.addEventListener('keydown', e => this._tecla(e));
    this.carregar();
  }

  // ── Dados ────────────────────────────────────────────────────────────────

  async carregar() {
    try {
      const r = await fetch(`/racks/cliente/${this.clienteId}/estado/`, {headers: {'Accept': 'application/json'}});
      const d = await this._json(r);
      if (!d || !d.ok) { this._toast((d && d.erro) || `Erro ${r.status} ao carregar`, true); return; }
      this._aplicar(d.estado);
      this._primeiraAbertura();
    } catch (e) { this._toast('Erro ao carregar: ' + e.message, true); }
  }

  /** Resposta pode não ser JSON: sessão expirada redireciona pro login. */
  async _json(r) {
    const tipo = r.headers.get('content-type') || '';
    if (!tipo.includes('application/json')) {
      if (r.redirected || r.status === 401) this._toast('Sessão expirada — entre de novo.', true);
      return null;
    }
    return r.json();
  }

  async _post(url, dados) {
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    try {
      const r = await fetch(url, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Accept': 'application/json'},
        body: JSON.stringify(dados || {}),
      });
      const d = await this._json(r);
      if (!d) { if (r.status !== 401) this._toast(`Erro ${r.status}`, true); return null; }
      if (!d.ok) { this._toast(d.erro || 'Não foi possível concluir.', true); return null; }
      this._aplicar(d.estado);
      return d;
    } catch (e) { this._toast('Erro de rede: ' + e.message, true); return null; }
  }

  _aplicar(estado) {
    this.estado = estado;
    const ids = estado.racks.map(r => r.id);
    if (!ids.includes(this.rackId)) {
      let salvo = null;
      try { salvo = parseInt(localStorage.getItem(`rack_atual_${this.clienteId}`), 10); } catch (e) {}
      this.rackId = ids.includes(salvo) ? salvo : (ids[0] || null);
    }
    if (this.sel && !this._eq(this.sel)) this.sel = null;
    this.render();
  }

  _primeiraAbertura() {
    const r = parseInt(this._rackInicial, 10);
    if (r && this.estado.racks.some(x => x.id === r)) this.selecionarRack(r);
    if (!this._linkInicial) return;
    const link = this.estado.links.find(l => l.link_id === this._linkInicial);
    this.focoLink = this._linkInicial;
    this.abaLateral('conexoes');
    if (!link) { this._toast('Enlace não encontrado na topologia salva (ou é lógico, sem cabo).', true); return; }
    if (link.status === 'pronta' && !this.leitura) this.dialogoConexao({link});
    if (link.status === 'criada') this._realcarConexao(link.conexao_id, true);
    setTimeout(() => document.querySelector(`[data-link="${CSS.escape(link.link_id)}"]`)?.scrollIntoView({block: 'center'}), 50);
  }

  _rack() { return this.estado.racks.find(r => r.id === this.rackId) || null; }
  _eq(id) {
    for (const r of this.estado.racks) { const e = r.equipamentos.find(x => x.id === id); if (e) return e; }
    return null;
  }
  _rackDe(eq) { return this.estado.racks.find(r => r.id === eq.rack_id); }
  _cabosDe(id) { return this.estado.conexoes.filter(c => c.ponta_a_id === id || c.ponta_b_id === id); }
  _tipo(t) { return this.cat.tipos[t] || this.cat.tipos.outro; }
  _corCabo(c) { return c.cor || (this.cat.meios[c.meio] || this.cat.meios.outro).cor; }

  // ── Render ───────────────────────────────────────────────────────────────

  render() {
    this._renderAbas();
    this._renderPaleta();
    this._renderPalco();
    this._renderLateral();
    document.getElementById('cont-disp').textContent =
      this.estado.dispositivos.filter(d => !d.montado).length;
    document.getElementById('cont-links').textContent =
      this.estado.links.filter(l => l.status !== 'criada').length;
  }

  _renderAbas() {
    document.getElementById('rack-tabs').innerHTML = this.estado.racks.map(r => {
      const ocup = this._ocupacao(r);
      return `<button class="rack-tab ${r.id === this.rackId ? 'on' : ''}" onclick="rb.selecionarRack(${r.id})">
        ${this._esc(r.nome)}<small>${r.local ? this._esc(r.local) + ' · ' : ''}${ocup}/${r.altura_u}U</small></button>`;
    }).join('');
  }

  /** U ocupados vistos na face atual (o que conta como "espaço usado"). */
  _ocupacao(rack) {
    const us = new Set();
    rack.equipamentos.forEach(e => {
      if (e.face !== this.face && !e.profundidade_total) return;
      for (let u = e.u_inicial; u < e.u_inicial + e.altura_u; u++) us.add(u);
    });
    return us.size;
  }

  _renderPaleta() {
    document.getElementById('aba-catalogo').classList.toggle('on', this.aPaleta === 'catalogo');
    document.getElementById('aba-dispositivos').classList.toggle('on', this.aPaleta === 'dispositivos');
    const corpo = document.getElementById('paleta-corpo');
    const busca = `<div class="busca"><i class="fas fa-magnifying-glass"></i>
      <input id="pal-busca" placeholder="Buscar…" value="${this._esc(this.busca)}" autocomplete="off"></div>`;
    const termo = this._norm(this.busca);

    if (this.aPaleta === 'catalogo') {
      const grupos = this.cat.grupos.map(g => {
        const itens = g.tipos.filter(t => !termo || this._norm(this.cat.tipos[t].label).includes(termo)).map(t => {
          const d = this.cat.tipos[t];
          return `<div class="pal-item so-escrita" data-tipo="${t}" style="--c:${d.cor}">
            <div class="pal-ico"><i class="fas ${d.icone}"></i></div>
            <div class="pal-txt"><b>${this._esc(d.label)}</b><span>${d.profundidade_total ? 'Profundidade total' : 'Raso — só ' + d.face}</span></div>
            <span class="pal-u">${d.altura}U</span></div>`;
        }).join('');
        return itens ? `<div class="grupo-tit">${this._esc(g.nome)}</div>${itens}` : '';
      }).join('');
      corpo.innerHTML = busca + (this.leitura ? '<div class="dica">Acesso somente leitura.</div>'
        : '<div class="dica"><i class="fas fa-hand-pointer"></i> Arraste para um U do rack, ou clique para montar no primeiro espaço livre de cima.</div>') + grupos;
    } else {
      const lista = this.estado.dispositivos.filter(d =>
        !termo || this._norm(`${d.label} ${d.ip}`).includes(termo));
      const livres = lista.filter(d => !d.montado), montados = lista.filter(d => d.montado);
      const item = d => {
        const t = this._tipo(d.tipo);
        const sub = d.montado ? `<i class="fas fa-check"></i> ${this._esc(d.montado.rack)} · U${d.montado.u}`
                              : this._esc(d.ip || (d.origem === 'crm' ? 'Host do CRM' : 'Desenhado na topologia'));
        return `<div class="pal-item ${d.montado ? 'montado' : ''}" data-disp="${this._esc(d.chave)}" style="--c:${t.cor}"
                  title="${d.montado ? 'Já montado — clique para ver' : 'Arraste para o rack'}">
          <div class="pal-ico"><i class="fas ${t.icone}"></i></div>
          <div class="pal-txt"><b>${this._esc(d.label || d.ip || d.node_id)}</b><span>${sub}</span></div>
          ${d.montado ? '' : `<span class="pal-u">${t.altura}U</span>`}</div>`;
      };
      corpo.innerHTML = busca +
        (lista.length ? '' : '<div class="prop-empty"><i class="fas fa-diagram-project"></i>Nenhum equipamento físico na topologia nem nos hosts do CRM.</div>') +
        (livres.length ? `<div class="grupo-tit">A montar (${livres.length})</div>` + livres.map(item).join('') : '') +
        (montados.length ? `<div class="grupo-tit">Já montados (${montados.length})</div>` + montados.map(item).join('') : '');
    }
    const inp = document.getElementById('pal-busca');
    inp.addEventListener('input', () => {
      this.busca = inp.value;
      const pos = inp.selectionStart;
      this._renderPaleta();
      const novo = document.getElementById('pal-busca'); novo.focus(); novo.setSelectionRange(pos, pos);
    });
    corpo.querySelectorAll('.pal-item').forEach(el => el.addEventListener('pointerdown', e => this._pegar(e, el)));
  }

  _renderPalco() {
    const palco = document.getElementById('palco');
    const rack = this._rack();
    if (!rack) {
      palco.innerHTML = `<div class="vazio"><i class="fas fa-server grande"></i>
        <b style="color:var(--text)">Nenhum rack cadastrado para este cliente.</b><br>
        Crie um rack e monte os equipamentos por U — os da topologia já aparecem prontos na aba
        <b>Da topologia</b>, e os enlaces viram cabos na aba <b>Conexões</b>.
        ${this.leitura ? '' : '<br><br><button class="tb-btn primary" style="margin:0 auto" onclick="rb.dialogoRack()"><i class="fas fa-plus"></i> Criar rack</button>'}</div>`;
      return;
    }
    const n = rack.altura_u, ocup = this._ocupacao(rack);
    const numeros = Array.from({length: n}, (_, i) => n - i)
      .map(u => `<div class="${u % 5 === 0 ? 'u5' : ''}">${u}</div>`).join('');
    const visiveis = rack.equipamentos.filter(e => e.face === this.face || e.profundidade_total);
    palco.innerHTML = `
      <div id="rack-cab">
        <div><h1>${this._esc(rack.nome)}</h1>
          <div class="sub">${rack.local ? this._esc(rack.local) + ' · ' : ''}${n}U · vista ${this.face === 'frente' ? 'frontal' : 'traseira'}</div></div>
        <div class="ocup"><div class="barra"><i style="width:${Math.round(ocup / n * 100)}%"></i></div>${ocup}/${n}U ocupados
          <button class="ico-btn so-escrita" title="Editar rack" onclick="rb.dialogoRack(${rack.id})"><i class="fas fa-gear"></i></button>
          <button class="ico-btn danger so-escrita" title="Excluir rack" onclick="rb.excluirRack(${rack.id})"><i class="fas fa-trash"></i></button></div>
      </div>
      <div class="rack" style="--n:${n}">
        <div class="rack-topo"></div>
        <div class="rack-corpo">
          <div class="trilho esq">${numeros}</div>
          <div class="baias" id="baias">${visiveis.map(e => this._eqHtml(e, n)).join('')}<div id="previa"></div></div>
          <div class="trilho dir">${numeros}</div>
        </div>
        <div class="rack-base"></div>
      </div>`;
    palco.querySelectorAll('.eq:not(.verso)').forEach(el => el.addEventListener('pointerdown', e => this._pegar(e, el)));
    palco.querySelectorAll('.eq.verso').forEach(el => el.addEventListener('click', () => this.selecionar(+el.dataset.id)));
  }

  _eqHtml(e, n) {
    const t = this._tipo(e.tipo);
    const verso = e.face !== this.face;
    const topo = (n - (e.u_inicial + e.altura_u - 1)) * this.U;
    const alt = e.altura_u * this.U - 1;
    const cabos = this._cabosDe(e.id);
    const classes = ['eq', `eq-${e.altura_u}u`];
    if (verso) classes.push('verso');
    if (!t.profundidade_total) classes.push('passivo');
    if (e.tipo === 'tampa') classes.push('tampa');
    if (e.id === this.sel) classes.push('sel');
    const titulo = `${e.nome} — U${e.u_inicial}${e.altura_u > 1 ? '–U' + (e.u_inicial + e.altura_u - 1) : ''}` +
      (verso ? ' (montado pela outra face)' : '');
    let miolo;
    if (verso) {
      miolo = `<div class="rosto"><i class="fas ${t.icone} ic"></i><div class="txt"><span class="nome">${this._esc(e.nome)}</span>
        <span class="ip">traseira</span></div><span class="ventoinha"></span></div>`;
    } else if (e.tipo === 'tampa' || e.tipo === 'organizador') {
      miolo = `<div class="rosto"><span class="ip">${this._esc(e.nome)}</span></div>`;
    } else {
      miolo = `<div class="rosto"><i class="fas ${t.icone} ic"></i>
        <div class="txt"><span class="nome">${this._esc(e.nome)}</span>${e.acesso_host ? `<span class="ip">${this._esc(e.acesso_host)}</span>` : ''}</div>
        ${this._portasHtml(e, cabos)}
        ${cabos.length ? `<span class="cabos" title="${cabos.length} cabo(s)"><i class="fas fa-ethernet"></i> ${cabos.length}</span>` : ''}
        ${t.profundidade_total ? '<span class="leds"><i></i><i></i></span>' : ''}</div>`;
    }
    return `<div class="${classes.join(' ')}" data-id="${e.id}" style="top:${topo}px;height:${alt}px;--c:${t.cor}" title="${this._esc(titulo)}">
      <div class="orelha"><i></i>${e.altura_u > 1 ? '<i></i>' : ''}</div><div class="faixa"></div>${miolo}
      <div class="orelha"><i></i>${e.altura_u > 1 ? '<i></i>' : ''}</div></div>`;
  }

  /** Portas desenhadas no espelho (ímpares em cima, pares embaixo, como nos
   *  switches). Só acende a porta cujo nome no cabo é o número dela ("7") —
   *  "ge0/0/7" não é necessariamente a 7ª porta física do espelho. */
  _portasHtml(e, cabos) {
    if (!e.num_portas) return '';
    const linhas = e.altura_u >= 2 ? 4 : 2;
    const total = Math.min(e.num_portas, linhas * 24);
    const lig = {};
    cabos.forEach(c => {
      const porta = (c.ponta_a_id === e.id ? c.porta_a : c.porta_b).trim();
      if (/^\d+$/.test(porta)) lig[parseInt(porta, 10)] = this._corCabo(c);
      if (c.ponta_a_id === e.id && c.ponta_b_id === e.id && /^\d+$/.test(c.porta_b.trim())) lig[parseInt(c.porta_b, 10)] = this._corCabo(c);
    });
    let html = '';
    for (let i = 1; i <= total; i++) {
      html += lig[i] ? `<b class="lig" style="--pc:${lig[i]}" title="Porta ${i}"></b>` : '<b></b>';
    }
    return `<span class="portas" style="--linhas:${linhas}">${html}</span>`;
  }

  _renderLateral() {
    document.getElementById('aba-equipamento').classList.toggle('on', this.aLateral === 'equipamento');
    document.getElementById('aba-conexoes').classList.toggle('on', this.aLateral === 'conexoes');
    const corpo = document.getElementById('lateral-corpo');
    corpo.innerHTML = this.aLateral === 'equipamento' ? this._htmlEquipamento() : this._htmlConexoes();
    corpo.querySelectorAll('[data-realce]').forEach(el => {
      el.addEventListener('mouseenter', () => this._realcarConexao(+el.dataset.realce, true));
      el.addEventListener('mouseleave', () => this._realcarConexao(+el.dataset.realce, false));
    });
  }

  _htmlEquipamento() {
    const e = this.sel && this._eq(this.sel);
    if (!e) return `<div class="prop-empty"><i class="fas fa-arrow-pointer"></i>Clique num equipamento do rack<br>para editar, ou arraste um do catálogo.</div>`;
    const t = this._tipo(e.tipo);
    const ro = this.leitura ? 'disabled' : '';
    const tipos = Object.entries(this.cat.tipos).map(([k, v]) =>
      `<option value="${k}" ${k === e.tipo ? 'selected' : ''}>${this._esc(v.label)}</option>`).join('');
    const racks = this.estado.racks.map(r =>
      `<option value="${r.id}" ${r.id === e.rack_id ? 'selected' : ''}>${this._esc(r.nome)}</option>`).join('');
    const hosts = this.estado.dispositivos.filter(d => d.acesso_id && (!d.montado || d.montado.equipamento_id === e.id))
      .map(d => `<option value="${d.acesso_id}" ${d.acesso_id === e.acesso_id ? 'selected' : ''}>${this._esc(d.label)}${d.ip ? ' — ' + this._esc(d.ip) : ''}</option>`).join('');
    const cabos = this._cabosDe(e.id);
    return `
      <div class="hero" style="--c:${t.cor}"><div class="pal-ico"><i class="fas ${t.icone}"></i></div>
        <div><b>${this._esc(e.nome)}</b><span>${this._esc(t.label)} · ${this._esc(this._rackDe(e).nome)} U${e.u_inicial}${e.altura_u > 1 ? '–U' + (e.u_inicial + e.altura_u - 1) : ''} · ${e.face}</span></div></div>
      <div class="prop-group"><label class="prop-label">Nome</label><input class="prop-input" id="eq-nome" value="${this._esc(e.nome)}" ${ro}></div>
      <div class="prop-row">
        <div class="prop-group"><label class="prop-label">Tipo</label><select class="prop-select" id="eq-tipo" ${ro}>${tipos}</select></div>
        <div class="prop-group"><label class="prop-label">Rack</label><select class="prop-select" id="eq-rack" ${ro}>${racks}</select></div>
      </div>
      <div class="prop-row">
        <div class="prop-group"><label class="prop-label">U inicial</label><input class="prop-input" id="eq-u" type="number" min="1" value="${e.u_inicial}" ${ro}></div>
        <div class="prop-group"><label class="prop-label">Altura (U)</label><input class="prop-input" id="eq-alt" type="number" min="1" max="${this.cat.altura_equipamento_max}" value="${e.altura_u}" ${ro}></div>
      </div>
      <div class="prop-row">
        <div class="prop-group"><label class="prop-label">Face</label><select class="prop-select" id="eq-face" ${ro}>
          <option value="frente" ${e.face === 'frente' ? 'selected' : ''}>Frente</option>
          <option value="traseira" ${e.face === 'traseira' ? 'selected' : ''}>Traseira</option></select></div>
        <div class="prop-group"><label class="prop-label">Portas</label><input class="prop-input" id="eq-portas" type="number" min="0" max="512" value="${e.num_portas}" ${ro}></div>
      </div>
      <div class="prop-group"><label class="chk"><input type="checkbox" id="eq-total" ${e.profundidade_total ? 'checked' : ''} ${ro}>
        Profundidade total <span style="color:var(--faint);font-size:.68rem">(ocupa frente e traseira do U)</span></label></div>
      <div class="prop-group"><label class="prop-label">Host do CRM</label><select class="prop-select" id="eq-acesso" ${ro}>
        <option value="">— nenhum —</option>${hosts}</select></div>
      <div class="prop-row">
        <div class="prop-group"><label class="prop-label">Fabricante</label><input class="prop-input" id="eq-fab" value="${this._esc(e.fabricante)}" ${ro}></div>
        <div class="prop-group"><label class="prop-label">Modelo</label><input class="prop-input" id="eq-mod" value="${this._esc(e.modelo)}" ${ro}></div>
      </div>
      <div class="prop-group"><label class="prop-label">Observações</label><textarea class="prop-input" id="eq-obs" rows="2" ${ro}>${this._esc(e.observacoes)}</textarea></div>
      <div class="so-escrita">
        <button class="prop-btn primary" onclick="rb.salvarEquipamento(${e.id})"><i class="fas fa-check"></i> Salvar</button>
        <button class="prop-btn danger" onclick="rb.excluirEquipamento(${e.id})"><i class="fas fa-trash"></i> Remover do rack</button>
      </div>
      <div class="sec-tit"><h3>Cabos (${cabos.length})</h3>
        <button class="prop-btn mini so-escrita" onclick="rb.dialogoConexao({pontaA:${e.id}})"><i class="fas fa-plus"></i> Cabo</button></div>
      ${cabos.map(c => this._cartaoCabo(c, e.id)).join('') || '<div class="dica">Nenhum cabo ligado. Os enlaces da topologia viram cabos na aba Conexões.</div>'}`;
  }

  _htmlConexoes() {
    const links = this.estado.links;
    const n = s => links.filter(l => l.status === s).length;
    const filtro = this.filtroLinks;
    const vis = links.filter(l => filtro === 'todos' || l.status === filtro);
    const btn = (k, rot) => `<button class="${filtro === k ? 'on' : ''}" onclick="rb.filtrar('${k}')">${rot}</button>`;
    const cartoes = vis.map(l => {
      const ponta = p => `<span class="ponta">${this._esc(p.label || p.node_id)}${p.porta ? ` <em>${this._esc(p.porta)}</em>` : ''}</span>`;
      // Rótulo da velocidade vem do TOPO_IFACES do editor (topo_engine.js).
      const iface = ((window.TOPO_IFACES || {})[l.iface] || {}).label || l.iface;
      let acao = '';
      if (!this.leitura && l.status === 'pronta') acao = `<button class="prop-btn mini" onclick="rb.dialogoConexao({linkId:'${this._escJs(l.link_id)}'})"><i class="fas fa-plug"></i> Criar cabo</button>`;
      if (!this.leitura && l.status === 'pendente' && this._rack()) acao = `<button class="prop-btn mini" title="Monta as pontas que faltam no rack ${this._esc(this._rack().nome)}" onclick="rb.montarPendentes('${this._escJs(l.link_id)}')"><i class="fas fa-download"></i> Montar aqui</button>`;
      const extra = l.status === 'criada' ? ` data-realce="${l.conexao_id}"` : '';
      const meta = l.status === 'pendente' ? `Falta montar: ${this._esc(l.faltando.join(', '))}`
        : [l.label, iface, l.vlan ? 'VLAN ' + l.vlan : ''].filter(Boolean).map(x => this._esc(x)).join(' · ');
      return `<div class="cartao ${this.focoLink === l.link_id ? 'foco' : ''}" data-link="${this._esc(l.link_id)}"${extra}>
        <div class="ln">${ponta(l.a)}<i class="fas fa-arrows-left-right seta"></i>${ponta(l.b)}</div>
        <div class="ln"><span class="pill ${l.status}">${l.status}</span><span class="meta">${meta}</span>${acao}</div></div>`;
    }).join('');
    const cabos = this.estado.conexoes.map(c => this._cartaoCabo(c)).join('');
    return `
      <div class="sec-tit"><h3>Enlaces da topologia</h3></div>
      <div class="filtros">${btn('todos', `Todos ${links.length}`)}${btn('pronta', `Prontos ${n('pronta')}`)}${btn('pendente', `Pendentes ${n('pendente')}`)}${btn('criada', `Com cabo ${n('criada')}`)}</div>
      ${cartoes || `<div class="dica">${links.length ? 'Nada neste filtro.' : 'Nenhum enlace físico na topologia salva deste cliente. Enlaces com Internet, IX, nuvem, VM ou grupo são lógicos e não aparecem aqui.'}</div>`}
      <div class="sec-tit"><h3>Cabos (${this.estado.conexoes.length})</h3>
        <button class="prop-btn mini so-escrita" onclick="rb.dialogoConexao({})"><i class="fas fa-plus"></i> Cabo manual</button></div>
      ${cabos || '<div class="dica">Nenhum cabo cadastrado.</div>'}`;
  }

  _cartaoCabo(c, doPontoDeVista) {
    let [a, pa, b, pb] = [this._eq(c.ponta_a_id), c.porta_a, this._eq(c.ponta_b_id), c.porta_b];
    if (doPontoDeVista && c.ponta_b_id === doPontoDeVista && c.ponta_a_id !== doPontoDeVista) [a, pa, b, pb] = [b, pb, a, pa];
    const lugar = eq => eq ? `${this._esc(eq.nome)}` : '?';
    const outroRack = eq => eq && eq.rack_id !== this.rackId ? ` <em>(${this._esc(this._rackDe(eq).nome)})</em>` : '';
    const meio = (this.cat.meios[c.meio] || this.cat.meios.outro).label;
    const meta = [c.identificacao, meio, c.conector, c.comprimento_m ? c.comprimento_m + ' m' : ''].filter(Boolean).map(x => this._esc(x)).join(' · ');
    return `<div class="cartao" data-realce="${c.id}">
      <div class="ln"><span class="cor-cabo" style="--cc:${this._corCabo(c)}"></span>
        <span class="ponta">${lugar(a)}${outroRack(a)} <em>${this._esc(pa || '—')}</em></span><i class="fas fa-arrows-left-right seta"></i>
        <span class="ponta">${lugar(b)}${outroRack(b)} <em>${this._esc(pb || '—')}</em></span></div>
      <div class="ln"><span class="meta">${meta}${c.topologia_link_id ? ' · <i class="fas fa-diagram-project" title="Criado a partir da topologia"></i>' : ''}</span>
        <button class="prop-btn mini so-escrita" onclick="rb.dialogoConexao({conexaoId:${c.id}})" title="Editar"><i class="fas fa-pen"></i></button>
        <button class="prop-btn mini danger so-escrita" onclick="rb.excluirConexao(${c.id})" title="Excluir"><i class="fas fa-trash"></i></button></div></div>`;
  }

  _realcarConexao(id, on) {
    const c = this.estado.conexoes.find(x => x.id === id);
    if (!c) return;
    [c.ponta_a_id, c.ponta_b_id].forEach(eid => {
      const el = document.querySelector(`.eq[data-id="${eid}"]`);
      if (!el) return;
      el.classList.toggle('realce', on);
      el.style.setProperty('--rc', this._corCabo(c));
    });
  }

  // ── Navegação ────────────────────────────────────────────────────────────

  selecionarRack(id) {
    this.rackId = id;
    try { localStorage.setItem(`rack_atual_${this.clienteId}`, id); } catch (e) {}
    this.render();
  }

  trocarFace(face) {
    this.face = face;
    document.getElementById('face-frente').classList.toggle('on', face === 'frente');
    document.getElementById('face-traseira').classList.toggle('on', face === 'traseira');
    this.render();
  }

  abaPaleta(a) { this.aPaleta = a; this.busca = ''; this._renderPaleta(); }
  abaLateral(a) { this.aLateral = a; this._renderLateral(); }
  filtrar(f) { this.filtroLinks = f; this._renderLateral(); }

  selecionar(id) {
    const e = this._eq(id);
    if (!e) return;
    this.sel = id;
    if (e.rack_id !== this.rackId) this.rackId = e.rack_id;
    if (e.face !== this.face && !e.profundidade_total) this.trocarFace(e.face);
    this.aLateral = 'equipamento';
    this.render();
    document.querySelector(`.eq[data-id="${id}"]`)?.scrollIntoView({block: 'nearest'});
  }

  // ── Arrastar e soltar ────────────────────────────────────────────────────

  _pegar(ev, el) {
    if (ev.button !== 0) return;
    let fonte;
    if (el.dataset.tipo) {
      const t = this._tipo(el.dataset.tipo);
      fonte = {tipo: 'catalogo', dados: {tipo: el.dataset.tipo}, altura: t.altura, total: t.profundidade_total, face: this.face, rotulo: t.label};
    } else if (el.dataset.disp) {
      const d = this.estado.dispositivos.find(x => x.chave === el.dataset.disp);
      if (!d) return;
      if (d.montado) { fonte = {tipo: 'ver', id: d.montado.equipamento_id}; }
      else {
        const t = this._tipo(d.tipo);
        fonte = {tipo: 'dispositivo', altura: t.altura, total: t.profundidade_total, face: this.face, rotulo: d.label,
                 dados: {tipo: d.tipo, nome: d.label, acesso_id: d.acesso_id, topologia_node_id: d.node_id}};
      }
    } else {
      const e = this._eq(+el.dataset.id);
      if (!e) return;
      // Linha (a partir do topo do equipamento) em que a pessoa pegou: mantém
      // o equipamento "preso" ao cursor no mesmo ponto durante o arraste.
      const r = el.getBoundingClientRect();
      fonte = {tipo: 'mover', id: e.id, altura: e.altura_u, total: e.profundidade_total, face: e.face, rotulo: e.nome,
               pega: Math.min(e.altura_u - 1, Math.floor((ev.clientY - r.top) / this.U)), el};
    }
    if (this.leitura && fonte.tipo !== 'ver' && fonte.tipo !== 'mover') return;
    this.drag = {...fonte, x0: ev.clientX, y0: ev.clientY, ativo: false, u: null, ok: false};
  }

  _mover(ev) {
    const d = this.drag;
    if (!d || d.tipo === 'ver' || this.leitura) return;
    if (!d.ativo) {
      if (Math.hypot(ev.clientX - d.x0, ev.clientY - d.y0) < RB_ARRASTE_MIN) return;
      if (!this._rack()) return;
      d.ativo = true;
      if (d.el) d.el.classList.add('arrastando');
      const f = document.getElementById('fantasma');
      f.textContent = `${d.rotulo} · ${d.altura}U`;
      f.style.display = 'block';
    }
    const f = document.getElementById('fantasma');
    f.style.left = ev.clientX + 14 + 'px';
    f.style.top = ev.clientY + 10 + 'px';

    const baias = document.getElementById('baias');
    const previa = document.getElementById('previa');
    const rack = this._rack();
    const r = baias.getBoundingClientRect();
    const dentro = ev.clientX > r.left - 60 && ev.clientX < r.right + 60 && ev.clientY > r.top - this.U && ev.clientY < r.bottom + this.U;
    if (!dentro) { previa.style.display = 'none'; d.u = null; return; }
    const n = rack.altura_u;
    const uCursor = n - Math.floor((ev.clientY - r.top) / this.U);
    let u = uCursor + (d.pega || 0) - d.altura + 1;
    u = Math.max(1, Math.min(n - d.altura + 1, u));
    d.u = u;
    const motivo = this._motivoConflito(rack, u, d.altura, d.face, d.total, d.tipo === 'mover' ? d.id : null);
    d.ok = !motivo;
    previa.className = d.ok ? '' : 'erro';
    previa.style.display = 'flex';
    previa.style.top = (n - (u + d.altura - 1)) * this.U + 'px';
    previa.style.height = d.altura * this.U - 1 + 'px';
    previa.textContent = d.ok ? `U${u}${d.altura > 1 ? '–U' + (u + d.altura - 1) : ''}` : motivo;
  }

  async _soltar(ev) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    document.getElementById('fantasma').style.display = 'none';
    const previa = document.getElementById('previa');
    if (previa) previa.style.display = 'none';
    if (d.el) d.el.classList.remove('arrastando');

    if (!d.ativo) { // clique
      if (d.tipo === 'ver' || d.tipo === 'mover') { this.selecionar(d.id); return; }
      if (!this._rack()) { this._toast('Crie um rack primeiro.', true); return; }
      return this._montar(d.dados, null, d.face); // primeiro espaço livre de cima
    }
    if (d.u == null) return;
    if (!d.ok) { this._toast(previa ? previa.textContent : 'Sem espaço', true); return; }
    if (d.tipo === 'mover') {
      const e = this._eq(d.id);
      if (e && e.u_inicial === d.u) return;
      await this._post(`/racks/equipamento/${d.id}/editar/`, {u_inicial: d.u});
    } else {
      await this._montar(d.dados, d.u, d.face);
    }
  }

  async _montar(dados, u, face) {
    const rack = this._rack();
    const r = await this._post(`/racks/rack/${rack.id}/equipamentos/criar/`, {...dados, u_inicial: u, face});
    if (r) { this.sel = r.equipamento_id; this.aLateral = 'equipamento'; this.render(); this._toast('Montado'); }
    return r;
  }

  /** Espelho de `services.conflitos` — só para a prévia. */
  _motivoConflito(rack, u, altura, face, total, ignorar) {
    const fim = u + altura - 1;
    if (fim > rack.altura_u) return 'Passa do topo';
    const bate = rack.equipamentos.find(e => e.id !== ignorar &&
      !(e.u_inicial > fim || e.u_inicial + e.altura_u - 1 < u) &&
      (total || e.profundidade_total || e.face === face));
    return bate ? `Ocupado: ${bate.nome}` : '';
  }

  _tecla(e) {
    if (e.key === 'Escape') { if (document.getElementById('dlg').classList.contains('on')) this.fecharDialogo(); else { this.sel = null; this.render(); } return; }
    const campo = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
    if ((e.key === 'Delete' || e.key === 'Backspace') && this.sel && !campo && !this.leitura) {
      e.preventDefault(); this.excluirEquipamento(this.sel);
    }
  }

  // ── Equipamento ──────────────────────────────────────────────────────────

  async salvarEquipamento(id) {
    const v = x => document.getElementById(x).value;
    const r = await this._post(`/racks/equipamento/${id}/editar/`, {
      nome: v('eq-nome'), tipo: v('eq-tipo'), rack_id: +v('eq-rack'), u_inicial: +v('eq-u'), altura_u: +v('eq-alt'),
      face: v('eq-face'), num_portas: +v('eq-portas'), profundidade_total: document.getElementById('eq-total').checked,
      acesso_id: v('eq-acesso') ? +v('eq-acesso') : null, fabricante: v('eq-fab'), modelo: v('eq-mod'), observacoes: v('eq-obs'),
    });
    if (r) { this.selecionar(id); this._toast('Salvo'); }
  }

  async excluirEquipamento(id) {
    const e = this._eq(id);
    if (!e) return;
    const n = this._cabosDe(id).length;
    if (!confirm(`Remover ${e.nome} do rack?` + (n ? `\n\n${n} cabo(s) ligado(s) a ele também serão excluídos.` : ''))) return;
    if (await this._post(`/racks/equipamento/${id}/excluir/`)) { this.sel = null; this.render(); this._toast('Removido'); }
  }

  async montarPendentes(linkId) {
    const link = this.estado.links.find(l => l.link_id === linkId);
    if (!link) return;
    for (const p of [link.a, link.b]) {
      if (p.equipamento_id) continue;
      const tipo = this.cat.tipo_da_topologia[p.tipo_topologia] || 'outro';
      const r = await this._montar({tipo, nome: p.label, acesso_id: p.acesso_id, topologia_node_id: p.node_id}, null, this.face);
      if (!r) return;
    }
    this.focoLink = linkId;
    this.abaLateral('conexoes');
  }

  // ── Rack ─────────────────────────────────────────────────────────────────

  dialogoRack(id) {
    const r = id ? this.estado.racks.find(x => x.id === id) : null;
    const alturas = [12, 16, 24, 32, 36, 42, 44, 47];
    const atual = r ? r.altura_u : this.cat.altura_rack_padrao;
    this._abrirDialogo(`
      <h2><i class="fas fa-server"></i> ${r ? 'Editar rack' : 'Criar rack'}</h2>
      <div class="sub">A altura é em U (1U = 44,45 mm). U1 é o de baixo.</div>
      <div class="prop-group"><label class="prop-label">Nome</label><input class="prop-input" id="rk-nome" value="${this._esc(r ? r.nome : `RACK-${String(this.estado.racks.length + 1).padStart(2, '0')}`)}"></div>
      <div class="prop-group"><label class="prop-label">Local (site, POP, sala)</label><input class="prop-input" id="rk-local" value="${this._esc(r ? r.local : '')}" placeholder="POP Centro — sala 2"></div>
      <div class="prop-group"><label class="prop-label">Altura</label>
        <div class="filtros" id="rk-alturas">${alturas.map(a => `<button type="button" class="${a === atual ? 'on' : ''}" onclick="document.getElementById('rk-alt').value=${a};this.parentNode.querySelectorAll('button').forEach(b=>b.classList.toggle('on',b===this))">${a}U</button>`).join('')}</div>
        <input class="prop-input" id="rk-alt" type="number" min="1" max="${this.cat.altura_rack_max}" value="${atual}"></div>
      <div class="prop-group"><label class="prop-label">Observações</label><textarea class="prop-input" id="rk-obs" rows="2">${this._esc(r ? r.observacoes : '')}</textarea></div>
      <div class="acoes"><button class="prop-btn" onclick="rb.fecharDialogo()">Cancelar</button>
        <button class="prop-btn primary" onclick="rb.salvarRack(${r ? r.id : 'null'})"><i class="fas fa-check"></i> ${r ? 'Salvar' : 'Criar'}</button></div>`);
    document.getElementById('rk-nome').select();
  }

  async salvarRack(id) {
    const dados = {nome: document.getElementById('rk-nome').value, local: document.getElementById('rk-local').value,
                   altura_u: +document.getElementById('rk-alt').value, observacoes: document.getElementById('rk-obs').value};
    const r = await this._post(id ? `/racks/rack/${id}/editar/` : `/racks/cliente/${this.clienteId}/racks/criar/`, dados);
    if (!r) return;
    this.fecharDialogo();
    if (r.rack_id) this.selecionarRack(r.rack_id);
    this._toast(id ? 'Rack salvo' : 'Rack criado — arraste os equipamentos para ele');
  }

  async excluirRack(id) {
    const r = this.estado.racks.find(x => x.id === id);
    if (!r) return;
    const n = r.equipamentos.length;
    if (!confirm(`Excluir o rack ${r.nome}?` + (n ? `\n\nOs ${n} equipamento(s) montados nele e os cabos deles também serão excluídos.` : ''))) return;
    if (await this._post(`/racks/rack/${id}/excluir/`)) this._toast('Rack excluído');
  }

  // ── Conexão física ───────────────────────────────────────────────────────

  /** opts: {linkId} cria a partir do enlace; {conexaoId} edita; {pontaA} ou
   *  {} cria à mão. */
  dialogoConexao(opts) {
    const link = opts.link || (opts.linkId && this.estado.links.find(l => l.link_id === opts.linkId));
    const c = opts.conexaoId && this.estado.conexoes.find(x => x.id === opts.conexaoId);
    const todos = this.estado.racks.flatMap(r => r.equipamentos.map(e => ({...e, rack: r.nome})))
      .filter(e => e.tipo !== 'tampa' && e.tipo !== 'organizador');
    if (!link && todos.length < 1) { this._toast('Monte equipamentos no rack antes de ligar cabos.', true); return; }
    const opcoes = sel => todos.map(e => `<option value="${e.id}" ${e.id === sel ? 'selected' : ''}>${this._esc(e.nome)} — ${this._esc(e.rack)} U${e.u_inicial}</option>`).join('');
    const base = link ? {a: link.a.equipamento_id, pa: link.a.porta, b: link.b.equipamento_id, pb: link.b.porta,
                         meio: link.meio_sugerido, conector: link.conector_sugerido, ident: link.label, cor: '', comp: '', obs: ''}
      : c ? {a: c.ponta_a_id, pa: c.porta_a, b: c.ponta_b_id, pb: c.porta_b, meio: c.meio, conector: c.conector,
             ident: c.identificacao, cor: c.cor, comp: c.comprimento_m, obs: c.observacoes}
      : {a: opts.pontaA || null, pa: '', b: null, pb: '', meio: 'utp', conector: 'RJ45', ident: '', cor: '', comp: '', obs: ''};
    const meios = Object.entries(this.cat.meios).map(([k, v]) => `<option value="${k}" ${k === base.meio ? 'selected' : ''}>${this._esc(v.label)}</option>`).join('');
    const conect = this.cat.conectores.map(k => `<option ${k === base.conector ? 'selected' : ''}>${k}</option>`).join('');
    const lado = (l, eqId, porta) => {
      const fixo = !!link;
      const eq = eqId && this._eq(eqId);
      return `<div class="lado"><div class="lado-tit">PONTA ${l.toUpperCase()}</div>
        ${fixo ? `<div style="font-size:.8rem;font-weight:600;margin-bottom:8px">${this._esc(eq ? eq.nome : '?')} <span style="color:var(--faint);font-weight:400">— ${this._esc(eq ? this._rackDe(eq).nome + ' U' + eq.u_inicial : '')}</span></div><input type="hidden" id="cx-${l}" value="${eqId}">`
               : `<div class="prop-group"><select class="prop-select" id="cx-${l}"><option value="">— equipamento —</option>${opcoes(eqId)}</select></div>`}
        <input class="prop-input" id="cx-p${l}" list="dl-p${l}" autocomplete="off" placeholder="Porta (ex.: 1, ge0/0/1, sfp-sfpplus1)" value="${this._esc(porta || '')}">
        <datalist id="dl-p${l}"></datalist></div>`;
    };
    const cabCor = base.cor || (this.cat.meios[base.meio] || {}).cor || '#58a6ff';
    this._abrirDialogo(`
      <h2><i class="fas fa-plug"></i> ${c ? 'Editar cabo' : link ? 'Conexão física do enlace' : 'Novo cabo'}</h2>
      <div class="sub">${link ? `Enlace da topologia <b>${this._esc(link.a.label)} ↔ ${this._esc(link.b.label)}</b>${link.iface ? ' · ' + this._esc(((window.TOPO_IFACES || {})[link.iface] || {}).label || link.iface) : ''}. As portas vieram de Interface Lado A/B e o tipo de cabo da velocidade — ajuste se o físico for diferente.` : 'Uma porta aceita um cabo só. Portas numéricas (1, 2, 3…) acendem no espelho do equipamento.'}</div>
      ${lado('a', base.a, base.pa)}${lado('b', base.b, base.pb)}
      <div class="prop-row">
        <div class="prop-group"><label class="prop-label">Cabo</label><select class="prop-select" id="cx-meio">${meios}</select></div>
        <div class="prop-group"><label class="prop-label">Conector</label><select class="prop-select" id="cx-con"><option value=""></option>${conect}</select></div>
      </div>
      <div class="prop-row">
        <div class="prop-group"><label class="prop-label">Etiqueta</label><input class="prop-input" id="cx-id" value="${this._esc(base.ident || '')}" placeholder="CB-0001"></div>
        <div class="prop-group" style="flex:.55"><label class="prop-label">Metros</label><input class="prop-input" id="cx-comp" value="${this._esc(base.comp || '')}" inputmode="decimal"></div>
        <div class="prop-group" style="flex:.35"><label class="prop-label">Cor</label><input class="prop-input" id="cx-cor" type="color" value="${this._esc(cabCor)}" style="padding:2px;height:34px"></div>
      </div>
      <div class="prop-group"><label class="prop-label">Observações</label><textarea class="prop-input" id="cx-obs" rows="2">${this._esc(base.obs || '')}</textarea></div>
      <div class="acoes"><button class="prop-btn" onclick="rb.fecharDialogo()">Cancelar</button>
        <button class="prop-btn primary" id="cx-ok"><i class="fas fa-check"></i> ${c ? 'Salvar' : 'Criar cabo'}</button></div>`);

    const corOriginal = base.cor;
    document.getElementById('cx-cor').dataset.mexeu = corOriginal ? '1' : '';
    document.getElementById('cx-cor').addEventListener('input', ev => { ev.target.dataset.mexeu = '1'; });
    document.getElementById('cx-meio').addEventListener('change', ev => {
      const cor = document.getElementById('cx-cor');
      if (!cor.dataset.mexeu) cor.value = (this.cat.meios[ev.target.value] || {}).cor || cor.value;
    });
    ['a', 'b'].forEach(l => {
      const sel = document.getElementById(`cx-${l}`);
      const sugerir = () => this._sugerirPortas(`dl-p${l}`, +sel.value);
      if (sel.tagName === 'SELECT') sel.addEventListener('change', sugerir);
      sugerir();
    });
    document.getElementById('cx-ok').onclick = () => this._salvarConexao({link, c});
  }

  async _salvarConexao({link, c}) {
    const v = x => document.getElementById(x).value;
    const corEl = document.getElementById('cx-cor');
    const dados = {porta_a: v('cx-pa'), porta_b: v('cx-pb'), meio: v('cx-meio'), conector: v('cx-con'),
                   identificacao: v('cx-id'), comprimento_m: v('cx-comp'), observacoes: v('cx-obs'),
                   // Cor só é gravada quando a pessoa escolheu: sem ela o cabo
                   // segue a cor do tipo (e muda junto se o tipo mudar).
                   cor: corEl.dataset.mexeu ? corEl.value : ''};
    let r;
    if (link) r = await this._post(`/racks/cliente/${this.clienteId}/conexoes/do-link/`, {...dados, link_id: link.link_id});
    else {
      Object.assign(dados, {ponta_a_id: +v('cx-a') || null, ponta_b_id: +v('cx-b') || null});
      r = await this._post(c ? `/racks/conexao/${c.id}/editar/` : `/racks/cliente/${this.clienteId}/conexoes/criar/`, dados);
    }
    if (!r) return;
    this.fecharDialogo();
    this._toast(c ? 'Cabo salvo' : 'Cabo criado');
  }

  /** Interfaces do backup do host (mesmo endpoint do painel do link da
   *  topologia) como sugestão de porta — o campo continua livre. */
  async _sugerirPortas(dlId, eqId) {
    const dl = document.getElementById(dlId);
    const e = eqId && this._eq(eqId);
    if (!dl) return;
    dl.innerHTML = '';
    let itens = [];
    if (e && e.acesso_id) {
      if (!(e.acesso_id in this._ifaceCache)) {
        try {
          const r = await fetch(`/clientes/acessos/${e.acesso_id}/interfaces-backup/`);
          this._ifaceCache[e.acesso_id] = r.ok ? ((await r.json()).interfaces || []).filter(i => !i.logica && !i.subinterface) : [];
        } catch (err) { this._ifaceCache[e.acesso_id] = []; }
      }
      itens = this._ifaceCache[e.acesso_id].map(i => ({v: i.nome, d: i.descricao || ''}));
    } else if (e && e.num_portas) {
      itens = Array.from({length: e.num_portas}, (_, i) => ({v: String(i + 1), d: ''}));
    }
    if (document.getElementById(dlId) !== dl) return; // diálogo trocou enquanto buscava
    dl.innerHTML = itens.map(i => `<option value="${this._esc(i.v)}"${i.d ? ` label="${this._esc(i.d)}"` : ''}>${this._esc(i.d)}</option>`).join('');
  }

  async excluirConexao(id) {
    if (!confirm('Excluir este cabo?')) return;
    if (await this._post(`/racks/conexao/${id}/excluir/`)) this._toast('Cabo excluído');
  }

  // ── Utilitários ──────────────────────────────────────────────────────────

  _abrirDialogo(html) {
    document.getElementById('dlg-caixa').innerHTML = html;
    document.getElementById('dlg').classList.add('on');
  }
  fecharDialogo() { document.getElementById('dlg').classList.remove('on'); }

  _toast(msg, erro = false) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'on' + (erro ? ' erro' : '');
    clearTimeout(this._toastT);
    this._toastT = setTimeout(() => { t.className = ''; }, erro ? 5200 : 2600);
  }

  _esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }
  _escJs(s) { return this._esc(String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'")); }
  _norm(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
}


document.addEventListener('DOMContentLoaded', () => { window.rb = new RackBuilder(window.RACK_CFG); });
