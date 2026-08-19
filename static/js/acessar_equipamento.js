/**
 * ARQUIVO: acessar_equipamento.js
 * 
 * NO SEU TEMPLATE, ADICIONE ANTES DOS SCRIPTS LOCAIS:
 * 
 * <script src="{% static 'js/acessar_equipamento.js' %}"></script>
 * <script src="{% static 'js/main.js' %}"></script>
 * <script src="{% static 'js/listar_acesso.js' %}"></script>
 * 
 * ORDEM IMPORTANTE:
 * 1. acessar_equipamento.js (PRIMEIRO - funções de equipamento)
 * 2. main.js (SEGUNDO - funções gerais)
 * 3. listar_acesso.js (TERCEIRO - funções de listagem)
 * 
 */

// ============================================
// FUNÇÕES DE ACESSO A EQUIPAMENTOS
// ============================================

/**
 * Acessa um equipamento via protocolo apropriado
 * @param {string} protocolo - SSH, HTTP, HTTPS, FTP, etc
 * @param {string} host - IP ou hostname do equipamento
 * @param {number} porta - Porta de conexão
 * @param {string} usuario - Usuário para autenticação
 * @param {string} senha - Senha para autenticação
 * @param {number} acessoId - ID do acesso no banco de dados
 * @param {string} tipo - Tipo/nome do equipamento
 */
function acessarEquipamento(protocolo, host, porta, usuario, senha, acessoId, tipo) {
    console.log('🔌 Tentando acessar:', {protocolo, host, porta, acessoId, tipo});
    
    // ✅ SE FOR SSH OU TELNET, ABRIR TERMINAL INTEGRADO
    if (protocolo && (protocolo.toLowerCase() === 'ssh' || protocolo.toLowerCase() === 'telnet')) {
        console.log(`🔗 Abrindo terminal ${protocolo.toUpperCase()} integrado...`);
        abrirTerminalSSH(acessoId, host, porta, tipo);
        return;
    }
    
    let urlAcesso = '';
    
    // Processar cada protocolo
    if (!protocolo) {
        const dados = `Host: ${host}\nPorta: ${porta}\nUsuário: ${usuario}\nSenha: ${senha}`;
        showInfo('DADOS DE CONEXÃO', dados, 10000);
        return;
    }

    const protocoloLower = protocolo.toLowerCase();
    
    switch(protocoloLower) {
        case 'http':
            // ✅ Não adiciona porta 80 (padrão)
            if (porta && porta !== 'None' && porta !== '' && porta !== null && porta !== '80') {
                urlAcesso = `http://${host}:${porta}`;
            } else {
                urlAcesso = `http://${host}`;
            }
            break;
            
        case 'https':
            // ✅ Não adiciona porta 443 (padrão)
            if (porta && porta !== 'None' && porta !== '' && porta !== null && porta !== '443') {
                urlAcesso = `https://${host}:${porta}`;
            } else {
                urlAcesso = `https://${host}`;
            }
            break;
            
        case 'ftp':
            urlAcesso = `ftp://${usuario}:${senha}@${host}:${porta}`;
            break;
            
        case 'sftp':
            urlAcesso = `sftp://${usuario}@${host}:${porta}`;
            break;
            
        case 'rdp':
            // rdp:// depende de um handler de protocolo instalado no SO do
            // usuário (não existe por padrão em nenhum navegador) — em vez
            // disso abre o acesso RDP via Web (mesmo padrão do Winbox Web:
            // servidor roda o cliente RDP num Xvfb e transmite via noVNC).
            window.open('/clientes/rdp/' + acessoId + '/', 'rdp_' + acessoId,
                'width=1400,height=800,menubar=no,toolbar=no,location=no,status=no');
            return;

        case 'vnc':
            urlAcesso = `vnc://${host}:${porta}`;
            break;
            
        default:
            const dados = `Protocolo: ${protocolo}\nHost: ${host}\nPorta: ${porta}\nUsuário: ${usuario}`;
            showInfo('DADOS DE CONEXÃO', dados, 10000);
            return;
    }
    
    console.log('✅ URL gerada:', urlAcesso);
    
    // ✅ Abrir em nova aba
    try {
        window.open(urlAcesso, '_blank');
        showSuccess('SUCESSO', `Conectando via ${protocolo.toUpperCase()}...`, 3000);
    } catch (error) {
        console.error('❌ Erro ao abrir URL:', error);
        showError('ERRO', 'Não foi possível iniciar a conexão. Verifique se há um aplicativo adequado instalado.', 5000);
    }
}

