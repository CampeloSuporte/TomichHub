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
    
    abrirTerminal(acessoId, host, porta, usuario, senha, protocolo, tipo) {
        console.log(`🔌 Abrindo terminal para: ${tipo} - ${host}:${porta}`);
        
        // Dados do acesso
        const acessoData = {
            id: acessoId,
            host: host,
            porta: porta,
            usuario: usuario,
            senha: senha,
            protocolo: protocolo,
            tipo: tipo
        };
        
        // Se a janela está aberta, enviar dados
        if (this.terminalWindow && !this.terminalWindow.closed) {
            console.log('✅ Enviando dados para janela existente');
            this.enviarParaTerminal(acessoData);
        } else {
            // Abrir nova janela
            console.log('📂 Abrindo nova janela do terminal');
            this.abrirNovaJanela(acessoData);
        }
    }
    
    abrirNovaJanela(acessoData) {
        // ✅ MUDANÇA 1: Usar localStorage em vez de sessionStorage
        // localStorage é compartilhado globalmente no browser
        localStorage.setItem('acessoPendente', JSON.stringify(acessoData));
        
        // Abrir janela com a página de terminal
        const url = '/clientes/terminal/';  // URL da página de terminal
        const opcoes = 'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no';
        
        this.terminalWindow = window.open(url, this.terminalWindowName, opcoes);
        
        if (!this.terminalWindow) {
            console.error('❌ Não foi possível abrir a janela do terminal');
            alert('⚠️ Não foi possível abrir a janela do terminal. Verifique se bloqueadores de popup estão desabilitados.');
            return;
        }
        
        console.log('✅ Janela do terminal aberta com sucesso');
        this.terminalWindow.focus();
    }
    
    enviarParaTerminal(acessoData) {
        try {
            // ✅ Enviar via postMessage (mais seguro)
            if (this.terminalWindow && !this.terminalWindow.closed) {
                this.terminalWindow.postMessage({
                    type: 'NOVA_CONEXAO',
                    acesso: acessoData
                }, window.location.origin);
                
                console.log('✅ Dados enviados via postMessage');
            }
            
            // ✅ Também armazenar em localStorage como backup
            localStorage.setItem('ultimoAcesso', JSON.stringify(acessoData));
            
            this.terminalWindow.focus();
            console.log('✅ Dados enviados para o terminal');
        } catch (e) {
            console.error('❌ Erro ao enviar dados:', e);
            // Se falhar, abrir nova janela
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

function acessarEquipamento(protocolo, host, porta, usuario, senha, acessoId, tipo) {
    
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
    
    // ✅ PARA SSH, TELNET, WINBOX, ETC - ABRIR TERMINAL
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
        tipo
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