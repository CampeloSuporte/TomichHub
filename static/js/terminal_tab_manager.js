// ============================================
// GERENCIADOR DE TERMINAL EM ABA SEPARADA
// ============================================
// ✅ CORRIGIDO: Usar localStorage em vez de sessionStorage

class TerminalTabManager {
    constructor() {
        this.terminalWindow = null;
        this.terminalWindowName = 'CONEXA_SSH_TERMINAL_' + Date.now();
        this.checkInterval = null;
        this.initListeners();
    }
    
    initListeners() {
        // Verificar se a janela do terminal está aberta periodicamente
        this.checkInterval = setInterval(() => {
            if (this.terminalWindow && this.terminalWindow.closed) {
                console.log('🔌 Janela do terminal foi fechada');
                this.terminalWindow = null;
            }
        }, 1000);
    }
    
    abrirTerminal(acessoId, host, porta, usuario, senha, protocolo, tipo, clienteId, protocoloId) {
        console.log(`🔌 Abrindo terminal para: ${tipo} - ${host}:${porta}`);

        const acessoData = {
            id: acessoId,
            host: host,
            porta: porta,
            usuario: usuario,
            senha: senha,
            protocolo: protocolo,
            tipo: tipo,
            cliente_id: clienteId || null,
            // AcessoProtocolo (protocolo extra do host); null = acesso padrão
            protocolo_id: protocoloId || null,
        };

        if (this.terminalWindow && !this.terminalWindow.closed) {
            this.enviarParaTerminal(acessoData);
        } else {
            this.abrirNovaJanela(acessoData);
        }
    }

    abrirNovaJanela(acessoData) {
        localStorage.setItem('acessoPendente', JSON.stringify(acessoData));
        const clienteParam = acessoData.cliente_id ? `?cliente=${acessoData.cliente_id}` : '';
        const url = '/clientes/terminal/' + clienteParam;
        const opcoes = 'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no';
        this.terminalWindow = window.open(url, this.terminalWindowName, opcoes);
        if (!this.terminalWindow) {
            alert('⚠️ Não foi possível abrir a janela do terminal. Verifique se bloqueadores de popup estão desabilitados.');
            return;
        }
        this.terminalWindow.focus();
    }

    enviarParaTerminal(acessoData) {
        try {
            if (this.terminalWindow && !this.terminalWindow.closed) {
                this.terminalWindow.postMessage({ type: 'NOVA_CONEXAO', acesso: acessoData }, window.location.origin);
                this.terminalWindow.focus();
            }
        } catch (e) {
            console.error('❌ Erro ao enviar dados:', e);
            this.abrirNovaJanela(acessoData);
        }
    }
    
    destroy() {
        if (this.checkInterval) {
            clearInterval(this.checkInterval);
        }
        if (this.terminalWindow && !this.terminalWindow.closed) {
            this.terminalWindow.close();
        }
    }
}

// Instância global
const terminalTabManager = new TerminalTabManager();

// ============================================
// FUNÇÃO CORRIGIDA - acessarEquipamento
// ============================================
// ✅ CORREÇÃO:
// 1. Limpar HOST para remover porta e caminhos
// 2. Converter PORTA para número
// 3. Comparar portas como NÚMERO, não STRING

