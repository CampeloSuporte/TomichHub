# ✅ Sistema de Tarefas — Documentação Técnica

## 📋 Visão Geral

To-do do back-office, opcionalmente vinculado a um Cliente. Qualquer atendente (Administrador/Consultor/Operador) pode criar, assumir e reatribuir tarefas dentro do próprio escopo de instância; o Administrador da plataforma vê e age em todas. Desde 18/08/2026 também tem um **Kanban por cliente** (aba "Tarefas" em `listar.html`), acessível ao portal do cliente final para as próprias tarefas.

**Última atualização:** 18/08/2026
**Status:** ✅ FUNCIONAL
**Stack:** Django, painel embutido no dashboard (`quadro_geral` / `quadro_instancia`) + Kanban na página do cliente (SortableJS via CDN)

---

## 🗂️ Modelo `Tarefa` (app `tarefas`)

```
titulo, descricao, cliente (FK Cliente, opcional), instancia (FK Instancia, opcional),
status (pendente/andamento/concluida/cancelada), prioridade (baixa/media/alta),
prazo, responsaveis (M2M User), criado_por (FK User), criado_em, atualizado_em, concluida_em
```

- `instancia` é derivada automaticamente: do `cliente.instancia` quando há cliente, ou da instância de quem criou (Consultor/Operador) quando não há. Tarefa sem cliente criada pelo Administrador fica com `instancia=None` — tarefa de plataforma, só ele vê.
- `atrasada` (property): `prazo` vencido e status ainda não é `concluida`/`cancelada`. **Não é um status gravado** — é recalculada a cada leitura, a partir do `prazo` e do `status` atual (ver seção Kanban).
- `Tarefa.objects.do_cliente(cliente)`: tarefas de um cliente específico, com `select_related('criado_por')` + `prefetch_related('responsaveis')` — base do Kanban.

### Migração de `assigned_to` (FK único) para `responsaveis` (M2M) — 18/08/2026

Até 18/08/2026 uma tarefa tinha um único responsável (`assigned_to`, `ForeignKey`). O usuário pediu suporte a **múltiplos responsáveis por tarefa**. Migração em 3 passos
(`tarefas/migrations/0002_remove_tarefa_tarefas_tar_assigne_587bff_idx_and_more.py`) pra não perder dado:

1. `AddField responsaveis` (M2M, `blank=True`, `related_name='tarefas_responsavel'`).
2. `RunPython`: copia `assigned_to_id` de cada tarefa pra dentro do M2M novo (reversível: no rollback, pega o primeiro `responsaveis` por id e devolve pro FK único — perde info se a tarefa ganhou mais de um responsável depois da migração, mas é best-effort documentado no próprio arquivo).
3. `RemoveIndex` + `RemoveField` do `assigned_to` antigo.

Todo código que fazia `tarefa.assigned_to = user; tarefa.save()` virou `tarefa.responsaveis.add(user)` (aditivo) ou `tarefa.responsaveis.set([...])` (substitui a lista inteira) — **M2M não passa por `save()`**, as mudanças em `responsaveis` são gravadas na hora, independente de quando o `save()` do resto dos campos acontece.

`tarefa_assumir` (botão "Assumir") agora **adiciona** o usuário à lista em vez de substituir — várias pessoas podem assumir a mesma tarefa clicando cada uma a própria vez.

## 🔒 Visibilidade e permissões

`Tarefa.objects.visiveis_para(user)` (mesmo padrão de `Cliente.objects.visiveis_para`):
- **Administrador**: vê e age em qualquer tarefa.
- **Consultor/Operador**: só as tarefas da própria instância (`usuario.perms.get_instancia`).
- Dentro do escopo visível, **qualquer** back-office pode assumir uma tarefa sem responsável, reatribuir uma já assumida, ou mudar status — não há hierarquia extra entre os papéis.
- Views usam `get_object_or_404(Tarefa.objects.visiveis_para(request.user), pk=...)`: uma tarefa fora do escopo dá 404, nunca 403 (não revela que ela existe em outra instância).

