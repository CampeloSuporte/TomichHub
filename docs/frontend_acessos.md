# Frontend — Aba de Acessos

**Arquivos:** `clientes/templates/listar.html`, `templates/modal_acessos.html`, `clientes/views.py`  
**Atualizado em:** 2026-07-20

---

## Visão Geral

A aba de Acessos exibe todos os `Acesso` do cliente agrupados por função (ex: Roteadores,
Switches, OLTs). Cada acesso é representado por um card com ações rápidas: terminal, ping,
editar, duplicar, excluir, Winbox, etc.

---

## Botão de Auditoria de Acessos — Adicionado em 2026-07-20

Novo ícone `fa-shield-halved` (roxo) em cada card de acesso, ao lado do botão de Comentários,
abrindo o modal `#modalAuditoriaAcesso` (`templates/modal_acessos.html`) com o histórico de
sessões (SSH/Telnet/WinBox), comandos digitados e gravações de tela. Documentação completa em
[AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md).

---

## Filtro Inline de Acessos — Adicionado em 2026-05-26

### Comportamento

O campo de busca substitui o antigo botão "Filtrar Acessos" e opera em tempo real
(evento `oninput`), sem necessidade de submissão.

**O que é buscado:**
- `data-tipo` do card → nome/tipo do acesso (ex: "Router MK", "OLT ZTE")
- `data-host` do card → host IPv4 e/ou host IPv6 do acesso

Ambos os critérios são avaliados com `||` (basta corresponder a um deles).

### Modos de exibição

| Estado             | Comportamento                                             |
|--------------------|-----------------------------------------------------------|
| Campo vazio        | Exibe abas de função normais (`#funcaoTabs` + `.funcao-content`) |
| Busca ativa        | Oculta abas; exibe grid flat (`#filtro-resultado`) com todos os cards correspondentes |

### Contador de resultados

Quando há busca ativa, aparece a mensagem:

```
X resultado(s) encontrado(s) para "termo"
```

exibida em `#filtro-contagem`.

### Estrutura HTML dos cards

Cada card de acesso possui atributos `data-*` para filtro client-side:

```html
<div class="acesso-card"
     data-tipo="{{ acesso.tipo|lower }}"
     data-host="{{ acesso.host|lower }} {{ acesso.host_ipv6|lower }}">
```

### Elementos HTML envolvidos

| ID / Classe              | Papel                                              |
|--------------------------|----------------------------------------------------|
| `#filtro-acessos-input`  | Input de busca                                     |
| `#filtro-resultado`      | Container flat (oculto quando vazio)               |
| `#filtro-resultado-row`  | Grid Bootstrap onde os cards clonados são inseridos|
| `#filtro-contagem`       | Span com o contador de resultados                  |
| `.acesso-card`           | Cards originais (usados como source do clone)      |
| `#funcaoTabs`            | Nav de abas de função (ocultado durante busca)     |
| `.funcao-content`        | Painéis de cada função (ocultados durante busca)   |

### Função JavaScript

```javascript
function filtrarAcessos(termo) {
    // termo vazio → restaura visão com abas
    // termo preenchido → filtra .acesso-card por data-tipo e data-host,
    //                    clona matches para #filtro-resultado-row
}
```

O botão de limpar (`×`) chama `filtrarAcessos('')` e limpa o input explicitamente.

---

## Modais de Acesso — Correção de Posicionamento (2026-05-26)

### Problema

Os modais (`modalAcesso`, `modalEditarAcesso`, `modalDuplicarAcesso`) apareciam no topo
da página em vez de sobrepor a tela toda. A causa era containers ancestrais com
`position: relative`, `transform` ou `filter` que criavam um novo contexto de empilhamento,
fazendo o `position: fixed` do modal se posicionar relativo ao container em vez do viewport.

### Solução

Duas funções globais foram adicionadas em `static/js/main.js`:

```javascript
function _abrirOverlay(id) {
    const el = document.getElementById(id);
    if (!el) return;
    // Move para <body> se ainda não estiver lá
    if (el.parentElement !== document.body) {
        document.body.appendChild(el);
    }
    el.style.display = 'block';
    el.scrollTop = 0;
    document.body.style.overflow = 'hidden';  // bloqueia scroll do fundo
}

function _fecharOverlay(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = 'none';
    document.body.style.overflow = '';        // restaura scroll
}
```

Ao mover o elemento diretamente para `document.body`, garante-se que o `position: fixed`
use o viewport como referência, eliminando a interferência de qualquer container intermediário.

### Modais afetados

| Modal                  | Função de abertura           | Função de fechamento         |
|------------------------|------------------------------|------------------------------|
| `#modalAcesso`         | `abrirModalAcesso()`         | `fecharModalAcesso()`        |
| `#modalEditarAcesso`   | (chamada interna ao editar)  | (chamada interna ao fechar)  |
| `#modalDuplicarAcesso` | (chamada interna ao duplicar)| (chamada interna ao fechar)  |

---

## CSS — Modal Overlay (`static/css/style.css`)

### Alterações aplicadas em 2026-05-26

#### `.modal-overlay`

```css
.modal-overlay {
    position: fixed;
    inset: 0;                    /* substitui top:0; left:0; width:100%; height:100% */
    background: rgba(0, 0, 0, 0.92);
    z-index: 9999;
    overflow-y: auto;            /* permite scroll do modal em telas pequenas */
    animation: fadeIn 0.3s ease;
}
```

Uso de `inset: 0` é mais conciso e equivalente a definir `top`, `right`, `bottom` e `left`
como zero individualmente.

#### `.modal-acesso`

