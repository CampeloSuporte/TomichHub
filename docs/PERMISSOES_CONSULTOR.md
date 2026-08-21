# Permissões do Consultor — Gaps Corrigidos (2026-08-10)

**Arquivos principais:**
- `usuario/perms.py` — ponto único de verdade de papel/instância (`get_role`, `is_admin`,
  `is_consultor`, `is_backoffice`, `pode_acessar_cliente`, `usuarios_gerenciaveis_por`)
- `usuario/models.py` — `PerfilUsuario` (back-office), `Instancia`, novo `PortalUsuarioInstancia`
- `usuario/views.py` — `cadastrar_usuario`, `editar_usuario`
- `clientes/consumers.py` — `SSHConsumer._usuario_pode_acessar`
- `clientes/views.py` — `deletar_backup`, `deletar_cliente` (este já corrigido em 09/08,
  commit `0370002cd`)

**Atualizado em:** 2026-08-10

**Ver também:** [terminal_ssh.md](terminal_ssh.md), [frontend_acessos.md](frontend_acessos.md)

---

## Contexto

O sistema de papéis (`PerfilUsuario.role` = `admin`/`consultor`/`operador`, escopado por
`Instancia`) substituiu, aos poucos, o esquema antigo baseado em `is_staff`/`is_superuser`. Um
Consultor **nunca** tem `is_staff=True` (só o Administrador tem) — então qualquer tela que ainda
cheque `is_staff`/`is_superuser` cru, em vez de `usuario.perms`, bloqueia o Consultor mesmo
quando ele deveria ter acesso total aos próprios clientes.

Esse padrão de bug — uma tela já migrada pra `usuario.perms`, uma tela irmã (geralmente a ação de
**excluir**, enquanto editar/ver já funcionava) ainda no esquema antigo — apareceu **três vezes**
nesta sessão, em partes bem diferentes do sistema. Documentado aqui pra virar o primeiro lugar a
checar da próxima vez que "Consultor não consegue X".

## 1. Terminal SSH — WebSocket aceitava host mas recusava conexão

**Sintoma:** o host aparecia normalmente na lista de "Hosts SSH" (`listar_acessos_terminal`), mas
ao tentar conectar o WebSocket devolvia `ERRO: Você não tem permissão para acessar este host.`

**Causa:** `SSHConsumer._usuario_pode_acessar` (`clientes/consumers.py`) usava só
`is_staff`/`is_superuser` + `Cliente.objects.filter_by_usuario_vinculado` (escopo de portal do
cliente final), enquanto a view que monta a lista já usava `usuario.perms.pode_acessar_cliente`
(cobre Consultor/Operador por `Instancia`). Resultado: a lista e a conexão usavam regras
diferentes pro mesmo host.

**Correção:**
```python
def _usuario_pode_acessar(self, acesso):
    user = getattr(self, '_crm_user', None)
    if not user:
        return False
    return _perms.pode_acessar_cliente(user, acesso.cliente)
```
Usado nas 3 chamadas do consumer (`connect`, `_entrar_em_sessao_compartilhada`,
`conectar_acesso`). Requer restart do **daphne** (processo ASGI que serve os `Consumer`) — não
pega no reload de template nem no gunicorn.

**Pendente (não corrigido nesta sessão, registrado por segurança):** `WinboxConsumer` e
`WinboxVNCConsumer` (mesmo arquivo, herdam o método já corrigido) **nunca chamam**
`_usuario_pode_acessar` antes de abrir a sessão pro `acesso_id` recebido do frontend.

## 2. `deletar_backup` — exigia `is_staff`, bloqueava Consultor e o cliente do portal

**Sintoma:** tanto o Consultor quanto o cliente final (usuário de portal com o módulo "Backups"
habilitado) recebiam "Você não possui permissão para acessar esta página." ao excluir um backup
— mesmo já conseguindo visualizar/baixar o mesmo backup (`download_backup`) sem problema.

**Causa:** `deletar_backup` (`clientes/views.py`) estava decorado com `@admin_required` (exige
`is_staff=True` — só o Administrador da plataforma passa), enquanto `download_backup`, logo
acima no mesmo arquivo, já fazia a checagem correta:
```python
if not _perms.pode_acessar_cliente(request.user, backup.cliente):
```
`pode_acessar_cliente` cobre os três papéis: Admin (tudo), Consultor/Operador (clientes da
própria Instancia) e portal do cliente final (`get_by_usuario_vinculado`) — por isso o mesmo bug
afetava os dois tipos de usuário ao mesmo tempo.

