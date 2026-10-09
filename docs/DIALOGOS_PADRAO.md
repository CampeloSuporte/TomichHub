# Diálogos padrão da plataforma (confirmação, alerta, aviso)

Desde 09/10/2026 nenhuma tela usa o `confirm()` / `alert()` nativo do navegador — aquela caixa
cinza do sistema operacional, fora do visual do CRM. Tudo passa pelo componente
`static/js/ui_dialogs.js`.

## Onde é carregado

- `templates/base.html` (no `<head>`) — vale para toda página que herda dele;
- páginas standalone que não herdam do base: `atendimento/base.html`, `terminal.html`,
  `bgp_automacao.html`, `scripts/gerenciar.html`, `topologia_editor.html`,
  `racks/rack_builder.html`, `base_clientes.html` (portal).

Página nova que **não** herda do `base.html` precisa incluir:

```html
<script src="/static/js/ui_dialogs.js?v=20261009"></script>
```

O arquivo fica em `static/` (servido direto pelo nginx) e é reincluído no git por negação no
`.gitignore` (`!static/js/ui_dialogs.js`), como os outros JS escritos à mão.

## API

```js
// Confirmação — Promise<boolean>. A função que chama precisa ser async.
if (!await uiConfirm('Excluir este item?')) return;
if (!await uiConfirm({ titulo: 'Excluir despesa', msg: '...', btnLabel: 'Excluir' })) return;
// tipo: 'perigo' (padrão, vermelho) | 'aviso' (laranja) | 'info' (azul, ações não destrutivas)
await uiConfirm({ titulo: 'Enviar cobrança', msg: '...', btnLabel: 'Enviar', tipo: 'info' });

// Alerta — Promise<void>. String solta: título/ícone deduzidos do texto
// ("erro/falha" → Erro, "✓/sucesso" → Sucesso, resto → Atenção).
uiAlert('Erro: falha ao salvar');
uiAlert('Grupo vinculado!').then(() => location.reload());   // espera o OK antes de recarregar
uiAlert({ titulo: 'Detalhes', msgHtml: '<table>…</table>' }); // HTML montado pela página, nunca texto do usuário

// Aviso rápido no canto (some sozinho)
uiToast('Salvo!');            // 'ok' | 'info' | 'erro'
```

### Sem JavaScript: `data-confirm`

Para `<form>` (e `<a>` / `<button>` que não seja submit) basta o atributo — o componente
intercepta o envio, pergunta e só então envia:

```html
<form method="post" action="..." data-confirm="O cliente X fica inativo..."
      data-confirm-titulo="Desativar cliente" data-confirm-btn="Desativar" data-confirm-tipo="aviso">
```

Use isso no lugar de `onsubmit="return confirm(...)"`. O texto vai como atributo HTML, então
usa o escape normal do Django (`{{ var }}`), não `escapejs`.

## Comportamento

- Um diálogo por vez: chamadas seguidas entram em fila (dois `uiAlert` em sequência aparecem
  um depois do outro, nenhum some por cima do outro).
- Teclado: **Esc** cancela (ou fecha o alerta), **Tab** fica preso nos botões, Enter aciona o
  botão focado. Na confirmação `perigo` o foco começa no **Cancelar** — Enter não apaga por engano.
  Enquanto o diálogo está aberto, os atalhos da página (Delete no mapa etc.) não disparam.
- Clique fora da caixa = cancelar.
- `z-index` 1000000 e anexado ao fim do `<body>`: fica acima de qualquer modal de página.
- `window.alert` é redirecionado para `uiAlert` (rede de segurança). `window.confirm` não dá para
  substituir — é síncrono —, então **não use `confirm()`**: use `await uiConfirm(...)`.

## Diferença para o `alert()` nativo

O nativo trava o script até o OK; o `uiAlert` não. Se depois do alerta vem `location.reload()` ou
um redirecionamento, encadeie: `uiAlert(msg).then(() => location.reload())` ou `await uiAlert(msg)`
— senão a página recarrega e a mensagem some.
