// ============================================
// FUNÇÃO DE EXCLUSÃO DE CLIENTE
// ============================================
function confirmarDelete(id) {
    // Encontra o cliente na tabela para pegar o nome
    const row = document.querySelector(`tr[data-cliente-id="${id}"]`);
    const nomeEmpresa = row ? row.querySelector('td:nth-child(2)').textContent : 'este cliente';
    
    // Preenche os dados no modal
    document.getElementById('delete_cliente_id').value = id;
    document.getElementById('delete_cliente_nome').textContent = nomeEmpresa;
    
    // Abre o modal de confirmação
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}


    // Função de pesquisa de clientes
    document.getElementById('searchInput').addEventListener('input', function(e) {
        const searchTerm = e.target.value.toLowerCase();
        const rows = document.querySelectorAll('.custom-table tbody tr');
        
        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            if (text.includes(searchTerm)) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    });


        // Seleção de linhas da tabela
    document.querySelectorAll('.table-row-selectable').forEach(row => {
        row.addEventListener('click', function(e) {
            // Não seleciona se clicou em um botão de ação
            if (e.target.closest('.btn-action')) {
                return;
            }
            
            // Remove seleção anterior
            document.querySelectorAll('.table-row-selectable').forEach(r => {
                r.classList.remove('selected');
            });
            
            // Adiciona seleção na linha clicada
            this.classList.add('selected');
            
            // Pega o ID do cliente
            const clienteId = this.dataset.clienteId;
            console.log('Cliente selecionado:', clienteId);
            
            // REDIRECIONA PARA A URL DO CLIENTE
            // Substitua 'nome_da_url' pelo nome da sua URL do Django
            // Exemplo: window.location.href = `/clientes/${clienteId}/detalhes/`;
            window.location.href = `/clientes/listar/?id=${clienteId}`;
        });
    });



        // Máscara para CNPJ
    document.getElementById('cnpj').addEventListener('input', function(e) {
        let value = e.target.value.replace(/\D/g, '');
        if (value.length <= 14) {
            value = value.replace(/^(\d{2})(\d)/, '$1.$2');
            value = value.replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3');
            value = value.replace(/\.(\d{3})(\d)/, '.$1/$2');
            value = value.replace(/(\d{4})(\d)/, '$1-$2');
            e.target.value = value;
        }
    });

    // Máscara para Telefone
    document.getElementById('telefone').addEventListener('input', function(e) {
        let value = e.target.value.replace(/\D/g, '');
        if (value.length <= 11) {
            value = value.replace(/^(\d{2})(\d)/, '($1) $2');
            value = value.replace(/(\d{5})(\d)/, '$1-$2');
            e.target.value = value;
        }
    });



      function selectUser(user) {
        // Armazena apenas o ID no campo hidden (esse será enviado no formulário)
        inputId.value = user.id;
        // Mostra o nome no campo de busca (apenas visual)
        inputSearch.value = user.username;
        dropdownMenu.classList.remove('show');
        
        console.log('ID que será enviado:', user.id); // Para debug
    }

    inputSearch.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase();
        
        if (searchTerm.length === 0) {
            dropdownMenu.classList.remove('show');
            inputId.value = ''; // Limpa o ID se o campo for limpo
            return;
        }

        const filtered = usuarios.filter(user => 
            user.username.toLowerCase().includes(searchTerm) ||
            user.id.toString().includes(searchTerm)
        );

        renderDropdown(filtered);
    });

    inputSearch.addEventListener('focus', () => {
        if (inputSearch.value.length === 0) {
            renderDropdown(usuarios);
        }
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.custom-dropdown-wrapper')) {
            dropdownMenu.classList.remove('show');
        }
    });
// ============================================
// FUNÇÃO DE EDIÇÃO DE CLIENTE
// ============================================
function editarCliente(id, nomeEmpresa, cnpj, cep, endereco, estado, cidade, telefone, email, usuarioId) {
    // Preenche os campos do modal com os dados do cliente
    document.getElementById('edit_id').value = id;
    document.getElementById('edit_nome_empresa').value = nomeEmpresa;
    document.getElementById('edit_cnpj').value = cnpj;
    document.getElementById('edit_cep').value = cep;
    document.getElementById('edit_endereco').value = endereco;
    document.getElementById('edit_estado').value = estado;
    document.getElementById('edit_cidade').value = cidade;
    document.getElementById('edit_telefone').value = telefone;
    document.getElementById('edit_email').value = email;
    
    // Preenche o dropdown de usuário
    document.getElementById('edit_usuario').value = usuarioId;
    const usuarioSelecionado = usuarios.find(u => u.id === usuarioId);
    if (usuarioSelecionado) {
        document.getElementById('edit_usuario_search').value = usuarioSelecionado.username;
    }
    
    // Abre o modal
    const modal = new bootstrap.Modal(document.getElementById('edicaoModal'));
    modal.show();
}


// Abrir modal de edição
function abrirModalEditarAcesso(acessoId) {
    fetch(`/acessos/buscar/${acessoId}/`)
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
            
            // Mostrar modal
            document.getElementById('modalEditarAcesso').style.display = 'flex';
        })
        .catch(error => {
            console.error('Erro ao buscar acesso:', error);
            showError('ERRO', 'Não foi possível carregar os dados do acesso.', 5000);
        });
}

// Fechar modal de edição
function fecharModalEditarAcesso() {
    document.getElementById('modalEditarAcesso').style.display = 'none';
}

// Adaptar filterOptions para funcionar com os campos de edição também
function filterOptions(fieldName) {
    const input = document.getElementById(`${fieldName}_input`);
    const dropdown = document.getElementById(`${fieldName}-dropdown`);
    const hiddenInput = document.getElementById(fieldName);
    const filter = input.value.toUpperCase();
    const options = dropdown.getElementsByClassName('search-select-option');
    
    let hasVisibleOption = false;
    
    for (let i = 0; i < options.length; i++) {
        const txtValue = options[i].textContent || options[i].innerText;
        if (txtValue.toUpperCase().indexOf(filter) > -1) {
            options[i].classList.add('visible');
            hasVisibleOption = true;
        } else {
            options[i].classList.remove('visible');
        }
    }
    
    // Mostrar dropdown se tiver opções visíveis
    if (hasVisibleOption && input.value !== '') {
        dropdown.classList.add('show');
    } else {
        dropdown.classList.remove('show');
    }
    
    // Adicionar event listeners nas opções
    for (let i = 0; i < options.length; i++) {
        options[i].onclick = function() {
            input.value = this.getAttribute('data-text');
            hiddenInput.value = this.getAttribute('data-value');
            dropdown.classList.remove('show');
        };
    }
}

// Fechar dropdowns ao clicar fora
document.addEventListener('click', function(event) {
    const dropdowns = document.querySelectorAll('.search-select-dropdown');
    const inputs = document.querySelectorAll('.search-select-input');
    
    let clickedInside = false;
    inputs.forEach(input => {
        if (input.contains(event.target)) {
            clickedInside = true;
        }
    });
    
    dropdowns.forEach(dropdown => {
        if (dropdown.contains(event.target)) {
            clickedInside = true;
        }
    });
    
    if (!clickedInside) {
        dropdowns.forEach(dropdown => {
            dropdown.classList.remove('show');
        });
    }
});