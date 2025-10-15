    function mostrarFuncao(event, funcao) {
        event.preventDefault();
        
        // Esconder todos os conteúdos
        document.querySelectorAll('.funcao-content').forEach(function(content) {
            content.style.display = 'none';
        });
        
        // Remover classe active de todas as abas
        document.querySelectorAll('#funcaoTabs .nav-link').forEach(function(link) {
            link.classList.remove('active');
        });
        
        // Mostrar o conteúdo selecionado
        document.getElementById('funcao-' + funcao).style.display = 'block';
        
        // Adicionar classe active na aba clicada
        event.target.classList.add('active');
    }

    function abrirModalAcesso() {
        document.getElementById('modalAcesso').style.display = 'flex';
        document.body.style.overflow = 'hidden';
        // Re-inicializar os listeners quando abrir o modal
        inicializarSearchSelects();
    }

    function fecharModalAcesso() {
        document.getElementById('modalAcesso').style.display = 'none';
        document.body.style.overflow = 'auto';
    }

    function filterOptions(fieldType) {
        const input = document.getElementById(fieldType + '_input');
        const dropdown = document.getElementById(fieldType + '-dropdown');
        const filter = input.value.toLowerCase().trim();
        const options = dropdown.querySelectorAll('.search-select-option');
        
        // Se vazio, esconde tudo
        if (filter === '') {
            dropdown.classList.remove('show');
            options.forEach(option => option.classList.remove('visible'));
            return;
        }
        
        let hasVisible = false;

        options.forEach(option => {
            const text = option.getAttribute('data-text').toLowerCase();
            if (text.includes(filter)) {
                option.classList.add('visible');
                hasVisible = true;
            } else {
                option.classList.remove('visible');
            }
        });

        const noResults = dropdown.querySelector('.no-results');
        if (!hasVisible) {
            if (!noResults) {
                const noResultsDiv = document.createElement('div');
                noResultsDiv.className = 'no-results';
                noResultsDiv.textContent = 'Nenhum resultado encontrado';
                dropdown.appendChild(noResultsDiv);
            }
        } else {
            if (noResults) noResults.remove();
        }

        dropdown.classList.add('show');
    }

    function selectOption(fieldType, value, text) {
        const input = document.getElementById(fieldType + '_input');
        const hiddenInput = document.getElementById(fieldType);
        
        // Define o texto no input visível e o ID no input hidden
        input.value = text;
        hiddenInput.value = value;
        
        const dropdown = document.getElementById(fieldType + '-dropdown');
        dropdown.classList.remove('show');
        
        // Limpar filtro e esconder todas as opções
        const options = dropdown.querySelectorAll('.search-select-option');
        options.forEach(option => option.classList.remove('visible'));
    }

    function inicializarSearchSelects() {
        // Adicionar event listeners para todas as opções de search select
        document.querySelectorAll('.search-select-option').forEach(option => {
            // Remover listeners antigos para evitar duplicação
            option.replaceWith(option.cloneNode(true));
        });
        
        // Re-aplicar os listeners
        document.querySelectorAll('.search-select-option').forEach(option => {
            option.addEventListener('click', function() {
                const dropdown = this.closest('.search-select-dropdown');
                const fieldType = dropdown.id.replace('-dropdown', '');
                const value = this.getAttribute('data-value');
                const text = this.getAttribute('data-text');
                selectOption(fieldType, value, text);
            });
        });
    }

    // Inicializar quando o DOM estiver pronto
    document.addEventListener('DOMContentLoaded', function() {
        inicializarSearchSelects();
        
        // Fechar ao clicar fora dos dropdowns
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.custom-search-select')) {
                document.querySelectorAll('.search-select-dropdown').forEach(d => {
                    d.classList.remove('show');
                });
            }
            
            const modal = document.getElementById('modalAcesso');
            if (e.target === modal) fecharModalAcesso();
        });

        // ESC fecha modal
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') fecharModalAcesso();
        });
    });

    // ====================================
    // SISTEMA DE ALERTAS TERMINAL
    // ====================================

    function createAlertContainer() {
        let container = document.querySelector('.alert-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'alert-container';
            document.body.appendChild(container);
        }
        return container;
    }

    function showAlert(type, title, message, duration = 5000) {
        const container = createAlertContainer();
        
        const icons = {
            success: 'fa-check-circle',
            error: 'fa-times-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };
        
        const alert = document.createElement('div');
        alert.className = `terminal-alert alert-${type}`;
        alert.innerHTML = `
            <div class="alert-icon-wrapper">
                <i class="fas ${icons[type]} alert-icon"></i>
            </div>
            <div class="alert-content">
                <h5 class="alert-title">${title}</h5>
                <p class="alert-message">${message}</p>
            </div>
            <button class="alert-close" onclick="closeAlert(this)">
                <i class="fas fa-times"></i>
            </button>
        `;
        
        container.appendChild(alert);
        
        setTimeout(() => {
            removeAlert(alert);
        }, duration);
        
        return alert;
    }

    function removeAlert(alertElement) {
        alertElement.classList.add('fade-out');
        setTimeout(() => {
            if (alertElement.parentNode) {
                alertElement.parentNode.removeChild(alertElement);
            }
        }, 400);
    }

    function closeAlert(button) {
        const alert = button.closest('.terminal-alert');
        removeAlert(alert);
    }

    function showSuccess(title, message, duration) {
        return showAlert('success', title, message, duration);
    }

    function showError(title, message, duration) {
        return showAlert('error', title, message, duration);
    }

    function showWarning(title, message, duration) {
        return showAlert('warning', title, message, duration);
    }

    function showInfo(title, message, duration) {
        return showAlert('info', title, message, duration);
    }

    // ====================================
    // EXIBIR MENSAGENS DO DJANGO
    // ====================================
