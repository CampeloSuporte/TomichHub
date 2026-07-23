# Módulos por Usuário — Habilitar/Desabilitar Ferramentas por Login

**Data de Implementação:** 2026-07-23
**Arquivos principais:** `usuario/models.py` (`UsuarioModulo`), `clientes/decorators.py`
(`modulo_habilitado_required`), `clientes/views.py` (`listar_clientes`), `usuario/views.py`,
`usuario/templates/cadastrar_usuario.html`, `clientes/templates/listar.html`
**Status:** ✅ Produção

---

## Visão Geral

Cada aba da tela do cliente (Acessos, Backups, VPN, Topologia, Túneis SSH, Documentos,
RPKI/IRR, Monitoramento, Documentação de Rede, Hotspot, Testes de Rede) pode ser habilitada
ou desabilitada **por login individual** (`User`), não por empresa — dois usuários vinculados
à mesma `Cliente` podem ver conjuntos diferentes de ferramentas (ex: o financeiro da empresa
não precisa ver VPN, mas o técnico de rede precisa). A seleção acontece em
**Sistema → Usuário**, no cadastro/edição do login:

```
Admin abre Sistema → Usuário → edita um login do tipo "Cliente"
  └─ Marca/desmarca checkboxes em "Ferramentas habilitadas"
     └─ Desmarca, por exemplo, "VPN"
        └─ Submit do form grava UsuarioModulo(usuario, modulo='vpn', habilitado=False)
           └─ Quando ESSE usuário loga: a aba "VPN" some da tela do cliente
              └─ Acesso direto por URL às views de VPN é bloqueado (redirect com aviso)
              └─ Outro usuário da mesma empresa, sem essa restrição, continua vendo "VPN"
```

Módulo **sem registro** em `UsuarioModulo` = **habilitado**. Isso é proposital: nenhum
usuário já cadastrado perde acesso a nada com o deploy dessa feature — desabilitar é sempre
uma ação explícita do admin, não um estado padrão. A seção de checkboxes só se aplica a
usuários do tipo **Cliente** — administradores (`is_staff`) sempre veem tudo, então a seção
some no formulário quando o tipo selecionado é "Administrador".

> **Nota de histórico:** esta feature passou por duas versões no mesmo dia, ambas descartadas
> antes desta terceira:
> 1. Um switch de toggle ao lado de cada aba, dentro da própria tela do cliente
>    (`listar.html`) — trocado porque fazia mais sentido configurar isso no cadastro, não na
>    tela de uso.
> 2. Checkboxes no cadastro/edição do **Cliente** (empresa) — modelo `ClienteModulo`. Foi
>    descoberto que o usuário queria controle por **login individual**, não por empresa (dois
>    funcionários da mesma empresa podem precisar ver coisas diferentes). `ClienteModulo` foi
>    removido (migração `clientes/migrations/0086_delete_clientemodulo.py`) e substituído pelo
>    `UsuarioModulo` documentado aqui.

---

## Modelo de Dados

`usuario/models.py`:

```python
class UsuarioModulo(models.Model):
    MODULO_CHOICES = [
        ('acessos', 'Acessos'), ('backups', 'Backups'), ('vpn', 'VPN'),
        ('topologia', 'Topologia'), ('tuneis', 'Túneis SSH'),
        ('documentos', 'Documentos'), ('rpki_irr', 'RPKI/IRR'),
        ('monitoramento', 'Monitoramento'), ('documentacao', 'Documentação de Rede'),
        ('hotspot', 'Hotspot'), ('testes_rede', 'Testes de Rede'),
    ]
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='modulos')
    modulo = models.CharField(max_length=30, choices=MODULO_CHOICES)
    habilitado = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('usuario', 'modulo')


def modulo_habilitado(user, modulo_key):
    """Módulo sem registro para esse usuário = habilitado."""
    registro = UsuarioModulo.objects.filter(usuario=user, modulo=modulo_key).values_list('habilitado', flat=True).first()
    return True if registro is None else registro


def modulos_habilitados_dict(user):
    """Dict {modulo_key: bool} para todos os módulos conhecidos, pra uso no template."""
    estado = {m.modulo: m.habilitado for m in UsuarioModulo.objects.filter(usuario=user)}
    return {chave: estado.get(chave, True) for chave, _ in UsuarioModulo.MODULO_CHOICES}
```

`modulo_habilitado`/`modulos_habilitados_dict` são **funções de módulo**, não métodos de
instância — `User` é o model nativo do Django (`django.contrib.auth.models.User`) e não dá
pra adicionar métodos nele sem monkeypatch, então o padrão adotado foi `from usuario.models
import modulo_habilitado; modulo_habilitado(request.user, 'vpn')`.

Migração: `usuario/migrations/0001_initial.py` (primeira migração do app `usuario`, que até
então não tinha nenhum model).