### `usuarios_atribuiveis(cliente)` — reescrita em 18/08/2026 (bug de escopo)

**Sintoma relatado:** no seletor de "Responsável", apareciam contas de admin que não deveriam
(`adm_466dee`, `admweb_2211cb`, `adm_6cb110` — sobra de cadastro de instância de teste, sem
e-mail, nunca correspondeu a uma pessoa real).

**Causa:** a assinatura antiga era `usuarios_atribuiveis(instancia)`. Pra tarefa de cliente **sem**
instância (cliente direto da plataforma, sem revenda), o código caía em
`User.objects.filter(Q(perfil__role='admin') | Q(is_staff=True, perfil__isnull=True))` —
**todo** admin legado da plataforma inteira, sem filtro nenhum, inclusive contas de teste.

**Correção** (`tarefas/services.py`): assinatura passou a `usuarios_atribuiveis(cliente)`
(recebe o cliente, não só a instância), e o resultado é a união de 3 grupos:

```python
def usuarios_atribuiveis(cliente):
    admins = User.objects.filter(is_active=True).filter(
        Q(perfil__role='admin') | Q(is_staff=True, perfil__isnull=True)
    ).exclude(email='')                              # exclui conta sem e-mail (sobra de cadastro)

    instancia = cliente.instancia if cliente is not None else None
    atendentes = User.objects.filter(is_active=True, perfil__instancia=instancia) \
        if instancia is not None else User.objects.none()

    portal = User.objects.filter(is_active=True).filter(
        Q(cliente=cliente) | Q(clientes_adicionais=cliente)
    ) if cliente is not None else User.objects.none()

    return (admins | atendentes | portal).distinct().order_by('first_name', 'username')
```

- **Administradores reais** — sempre, qualquer cliente, filtrados por `email` não-vazio (o mesmo
  filtro anti-"conta fantasma" que `atendimento.views.api_agents_list` já usava pro seletor de
  transferir chamado).
- **Atendentes** (Consultor/Operador) da instância do cliente, se houver.
- **Usuário(s) de portal vinculado(s)** ao próprio cliente (`Cliente.usuario` +
  `Cliente.usuarios_adicionais`) — pra que o cliente final também possa participar do vínculo.

Todo caller que passava `tarefa.instancia` passou a passar `tarefa.cliente` (`_aplicar_responsaveis`,
`tarefa_usuarios_json`, as views do Kanban).

## 🖥️ Painel no Dashboard

Incluído via `{% include 'tarefas/_painel.html' %}` em `home/templates/quadro_geral.html` — usado tanto pelo dashboard do Administrador (`quadro_geral`) quanto pelo do Consultor/Operador (`quadro_instancia`, mesma template). Contexto montado por `home/views.py::_contexto_tarefas(request)`.

Seções do painel:
- Contadores: Pendentes / Em Andamento / Atrasadas / Concluídas Hoje.
- **Atrasadas** em destaque (borda vermelha), sempre no topo quando há alguma.
- **Minhas Tarefas** — `em_aberto.filter(responsaveis=request.user)`.
- **Não Assumidas** — `em_aberto.filter(responsaveis__isnull=True)`, com botão "Assumir".
- Modal "Nova Tarefa" e modal "Editar Tarefa" (reaproveitado por todas as linhas via `data-*`
  attributes + JS) — sem página dedicada, tudo dentro do próprio painel. O seletor de
  "Responsáveis" (`<select multiple>`) é populado a partir de `tarefa_usuarios_json`, que agora
  também devolve `responsaveis_ids` prontos (a linha da tabela não precisa mais carregar
  `data-assigned-to-id`, evita ficar sincronizando esse dado em dois lugares).

## 🗂️ Kanban por cliente — novo em 18/08/2026