function acessarEquipamento(protocolo, host, porta, usuario, senha, acessoId, tipo, clienteId) {
    
    // ✅ Converter porta para NÚMERO
    const portaNum = parseInt(String(porta).trim(), 10);
    console.log('📊 Porta Numérica:', portaNum);
    
    // ✅ VERIFICAÇÃO DE PROTOCOLO WEB
    const proto = String(protocolo).toUpperCase().trim();
    
    if (proto === 'HTTPS' || proto === 'HTTP') {
        console.log('🌐 Protocolo WEB detectado:', proto);

        // IP privado: proxy do CRM primeiro, conexão direta se ele falhar
        if (acessoId && _hostEhPrivado(host)) {
            abrirWebProxyComFallback(acessoId, proto.toLowerCase(), portaNum, host);
            return;
        }
        
        // ✅ Construir URL mantendo o host e caminho intacto
        let url = `${proto.toLowerCase()}://${host}`;
        
        // ✅ NÃO adicionar porta se for a padrão
        if (!isNaN(portaNum)) {
            if (proto === 'HTTP' && portaNum === 80) {
                console.log('   ✅ Ignorando porta padrão HTTP (80)');
                // Não adiciona nada
            } else if (proto === 'HTTPS' && portaNum === 443) {
                console.log('   ✅ Ignorando porta padrão HTTPS (443)');
                // Não adiciona nada
            } else {
                console.log('   ✅ Adicionando porta ' + portaNum + ' (não é padrão)');
                url += `:${portaNum}`;
            }
        }
        
        // Abrir em nova aba
        window.open(url, '_blank');
        
        // Mostrar notificação
        if (typeof showSuccess === 'function') {
            showSuccess('NAVEGADOR ABERTO', `Acessando ${url}`, 3000);
        } else {
            alert('Abrindo: ' + url);
        }
        
        return;  // ⚠️ IMPORTANTE: Parar aqui e NÃO abrir o terminal
    }
    
    // ✅ PARA WINBOX - ABRIR TERMINAL WINBOX WEB
    if (proto === 'WINBOX') {
        console.log('🖥️ Protocolo WINBOX detectado');
        const winboxUrl = `/clientes/winbox/${acessoId}/`;
        const opcoes = 'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no';
        window.open(winboxUrl, `WINBOX_${acessoId}`, opcoes);
        
        if (typeof showSuccess === 'function') {
            showSuccess('WINBOX ABERTO', `Abrindo terminal Winbox`, 3000);
        }
        return;
    }
    
    // ✅ PARA RDP - ABRIR ACESSO REMOTO VIA WEB (Xvfb + xfreerdp + x11vnc + noVNC)
    // Sem isso o RDP caía no fluxo de terminal abaixo e o CRM tentava abrir SSH.
    if (proto === 'RDP') {
        console.log('🖥️ Protocolo RDP detectado');
        const rdpUrl = `/clientes/rdp/${acessoId}/`;
        const opcoes = 'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no';
        window.open(rdpUrl, `RDP_${acessoId}`, opcoes);

        if (typeof showSuccess === 'function') {
            showSuccess('RDP ABERTO', `Abrindo área de trabalho remota de ${host}`, 3000);
        }
        return;
    }

    // ✅ PARA SSH, TELNET, ETC - ABRIR TERMINAL
    console.log('🖥️ Protocolo de terminal detectado:', proto);
    
    // Limpar host apenas para SSH/Telnet (remover caminho e porta extra)
    let hostLimpo = String(host).trim();
    if (hostLimpo.includes('/')) {
        hostLimpo = hostLimpo.split('/')[0];
    }
    if (hostLimpo.includes(':') && proto !== 'SSH' && proto !== 'TELNET') {
        hostLimpo = hostLimpo.split(':')[0];
    }
    
    // Abrir terminal em nova aba
    terminalTabManager.abrirTerminal(
        acessoId,
        hostLimpo,
        portaNum,
        usuario,
        senha,
        protocolo,
        tipo,
        clienteId || null
    );
}

// ============================================
// ACESSO WEB (HTTP/HTTPS) — IP PRIVADO: PROXY PRIMEIRO
// ============================================

// Mesmo critério de ipaddress.is_private do backend (views.is_private_ip):
// host com porta ou nome de DNS não é IP → não privado
function _hostEhPrivado(host) {
    const h = String(host || '').trim().replace(/^[a-z]+:\/\//i, '').split('/')[0];
    const v4 = h.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (v4) {
        const [a, b, c] = v4.slice(1).map(Number);
        return a === 0 || a === 10 || a === 127 || a >= 240
            || (a === 172 && b >= 16 && b <= 31)
            || (a === 192 && b === 168)
            || (a === 169 && b === 254)
            || (a === 198 && (b === 18 || b === 19))
            || (a === 192 && b === 0 && (c === 0 || c === 2))
            || (a === 198 && b === 51 && c === 100)
            || (a === 203 && b === 0 && c === 113);
    }
    const v6 = h.replace(/^\[|\]$/g, '').toLowerCase();
    return v6.includes(':') && (v6 === '::1' || /^f[cd]/.test(v6) || /^fe[89ab]/.test(v6));
}

// Host pode ter caminho fixo ("1.2.3.4/zabbix"): a porta entra antes dele
function _urlWebDireta(scheme, host, porta) {
    let h = String(host).trim().replace(/^https?:\/\//i, '');
    const barra = h.indexOf('/');
    const caminho = barra >= 0 ? h.slice(barra) : '';
    if (barra >= 0) h = h.slice(0, barra);
    const portaPadrao = (scheme === 'http' && porta === 80) || (scheme === 'https' && porta === 443);
    return `${scheme}://${h}${portaPadrao || !porta ? '' : ':' + porta}${caminho}`;
}

const _WEB_PROXY_TIMEOUT_MS = 15000;

// IP privado: tenta o proxy web do CRM (túnel SSH ou OpenVPN do cliente). Se
// ele falhar (página de erro do proxy com X-CRM-Proxy-Falha, erro de rede ou
// demora além do limite), abre a conexão direta no navegador, que funciona
// quando o PC do operador alcança a rede do cliente. Resposta do equipamento,
// com qualquer status, conta como proxy funcionando.
async function abrirWebProxyComFallback(acessoId, scheme, porta, host) {
    porta = porta || (scheme === 'https' ? 443 : 80);
    const urlProxy = `/clientes/acessos/${acessoId}/web/${porta}/${scheme}/`;
    const urlDireta = _urlWebDireta(scheme, host, porta);

    // Aba aberta já no clique: depois do await o navegador bloquearia o popup
    const janela = window.open('', '_blank');
    if (!janela) {
        alert('⚠️ Não foi possível abrir a nova aba. Verifique se bloqueadores de popup estão desabilitados.');
        return;
    }
    _avisoJanelaWeb(janela, `Conectando via proxy do CRM a ${host}:${porta}…`);

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), _WEB_PROXY_TIMEOUT_MS);
    let proxyOk = false;
    try {
        const r = await fetch(urlProxy, {credentials: 'same-origin', cache: 'no-store', signal: ctrl.signal});
        proxyOk = !r.headers.get('X-CRM-Proxy-Falha');
    } catch (e) {
        proxyOk = false;
    } finally {
        clearTimeout(timer);
        ctrl.abort();   // só interessa o status, não o corpo
    }

    if (janela.closed) return;
    if (proxyOk) {
        janela.location.href = urlProxy;
        return;
    }
    console.warn(`Proxy web falhou para ${host}:${porta}; tentando conexão direta ${urlDireta}`);
    _avisoJanelaWeb(janela, `Proxy indisponível. Tentando conexão direta a ${urlDireta}…`);
    janela.location.href = urlDireta;
}

