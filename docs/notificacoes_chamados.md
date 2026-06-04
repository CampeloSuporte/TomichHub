# 🔔 Notificações de Chamados em Aberto

**Implementado em:** 02/06/2026
**Status:** ✅ Funcional
**Escopo:** `templates/base.html`, `atendimento/templates/atendimento/base.html`

---

## Problema Resolvido

Atendentes podiam estar em uma conversa já assumida ou em outra seção do CRM
(Clientes, Financeiro, Monitoramento) e **não percebiam** que um novo chamado em aberto
havia chegado de outro grupo. A notificação por WhatsApp (task `notificar_chamados_abertos`)
só rodava a cada 10 minutos e não era imediata.

---

## Solução Implementada

### Contexto 1 — Usuário dentro do módulo de Atendimento

**Arquivo:** `atendimento/templates/atendimento/base.html`

O JavaScript do base já ouvia o WebSocket `/ws/atendimento/inbox/`. A mudança adicionou
**diferenciação visual** ao toast para chamados em aberto:

#### CSS adicionado (após `.msg-toast:hover`)

```css
.msg-toast-ticket {
    border-color: rgba(248,81,73,0.5);
    background: rgba(248,81,73,0.08);
    animation: toastIn 0.18s ease, ticketPulse 1.2s ease-in-out 0.2s 2;
}
.msg-toast-ticket:hover { border-color: #f85149; }
@keyframes ticketPulse {
    0%,100% { box-shadow: 0 0 0 0 rgba(248,81,73,0); }
    50%      { box-shadow: 0 0 0 5px rgba(248,81,73,0.25); }
}
.msg-toast-icon-ticket {
    background: rgba(248,81,73,0.15); color: #f85149;
    border-radius: 50%; display: flex;
    align-items: center; justify-content: center;
    width: 36px; height: 36px; flex-shrink: 0; font-size: 14px;
}
.msg-toast-label {
    font-size: 9px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.8px; color: #f85149; margin-bottom: 2px;
}
```

#### Função `showToast` alterada

**Antes:**
```javascript
function showToast(groupName, msgText, convId, initials) {
    var toast = document.createElement('div');
    toast.className = 'msg-toast';
    // ícone: iniciais do grupo
    toast.innerHTML = '<div class="msg-toast-icon">' + initials + '</div>' + ...
}
// Chamada:
showToast(groupName, msgContent, convId, initials);
```

**Depois:**
```javascript
function showToast(groupName, msgText, convId, initials, isNewTicket) {
    var toast = document.createElement('div');
    toast.className = 'msg-toast' + (isNewTicket ? ' msg-toast-ticket' : '');

    var iconHtml = isNewTicket
        ? '<div class="msg-toast-icon msg-toast-icon-ticket"><i class="fas fa-bell"></i></div>'
        : '<div class="msg-toast-icon">' + initials + '</div>';
    var labelHtml = isNewTicket
        ? '<div class="msg-toast-label">Novo chamado em aberto</div>'
        : '';
    // ...
}
// Chamada:
showToast(groupName, msgContent, convId, initials, isUnassigned);
```

`isUnassigned` já existia no código como `var isUnassigned = !assignedTo;`.

---

### Contexto 2 — Usuário fora do módulo de Atendimento

**Arquivo:** `templates/base.html`

#### Alterações no botão do nav

```html
<!-- Antes -->
<a href="{% url 'atendimento:dashboard' %}" class="btn btn-sm">
    <i class="fas fa-headset me-1"></i> Atendimento
</a>

<!-- Depois -->
<a href="{% url 'atendimento:dashboard' %}" class="btn btn-sm"
   id="globalAtendBtn" style="position:relative;">
    <i class="fas fa-headset me-1"></i> Atendimento
</a>
```

O `id` e `position:relative` permitem que o badge numérico seja posicionado com
`position:absolute` no canto superior direito do botão.

#### Bloco adicionado antes de `</html>`

Envolvido em `{% if request.user.is_staff %}` para não carregar para usuários comuns.