/**
 * Abre terminal SSH/Telnet integrado
 * @param {number} acessoId - ID do acesso
 * @param {string} host - Host do equipamento
 * @param {number} porta - Porta SSH
 * @param {string} tipo - Tipo do equipamento
 */
function abrirTerminalSSH(acessoId, host, porta, tipo) {
    console.log('🔥 Abrindo terminal SSH para:', {acessoId, host, porta, tipo});
    
    // Verificar se o modal existe
    const modal = document.getElementById('modalSSHTerminal');
    if (!modal) {
        console.error('❌ Modal SSH Terminal não encontrado!');
        showError('ERRO', 'Modal de terminal SSH não está disponível', 5000);
        return;
    }
    
    // Definir informações do acesso no modal
    const sshHostInfo = document.getElementById('ssh-host-info');
    const sshAcessoId = document.getElementById('ssh-acesso-id');
    const terminalOutput = document.getElementById('terminal-output');
    
    if (sshHostInfo) {
        sshHostInfo.textContent = `${tipo} - ${host}:${porta}`;
    }
    
    if (sshAcessoId) {
        sshAcessoId.value = acessoId;
    }
    
    // Limpar terminal anterior
    if (terminalOutput) {
        terminalOutput.innerHTML = '<div class="terminal-welcome">🔌 Conectando ao servidor SSH...</div>';
    }
    
    // Abrir o modal
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    
    console.log('✅ Modal aberto com sucesso!');
    
    // Focar no input do terminal
    setTimeout(() => {
        const terminalInput = document.getElementById('terminal-input');
        if (terminalInput) {
            terminalInput.focus();
        }
    }, 300);
    
    // Iniciar conexão WebSocket
    iniciarConexaoSSH(acessoId);
}

/**
 * Fecha o modal de terminal SSH
 */
function fecharTerminalSSH() {
    const modal = document.getElementById('modalSSHTerminal');
    if (modal) {
        modal.style.display = 'none';
        document.body.style.overflow = 'auto';
    }
    
    // Fechar conexão WebSocket se existir
    if (window.sshWebSocket && window.sshWebSocket.readyState === WebSocket.OPEN) {
        window.sshWebSocket.close();
    }
    
    // Limpar terminal
    const terminalOutput = document.getElementById('terminal-output');
    if (terminalOutput) {
        terminalOutput.innerHTML = '';
    }
}

// ============================================
// CONEXÃO WEBSOCKET SSH
// ============================================

let sshWebSocket = null;

/**
 * Inicia conexão WebSocket com o servidor SSH
 * @param {number} acessoId - ID do acesso
 */