```css
.modal-acesso {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    width: 90%;
    max-width: 900px;
    max-height: 90vh;
    overflow-y: auto;
    position: relative;
    margin: 40px auto;           /* centralização horizontal + espaço no topo */
    animation: slideIn 0.3s ease;
}
```

O `margin: 40px auto` garante a centralização horizontal e deixa 40 px de respiro no topo,
funcionando corretamente em conjunto com o `overflow-y: auto` do `.modal-overlay`.

---

---

## Controle de Visibilidade — Senha Root (2026-05-27)

O campo **Senha Root** (`acesso.senha_adm`) é exibido/editável apenas para
administradores e operadores (`is_staff=True` ou `is_superuser=True`).
Usuários do tipo cliente **nunca** veem este campo.

### Locais afetados

| Arquivo | Local | Controle |
|---|---|---|
| `listar.html` | Card de acesso — linha "Senha Root" | `{% if is_admin %}` |
| `modal_acessos.html` | Modal **Novo Acesso** — campo Senha Admin | `{% if is_admin %}` |
| `modal_acessos.html` | Modal **Editar Acesso** — campo Senha Admin | `{% if is_admin %}` |
| `modal_acessos.html` | Modal **Duplicar Acesso** — campo Senha Admin | `{% if is_admin %}` |
| `clientes/views.py` | API `buscar_acesso` (`/acessos/buscar/<id>/`) | retorna `senha_adm=''` se não for staff/superuser |

A variável `is_admin` é definida na view `listar_clientes` como:
```python
is_admin = request.user.is_staff or request.user.is_superuser
```

---

## Gerador de Senha Aleatória — Modais de Acesso (2026-05-27)

Os modais **Novo Acesso** e **Duplicar Acesso** possuem um botão de dado (🎲) ao lado
dos campos **Senha** e **Senha Admin** (quando admin).

### Comportamento

- Gera 16 caracteres usando `crypto.getRandomValues` (criptograficamente seguro)
- Conjunto de caracteres: letras maiúsculas, minúsculas, dígitos e símbolos `!@#$%&*`
  (sem caracteres ambíguos como `0`, `O`, `l`, `1`)
- Exibe a senha em texto plano no campo para visualização
- Copia automaticamente para o clipboard
- O botão vira ✓ verde por 1,5s confirmando a cópia

### Função JS

```javascript
function _gerarSenha(inputId) {
    // usa crypto.getRandomValues — seguro contra previsibilidade
    // copia para clipboard via execCommand('copy')
}
```

### IDs dos inputs afetados

| Modal | Senha | Senha Admin |
|---|---|---|
| Novo Acesso | `novo_senha` | `novo_senha_adm` |
| Editar Acesso | `dup1_senha` | `dup1_senha_adm` |
| Duplicar Acesso | `dup_senha` | `dup_senha_adm` |

---

## Exportação de Credenciais em PDF e TXT (2026-05-27, atualizado 2026-07-29)

### Botão na interface

Visível apenas para **superusuários** (`is_superuser=True`). Aparece ao lado do nome/CNPJ
do cliente no cabeçalho da página. Abre um dropdown com quatro opções (PDF e TXT, cada um
com/sem Senha Root):

| Opção | Parâmetro | Conteúdo |
|---|---|---|
| PDF — Sem Senha Root | `?root=0` | Descrição, Host, Proto, Porta, Usuário, Senha, Função |
| PDF — Com Senha Root | `?root=1` | + coluna Senha Root |
| TXT — Sem Senha Root | `?root=0` | Mesmos campos do PDF, em texto plano |
| TXT — Com Senha Root | `?root=1` | + Senha Root |

### Views

```
GET /clientes/<id>/senhas/pdf/?root=0|1
GET /clientes/<id>/senhas/txt/?root=0|1
```

- Requer `request.user.is_superuser` — retorna 403 caso contrário
- PDF gerado via **ReportLab** com tabela de acessos ordenada por tipo; TXT é texto plano com
  um bloco por acesso (`Descrição/Host/Protocolo/Porta/Usuário/Senha[/Senha Root]/Função`)
- Nome do arquivo: `senhas_<NomeCliente>_sem_root.{pdf,txt}` ou `senhas_<NomeCliente>_com_root.{pdf,txt}`

### Correção — PDF cortando nas laterais (2026-07-29)

O modo "Sem Senha Root" usava A4 **retrato** (18cm úteis de largura, com margens de 1,5cm)
com colunas somando **22,5cm** — a tabela ultrapassava a borda direita da página e o
ReportLab simplesmente cortava host/senha/função fora da área visível. O modo "Com Senha
Root" já usava paisagem e não tinha esse problema. Correção: os dois modos agora usam A4
**paisagem** (26,7cm úteis) e as larguras de coluna foram recalculadas para caber com folga
(26,3cm e 26,6cm respectivamente).

### Arquivos

`clientes/views.py` — funções `exportar_senhas_pdf` e `exportar_senhas_txt`  
`clientes/urls.py` — `path('<int:cliente_id>/senhas/pdf/', ...)` e `path('<int:cliente_id>/senhas/txt/', ...)`

---

## Organização da Aba de Acessos por Função

Os acessos são agrupados no template Django via `{% regroup %}`:

```django
{% regroup acessos|dictsort:"funcao_id" by funcao_id as acessos_por_funcao %}
```

Cada grupo gera uma aba na nav (`#funcaoTabs`) e um painel (`.funcao-content`).  
A aba ativa ao carregar é a primeira; a troca de aba chama `mostrarFuncao(event, funcao_id)`
que também persiste o estado no hash da URL (`#funcao-<id>`).
