// Abrir modal de edição
function abrirModalEditarAcesso(acessoId) {
    fetch(`/clientes/buscar/${acessoId}/`)
        .then(response => response.json())
        .then(data => {
            document.getElementById('edit_acesso_id').value = data.id;
            document.getElementById('edit_tipo').value = data.tipo;
            document.getElementById('edit_hostname').value = data.host;
            document.getElementById('edit_hostname_ipv6').value = data.host_ipv6 || '';
            document.getElementById('edit_protocolo').value = data.protocolo;
            document.getElementById('edit_porta').value = data.porta;
            document.getElementById('edit_usuario').value = data.usuario;
            document.getElementById('edit_senha').value = data.senha;
            document.getElementById('edit_senha_adm').value = data.senha_adm || '';
            document.getElementById('edit_vlan').value = data.vlan || '';
            
            // Preencher função
            document.getElementById('edit_funcao').value = data.funcao_id;
            document.getElementById('edit_funcao_input').value = data.funcao_nome;
            
            // Preencher modelo
            document.getElementById('edit_modelo').value = data.modelo_id;
            document.getElementById('edit_modelo_input').value = data.modelo_nome;
            
            // Alterar action do form
            document.getElementById('formEditarAcesso').action = `/acessos/editar/${acessoId}/`;
            
            document.getElementById('modalEditarAcesso').style.display = 'flex';
        });
}

// Fechar modal de edição
function fecharModalEditarAcesso() {
    document.getElementById('modalEditarAcesso').style.display = 'none';
}



// Funções do Modal de Upload
function abrirModalUpload() {
    document.getElementById('modalUploadDocumento').style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function fecharModalUpload() {
    document.getElementById('modalUploadDocumento').style.display = 'none';
    document.body.style.overflow = 'auto';
    limparFormularioUpload();
}

function limparFormularioUpload() {
    document.getElementById('formUploadDocumento').reset();
    document.getElementById('uploadArea').style.display = 'flex';
    document.getElementById('filePreview').style.display = 'none';
}

// Upload Area - Click
document.addEventListener('DOMContentLoaded', function() {
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    
    if (uploadArea && fileInput) {
        uploadArea.addEventListener('click', () => {
            fileInput.click();
        });

        // Drag and Drop
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('drag-over');
        });

        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('drag-over');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('drag-over');
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                fileInput.files = files;
                mostrarPreviewArquivo(files[0]);
            }
        });

        // Change do input
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                mostrarPreviewArquivo(e.target.files[0]);
            }
        });
    }
});

function mostrarPreviewArquivo(file) {
    document.getElementById('uploadArea').style.display = 'none';
    document.getElementById('filePreview').style.display = 'block';
    
    document.getElementById('fileName').textContent = file.name;
    document.getElementById('fileSize').textContent = formatarTamanho(file.size);
    
    // Atualizar ícone baseado no tipo
    const fileIcon = document.querySelector('.file-icon');
    const extension = file.name.split('.').pop().toLowerCase();
    
    if (['pdf'].includes(extension)) {
        fileIcon.className = 'fas fa-file-pdf file-icon';
    } else if (['doc', 'docx'].includes(extension)) {
        fileIcon.className = 'fas fa-file-word file-icon';
    } else if (['xls', 'xlsx'].includes(extension)) {
        fileIcon.className = 'fas fa-file-excel file-icon';
    } else if (['jpg', 'jpeg', 'png', 'gif'].includes(extension)) {
        fileIcon.className = 'fas fa-file-image file-icon';
    } else if (['zip', 'rar', '7z'].includes(extension)) {
        fileIcon.className = 'fas fa-file-archive file-icon';
    } else {
        fileIcon.className = 'fas fa-file file-icon';
    }
}

function removerArquivo() {
    document.getElementById('fileInput').value = '';
    document.getElementById('uploadArea').style.display = 'flex';
    document.getElementById('filePreview').style.display = 'none';
}