function _avisoJanelaWeb(janela, texto) {
    try {
        janela.document.title = 'Conectando…';
        janela.document.body.style.cssText = 'margin:0;display:flex;align-items:center;justify-content:center;'
            + 'height:100vh;background:#0d1117;color:#8b949e;font:14px system-ui,sans-serif';
        janela.document.body.textContent = texto;
    } catch (e) { /* a aba já saiu do about:blank */ }
}

// ============================================
// PROTOCOLO EXTRA DO HOST (AcessoProtocolo)
// ============================================
// Mesmo IP e credenciais do acesso, outro protocolo/porta. Segue a regra do
// acesso padrão: IP privado passa pelo CRM (proxy SSH ou OpenVPN do
// cliente), IP público vai direto.

function acessarProtocoloExtra(protocolo, host, porta, usuario, senha, acessoId, tipo, clienteId, protocoloId, hostPrivado) {
    const proto = String(protocolo).toUpperCase().trim();
    const portaNum = parseInt(String(porta).trim(), 10);
    const opcoes = 'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no';

    if (proto === 'HTTP' || proto === 'HTTPS') {
        const scheme = proto.toLowerCase();
        if (hostPrivado) {
            abrirWebProxyComFallback(acessoId, scheme, portaNum, host);
            return;
        }
        window.open(_urlWebDireta(scheme, host, portaNum), '_blank');
        return;
    }

    if (proto === 'RDP') {
        window.open(`/clientes/rdp/${acessoId}/?pid=${encodeURIComponent(protocoloId)}`, `RDP_${acessoId}_${protocoloId}`, opcoes);
        return;
    }

    // SSH/Telnet: o consumer lê porta e protocolo do AcessoProtocolo pelo id
    // e decide proxy x direto pelo IP do host, como no acesso padrão.
    const hostLimpo = String(host).trim().split('/')[0];
    terminalTabManager.abrirTerminal(acessoId, hostLimpo, portaNum, usuario, senha, proto, tipo, clienteId || null, protocoloId);
}

// ============================================
// LIMPEZA AO FECHAR A PÁGINA
// ============================================

window.addEventListener('beforeunload', () => {
    // Não fechar a janela do terminal ao sair da página
    // terminalTabManager.destroy();
});

console.log('✅ Terminal Tab Manager inicializado');
console.log('📌 Função acessarEquipamento foi corrigida com:');
console.log('   ✓ Limpeza de HOST (remove porta e caminhos)');
console.log('   ✓ Conversão de PORTA para número');
console.log('   ✓ Comparação numérica de portas padrão');
console.log('🌐 HTTP/HTTPS abrirão no navegador');
console.log('🖥️ SSH/Telnet abrirão no terminal');
console.log('🔄 Usando localStorage para compartilhar dados entre abas');

// ============================================
// FUNÇÃO: Abrir Winbox Web Terminal
// ============================================
// Abre a página de Winbox Web para o acesso pelo ID.
// Usada pelo botão "Winbox Web" na listagem de acessos.

function abrirWinboxWeb(acessoId) {
    console.log('🖥️ Abrindo Winbox Web para acesso ID:', acessoId);
    const winboxUrl = `/clientes/winbox/${acessoId}/`;
    const opcoes = 'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no';
    const janela = window.open(winboxUrl, `WINBOX_WEB_${acessoId}`, opcoes);
    
    if (!janela) {
        alert('⚠️ Não foi possível abrir o Winbox Web. Verifique se bloqueadores de popup estão desabilitados.');
        return;
    }
    
    janela.focus();
    
    if (typeof showSuccess === 'function') {
        showSuccess('WINBOX WEB', 'Abrindo terminal Winbox Web...', 3000);
    }
}