**Correção:** removido `@admin_required`; adicionada a mesma checagem inline de
`download_backup`, mantendo `@modulo_habilitado_required('backups')` (que já trata os três
papéis corretamente — ver docstring do decorator em `clientes/decorators.py`).

**Pendente (mesmo padrão, achado mas fora do escopo desta correção):**
- `clientes/script_views.py` (`gerenciar_scripts`, `salvar_script`, `deletar_script`) —
  checam `is_staff or is_superuser` depois de já passar por
  `@ferramenta_instancia_required('scripts')`, que libera Consultor/Operador.
- `atendimento/views.py` (`staff_required`/`_is_staff`) — quase todas as views do módulo de
  atendimento exigem `is_staff` puro.

## 3. Usuário de portal cadastrado pelo Consultor sumia da própria listagem e do vínculo em Cliente

**Sintoma:** um Consultor cadastra um usuário do tipo "Cliente" (login de portal, sem
`PerfilUsuario`) em `/auth/cadastrar_usuario/` — o usuário é criado com sucesso, mas **some** da
própria listagem de usuários gerenciáveis e não aparece no dropdown de "vincular usuário" ao
editar/cadastrar um Cliente.

**Causa:** `usuarios_gerenciaveis_por(user)` (`usuario/perms.py`) fazia
`User.objects.filter(perfil__instancia=instancia)` — um INNER JOIN na relação reversa
`PerfilUsuario.usuario`. Usuários de portal (`role='cliente'`) **nunca** ganham `PerfilUsuario`
(é a própria definição do papel — ver docstring do model), então essa query os exclui
estruturalmente. Usada tanto na listagem de usuários (`usuario/views.py`) quanto no dropdown de
vínculo em `cadastrar_cliente` (`clientes/views.py`).

**Correção:** novo modelo `usuario.models.PortalUsuarioInstancia` (migração
`usuario/migrations/0005_portalusuarioinstancia.py`):

```python
class PortalUsuarioInstancia(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='portal_instancia')
    instancia = models.ForeignKey(Instancia, on_delete=models.CASCADE, related_name='usuarios_portal')
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios_portal_criados')
    criado_em = models.DateTimeField(auto_now_add=True)
```

- `usuario/views.py` `cadastrar_usuario` (POST, ramo `role == 'cliente'`): grava
  `PortalUsuarioInstancia(usuario=user, instancia=get_instancia(request.user), criado_por=request.user)`
  quando quem cria é Consultor.
- `usuario/perms.py` `usuarios_gerenciaveis_por`: passou a incluir também esse vínculo —
  ```python
  User.objects.filter(
      Q(perfil__instancia=instancia) | Q(portal_instancia__instancia=instancia)
  ).distinct()
  ```

**Por que não afeta `is_backoffice`/segurança:** `PortalUsuarioInstancia` é um modelo separado de
`PerfilUsuario` — não altera `get_role()`/`is_backoffice()`/`is_admin()`/`is_consultor()`, que
continuam olhando só pra `PerfilUsuario`. Um usuário de portal rastreado por
`PortalUsuarioInstancia` continua sem nenhum acesso a telas de back-office; o único efeito é
aparecer nas duas listagens de "usuários que este Consultor gerencia".

**Dado histórico não migrado:** usuários de portal órfãos criados **antes** desta correção
continuam invisíveis pro Consultor que os criou — não existe log de auditoria de criação de
usuário no sistema antes disso, então não dá pra inferir com segurança a instância de cada um
retroativamente. Só o caso relatado em produção (`ademir`, instância "marinho") foi corrigido
manualmente via shell. Qualquer usuário novo, a partir de 2026-08-10, já funciona automaticamente.

## Escopo de Operador — nota lateral (não alterada)

`usuarios_gerenciaveis_por` continua restrita a `is_consultor(user)` — um Operador chama essa
função (via `cadastrar_cliente`, que é `@backoffice_required`) e recebe `User.objects.none()`.
Isso é comportamento pré-existente (Operador não gerencia usuários,
`pode_gerenciar_usuarios = is_admin or is_consultor`), não um bug introduzido aqui. Se um Operador
precisar vincular usuários de portal a Cliente no futuro, essa é a função a revisar.

