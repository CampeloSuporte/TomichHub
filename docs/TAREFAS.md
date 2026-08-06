# ✅ Sistema de Tarefas — Documentação Técnica

## 📋 Visão Geral

To-do do back-office, opcionalmente vinculado a um Cliente. Qualquer atendente (Administrador/Consultor/Operador) pode criar, assumir e reatribuir tarefas dentro do próprio escopo de instância; o Administrador da plataforma vê e age em todas.

**Última atualização:** 06/08/2026
**Status:** ✅ FUNCIONAL
**Stack:** Django, painel embutido no dashboard (`quadro_geral` / `quadro_instancia`)

---

## 🗂️ Modelo `Tarefa` (app `tarefas`)

```
titulo, descricao, cliente (FK Cliente, opcional), instancia (FK Instancia, opcional),
status (pendente/andamento/concluida/cancelada), prioridade (baixa/media/alta),
prazo, assigned_to (FK User), criado_por (FK User), criado_em, atualizado_em, concluida_em
```

- `instancia` é derivada automaticamente: do `cliente.instancia` quando há cliente, ou da instância de quem criou (Consultor/Operador) quando não há. Tarefa sem cliente criada pelo Administrador fica com `instancia=None` — tarefa de plataforma, só ele vê.
- `atrasada` (property): `prazo` vencido e status ainda não é `concluida`/`cancelada`.

## 🔒 Visibilidade e permissões

`Tarefa.objects.visiveis_para(user)` (mesmo padrão de `Cliente.objects.visiveis_para`):
- **Administrador**: vê e age em qualquer tarefa.
- **Consultor/Operador**: só as tarefas da própria instância (`usuario.perms.get_instancia`).
- Dentro do escopo visível, **qualquer** back-office pode assumir uma tarefa sem responsável, reatribuir uma já assumida, ou mudar status — não há hierarquia extra entre os papéis.
- Views usam `get_object_or_404(Tarefa.objects.visiveis_para(request.user), pk=...)`: uma tarefa fora do escopo dá 404, nunca 403 (não revela que ela existe em outra instância).

`tarefas/services.py::usuarios_atribuiveis(instancia)` — quem aparece no seletor "Responsável" do modal de edição: usuários com `PerfilUsuario` daquela instância, ou (instância vazia = tarefa de plataforma) só Administradores.

## 🖥️ Painel no Dashboard

Incluído via `{% include 'tarefas/_painel.html' %}` em `home/templates/quadro_geral.html` — usado tanto pelo dashboard do Administrador (`quadro_geral`) quanto pelo do Consultor/Operador (`quadro_instancia`, mesma template). Contexto montado por `home/views.py::_contexto_tarefas(request)`.

Seções do painel:
- Contadores: Pendentes / Em Andamento / Atrasadas / Concluídas Hoje.
- **Atrasadas** em destaque (borda vermelha), sempre no topo quando há alguma.
- **Minhas Tarefas** — atribuídas ao usuário logado.
- **Não Assumidas** — sem responsável, com botão "Assumir".
- Modal "Nova Tarefa" e modal "Editar Tarefa" (reaproveitado por todas as linhas via `data-*` attributes + JS) — sem página dedicada, tudo dentro do próprio painel.

## 🔌 Endpoints (`/tarefas/...`)

| Rota | Método | Descrição |
|---|---|---|
| `criar/` | POST | Cria tarefa (form do modal "Nova Tarefa") |
| `<id>/editar/` | POST | Atualiza título/descrição/cliente/prazo/prioridade/status/responsável (form completo do modal "Editar") |
| `<id>/assumir/` | POST | Auto-atribuição de um clique; se estava pendente, vira "Em Andamento" |
| `<id>/status/` | POST | Mudança rápida de status sem abrir modal |
| `<id>/usuarios/` | GET (JSON) | Lista de usuários elegíveis pro seletor "Responsável", escopada à instância da tarefa |

Todas as views (exceto `usuarios/`, que é GET) fazem `redirect` de volta pra `next` (ou `HTTP_REFERER`) — sem API JSON para as ações de escrita, mesmo padrão de formulário simples usado no `financeiro/dashboard.html`.

## 🚧 Fora de escopo (não implementado)

- Notificação (WhatsApp/e-mail) de tarefa atrasada — hoje é só destaque visual no painel.
- Aba "Tarefas" na tela do cliente.
- Kanban ou relatórios de tarefas.

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

**Correção:** guard explícito antes de acessar os atributos:

```django
{% if t.assigned_to %}{{ t.assigned_to.get_full_name|default:t.assigned_to.username }}{% else %}—{% endif %}
```

**Lição:** nunca usar uma variável encadeada (`a.b.c`) como argumento de `|default:`
sem garantir antes que `a.b` não é `None` — o filtro não protege contra isso. Mesmo
padrão latente corrigido em `wiki/visualizar_artigo.html` (ver
[WIKI_ARTIGOS.md](WIKI_ARTIGOS.md#correção--mesmo-crash-latente-em-criado_por-2026-08-06)).
