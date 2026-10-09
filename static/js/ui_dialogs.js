/* ══ Diálogos padrão da plataforma: uiConfirm / uiAlert / uiToast ══════════
 *
 * Substituem o confirm()/alert() nativos do navegador (que fogem do visual do
 * CRM). Carregado por templates/base.html e pelas páginas que não herdam dele
 * (atendimento/base.html, terminal, BGP, scripts...).
 *
 *   if (!await uiConfirm('Excluir?')) return;
 *   if (!await uiConfirm({titulo:'Excluir', msg:'...', btnLabel:'Excluir'})) return;
 *   uiAlert('Erro: ...');                 // título/ícone deduzidos do texto
 *   await uiAlert({titulo:'Pronto', msg:'...', tipo:'ok'});
 *   uiAlert({titulo:'Detalhes', msgHtml:'<table>…</table>'}); // HTML da própria página
 *   uiToast('Salvo!');                    // 'ok' | 'info' | 'erro'
 *
 * Formulário/link sem JS: <form data-confirm="Excluir o cliente?"> ou
 * <a href="..." data-confirm="..."> (opcionais: data-confirm-titulo,
 * data-confirm-btn, data-confirm-tipo="perigo|aviso|info").
 *
 * window.alert também passa a abrir o uiAlert (rede de segurança para o que
 * ainda chamar alert()). window.confirm não dá: ele é síncrono.
 * ═════════════════════════════════════════════════════════════════════════ */