## Wiki liberável por instância (2026-08-13)

**Sintoma:** não existia como o Administrador habilitar a Wiki num Consultor — a ferramenta
simplesmente não aparecia na lista de checkboxes do cadastro de usuário.

**Causa:** `wiki` nunca esteve em `InstanciaFerramenta.FERRAMENTA_CHOICES` (a tela de cadastro
renderiza essa lista direto), e todas as views de `wiki/views.py` eram `@admin_required`.

**O que mudou:**

- `usuario/models.py`: nova opção `('wiki', 'Wiki (leitura)')` em `FERRAMENTA_CHOICES`
  (migração `usuario/0006_alter_instanciaferramenta_ferramenta`). O checkbox aparece sozinho no
  formulário, que é montado a partir dessa lista.
- `wiki/views.py`: as views de **leitura** (`dashboard_wiki`, `visualizar_artigo`, `buscar_wiki`,
  `listar_por_categoria`, `listar_por_tag`, `listar_por_fabricante`, `api_buscar_wiki`,
  `api_visualizar_artigo`) passaram de `@admin_required` para
  `@ferramenta_instancia_required('wiki')`.
- **Escrita continua só do Administrador** (`cadastrar_artigo`, `editar_artigo`,
  `deletar_artigo`, `cadastrar_categoria_ajax`). Motivo: `ArtigoWiki` **não tem `instancia` nem
  `cliente`** — a base de conhecimento é global, então um Consultor com permissão de escrita
  editaria/apagaria conteúdo de todas as outras instâncias. Se um dia a Wiki ganhar escopo por
  instância, é aí que a decisão se revisa.
- Templates: o item "Wiki" do menu (`templates/base.html`) e os botões da barra lateral do
  terminal (`clientes/templates/terminal.html`, desktop e mobile) passaram a testar
  `is_admin_bo or ferramentas_habilitadas.wiki` em vez de `is_admin_bo`/`request.user.is_staff`
  (Consultor **não** é `is_staff` neste sistema — só o Administrador é). Os botões de
  criar/editar/excluir artigo nos templates da Wiki ficaram sob `{% if is_admin_bo %}`.

**Portal do cliente final não recebe a Wiki:** `portal_pode_usar_ferramenta` exige equivalente em
`UsuarioModulo.MODULO_CHOICES`; `wiki` não tem, então retorna `False` — mesmo caso de
`scripts`/`bgp`.

**Verificado** com o Consultor `mmarinho` (instância "marinho"): com a ferramenta desligada,
`/wiki/` e `/wiki/api/buscar/` redirecionam com aviso; ligada, ambas devolvem 200; e
`/wiki/artigo/novo/` continua bloqueada nos dois casos.

---

## Instância "Principal" — a operação do Administrador virou instância (2026-08-19)

### O sintoma

Ao cadastrar um Operador, o dropdown de instância mostrava **seis** opções onde deveriam
aparecer duas (a do consultor Marinho e a operação própria do Administrador).

### As duas causas

**1. Lixo de teste no banco de produção.** Cinco instâncias (`I_ea19cb`, `I_1ae493`,
`Instancia1_466dee`, `Instancia2_466dee`, `Instancia1_6cb110`), cinco logins (`c_ea19cb`,
`c_1ae493`, `cons_466dee`, `op_466dee`, `cons_6cb110`) e dois clientes (`ClienteA466dee`,
`ClienteB466dee`, zero acessos, e-mails `@x.com`) criados em 02/08/2026 entre 21:37 e 22:05 por
um script de verificação rodado contra o banco real — nada no repositório recria isso. Removidos
em 19/08/2026, com dump em `backups/lixo_teste_instancias_20260819.json`.

**2. A operação do Administrador não existia como `Instancia`.** Cliente criado pelo
Administrador sem escolher instância nascia com `instancia = NULL` ("da plataforma") — 47
clientes e 16 tarefas nessa situação. Como `Instancia.objects.filter(ativo=True)` é o que
alimenta o dropdown, essa operação nunca podia aparecer nele.

