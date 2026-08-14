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
    
    abrirTerminal(acessoId, host, porta, usuario, senha, protocolo, tipo, clienteId) {
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