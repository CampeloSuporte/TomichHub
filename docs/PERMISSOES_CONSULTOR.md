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