O efeito colateral era mais sério que a lista feia: `pode_acessar_cliente` exige, para
Consultor/Operador, `instancia is not None and cliente.instancia_id == instancia.id`. Um Operador
criado para a operação do Administrador não enxergaria **nenhum** cliente — ou seja, não havia
como ter operador da operação principal.

### O que foi feito

Criada a instância **Principal** (id 25), com os 47 clientes e as 16 tarefas de `instancia=NULL`
migrados para ela e as 18 ferramentas habilitadas. Os Administradores continuam fora de qualquer
instância: `get_role` trata `is_staff`/`is_superuser` sem `PerfilUsuario` como admin legado, e
todo escopo (`Cliente.visiveis_para`, `Tarefa.visiveis_para`, `pode_acessar_cliente`)
curto-circuita em `is_admin` — nenhum deles perdeu visibilidade (49 clientes antes e depois).

Conferido com um Operador simulado na Principal (criado e revertido em transação): 47 clientes
visíveis, ferramentas liberadas, e `pode_acessar_cliente` negando cliente da instância do
Marinho.

### O que ficou de fora (decidir depois)

- Cliente novo criado pelo Administrador **continua nascendo com `instancia = NULL`** se ele não
  escolher nada no formulário — e aí some da vista dos Operadores da Principal. Tornar o campo
  obrigatório para Administrador, ou fazer a Principal ser o default, resolve; não foi feito
  porque muda comportamento de cadastro.
- Três logins com `is_staff=True` do mesmo lote de teste (`adm_466dee`, `adm_6cb110`,
  `admweb_2211cb`) e um operador órfão (`semperfil_6cb110`) continuam no banco. `is_staff` sem
  `PerfilUsuario` = **administrador legado**, com acesso a tudo.

---

## Consultor enxergava os clientes das outras instâncias (2026-08-21)

### O sintoma

O Consultor `mmarinho` (instância "marinho", **2 clientes**) via os **47 clientes** da instância
Principal — no quadro geral, no módulo de atendimento, no Agent NOC e no relatório de backups.

### A causa: `is_staff` deixou de significar "Administrador"

Duas mudanças corretas, isoladamente, se combinaram numa falha de isolamento:

1. `_is_staff_para_role` (em `usuario/views.py`) passou a criar Consultor e Operador com
   `is_staff=True`. Isso foi de propósito: o módulo de atendimento inteiro é `staff_required`, e
   sem `is_staff` nenhum Consultor conseguia atender nem aparecia como atendente.
2. Só que `admin_required` — tanto o de `clientes/decorators.py` quanto o alias local de
   `atendimento/views.py` — continuou testando `request.user.is_staff` como se fosse "é o
   Administrador da plataforma". O próprio docstring afirmava "Consultor/Operador têm
   `is_staff=False`", o que deixou de ser verdade.

Resultado: **toda view `@admin_required` virou pública para Consultor/Operador** — e essas são
justamente as telas globais, que listam `Cliente.objects.all()` sem escopo nenhum. O mesmo valia
para os menus do `templates/base.html`, gateados por `{% if request.user.is_staff %}`.

Levantado com um crawl autenticado como `mmarinho` nas 795 rotas: **21 rotas** devolviam nome de
cliente de outra instância.

### O que foi corrigido

**1. O papel volta a ser a fonte da verdade (`usuario.perms.is_admin`)**

- `clientes/decorators.py`: `admin_required` checa `is_admin`; Consultor/Operador vão pro
  `quadro_instancia` (mandar pra tela de login prenderia num loop de "já estou logado").
  `cliente_login_required` e `cliente_or_admin_required` passaram a usar `is_backoffice` em vez
  de `is_staff` para separar equipe do portal.
- `atendimento/views.py`: o alias local `admin_required` também passou a checar `is_admin`.
  `staff_required` continua como está — ele significa "back-office", que é o correto para o
  módulo; quem filtra os dados agora é o escopo abaixo.
- `templates/base.html`, `atendimento/base.html` e `atendimento/dashboard.html`: os menus de
  plataforma (Ferramentas, Agent NOC, seção "Sistema" do atendimento) saíram de
  `request.user.is_staff` para `is_admin_bo`.

**2. Escopo por instância onde o Consultor deve continuar entrando**

Bloquear não bastava: várias telas são operação legítima do Consultor sobre os próprios clientes.
Essas passaram a `backoffice_required` **com queryset escopado**:

