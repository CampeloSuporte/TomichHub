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