function iniciarConexaoSSH(acessoId) {
    console.log('📡 Iniciando conexão SSH para acesso ID:', acessoId);
    
    // Fechar conexão anterior se existir
    if (sshWebSocket && sshWebSocket.readyState === WebSocket.OPEN) {
        console.log('⚠️ Fechando conexão anterior...');
        sshWebSocket.close();
    }
    
    // Determinar protocolo WebSocket (ws ou wss)
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/ssh/`;
    
    console.log('🔗 URL WebSocket:', wsUrl);
    
    try {
        sshWebSocket = new WebSocket(wsUrl);
        
        sshWebSocket.onopen = function(e) {
            console.log('✅ WebSocket conectado com sucesso!');
            adicionarSaidaTerminal('Sistema: WebSocket conectado\n', 'info');
            
            // Enviar comando de conexão
            const payload = {
                action: 'connect',
                acesso_id: acessoId
            };
            console.log('📤 Enviando comando de conexão:', payload);
            sshWebSocket.send(JSON.stringify(payload));
        };
        
        sshWebSocket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                console.log('📨 Mensagem recebida:', data);
                
                switch(data.type) {
                    case 'connected':
                        adicionarSaidaTerminal(data.message + '\n', 'success');
                        break;
                    case 'output':
                        adicionarSaidaTerminal(data.data, 'output');
                        break;
                    case 'error':
                        adicionarSaidaTerminal('ERRO: ' + data.message + '\n', 'error');
                        break;
                    case 'info':
                        adicionarSaidaTerminal('INFO: ' + data.message + '\n', 'info');
                        break;
                }
            } catch (error) {
                console.error('Erro ao parsear mensagem WebSocket:', error);
            }
        };
        
        sshWebSocket.onerror = function(error) {
            console.error('❌ Erro WebSocket:', error);
            adicionarSaidaTerminal('ERRO: Falha na conexão WebSocket\n', 'error');
        };
        
        sshWebSocket.onclose = function(event) {
            console.log('🔌 WebSocket fechado:', event);
            if (event.wasClean) {
                adicionarSaidaTerminal('Sistema: Conexão encerrada\n', 'info');
            } else {
                adicionarSaidaTerminal('ERRO: Conexão perdida\n', 'error');
            }
        };
        
        window.sshWebSocket = sshWebSocket;
        
    } catch (error) {
        console.error('❌ Erro ao criar WebSocket:', error);
        adicionarSaidaTerminal('ERRO: Não foi possível estabelecer conexão\n', 'error');
    }
}

/**
 * Adiciona uma linha ao output do terminal
 * @param {string} texto - Texto a adicionar
 * @param {string} tipo - Tipo de mensagem (output, input, success, error, info)
 */
function adicionarSaidaTerminal(texto, tipo = 'output') {
    const terminalOutput = document.getElementById('terminal-output');
    if (!terminalOutput) {
        console.error('❌ Elemento terminal-output não encontrado!');
        return;
    }
    
    const linha = document.createElement('div');
    linha.className = `terminal-line terminal-${tipo}`;
    linha.textContent = texto;
    
    terminalOutput.appendChild(linha);
    
    // Auto-scroll para o final
    terminalOutput.scrollTop = terminalOutput.scrollHeight;
}

/**
 * Envia comando via terminal SSH
 * @param {Event} event - Evento do formulário
 */
function enviarComandoSSH(event) {
    if (event) {
        event.preventDefault();
    }
    
    const input = document.getElementById('terminal-input');
    if (!input) return;
    
    const comando = input.value;
    
    if (!comando.trim()) return;
    
    // Adicionar comando ao terminal
    adicionarSaidaTerminal('$ ' + comando + '\n', 'input');
    
    // Enviar comando via WebSocket
    if (sshWebSocket && sshWebSocket.readyState === WebSocket.OPEN) {
        sshWebSocket.send(JSON.stringify({
            action: 'command',
            command: comando + '\n'
        }));
    } else {
        adicionarSaidaTerminal('ERRO: Não há conexão ativa\n', 'error');
    }
    
    // Limpar input
    input.value = '';
}

/**
 * Limpa o terminal
 */
function limparTerminal() {
    const terminalOutput = document.getElementById('terminal-output');
    if (terminalOutput) {
        terminalOutput.innerHTML = '<div class="terminal-welcome">Terminal limpo</div>';
    }
}

/**
 * Copia dados de conexão para a área de transferência
 */
function copiarDadosConexao(host, porta, usuario, senha, protocolo) {
    const texto = `Host: ${host}\nPorta: ${porta}\nUsuário: ${usuario}\nSenha: ${senha}\nProtocolo: ${protocolo}`;
    
    navigator.clipboard.writeText(texto).then(() => {
        showSuccess('SUCESSO', 'Dados copiados para a área de transferência!', 3000);
    }).catch(err => {
        // Fallback para navegadores mais antigos
        const textArea = document.createElement('textarea');
        textArea.value = texto;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        showSuccess('SUCESSO', 'Dados copiados para a área de transferência!', 3000);
    });
}

/**
 * Copia um texto para a área de transferência
 */
function copiarTexto(texto) {
    navigator.clipboard.writeText(texto).then(() => {
        showSuccess('SUCESSO', 'Copiado para a área de transferência!', 2000);
    }).catch(err => {
        // Fallback
        const textArea = document.createElement('textarea');
        textArea.value = texto;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        showSuccess('SUCESSO', 'Copiado para a área de transferência!', 2000);
    });
}