function formatarTamanho(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// Fechar modal ao clicar fora
document.addEventListener('click', function(event) {
    const modal = document.getElementById('modalUploadDocumento');
    if (event.target === modal) {
        fecharModalUpload();
    }
});



// ============================================
// FUNÇÕES DO MODAL DE UPLOAD VPN
// ============================================

function abrirModalUploadVPN() {
    document.getElementById('modalUploadVPN').style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function fecharModalUploadVPN() {
    document.getElementById('modalUploadVPN').style.display = 'none';
    document.body.style.overflow = 'auto';
    limparFormularioUploadVPN();
}

function limparFormularioUploadVPN() {
    document.getElementById('formUploadVPN').reset();
    document.getElementById('uploadAreaVPN').style.display = 'flex';
    document.getElementById('filePreviewVPN').style.display = 'none';
}

function mostrarPreviewArquivoVPN(file) {
    document.getElementById('uploadAreaVPN').style.display = 'none';
    document.getElementById('filePreviewVPN').style.display = 'block';
    
    document.getElementById('fileNameVPN').textContent = file.name;
    document.getElementById('fileSizeVPN').textContent = formatarTamanho(file.size);
}

function removerArquivoVPN() {
    document.getElementById('fileInputVPN').value = '';
    document.getElementById('uploadAreaVPN').style.display = 'flex';
    document.getElementById('filePreviewVPN').style.display = 'none';
}

// ============================================
// FUNÇÕES DO MODAL DE EDIÇÃO VPN
// ============================================

function abrirModalEditarVPN(vpnId) {
    fetch(`/clientes/vpn/buscar/${vpnId}/`)
        .then(response => response.json())
        .then(data => {
            document.getElementById('edit_vpn_id').value = data.id;
            document.getElementById('edit_nome_vpn').value = data.nome;
            document.getElementById('edit_usuario_vpn').value = data.usuario || '';
            document.getElementById('edit_senha_vpn').value = data.senha || '';
            document.getElementById('edit_private_key_vpn').value = data.private_key || '';
            
            document.getElementById('formEditarVPN').action = `/clientes/vpn/editar/${vpnId}/`;
            
            document.getElementById('modalEditarVPN').style.display = 'flex';
            document.body.style.overflow = 'hidden';
        })
        .catch(error => {
            console.error('Erro:', error);
            alert('Não foi possível carregar os dados da VPN.');
        });
}

function fecharModalEditarVPN() {
    document.getElementById('modalEditarVPN').style.display = 'none';
    document.body.style.overflow = 'auto';
}

// ============================================
// VISUALIZAR PRIVATE KEY
// ============================================

function visualizarPrivateKey(key, nome) {
    document.getElementById('nomeVPNKey').textContent = nome;
    document.getElementById('privateKeyContent').textContent = key;
    document.getElementById('modalPrivateKey').style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function fecharModalPrivateKey() {
    document.getElementById('modalPrivateKey').style.display = 'none';
    document.body.style.overflow = 'auto';
}

function copiarPrivateKey() {
    const keyContent = document.getElementById('privateKeyContent').textContent;
    copiarTexto(keyContent);
}

// ============================================
// FUNÇÃO PARA COPIAR TEXTO
// ============================================

function copiarTexto(texto) {
    navigator.clipboard.writeText(texto).then(function() {
        // Mostra feedback visual
        const tempMsg = document.createElement('div');
        tempMsg.className = 'toast-copy';
        tempMsg.textContent = 'Copiado!';
        tempMsg.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--primary-green);
            color: #000;
            padding: 12px 20px;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-weight: 700;
            z-index: 99999;
            animation: fadeInOut 2s ease-in-out;
        `;
        document.body.appendChild(tempMsg);
        setTimeout(() => tempMsg.remove(), 2000);
    }).catch(err => {
        console.error('Erro ao copiar:', err);
        alert('Erro ao copiar para área de transferência');
    });
}

// ============================================
// FUNÇÕES DO MODAL DE UPLOAD TOPOLOGIA
// ============================================

function abrirModalUploadTopologia() {
    document.getElementById('modalUploadTopologia').style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function fecharModalUploadTopologia() {
    document.getElementById('modalUploadTopologia').style.display = 'none';
    document.body.style.overflow = 'auto';
    limparFormularioUploadTopologia();
}

function limparFormularioUploadTopologia() {
    document.getElementById('formUploadTopologia').reset();
    document.getElementById('uploadAreaTopologia').style.display = 'flex';
    document.getElementById('imagePreviewTopologia').style.display = 'none';
}

function mostrarPreviewImagemTopologia(file) {
    document.getElementById('uploadAreaTopologia').style.display = 'none';
    document.getElementById('imagePreviewTopologia').style.display = 'block';
    
    // Criar preview da imagem
    const reader = new FileReader();
    reader.onload = function(e) {
        document.getElementById('previewImageTopologia').src = e.target.result;
    };
    reader.readAsDataURL(file);
    
    document.getElementById('fileNameTopologia').textContent = file.name;
    document.getElementById('fileSizeTopologia').textContent = formatarTamanho(file.size);
}

function removerArquivoTopologia() {
    document.getElementById('fileInputTopologia').value = '';
    document.getElementById('uploadAreaTopologia').style.display = 'flex';
    document.getElementById('imagePreviewTopologia').style.display = 'none';
}

// ============================================
// VISUALIZAR IMAGEM EM TAMANHO GRANDE
// ============================================

function visualizarImagemGrande(url, nome) {
    document.getElementById('imagemGrande').src = url;
    document.getElementById('nomeImagemGrande').textContent = nome;
    document.getElementById('modalVisualizarImagem').style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function fecharModalImagem() {
    document.getElementById('modalVisualizarImagem').style.display = 'none';
    document.body.style.overflow = 'auto';
}

// ============================================
// EVENT LISTENERS PARA VPN E TOPOLOGIA
// ============================================

document.addEventListener('DOMContentLoaded', function() {
    // Upload VPN - Click e Drag & Drop
    const uploadAreaVPN = document.getElementById('uploadAreaVPN');
    const fileInputVPN = document.getElementById('fileInputVPN');
    
    if (uploadAreaVPN && fileInputVPN) {
        uploadAreaVPN.addEventListener('click', () => {
            fileInputVPN.click();
        });

        uploadAreaVPN.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadAreaVPN.classList.add('drag-over');
        });

        uploadAreaVPN.addEventListener('dragleave', () => {
            uploadAreaVPN.classList.remove('drag-over');
        });

        uploadAreaVPN.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadAreaVPN.classList.remove('drag-over');
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                fileInputVPN.files = files;
                mostrarPreviewArquivoVPN(files[0]);
            }
        });

        fileInputVPN.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                mostrarPreviewArquivoVPN(e.target.files[0]);
            }
        });
    }

    // Upload Topologia - Click e Drag & Drop
    const uploadAreaTopologia = document.getElementById('uploadAreaTopologia');
    const fileInputTopologia = document.getElementById('fileInputTopologia');
    
    if (uploadAreaTopologia && fileInputTopologia) {
        uploadAreaTopologia.addEventListener('click', () => {
            fileInputTopologia.click();
        });

        uploadAreaTopologia.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadAreaTopologia.classList.add('drag-over');
        });

        uploadAreaTopologia.addEventListener('dragleave', () => {
            uploadAreaTopologia.classList.remove('drag-over');
        });

        uploadAreaTopologia.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadAreaTopologia.classList.remove('drag-over');
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                // Validar se é imagem
                if (files[0].type.startsWith('image/')) {
                    fileInputTopologia.files = files;
                    mostrarPreviewImagemTopologia(files[0]);
                } else {
                    alert('Por favor, selecione apenas arquivos de imagem.');
                }
            }
        });

        fileInputTopologia.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                mostrarPreviewImagemTopologia(e.target.files[0]);
            }
        });
    }

    // Fechar modais ao clicar fora
    const modalVPN = document.getElementById('modalUploadVPN');
    const modalEditarVPN = document.getElementById('modalEditarVPN');
    const modalTopologia = document.getElementById('modalUploadTopologia');
    const modalPrivateKey = document.getElementById('modalPrivateKey');
    
    document.addEventListener('click', function(event) {
        if (event.target === modalVPN) {
            fecharModalUploadVPN();
        }
        if (event.target === modalEditarVPN) {
            fecharModalEditarVPN();
        }
        if (event.target === modalTopologia) {
            fecharModalUploadTopologia();
        }
        if (event.target === modalPrivateKey) {
            fecharModalPrivateKey();
        }
    });

    // Fechar modal de visualização com ESC
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            fecharModalImagem();
            fecharModalPrivateKey();
        }
    });
});



// chamados.js - Sistema de Gerenciamento de Chamados

// Variável global para armazenar o chamado atual
let chamadoAtual = null;

// ============================================
// FUNÇÕES DE CARREGAMENTO
// ============================================

/**
 * Carregar chamados do cliente
 */
function carregarChamados() {
    const clienteId = document.querySelector('[name="cliente"]')?.value;
    
    if (!clienteId) {
        console.error('ID do cliente não encontrado');
        return;
    }
    
    fetch(`/clientes/chamados/listar/?id=${clienteId}`)
        .then(response => response.json())
        .then(data => {
            renderizarChamados(data.chamados);
        })
        .catch(error => {
            console.error('Erro ao carregar chamados:', error);
            document.getElementById('listaChamados').innerHTML = `
                <div class="text-center py-5">
                    <i class="fas fa-exclamation-triangle" style="font-size: 64px; color: var(--error-red); opacity: 0.3;"></i>
                    <p class="mt-3 text-muted">Erro ao carregar chamados</p>
                    <button class="btn btn-primary mt-3" onclick="carregarChamados()">
                        <i class="fas fa-sync-alt me-2"></i> Tentar Novamente
                    </button>
                </div>
            `;
        });
}

/**
 * Renderizar lista de chamados
 */
function renderizarChamados(chamados) {
    const container = document.getElementById('listaChamados');
    
    if (!chamados || chamados.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5">
                <i class="fas fa-ticket-alt" style="font-size: 64px; color: var(--accent-cyan); opacity: 0.3;"></i>
                <p class="mt-3 text-muted">Nenhum chamado cadastrado</p>
                <button class="btn btn-primary mt-3" onclick="abrirModalCadastrarChamado()">
                    <i class="fas fa-plus me-2"></i> Criar Primeiro Chamado
                </button>
            </div>
        `;
        return;
    }
    
    let html = '';
    chamados.forEach(chamado => {
        const prioridadeClass = chamado.prioridade.toLowerCase().replace(' ', '-');
        const statusClass = chamado.status.toLowerCase().replace(' ', '-');
        
        html += `
            <div class="chamado-card ${prioridadeClass}" onclick="abrirModalVisualizarChamado(${chamado.id})">
                <div class="chamado-header">
                    <div>
                        <div class="chamado-numero">#${chamado.id}</div>
                        <div class="chamado-titulo">${escapeHtml(chamado.titulo)}</div>
                    </div>
                    <div class="chamado-badges">
                        <span class="badge-custom badge-prioridade ${prioridadeClass}">${chamado.prioridade}</span>
                        <span class="badge-custom badge-status ${statusClass}">${chamado.status}</span>
                    </div>
                </div>
                
                <div class="chamado-info">
                    <div class="chamado-info-item">
                        <span class="chamado-info-label">Categoria</span>
                        <span class="chamado-info-value">${chamado.categoria || 'Não definida'}</span>
                    </div>
                    <div class="chamado-info-item">
                        <span class="chamado-info-label">Departamento</span>
                        <span class="chamado-info-value">${chamado.departamento}</span>
                    </div>
                    <div class="chamado-info-item">
                        <span class="chamado-info-label">Responsável</span>
                        <span class="chamado-info-value">${chamado.responsavel}</span>
                    </div>
                    <div class="chamado-info-item">
                        <span class="chamado-info-label">Data</span>
                        <span class="chamado-info-value">${chamado.data_criacao}</span>
                    </div>
                    <div class="chamado-info-item">
                        <span class="chamado-info-label">Comentários</span>
                        <span class="chamado-info-value">
                            <i class="fas fa-comments"></i> ${chamado.total_comentarios}
                        </span>
                    </div>
                </div>
                
                <div class="chamado-actions" onclick="event.stopPropagation()">
                    <button class="btn-action-chamado" onclick="abrirModalVisualizarChamado(${chamado.id})">
                        <i class="fas fa-eye me-1"></i> Visualizar
                    </button>
                    <form method="POST" action="/clientes/chamados/deletar/${chamado.id}/" style="display: inline;" 
                          onsubmit="return confirm('Tem certeza que deseja excluir o chamado #${chamado.id}?')">
                        <input type="hidden" name="csrfmiddlewaretoken" value="${getCookie('csrftoken')}">
                        <button type="submit" class="btn-action-chamado btn-delete-chamado">
                            <i class="fas fa-trash me-1"></i> Excluir
                        </button>
                    </form>
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// ============================================
// MODAL CADASTRAR CHAMADO
// ============================================

function abrirModalCadastrarChamado() {
    document.getElementById('modalCadastrarChamado').style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function fecharModalCadastrarChamado() {
    document.getElementById('modalCadastrarChamado').style.display = 'none';
    document.body.style.overflow = 'auto';
    document.getElementById('formCadastrarChamado').reset();
    document.getElementById('categoria').value = '';
    document.getElementById('categoria_input').value = '';
    document.getElementById('responsavel').value = '';
    document.getElementById('responsavel_input').value = '';
}

// ============================================
// MODAL ADICIONAR CATEGORIA
// ============================================

function abrirModalAdicionarCategoria() {
    document.getElementById('modalAdicionarCategoria').style.display = 'flex';
}

function fecharModalAdicionarCategoria() {
    document.getElementById('modalAdicionarCategoria').style.display = 'none';
    document.getElementById('nova_categoria_nome').value = '';
    document.getElementById('nova_categoria_descricao').value = '';
}

function salvarNovaCategoria() {
    const nome = document.getElementById('nova_categoria_nome').value.trim();
    const descricao = document.getElementById('nova_categoria_descricao').value.trim();
    
    if (!nome) {
        showError('ERRO', 'Digite o nome da categoria', 3000);
        return;
    }
    
    fetch('/clientes/categorias/cadastrar/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: `nome=${encodeURIComponent(nome)}&descricao=${encodeURIComponent(descricao)}`
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showError('ERRO', data.error, 4000);
        } else {
            showSuccess('SUCESSO', data.message, 3000);
            fecharModalAdicionarCategoria();
            // Adicionar a categoria ao campo de seleção
            document.getElementById('categoria').value = data.id;
            document.getElementById('categoria_input').value = data.nome;
        }
    })
    .catch(error => {
        console.error('Erro:', error);
        showError('ERRO', 'Erro ao cadastrar categoria', 3000);
    });
}

// ============================================
// BUSCA DE CATEGORIAS
// ============================================

function buscarCategorias(query, prefix = '') {
    const inputId = prefix ? `${prefix}_categoria_input` : 'categoria_input';
    const dropdownId = prefix ? `${prefix}_categoria-dropdown` : 'categoria-dropdown';
    const hiddenId = prefix ? `${prefix}_categoria` : 'categoria';
    
    const dropdown = document.getElementById(dropdownId);
    
    if (query.length < 1) {
        dropdown.classList.remove('show');
        return;
    }
    
    fetch(`/clientes/categorias/buscar/?q=${encodeURIComponent(query)}`)
        .then(response => response.json())
        .then(data => {
            let html = '';
            
            if (data.results.length === 0) {
                html = '<div class="no-results">Nenhuma categoria encontrada</div>';
            } else {
                data.results.forEach(cat => {
                    html += `
                        <div class="search-select-option visible" 
                             data-value="${cat.id}" 
                             data-text="${escapeHtml(cat.nome)}"
                             onclick="selecionarOpcao('${prefix ? prefix + '_' : ''}categoria', ${cat.id}, '${escapeHtml(cat.nome)}')">
                            ${escapeHtml(cat.nome)}
                        </div>
                    `;
                });
            }
            
            dropdown.innerHTML = html;
            dropdown.classList.add('show');
        })
        .catch(error => console.error('Erro:', error));
}

// ============================================
// BUSCA DE USUÁRIOS
// ============================================

function buscarUsuarios(query, prefix = '') {
    const dropdownId = prefix ? `${prefix}_responsavel-dropdown` : 'responsavel-dropdown';
    const dropdown = document.getElementById(dropdownId);
    
    if (query.length < 2) {
        dropdown.classList.remove('show');
        return;
    }
    
    fetch(`/clientes/usuarios/buscar/?q=${encodeURIComponent(query)}`)
        .then(response => response.json())
        .then(data => {
            let html = '';
            
            if (data.results.length === 0) {
                html = '<div class="no-results">Nenhum usuário encontrado</div>';
            } else {
                data.results.forEach(user => {
                    html += `
                        <div class="search-select-option visible" 
                             data-value="${user.id}" 
                             data-text="${escapeHtml(user.nome)}"
                             onclick="selecionarOpcao('${prefix ? prefix + '_' : ''}responsavel', ${user.id}, '${escapeHtml(user.nome)}')">
                            <strong>${escapeHtml(user.nome)}</strong> 
                            <small style="color: var(--text-muted);">(@${escapeHtml(user.username)})</small>
                        </div>
                    `;
                });
            }
            
            dropdown.innerHTML = html;
            dropdown.classList.add('show');
        })
        .catch(error => console.error('Erro:', error));
}

function selecionarOpcao(campo, valor, texto) {
    document.getElementById(campo).value = valor;
    document.getElementById(campo + '_input').value = texto;
    document.getElementById(campo + '-dropdown').classList.remove('show');
}

// ============================================
// MODAL VISUALIZAR/EDITAR CHAMADO
// ============================================

function abrirModalVisualizarChamado(chamadoId) {
    fetch(`/clientes/chamados/buscar/${chamadoId}/`)
        .then(response => response.json())
        .then(data => {
            chamadoAtual = data;
            
            document.getElementById('view_chamado_id').value = data.id;
            document.getElementById('view_chamado_numero').textContent = data.id;
            document.getElementById('view_titulo').value = data.titulo;
            document.getElementById('view_descricao').value = data.descricao;
            document.getElementById('view_status').value = data.status;
            document.getElementById('view_prioridade').value = data.prioridade;
            document.getElementById('view_departamento').value = data.departamento;
            document.getElementById('view_cliente').textContent = data.cliente_nome;
            document.getElementById('view_data_criacao').textContent = data.data_criacao;
            
            document.getElementById('view_categoria').value = data.categoria_id || '';
            document.getElementById('view_categoria_input').value = data.categoria_nome || '';
            document.getElementById('view_responsavel').value = data.responsavel_id || '';
            document.getElementById('view_responsavel_input').value = data.responsavel_nome || '';
            
            // Renderizar comentários
            renderizarComentarios(data.comentarios);
            document.getElementById('badge_comentarios').textContent = data.comentarios.length;
            
            document.getElementById('modalVisualizarChamado').style.display = 'flex';
            document.body.style.overflow = 'hidden';
            
            // Voltar para aba de detalhes
            document.querySelectorAll('#modalVisualizarChamado .nav-link').forEach(link => {
                link.classList.remove('active');
            });
            document.querySelector('#modalVisualizarChamado .nav-link').classList.add('active');
            document.getElementById('aba-detalhes').style.display = 'block';
            document.getElementById('aba-comentarios').style.display = 'none';
        })
        .catch(error => {
            console.error('Erro:', error);
            showError('ERRO', 'Não foi possível carregar o chamado', 3000);
        });
}

function fecharModalVisualizarChamado() {
    document.getElementById('modalVisualizarChamado').style.display = 'none';
    document.body.style.overflow = 'auto';
    document.getElementById('novo_comentario').value = '';
    document.getElementById('comentario_interno').checked = false;
    chamadoAtual = null;
}

function renderizarComentarios(comentarios) {
    const container = document.getElementById('lista-comentarios');
    
    if (comentarios.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4">
                <i class="fas fa-comments" style="font-size: 48px; color: var(--text-muted); opacity: 0.3;"></i>
                <p class="mt-3 text-muted" style="font-size: 13px;">Nenhum comentário ainda</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    comentarios.forEach(com => {
        html += `
            <div class="comentario-item ${com.is_internal ? 'interno' : ''}">
                <div class="comentario-header">
                    <span class="comentario-autor">
                        ${escapeHtml(com.usuario)}
                        ${com.is_internal ? '<span class="badge-interno">Interno</span>' : ''}
                    </span>
                    <span class="comentario-data">${com.data}</span>
                </div>
                <div class="comentario-texto">${escapeHtml(com.comentario)}</div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

function salvarEdicaoChamado() {
    if (!chamadoAtual) return;
    
    const form = document.getElementById('formEditarChamado');
    const formData = new FormData(form);
    
    // Adicionar campos hidden
    formData.append('titulo', document.getElementById('view_titulo').value);
    formData.append('descricao', document.getElementById('view_descricao').value);
    formData.append('status', document.getElementById('view_status').value);
    formData.append('prioridade', document.getElementById('view_prioridade').value);
    formData.append('departamento', document.getElementById('view_departamento').value);
    formData.append('categoria', document.getElementById('view_categoria').value);
    formData.append('responsavel', document.getElementById('view_responsavel').value);
    
    fetch(`/clientes/chamados/editar/${chamadoAtual.id}/`, {
        method: 'POST',
        body: formData,
        headers: {
            'X-CSRFToken': getCookie('csrftoken')
        }
    })
    .then(response => {
        if (response.redirected) {
            showSuccess('SUCESSO', 'Chamado atualizado com sucesso!', 3000);
            fecharModalVisualizarChamado();
            carregarChamados();
        }
    })
    .catch(error => {
        console.error('Erro:', error);
        showError('ERRO', 'Erro ao salvar alterações', 3000);
    });
}

function adicionarComentario() {
    if (!chamadoAtual) return;
    
    const comentario = document.getElementById('novo_comentario').value.trim();
    const isInternal = document.getElementById('comentario_interno').checked;
    
    if (!comentario) {
        showError('ERRO', 'Digite um comentário', 3000);
        return;
    }
    
    const formData = new FormData();
    formData.append('comentario', comentario);
    formData.append('is_internal', isInternal);
    
    fetch(`/clientes/chamados/${chamadoAtual.id}/comentario/`, {
        method: 'POST',
        body: formData,
        headers: {
            'X-CSRFToken': getCookie('csrftoken')
        }
    })
    .then(response => {
        if (response.ok) {
            showSuccess('SUCESSO', 'Comentário adicionado', 3000);
            document.getElementById('novo_comentario').value = '';
            document.getElementById('comentario_interno').checked = false;
            // Recarregar o chamado para atualizar os comentários
            abrirModalVisualizarChamado(chamadoAtual.id);
        }
    })
    .catch(error => {
        console.error('Erro:', error);
        showError('ERRO', 'Erro ao adicionar comentário', 3000);
    });
}

function trocarAbaModal(event, aba) {
    event.preventDefault();
    
    // Esconder todas as abas
    document.getElementById('aba-detalhes').style.display = 'none';
    document.getElementById('aba-comentarios').style.display = 'none';
    
    // Remover classe active de todos os links
    event.target.closest('.nav-tabs').querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
    });
    
    // Mostrar aba selecionada
    document.getElementById('aba-' + aba).style.display = 'block';
    event.target.classList.add('active');
}

// ============================================
// FUNÇÕES AUXILIARES
// ============================================

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// ============================================
// INICIALIZAÇÃO
// ============================================

document.addEventListener('DOMContentLoaded', function() {
    // Observer para detectar quando a aba de chamados é aberta
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            const tabChamados = document.getElementById('tab-chamados');
            if (tabChamados && tabChamados.style.display === 'block') {
                carregarChamados();
            }
        });
    });
    
    const tabChamados = document.getElementById('tab-chamados');
    if (tabChamados) {
        observer.observe(tabChamados, {
            attributes: true,
            attributeFilter: ['style']
        });
    }
    
    // Fechar dropdowns ao clicar fora
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.custom-search-select')) {
            document.querySelectorAll('.search-select-dropdown').forEach(d => {
                d.classList.remove('show');
            });
        }
        
        // Fechar modais ao clicar no overlay
        const modalCadastrar = document.getElementById('modalCadastrarChamado');
        if (e.target === modalCadastrar) {
            fecharModalCadastrarChamado();
        }
        
        const modalCategoria = document.getElementById('modalAdicionarCategoria');
        if (e.target === modalCategoria) {
            fecharModalAdicionarCategoria();
        }
        
        const modalVisualizar = document.getElementById('modalVisualizarChamado');
        if (e.target === modalVisualizar) {
            fecharModalVisualizarChamado();
        }
    });
    
    // ESC fecha modais
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            fecharModalCadastrarChamado();
            fecharModalAdicionarCategoria();
            fecharModalVisualizarChamado();
        }
    });
});