| View | O que mudou |
|---|---|
| `home.relatorio_backups` | `BackupLog` filtrado por `Cliente.objects.visiveis_para`; o `?cliente=` também |
| `home.listar_chamados_por_status` | `Chamado` filtrado pelos clientes visíveis |
| `home.backup_acesso_config` | valida `pode_acessar_cliente(acesso.cliente)` — o `acesso_id` da URL alcançava qualquer host |
| `modelo_equipamento` / `funcao_equipamento` | catálogos globais sem dado de cliente; Consultor precisa deles para cadastrar acesso |
| `home.geo_*` (correção/histórico) | `ferramenta_instancia_required('geoip')` + `_correcoes_geoip_visiveis` (dono = `solicitante`) |

Continuam exclusivas do Administrador: `quadro_geral`, `agent_grupos`, `agent_knowledge`,
`agent_config`, `backup_config`, `backup_template_*` e o cadastro de **Blocos do Geofeed**
(`GeofeedBloco` é tabela global, sem dono por instância — o card sumiu da tela para não quebrar).

**3. Módulo de atendimento: `atendimento/scope.py` (novo)**

`clientes_visiveis`, `conversations_visiveis`, `groups_visiveis`, `pode_ver_conversation` e
`pode_ver_group`. Aplicados em `_base_ctx` (sidebar/badges), dashboard, inbox, histórico,
relatórios, empresas, grupos, auto-atendimento, kanban, PDF e nas APIs. Além das listagens, **10
guardas em conversas e 4 em grupos** fecharam os IDOR por id na URL — enviar mensagem, mesclar,
agendar, taguear, ler mensagens no polling e, o mais sério, `api_conversation_hosts`, que
devolvia os hosts (`Acesso`) do cliente de qualquer instância.

Regra de escopo: conversa pertence à instância por `Conversation.cliente` **ou** por
`group.cliente` (chamado antigo pode ter só o vínculo do grupo). Grupo de WhatsApp **ainda sem
cliente** é a única exceção — continua visível e vinculável por qualquer back-office, senão o
Consultor não conseguiria cadastrar os próprios grupos. Grupo já vinculado a outra instância
nunca aparece nem pode ser alterado (inclusive no "vincular automático", que reatribuía os
grupos alheios).

**4. Listas de usuários**

`conversation_detail`, `tarefas` e `kanban` traziam `User.objects.filter(is_active=True)` cru —
o seletor de responsável do kanban listava até os logins de portal dos clientes das outras
instâncias. Agora saem de `perms.colegas_de_instancia`.

**5. Financeiro**

`api_painel_blocos_ip`, `listar_contratos_aluguel` e `assinatura_locador` estavam só com
`@login_required` — qualquer usuário autenticado (inclusive login de portal) listava os aluguéis
de IP de todos os clientes com os dados de fatura. Passaram a `@acesso_financeiro_restrito`.

### Verificação

Crawl autenticado nas 795 rotas, antes e depois:

| Perfil | Rotas vazando antes | Depois |
|---|---|---|
| Consultor (`mmarinho`) | 21 | 0 |
| Operador (`testemarinho`) | 21 | 0 |
| Administrador (`lucas`) | — | **0 mudanças** de status em nenhuma rota |

Regressão automatizada em `atendimento.tests.IsolamentoInstanciaTest` (8 testes) — conferido que
**6 deles falham** se o escopo for desligado, para não virarem teste vazio.

### O que ficou de fora

- **Auditoria completa do módulo Financeiro.** Ele tem trava própria por lista de IDs
  (`USUARIOS_FINANCEIRO = [1, 2]`), então o Consultor já não entra nas telas principais. Mas
  outros ~25 endpoints só têm `@login_required` + `if not request.user.is_staff` inline — que
  hoje deixa Consultor/Operador passar. Alguns são usados pelo portal do cliente final, então
  cada um precisa ser decidido caso a caso.
- **Os "administradores legados"** (`is_staff=True` sem `PerfilUsuario`: `adm_466dee`,
  `adm_6cb110`, `admweb_2211cb`) continuam com acesso total por `get_role`. São sobra do lote de
  teste citado na seção anterior — enquanto existirem, são contas de Administrador de fato.