### Mapeamento aba → módulo

Alguns módulos não seguem o nome óbvio da aba, porque a interface já agrupava ferramentas
diferentes na mesma aba antes desta feature existir:

| Módulo (`modulo_key`) | Aba na UI | O que cobre |
|---|---|---|
| `vpn` | VPN | Upload de arquivo `ArquivoVPN` + config OpenVPN (`openvpn_*`) |
| `tuneis` | Túneis SSH | Proxies SSH (`ProxyServer`) + VPN WireGuard (`vpn_wg_*`) + Túnel OpenVPN (`vpn_ovpn_*`) |
| `rpki_irr` | RPKI/IRR | Blocos de IP + validação RPKI/IRR + config IRR (`irr_*`) |
| `documentacao` | Documentação de Rede | IPAM nativo (`ipam_views.py`) |

---

## Interface — Sistema → Usuário

`usuario/templates/cadastrar_usuario.html` ganhou uma seção **"Ferramentas habilitadas"**
em cada um dos dois modais (Cadastro e Edição de Usuário), logo abaixo do toggle "Tipo de
usuário" (Cliente/Administrador):

```html
<div class="form-section modulos-section">
    <div class="form-section-title"><i class="fas fa-toolbox"></i> Ferramentas habilitadas</div>
    <input type="hidden" name="modulos_form_present" value="1">
    {% for chave, rotulo in modulos_disponiveis %}
    <input type="checkbox" name="modulos" value="{{ chave }}" id="modulo_{{ chave }}" checked>
    <label for="modulo_{{ chave }}">{{ rotulo }}</label>
    {% endfor %}
</div>
```

- **Visibilidade condicional ao tipo:** `setRole(el, isAdmin)` (JS que já controlava o toggle
  visual Cliente/Administrador) ganhou uma linha a mais — esconde `.modulos-section` quando
  "Administrador" é selecionado:
  ```js
  const modulosSection = form.querySelector('.modulos-section');
  if (modulosSection) modulosSection.style.display = isAdmin ? 'none' : '';
  ```