Aba **"Tarefas"** em `clientes/templates/listar.html` (`tab-tarefas`), gated por
`modulos_habilitados.tarefas` (`UsuarioModulo.MODULO_CHOICES` + `InstanciaFerramenta.FERRAMENTA_CHOICES`,
chave `'tarefas'` em ambos). Reaproveita o mesmo model `Tarefa` — nada duplicado.

### Colunas

5 colunas, a primeira **calculada** (não é um status gravado):

| Coluna | Origem |
|---|---|
| **Atrasada** | `t.atrasada == True` (prazo vencido + status pendente/andamento) — nunca aparece também na coluna do seu status real |
| Pendente | `status == 'pendente' and not atrasada` |
| Em Andamento | `status == 'andamento' and not atrasada` |
| Concluída | `status == 'concluida'` |
| Cancelada | `status == 'cancelada'` |

Drag-and-drop via **SortableJS** (`cdn.jsdelivr.net`, já liberado no `script-src` da CSP de
`crm/middleware.py`). A coluna "Atrasada" usa `group: {put: false}` — dá pra arrastar um cartão
**pra fora** dela (ex: marcar como Concluída), mas não dá pra soltar um cartão **dentro** dela
manualmente, porque ela não é um status real; sair da lista de atrasadas só acontece completando,
cancelando, ou adiando o prazo.

Ao mover um cartão, o front-end sincroniza com a tarefa completa devolvida pelo backend (não só o
`status`) — necessário porque `atrasada` também muda (ex: mover um cartão atrasado pra "Concluída"
tem que limpar o `atrasada` local, senão o cartão "sumia" da tela por ficar filtrado nas duas
listas ao mesmo tempo).

### Endpoints (`/tarefas/kanban/...`)

| Rota | Método | Descrição |
|---|---|---|
| `<cliente_id>/` | GET (JSON) | Lista tarefas do cliente + `responsaveis` elegíveis (só se `is_backoffice`) |
| `<cliente_id>/criar/` | POST (JSON) | Cria tarefa vinculada ao cliente |
| `mover/<tarefa_id>/` | POST (JSON) | Só muda o `status` (drag-and-drop) |
| `editar/<tarefa_id>/` | POST (JSON) | Atualiza título/descrição/prazo/prioridade/responsáveis |
| `excluir/<tarefa_id>/` | POST (JSON) | Exclui — só backoffice ou quem criou |

Permissão: `cliente_can_view_cliente` (listar/criar) + `pode_acessar_cliente` verificado
manualmente contra o cliente da tarefa (mover/editar/excluir, já que a URL não carrega o
`cliente_id`) — quem pode ver o cliente pode ver e criar tarefas dele; **o portal do cliente
final também pode**, diferente das views do dashboard (`@backoffice_required`). Só back-office
designa responsável — o campo nem aparece pro portal.

Arrastar pra "Em Andamento" sem ninguém responsável = quem arrastou entra como responsável
automaticamente (mesmo comportamento do botão "Assumir").

### Campo `responsaveis` no Kanban — `<select multiple>`

Tanto o modal de edição do Kanban quanto o do dashboard usam `<select multiple>`. Um detalhe de
HTML forms: **um `<select multiple>` sem nenhuma opção marcada não manda a própria chave no
POST** — não dá pra distinguir "quero limpar todo mundo" de "campo nem veio nesse form" só olhando
se a chave existe. O Kanban resolve isso com um campo oculto sentinela
(`responsaveis_form_present=1`) sempre enviado; o backend só mexe em `responsaveis` quando esse
marcador está presente:

```python
if is_backoffice(request.user) and 'responsaveis_form_present' in request.POST:
    _aplicar_responsaveis(tarefa, request.POST.getlist('responsaveis'), tarefa.cliente)
```

O form do dashboard não precisa do marcador porque ali o campo é sempre a fonte de verdade da
edição inteira (mesmo padrão de `titulo`/`descricao`, sempre sobrescritos a cada submit).

## 🔌 Endpoints do Dashboard (`/tarefas/...`)

