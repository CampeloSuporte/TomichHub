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



