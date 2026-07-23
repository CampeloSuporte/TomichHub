# Módulos do Cliente — Habilitar/Desabilitar Ferramentas por Contrato

**Data de Implementação:** 2026-07-23
**Arquivos principais:** `clientes/models.py` (`ClienteModulo`), `clientes/decorators.py`
(`modulo_habilitado_required`), `clientes/views.py`, `clientes/templates/listar.html`,
`clientes/templates/cadastrar_cliente.html`, `staticfiles/js/cadastrar_cliente.js`
**Status:** ✅ Produção

---

## Visão Geral

Cada aba da tela do cliente (Acessos, Backups, VPN, Topologia, Túneis SSH, Documentos,
RPKI/IRR, Monitoramento, Documentação de Rede, Hotspot, Testes de Rede) pode ser habilitada
ou desabilitada **por cliente**, para refletir o que foi contratado. A seleção acontece no
cadastro/edição do cliente (checkboxes), não mais na própria tela de ferramentas:

```
Admin cadastra ou edita um cliente
  └─ Marca/desmarca checkboxes em "Ferramentas habilitadas"
     └─ Desmarca, por exemplo, "VPN" (cliente não contratou)
        └─ Submit do form grava ClienteModulo(cliente, modulo='vpn', habilitado=False)
           └─ Para o cliente final: a aba "VPN" some da tela
              └─ Acesso direto por URL às views de VPN é bloqueado (redirect com aviso)
```

Módulo **sem registro** em `ClienteModulo` = **habilitado**. Isso é proposital: nenhum
cliente já cadastrado perde acesso a nada com o deploy dessa feature — desabilitar é sempre
uma ação explícita do admin, não um estado padrão.

> **Nota de histórico:** a primeira versão desta feature (mesmo dia) colocava um switch de
> toggle ao lado de cada aba, dentro da própria tela do cliente (`listar.html`). Foi trocado
> por checkboxes no cadastro/edição do cliente a pedido do usuário — o admin já define o
> pacote contratado no mesmo lugar onde cadastra os dados da empresa, em vez de precisar abrir
> a tela de ferramentas de cada cliente pra configurar isso. Ver seção "Migração da UI" abaixo.

---

## Modelo de Dados

`clientes/models.py`:

```python
class ClienteModulo(models.Model):
    MODULO_CHOICES = [
        ('acessos', 'Acessos'), ('backups', 'Backups'), ('vpn', 'VPN'),
        ('topologia', 'Topologia'), ('tuneis', 'Túneis SSH'),
        ('documentos', 'Documentos'), ('rpki_irr', 'RPKI/IRR'),
        ('monitoramento', 'Monitoramento'), ('documentacao', 'Documentação de Rede'),
        ('hotspot', 'Hotspot'), ('testes_rede', 'Testes de Rede'),
    ]
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='modulos')
    modulo = models.CharField(max_length=30, choices=MODULO_CHOICES)
    habilitado = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cliente', 'modulo')
```

`Cliente` ganhou dois métodos auxiliares:

- `modulo_habilitado(modulo_key)` — retorna `True`/`False` para um módulo específico
  (consulta única, usada pelo decorator).
- `modulos_habilitados_dict()` — retorna `{modulo_key: bool}` para todos os módulos
  conhecidos de uma vez (usado no contexto da view para renderizar o template); também
  popula um cache de instância (`self._modulos_cache`) para não repetir a query se
  `modulo_habilitado()` for chamado depois no mesmo request.

Migração: `clientes/migrations/0085_clientemodulo.py` (`CREATE TABLE`, sem alterar nada
existente).

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

## Interface — Seleção no Cadastro/Edição do Cliente

`clientes/templates/cadastrar_cliente.html` ganhou uma seção **"Ferramentas habilitadas"**
em cada um dos dois modais (Cadastro e Edição), com um checkbox por módulo
(`name="modulos" value="{{ chave }}"`), mais um marcador oculto
`<input type="hidden" name="modulos_form_present" value="1">`:

```html
<div class="form-section">
    <div class="form-section-title"><i class="fas fa-toolbox"></i> Ferramentas habilitadas</div>
    <input type="hidden" name="modulos_form_present" value="1">
    {% for chave, rotulo in modulos_disponiveis %}
    <input type="checkbox" name="modulos" value="{{ chave }}" id="modulo_{{ chave }}" checked>
    <label for="modulo_{{ chave }}">{{ rotulo }}</label>
    {% endfor %}
</div>
```