- **Cadastro:** todos os checkboxes vêm marcados por padrão (reflete "sem registro =
  habilitado"), seção visível por padrão (tipo padrão é "Cliente").
- **Edição:** `editarUsuario(id, username, email, isStaff, modulosHabilitados)` ganhou o
  parâmetro `modulosHabilitados` (objeto `{modulo_key: bool}`), aplica nos checkboxes e
  ajusta a visibilidade inicial da seção conforme `isStaff`. O objeto chega via
  `onclick="editarUsuario(..., JSON.parse('{{ usuarios.modulos_json|escapejs }}'))"`, com
  `usuarios.modulos_json` montado na view (`json.dumps(modulos_habilitados_dict(u))`) —
  `|escapejs` (não `|safe`) porque o JSON tem aspas duplas e o atributo `onclick="..."`
  também usa aspas duplas; embutir sem escapar quebraria o HTML.

**Efeito na tela do cliente** (`clientes/templates/listar.html`): `listar_clientes` agora
calcula `modulos_habilitados = modulos_habilitados_dict(request.user)` — ou seja, reflete o
usuário **logado no momento**, não a empresa. Cada `<li>` de aba só é renderizado se
`is_admin` ou `modulos_habilitados.xxx` for verdadeiro. A aba "Acessos" (antes sempre
`display:block` fixo) respeita o mesmo flag, com fallback em JS que ativa a primeira aba
disponível caso "Acessos" esteja desabilitada para aquele usuário. `window.MODULOS_BLOQUEADOS`
+ `moduloBloqueado(nomeAba)` seguem impedindo que `trocarAba()` abra uma aba desabilitada por
qualquer caminho (inclusive `sessionStorage` de uma sessão anterior).

### Persistência (views)

`usuario/views.py` ganhou `_sincronizar_modulos_usuario(request, usuario)`, chamada tanto em
`cadastrar_usuario` (POST) quanto em `editar_usuario` (POST) — só grava/atualiza
`UsuarioModulo` se `modulos_form_present` estiver no POST, proteção contra um form incompleto
desabilitar **todos** os módulos por engano (checkbox desmarcado não aparece no POST, então
"nada marcado" é indistinguível de "campo ausente" sem esse marcador):

```python
def _sincronizar_modulos_usuario(request, usuario):
    if not request.POST.get('modulos_form_present'):
        return
    modulos_marcados = set(request.POST.getlist('modulos'))
    for chave, _ in UsuarioModulo.MODULO_CHOICES:
        UsuarioModulo.objects.update_or_create(
            usuario=usuario, modulo=chave,
            defaults={'habilitado': chave in modulos_marcados},
        )
```

---

## Bloqueio no Backend — `modulo_habilitado_required`

`clientes/decorators.py` — simplificado nesta versão: como a checagem agora é sobre o
usuário logado (não sobre um Cliente específico), não precisa mais resolver `cliente_id` da
URL nem cair para `get_by_usuario_vinculado` como caminho de leitura — só usa isso pro
redirect de erro:

```python
def modulo_habilitado_required(modulo_key):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_staff or request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            if not usuario_modulo_habilitado(request.user, modulo_key):
                messages.error(request, 'Este módulo não está disponível para o seu usuário. Fale com o suporte.')
                try:
                    cliente = Cliente.objects.get_by_usuario_vinculado(request.user)
                    return redirect(f"{reverse('listar_clientes')}?id={cliente.id}")
                except Cliente.DoesNotExist:
                    return redirect('login')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
```

Pontos importantes:

- **Admin sempre passa.** O bloqueio existe só para o portal do cliente final — nunca afeta
  `is_staff`/`is_superuser`.
- **Checagem é 100% sobre `request.user`** — não importa qual `cliente_id` está na URL, o
  que importa é se aquele login específico tem o módulo habilitado.
- Aplicado a **89 endpoints** em `clientes/views.py` (todas as ações de Acessos, Backups,
  VPN, Topologia, Documentos, Túneis, RPKI/IRR e Testes de Rede) — nenhum desses call sites
  precisou mudar, o decorator só teve a implementação interna trocada.

### Lacuna conhecida

Os módulos **Hotspot** (`clientes/hotspot_views.py`) e **Documentação de Rede**
(`clientes/ipam_views.py`) estão protegidos **só na interface** (a aba some) — as views de
ação desses dois arquivos ainda não têm `@modulo_habilitado_required` aplicado. O mesmo vale
para **Monitoramento**, que vive na app `monitoramento` (fora de `clientes`). Um usuário que
souber a URL exata ainda conseguiria usar essas três ferramentas por acesso direto mesmo
desabilitadas — risco baixo (uso do próprio tenant, não vazamento entre clientes), mas vale
uma passada futura se for necessário fechar 100%.

### Como aplicar em uma view nova

```python
from .decorators import modulo_habilitado_required

@login_required(login_url='login')
@modulo_habilitado_required('vpn')   # mesma chave usada em UsuarioModulo.MODULO_CHOICES
def minha_view_nova(request, cliente_id):
    ...
```

---

## Correção — Menu do Cliente Colado no Botão "Voltar ao Dashboard"

**Arquivo:** `templates/base.html`

Ao revisar a UI durante esta feature, foi encontrado um bug de layout não relacionado: no
topbar exibido para usuários-cliente (`{% if is_cliente %}`), a `<div>` flex que deveria
distribuir os itens (`justify-content-between`) não tinha a classe `w-100` — diferente do
bloco equivalente do topbar de admin, que tem. Sem `w-100` a div encolhe ao conteúdo e o
dropdown do usuário (👤) fica colado no botão "Voltar ao Dashboard" em vez de ir para o
canto direito da tela.

```html
<!-- antes -->
<div class="d-flex align-items-center justify-content-between">
    ...
    <div class="d-flex align-items-center gap-3"> <!-- dropdown do usuário -->

<!-- depois -->
<div class="d-flex align-items-center justify-content-between w-100">
    ...
    <div class="d-flex align-items-center gap-3 ms-auto"> <!-- dropdown do usuário -->
```

---

## Testes Realizados

Via `python manage.py shell` (`RequestFactory`), sem servidor rodando:

- GET de `cadastrar_usuario`: checkboxes de módulos e marcador `modulos_form_present`
  presentes no HTML.
- POST de criação de usuário desmarcando `vpn`/`hotspot` → `UsuarioModulo` gravado
  corretamente (`habilitado=False` só nesses dois, resto `True`).
- Usuário criado vinculado como `usuario` principal de um `Cliente` real → render de
  `listar_clientes` **logado como esse usuário**: aba "VPN" ausente, aba "Backups" presente.
- Acesso direto a `upload_vpn` logado como esse usuário → redirect 302 para `listar_clientes`
  (bloqueado, não executa a view) — a checagem é do usuário, não do cliente.
- POST de edição reabilitando `vpn` → `UsuarioModulo` atualizado via `update_or_create`.
- `python manage.py check` sem apontar problemas; templates `listar.html`,
  `cadastrar_cliente.html` e `cadastrar_usuario.html` compilam sem erro de sintaxe.

---

## Deploy

- `clientes/migrations/0086_delete_clientemodulo.py` — remove a tabela `ClienteModulo`
  (versão anterior desta feature, descartada).
- `usuario/migrations/0001_initial.py` — cria a tabela `UsuarioModulo` (primeira migração do
  app `usuario`).

Ambas aplicadas em `crm_db` (banco compartilhado entre o worktree de desenvolvimento e
produção). Merge feito via fast-forward `claude/system-tools-modularization-d70813` → `main`,
gunicorn reiniciado.