**Container:**
```html
<div id="globalTicketToasts" style="
    position:fixed; bottom:28px; right:28px; z-index:99990;
    display:flex; flex-direction:column; gap:10px; pointer-events:none;
"></div>
```

**CSS das classes `.gtkt-*`** (inlinado no `<style>` do bloco):
- `.gtkt-toast` — container do toast com borda vermelha e animação
- `.gtkt-icon` — círculo vermelho com ícone de sino
- `.gtkt-label` — "NOVO CHAMADO EM ABERTO" em caixa alta
- `.gtkt-group` — nome do grupo em branco
- `.gtkt-sub` — "Clique para atender" em cinza
- `.gtkt-close` — botão ✕ para fechar
- `@keyframes gtktIn` — entrada deslizante de baixo
- `@keyframes gtktPulse` — pulso de glow vermelho (2×)

**Script JS — funções:**

| Função | Parâmetros | Descrição |
|--------|-----------|-----------|
| `connect()` | — | Abre WebSocket `/ws/atendimento/inbox/`, reconecta com backoff |
| `showToast(groupName, convId)` | string, string | Cria e exibe toast, chama badge e som |
| `updateBadge(delta)` | +1 ou -1 | Cria/atualiza/remove badge no botão nav |
| `dismissToast(el)` | elemento DOM | Remove toast com fade + chama `updateBadge(-1)` |
| `playTick()` | — | Emite beep duplo via Web Audio API |

**Lógica de filtragem no `onmessage`:**
```javascript
ws.onmessage = function(e) {
    var p = JSON.parse(e.data);
    if (p.type !== 'new_message') return;           // só new_message
    if (p.message.sender_type === 'agent') return;  // ignora echo do atendente
    var conv = p.conversation;
    if (conv.assigned_to_id) return;                // só chamados SEM atendente
    showToast(conv.group_name, conv.id);
};
```

---

## Comportamento por Cenário

| Cenário | Toast | Badge | Som | Notif. Browser |
|---------|-------|-------|-----|----------------|
| Usuário na conversa assumida, chega novo aberto | ✅ Vermelho | ✅ Nav inbox | ✅ | ✅ |
| Usuário na caixa de entrada, chega novo aberto | ✅ Vermelho | ✅ Nav inbox | ✅ | ✅ |
| Usuário em Clientes/Financeiro, chega novo aberto | ✅ Vermelho global | ✅ Nav global | ✅ | ✅ |
| Chega mensagem na conversa do próprio usuário | ✅ Azul/cinza | — | ✅ mensagem | ✅ |
| Chega mensagem em conversa de outro atendente | ❌ (filtrado) | — | — | — |

---

## Arquivos Modificados

| Arquivo | Tipo de Mudança |
|---------|----------------|
| `templates/base.html` | Adicionado `id` no botão nav + bloco completo de notificação global |
| `atendimento/templates/atendimento/base.html` | CSS de `.msg-toast-ticket`, assinatura de `showToast()` |

Nenhuma alteração em models, views, migrations ou URLs.

---

## Manutenção

### Desabilitar notificação global
Remover ou comentar o bloco `{% if request.user.is_staff %}...{% endif %}` em `templates/base.html`.

### Ajustar tempo de auto-dismiss
No script global (`templates/base.html`):
```javascript
el._timer = setTimeout(function(){ dismissToast(el); }, 10000); // 10s → ajustar aqui
```
No atendimento (`atendimento/templates/atendimento/base.html`):
```javascript
var timer = setTimeout(function(){ dismissToast(toast); }, 5000); // 5s → ajustar aqui
```

### Ajustar cor do toast de chamado aberto
Procurar `rgba(248,81,73` nos dois arquivos e substituir pela cor desejada (`#f85149` = vermelho).

---

## Referências

- **WebSocket consumer:** `atendimento/consumers.py` — `InboxConsumer`
- **Envio WS:** `atendimento/services.py` — `_ws_send_inbox()`
- **Notificação WhatsApp (periódica):** `atendimento/tasks.py` — `notificar_chamados_abertos()`
- **Base atendimento:** `atendimento/templates/atendimento/base.html`
- **Base global:** `templates/base.html`