(function () {
    if (window.__uiDialogs) return;
    window.__uiDialogs = true;

    const TIPOS = {
        perigo: { cor: '#e06060', icone: 'fas fa-exclamation-triangle', titulo: 'Confirmar' },
        aviso:  { cor: '#ffaa00', icone: 'fas fa-exclamation-circle',   titulo: 'Atenção' },
        info:   { cor: '#58a6ff', icone: 'fas fa-question-circle',      titulo: 'Confirmar' },
        erro:   { cor: '#e06060', icone: 'fas fa-times-circle',         titulo: 'Erro' },
        ok:     { cor: '#3fb950', icone: 'fas fa-check-circle',         titulo: 'Sucesso' },
    };

    const CSS = `
.ui-dlg-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:1000000;
  backdrop-filter:blur(3px);align-items:center;justify-content:center;padding:16px}
.ui-dlg-overlay.aberto{display:flex}
.ui-dlg-box{background:#1c2128;border:1px solid #30363d;box-shadow:0 8px 24px rgba(0,0,0,.5);
  font-family:'Inter',system-ui,sans-serif;border-radius:10px;width:380px;max-width:100%;
  animation:uiDlgIn .14s ease-out}
@keyframes uiDlgIn{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:none}}
.ui-dlg-corpo{padding:26px 24px 16px;text-align:center}
.ui-dlg-icone{font-size:2rem;margin-bottom:12px}
.ui-dlg-titulo{font-size:.9rem;font-weight:600;color:#e6edf3;margin-bottom:8px;letter-spacing:1px;text-transform:uppercase}
.ui-dlg-msg{font-size:.82rem;color:#9da7b3;line-height:1.6;white-space:pre-line;word-break:break-word;
  max-height:50vh;overflow-y:auto}
.ui-dlg-rodape{border-top:1px solid rgba(88,166,255,.1);padding:12px 20px;display:flex;gap:8px}
.ui-dlg-btn{flex:1;font-family:inherit;font-size:12px;font-weight:600;padding:8px;border-radius:6px;
  cursor:pointer;border:1px solid;background:transparent;transition:filter .12s}
.ui-dlg-btn:hover{filter:brightness(1.25)}
.ui-dlg-btn:focus-visible{outline:2px solid #58a6ff;outline-offset:2px}
.ui-dlg-btn.cancelar{border-color:#30363d;color:#7d8590;font-weight:500}
#ui-toast{position:fixed;bottom:28px;right:28px;z-index:1000001;padding:10px 20px;border-radius:8px;
  border:1px solid;font-family:'Inter',system-ui,sans-serif;font-size:.85rem;font-weight:500;min-width:200px;
  max-width:min(420px,calc(100vw - 32px));text-align:center;opacity:0;transform:translateY(12px);
  transition:opacity .25s,transform .25s;pointer-events:none}
`;

    function hexRgb(hex) {
        const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
        return r ? `${parseInt(r[1],16)},${parseInt(r[2],16)},${parseInt(r[3],16)}` : '224,96,96';
    }

    function pintarBotao(btn, cor) {
        const rgb = hexRgb(cor);
        btn.style.background  = `rgba(${rgb},.15)`;
        btn.style.borderColor = `rgba(${rgb},.5)`;
        btn.style.color       = cor;
    }

    let overlay, elIcone, elTitulo, elMsg, btnOk, btnCancel, toastEl;

    function montar() {
        if (overlay) return;
        const style = document.createElement('style');
        style.textContent = CSS;
        document.head.appendChild(style);

        overlay = document.createElement('div');
        overlay.className = 'ui-dlg-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.innerHTML = `
          <div class="ui-dlg-box">
            <div class="ui-dlg-corpo">
              <div class="ui-dlg-icone"></div>
              <div class="ui-dlg-titulo"></div>
              <div class="ui-dlg-msg"></div>
            </div>
            <div class="ui-dlg-rodape">
              <button type="button" class="ui-dlg-btn cancelar">Cancelar</button>
              <button type="button" class="ui-dlg-btn ok">OK</button>
            </div>
          </div>`;
        document.body.appendChild(overlay);
        elIcone   = overlay.querySelector('.ui-dlg-icone');
        elTitulo  = overlay.querySelector('.ui-dlg-titulo');
        elMsg     = overlay.querySelector('.ui-dlg-msg');
        btnOk     = overlay.querySelector('.ui-dlg-btn.ok');
        btnCancel = overlay.querySelector('.ui-dlg-btn.cancelar');

        toastEl = document.getElementById('ui-toast');
        if (!toastEl) {
            toastEl = document.createElement('div');
            toastEl.id = 'ui-toast';
            document.body.appendChild(toastEl);
        }
    }

    // Um diálogo por vez: chamadas seguidas entram em fila (um alert de erro
    // logo depois de outro não some por cima do primeiro).
    let fila = Promise.resolve();

    function abrir(cfg) {
        const proximo = fila.then(() => new Promise(resolve => {
            montar();
            const t = TIPOS[cfg.tipo] || TIPOS.info;
            const cor = cfg.iconeCor || t.cor;
            elIcone.innerHTML = `<i class="${cfg.icone || t.icone}" style="color:${cor}"></i>`;
            elTitulo.textContent = cfg.titulo || t.titulo;
            // msgHtml: HTML montado pelo próprio código da página (nunca texto do usuário)
            if (cfg.msgHtml != null) { elMsg.innerHTML = cfg.msgHtml; elMsg.style.whiteSpace = 'normal'; elMsg.style.textAlign = 'left'; }
            else { elMsg.textContent = cfg.msg == null ? '' : String(cfg.msg); elMsg.style.whiteSpace = ''; elMsg.style.textAlign = ''; }

            btnOk.textContent = cfg.btnLabel || (cfg.confirmar ? 'Confirmar' : 'OK');
            pintarBotao(btnOk, cfg.btnCor || (cfg.confirmar ? t.cor : '#58a6ff'));
            btnCancel.textContent = cfg.btnCancelar || 'Cancelar';
            btnCancel.style.display = cfg.confirmar ? '' : 'none';

            const focoAnterior = document.activeElement;
            overlay.classList.add('aberto');
            // Confirmação destrutiva começa no Cancelar (Enter não apaga por engano)
            (cfg.confirmar && cfg.tipo === 'perigo' ? btnCancel : btnOk).focus();

            const fechar = (val) => {
                overlay.classList.remove('aberto');
                btnOk.removeEventListener('click', onOk);
                btnCancel.removeEventListener('click', onCancel);
                overlay.removeEventListener('mousedown', onFundo);
                document.removeEventListener('keydown', onTecla, true);
                if (focoAnterior && focoAnterior.focus) { try { focoAnterior.focus(); } catch (e) {} }
                resolve(val);
            };
            const onOk     = () => fechar(true);
            const onCancel = () => fechar(false);
            const onFundo  = (e) => { if (e.target === overlay) fechar(cfg.confirmar ? false : true); };
            const onTecla  = (e) => {
                // Atalhos da página (Delete no mapa, Esc de outro modal...) não
                // disparam por baixo do diálogo; Enter/Espaço seguem no botão focado.
                e.stopPropagation();
                if (e.key === 'Escape') { e.preventDefault(); fechar(cfg.confirmar ? false : true); }
                else if (e.key === 'Tab') {
                    // Foco preso no diálogo
                    const alvos = [btnCancel, btnOk].filter(b => b.style.display !== 'none');
                    const i = alvos.indexOf(document.activeElement);
                    e.preventDefault();
                    alvos[(i + (e.shiftKey ? -1 : 1) + alvos.length) % alvos.length].focus();
                }
            };
            btnOk.addEventListener('click', onOk);
            btnCancel.addEventListener('click', onCancel);
            overlay.addEventListener('mousedown', onFundo);
            document.addEventListener('keydown', onTecla, true);
        }));
        fila = proximo.catch(() => {});
        return proximo;
    }

    function deduzirTipoAlerta(txt) {
        if (/^\s*(✓|✅)|sucesso|\bok!?$/i.test(txt)) return 'ok';
        if (/^\s*(✗|❌)|erro|error|falha|fail|não foi possível|inválid/i.test(txt)) return 'erro';
        return 'aviso';
    }

    function limparPrefixo(txt) {
        return txt.replace(/^\s*(✓|✅|✗|❌|⚠️)\s*/, '');
    }

    // ── uiConfirm ─────────────────────────────────────────────────────────
    // tipo: 'perigo' (padrão, botão vermelho) | 'aviso' | 'info'
    window.uiConfirm = function (opts) {
        if (typeof opts !== 'object' || opts === null) opts = { msg: opts };
        return abrir({ tipo: 'perigo', ...opts, confirmar: true });
    };

    // ── uiAlert ───────────────────────────────────────────────────────────
    // tipo: 'erro' | 'aviso' | 'ok' | 'info' (string solta → deduzido)
    window.uiAlert = function (opts) {
        if (typeof opts !== 'object' || opts === null) {
            const txt = String(opts == null ? '' : opts);
            opts = { tipo: deduzirTipoAlerta(txt), msg: limparPrefixo(txt) };
        }
        return abrir({ tipo: 'aviso', ...opts, confirmar: false });
    };

    // Rede de segurança: alert() esquecido em algum canto abre no padrão.
    window.alert = function (msg) { window.uiAlert(msg); };

    // ── uiToast ───────────────────────────────────────────────────────────
    window.uiToast = function (msg, tipo = 'ok') {
        montar();
        const cor = tipo === 'ok' ? '#3fb950' : tipo === 'info' ? '#00d9ff' : '#e06060';
        const rgb = hexRgb(cor);
        toastEl.textContent       = msg;
        toastEl.style.background  = `rgba(${rgb},.12)`;
        toastEl.style.borderColor = `rgba(${rgb},.4)`;
        toastEl.style.color       = cor;
        toastEl.style.opacity     = '1';
        toastEl.style.transform   = 'translateY(0)';
        clearTimeout(toastEl._t);
        toastEl._t = setTimeout(() => {
            toastEl.style.opacity   = '0';
            toastEl.style.transform = 'translateY(12px)';
        }, 3200);
    };

    // ── data-confirm em <form> e <a>/<button> ─────────────────────────────
    function optsDe(el) {
        return {
            msg: el.dataset.confirm,
            titulo: el.dataset.confirmTitulo || undefined,
            btnLabel: el.dataset.confirmBtn || undefined,
            tipo: el.dataset.confirmTipo || 'perigo',
        };
    }

    document.addEventListener('submit', function (e) {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
        if (form.__uiConfirmado) { form.__uiConfirmado = false; return; }
        e.preventDefault();
        e.stopImmediatePropagation();
        const submitter = e.submitter;
        window.uiConfirm(optsDe(form)).then(ok => {
            if (!ok) return;
            form.__uiConfirmado = true;
            if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
            else form.submit();
        });
    }, true);

    document.addEventListener('click', function (e) {
        const el = e.target.closest && e.target.closest('a[data-confirm], button[data-confirm]:not([type="submit"])');
        if (!el) return;
        if (el.__uiConfirmado) { el.__uiConfirmado = false; return; }
        e.preventDefault();
        e.stopImmediatePropagation();
        window.uiConfirm(optsDe(el)).then(ok => {
            if (!ok) return;
            el.__uiConfirmado = true;
            el.click();
        });
    }, true);
})();
