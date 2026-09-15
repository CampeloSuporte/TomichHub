# ✅ Sistema de Tarefas — Documentação Técnica

## 📋 Visão Geral

To-do do back-office, opcionalmente vinculado a um Cliente. Qualquer atendente (Administrador/Consultor/Operador) pode criar, assumir e reatribuir tarefas dentro do próprio escopo de instância; o Administrador da plataforma vê e age em todas.

**Última atualização:** 15/09/2026 (rotinas mensais com checklist — ver [seção](#rotinas-mensais-com-checklist-2026-09-15))
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
- **Minhas Tarefas** — atribuídas ao usuário logado. O **+ Adicionar tarefa** no fim da lista já cria a tarefa com quem clicou como responsável.
- **Não Assumidas** — sem responsável, com botão "Assumir" e **+ Adicionar tarefa** no fim.
- **Rotinas mensais** — ver seção própria abaixo.
- Modal "Nova Tarefa" (com a opção **Tarefa única / Rotina mensal**) e modal "Editar Tarefa" (reaproveitado por todas as linhas via `data-*` attributes + JS) — sem página dedicada, tudo dentro do próprio painel. Desde 15/09/2026 não há mais botão "Nova Tarefa" no cabeçalho do card: a criação fica dentro das listas.
- Botão **Excluir** em cada linha, com confirmação via `uiConfirm` (definido em `templates/base.html`).

## 🔌 Endpoints (`/tarefas/...`)

| Rota | Método | Descrição |
|---|---|---|
| `criar/` | POST | Cria tarefa (form do modal "Nova Tarefa") |
| `<id>/editar/` | POST | Atualiza título/descrição/cliente/prazo/prioridade/status/responsável (form completo do modal "Editar") |
| `<id>/assumir/` | POST | Auto-atribuição de um clique; se estava pendente, vira "Em Andamento" |
| `<id>/status/` | POST | Mudança rápida de status sem abrir modal |
| `<id>/excluir/` | POST | Exclui a tarefa (botão "Excluir" da linha) |
| `<id>/usuarios/` | GET (JSON) | Lista de usuários elegíveis pro seletor "Responsável", escopada à instância da tarefa; traz também `status` e o `checklist` que o modal mostra |
| `checklist/<item_id>/marcar/` | POST (JSON) | Marca/desmarca item (`verificado=1/0`); devolve item, `feitos`, `total`, `status` e a tarefa no formato do Kanban |
| `rotinas/criar/` | POST | Cria rotina (título, `dia_do_mes`, `itens` repetido, prioridade, cliente, `atribuir_a_mim`) |
| `rotinas/<id>/editar/` | POST | Altera a rotina; vale da próxima ocorrência em diante |
| `rotinas/<id>/ativar/` | POST | Pausa/retoma (alterna) |
| `rotinas/<id>/excluir/` | POST | Apaga a rotina; tarefas já geradas ficam |
| `rotinas/<id>/usuarios/` | GET (JSON) | Responsáveis elegíveis + itens atuais, para o modal "Editar Rotina" |

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


---

## Exclusão de tarefa pelo painel (2026-08-21)

Antes só dava pra excluir tarefa pelo **kanban da página do cliente**
(`tarefa_kanban_excluir`, aba "Tarefas" de `clientes/listar.html`). O painel do
dashboard tinha "Assumir" e "Editar", mas nenhuma forma de apagar — e o kanban não
cobria o buraco, porque ele lista tarefa **por cliente**: uma tarefa de plataforma
(`cliente = NULL`, criada pelo modal "Nova Tarefa" sem escolher cliente) não aparecia
em kanban nenhum e ficava impossível de excluir por qualquer caminho.

`tarefas.views.tarefa_excluir` fecha isso:

- `@backoffice_required` + `@require_POST`. O painel só é renderizado pro back-office
  (`quadro_geral` / `quadro_instancia`); e exigir POST evita que prefetch de link do
  navegador apague tarefa.
- Escopo por `_get_tarefa_no_escopo` → `Tarefa.objects.visiveis_para(user)`, que dá
  **404** (não 403) fora da instância: não revela que a tarefa existe do outro lado.
- A checagem **não** passa por `pode_acessar_cliente`, ao contrário do
  `tarefa_kanban_excluir` — é justamente o que permitiria excluir a tarefa sem cliente.
  Quem escopa é o manager.
- Regra de quem pode: back-office exclui qualquer tarefa que enxerga, mesma semântica
  já usada no kanban (lá o portal do cliente final só apaga o que ele mesmo criou; aqui
  o portal nem chega, o painel é de back-office).

`tarefas/tests.py::ExcluirTarefaTest` cobre: Consultor exclui da própria instância,
exclui tarefa sem cliente, **não** exclui de outra instância (404), Administrador
exclui de qualquer uma, login de portal não exclui, GET devolve 405 e o botão aparece
no painel.

---

## Rotinas mensais com checklist (2026-09-15)

Tarefas que se repetem todo mês num dia fixo ("dia 10: conferir backups, OLT,
concentrador") e são feitas marcando cada item como **verificado**.

### Modelo

```
Rotina              titulo, descricao, cliente, instancia, dia_do_mes (1–31), prioridade,
                    responsaveis (M2M), ativa, inicio, ultima_competencia, criado_por
RotinaItem          rotina → itens do checklist-modelo (texto, ordem)
Tarefa (+)          rotina (FK, SET_NULL), competencia (1º dia do mês da ocorrência)
TarefaChecklistItem tarefa → checklist (texto, ordem, verificado, verificado_por, verificado_em)
```

A rotina é só o **modelo**. No dia configurado, `services.gerar_ocorrencias_rotinas`
cria uma **Tarefa comum**: prazo às 23:59 do dia, responsáveis e checklist copiados.
Por ser uma Tarefa, ela entra sozinha em Pendentes/Atrasadas/Minhas/Não Assumidas e no
Kanban do cliente, sem caminho paralelo.

### Regras de geração

- **Quando:** beat `tarefas-gerar-rotinas-mensais` (`crontab(minute=5)`, de hora em hora)
  e logo depois de criar, editar ou retomar a rotina. Rotina criada com o dia de hoje já
  gera na hora.
- **Uma por mês:** controlado por `Rotina.ultima_competencia`, com `select_for_update`.
  Não é pela existência da tarefa, de propósito: se alguém excluir a tarefa do mês, ela
  **não** volta na hora seguinte. A constraint `tarefa_uma_ocorrencia_por_mes`
  (rotina + competencia) é a segunda trava.
- **Dia 29/30/31:** em mês mais curto cai no último dia (`Rotina.data_no_mes`).
- **Sem ocorrência vencida:** não gera data anterior a `Rotina.inicio` (default: dia da
  criação). Criada no dia 15 com dia 10 → a primeira é no mês seguinte. **Retomar** uma
  pausada zera `inicio` para hoje pelo mesmo motivo.
- **Sem retroativo:** se o worker ficou parado o mês inteiro, aquele mês fica sem
  ocorrência, em vez de despejar tarefas velhas.
- **Editar a rotina** não mexe na tarefa já gerada: o checklist dela é uma cópia, então
  nada do que já foi verificado some. Vale da próxima em diante.
- **Excluir a rotina** mantém as tarefas geradas (`rotina = NULL`), com checklist e histórico.

### Checklist (`services.marcar_item_checklist`)

- Grava `verificado_por`/`verificado_em`; desmarcar limpa os dois.
- Primeiro item marcado numa **Pendente** → **Em Andamento**, e quem marcou assume se
  ninguém tinha assumido (mesma regra de arrastar no Kanban).
- Todos marcados → **Concluída** (`concluida_em`). Desmarcar um item de uma concluída →
  volta para **Em Andamento**. **Cancelada** não muda de status.
- Permissão (`_pode_mexer_na_tarefa`): back-office pelo escopo de instância (vale para
  tarefa sem cliente); portal do cliente final só na tarefa do próprio cliente e com o
  módulo Tarefas liberado no login, igual ao Kanban. Fora disso: **404**.

### Onde aparece

- **Dashboard → Rotinas mensais** (`home/views.py::_contexto_rotinas`): cada rotina com
  "Todo dia N", cliente, responsáveis e o estado do mês: **Agendada · dd/mm**,
  **Vence dd/mm** (barra + `2/4`), **Atrasada**, **Concluída** ou **Pausada**. A tarefa em
  aberto já vem expandida com o checklist para marcar. Se a do mês ainda não foi criada e
  a do mês anterior ficou em aberto, mostra essa. Agendada mostra a prévia dos itens.
  Ordem: o que pede ação agora, depois agendadas por data, pausadas por último.
  Ações: editar, pausar/retomar, excluir.
- **Linhas das listas**: selo roxo **Rotina** e selo `2/4` que abre o modal de edição,
  onde o checklist também pode ser marcado.
- **Kanban do cliente**: mesmos selos no cartão; o modal de edição mostra o checklist no
  topo. Marcar tudo move o cartão para Concluída na hora.
- Marcar é AJAX: a linha, o selo, a barra da rotina, o resumo "x de y itens verificados"
  e o status do modal de edição são atualizados sem recarregar. Se falhar (inclusive
  sessão expirada, que vira redirect HTML), a caixa volta ao estado anterior e aparece um toast.

### Botão "Nova Tarefa" realocado

- **Dashboard:** saiu do cabeçalho do card. Agora é **+ Adicionar tarefa** no fim de
  "Minhas Tarefas" (já atribuída a quem clicou) e de "Não Assumidas", e **+ Nova rotina
  mensal** no fim da seção de rotinas. Os três abrem o mesmo modal, que alterna
  **Tarefa única / Rotina mensal**: prazo ou "Repetir todo dia", e o editor de checklist
  (Enter cria o próximo item, Backspace no item vazio apaga).
- **Kanban do cliente:** saiu da barra superior. Agora é **+ Adicionar tarefa** no pé da
  coluna **Pendente**, fora da área rolável, então fica sempre visível.

### Correção junto: prazo do Kanban em UTC

`_tarefa_kanban_dict` formatava `prazo` sem `timezone.localtime`, então o cartão e o
modal do Kanban mostravam **3 h a mais** (23:59 virava "02:59" do dia seguinte). A
ocorrência de rotina, com prazo 23:59, deixava isso evidente. Coberto por
`ChecklistTest.test_kanban_traz_checklist_e_prazo_no_fuso_local`.

### Testes

`tarefas/tests.py`: `GeracaoRotinaTest` (gera no dia com checklist e prazo local, não
antes, uma por mês, excluída não volta, dia 31 em fevereiro, criada depois do dia,
virada de ano, pausada, excluir rotina mantém tarefas), `ChecklistTest` (andamento +
assume, conclui e reabre, cancelada, outra instância 404, GET 405, Kanban com checklist e
fuso) e `RotinaViewsTest` (criar com checklist e ocorrência do dia, sem itens, dia
inválido, editar não mexe no mês, outra instância 404, retomar, "atribuída a mim",
painel sem botão no cabeçalho e sem vazar rotina de outra instância).