- **Cadastro:** todos os checkboxes vêm marcados por padrão (reflete o "sem registro =
  habilitado"); o JS reseta pra todos marcados sempre que o modal reabre do zero
  (`show.bs.modal` em `#cadastroModal`).
- **Edição:** os checkboxes começam desmarcados no HTML e são marcados via JS quando o
  admin clica em "Editar" — `editarCliente(...)` (em `staticfiles/js/cadastrar_cliente.js`)
  ganhou um parâmetro `modulosHabilitados` (objeto `{modulo_key: bool}`) e aplica:
  ```js
  document.querySelectorAll('#edicaoForm input[name="modulos"]').forEach(function(cb) {
      cb.checked = !!(modulosHabilitados && modulosHabilitados[cb.value]);
  });
  ```
  O objeto chega via `onclick="editarCliente(..., JSON.parse('{{ cliente.modulos_json|escapejs }}'))"`,
  onde `cliente.modulos_json` é montado na view (`json.dumps(cliente.modulos_habilitados_dict())`)
  para cada linha da tabela — `|escapejs` (não `|safe`) porque o JSON tem aspas duplas e o
  atributo `onclick="..."` também usa aspas duplas; embutir sem escapar quebraria o HTML.

**Efeito na tela do cliente** (`listar.html`) continua igual: cada `<li>` de aba só é
renderizado se `is_admin` ou `modulos_habilitados.xxx` for verdadeiro. A aba "Acessos"
(antes sempre `display:block` fixo) respeita o mesmo flag, com fallback em JS que ativa a
primeira aba disponível caso "Acessos" esteja desabilitada. `window.MODULOS_BLOQUEADOS` +
`moduloBloqueado(nomeAba)` seguem impedindo que `trocarAba()` abra uma aba desabilitada por
qualquer caminho (inclusive `sessionStorage` de uma sessão anterior).

### Persistência (views)

`cadastrar_cliente` (POST) e `editar_cliente` (POST) só gravam/atualizam `ClienteModulo`
se `modulos_form_present` estiver no POST — proteção contra um form incompleto (por bug ou
por um client externo que não manda esse campo) desabilitar **todos** os módulos por engano,
já que checkboxes desmarcados simplesmente não aparecem no POST e são indistinguíveis de
"campo ausente":

```python
if request.POST.get('modulos_form_present'):
    modulos_marcados = set(request.POST.getlist('modulos'))
    for chave, _ in ClienteModulo.MODULO_CHOICES:
        ClienteModulo.objects.update_or_create(
            cliente=cliente, modulo=chave,
            defaults={'habilitado': chave in modulos_marcados},
        )
```

No cadastro usa `bulk_create` (cliente é novo, sem risco de conflito); na edição usa
`update_or_create` por módulo (cliente já pode ter registros de antes).

---

## Bloqueio no Backend — `modulo_habilitado_required`

`clientes/decorators.py`:

```python
def modulo_habilitado_required(modulo_key, cliente_kwarg='cliente_id'):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_staff or request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            cliente_id = kwargs.get(cliente_kwarg)
            cliente = Cliente.objects.get(id=cliente_id) if cliente_id else Cliente.objects.get_by_usuario_vinculado(request.user)
            if not cliente.modulo_habilitado(modulo_key):
                messages.error(request, 'Este módulo não está disponível no seu contrato. Fale com o suporte.')
                return redirect(f"{reverse('listar_clientes')}?id={cliente.id}")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
```

Pontos importantes:

- **Admin sempre passa.** O bloqueio existe só para o portal do cliente final — nunca afeta
  `is_staff`/`is_superuser`.
- **Resolução do cliente:** se a view recebe `cliente_id` na URL (ex: `topologia_editor(request, cliente_id)`),
  usa esse id. Se não recebe (ex: `cadastrar_acesso(request)`, que lê o cliente do `POST`),
  cai para `Cliente.objects.get_by_usuario_vinculado(request.user)` — que resolve
  corretamente para um cliente-portal, já que esse tipo de usuário só pode agir dentro do
  próprio tenant (mesma premissa usada em `cliente_can_view_cliente`).
- Aplicado a **89 endpoints** em `clientes/views.py` (todas as ações de Acessos, Backups,
  VPN, Topologia, Documentos, Túneis, RPKI/IRR e Testes de Rede).

### Lacuna conhecida

Os módulos **Hotspot** (`clientes/hotspot_views.py`) e **Documentação de Rede**
(`clientes/ipam_views.py`) estão protegidos **só na interface** (a aba some) — as views de
ação desses dois arquivos ainda não têm `@modulo_habilitado_required` aplicado. O mesmo vale
para **Monitoramento**, que vive na app `monitoramento` (fora de `clientes`). Um cliente que
souber a URL exata ainda conseguiria usar essas três ferramentas por acesso direto mesmo
desabilitadas — risco baixo (uso do próprio tenant, não vazamento entre clientes), mas vale
uma passada futura se for necessário fechar 100%.

### Como aplicar em uma view nova

```python
from .decorators import modulo_habilitado_required

@login_required(login_url='login')
@modulo_habilitado_required('vpn')   # mesma chave usada em ClienteModulo.MODULO_CHOICES
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

- Render como cliente (usuário `is_staff=False` vinculado ao `Cliente`): aba presente por
  padrão, some quando o módulo é desabilitado.
- Acesso direto a `upload_vpn` como cliente com módulo desabilitado → redirect 302 para
  `listar_clientes` (bloqueado, não executa a view).
- GET de `cadastrar_cliente`: checkboxes de módulos e marcador `modulos_form_present`
  presentes no HTML.
- POST de criação desmarcando `vpn`/`hotspot` → `ClienteModulo` gravado corretamente
  (`habilitado=False` só nesses dois).
- POST de edição alterando a seleção (reabilita `vpn`, desabilita `backups`) → `ClienteModulo`
  atualizado corretamente via `update_or_create`.
- POST de edição **sem** `modulos_form_present` (simulando form incompleto) → nenhum
  `ClienteModulo` alterado (guard funcionando).
- `python manage.py check` sem apontar problemas.
- Templates `listar.html` e `cadastrar_cliente.html` compilam sem erro de sintaxe.

---

## Deploy

Migração `0085_clientemodulo` já aplicada em `crm_db` (banco compartilhado entre o worktree
de desenvolvimento e produção). Merge feito via fast-forward `claude/system-tools-modularization-d70813` → `main`,
gunicorn reiniciado.