| Rota | Método | Descrição |
|---|---|---|
| `criar/` | POST | Cria tarefa (form do modal "Nova Tarefa") |
| `<id>/editar/` | POST | Atualiza título/descrição/cliente/prazo/prioridade/status/**responsaveis** (`getlist`) |
| `<id>/assumir/` | POST | Adiciona o usuário aos responsáveis (aditivo); se estava pendente, vira "Em Andamento" |
| `<id>/status/` | POST | Mudança rápida de status sem abrir modal |
| `<id>/usuarios/` | GET (JSON) | `{results, responsaveis_ids}` — elegíveis pro seletor + quem já é responsável |

Todas as views de escrita (exceto `usuarios/`, GET) fazem `redirect` de volta pra `next` (ou
`HTTP_REFERER`) — sem API JSON, mesmo padrão de formulário simples do dashboard (diferente do
Kanban, que é JSON puro).

## 🐛 Correção — naive datetime no campo `prazo` (18/08/2026)

**Sintoma:** criar/editar uma tarefa com prazo preenchido (Kanban **ou** dashboard) estourava
`TypeError: can't compare offset-naive and offset-aware datetimes` na primeira leitura de
`Tarefa.atrasada` (ex: logo depois de criar, ao montar o JSON de resposta do Kanban).

**Causa:** `<input type="datetime-local">` sempre manda a data **sem timezone**.
`parse_datetime(request.POST.get('prazo'))` devolve um `datetime` naive, salvo assim no campo;
com `USE_TZ=True`, comparar `timezone.now()` (aware) contra esse `prazo` (naive) quebra.
Latente desde sempre no form do dashboard — só ficou visível quando o Kanban passou a calcular
`atrasada` na mesma resposta da criação/edição (o dashboard nunca fazia isso, só redirecionava).

**Correção** (`tarefas/views.py`), helper único usado nos 4 pontos que processam `prazo`:

```python
def _parse_prazo(valor):
    if not valor:
        return None
    dt = parse_datetime(valor)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt
```

## 🚧 Fora de escopo (não implementado)

- Notificação (WhatsApp/e-mail) de tarefa atrasada — hoje é só destaque visual (painel + coluna do Kanban).
- Relatórios/exportação de tarefas.
- Reordenar manualmente dentro de uma mesma coluna do Kanban (não há campo `ordem`; a ordenação
  é sempre por prioridade/prazo/criação, vinda do backend).

## 🐛 Correção — `VariableDoesNotExist` em `/homegeral` com tarefa sem responsável (2026-08-06)

Uma tarefa sem `assigned_to` (não assumida) derrubava o dashboard inteiro com
`VariableDoesNotExist: Failed lookup for key [username] in None`, em
`tarefas/_linha.html`:

```django
{{ t.assigned_to.get_full_name|default:t.assigned_to.username|default:"—" }}
```

**Causa:** `t.assigned_to.username` é o *argumento* do filtro `default`, não a
variável principal da tag `{{ }}`. Django só suprime `VariableDoesNotExist` (caindo
pra `string_if_invalid`) na resolução da variável principal — a resolução do
argumento de um filtro não passa por esse tratamento e propaga a exceção crua. Com
`assigned_to=None`, o lookup de `.username` em `None` explode e derruba a página
inteira em vez de simplesmente cair no `default:"—"` esperado.

**Correção:** guard explícito antes de acessar os atributos (hoje adaptado pra
`responsaveis`, um loop no lugar de um único acesso):

```django
{% for r in t.responsaveis.all %}{{ r.get_full_name|default:r.username }}{% if not forloop.last %}, {% endif %}{% empty %}—{% endfor %}
```

**Lição:** nunca usar uma variável encadeada (`a.b.c`) como argumento de `|default:`
sem garantir antes que `a.b` não é `None` — o filtro não protege contra isso. Mesmo
padrão latente corrigido em `wiki/visualizar_artigo.html` (ver
[WIKI_ARTIGOS.md](WIKI_ARTIGOS.md#correção--mesmo-crash-latente-em-criado_por-2026-08-06)).
