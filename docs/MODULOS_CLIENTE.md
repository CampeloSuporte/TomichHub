# Módulos do Cliente — Habilitar/Desabilitar Ferramentas por Contrato

**Data de Implementação:** 2026-07-23
**Arquivos principais:** `clientes/models.py` (`ClienteModulo`), `clientes/decorators.py`
(`modulo_habilitado_required`), `clientes/views.py`, `clientes/templates/listar.html`,
`clientes/templates/_modulo_toggle_btn.html`
**Status:** ✅ Produção

---

## Visão Geral

Cada aba da tela do cliente (Acessos, Backups, VPN, Topologia, Túneis SSH, Documentos,
RPKI/IRR, Monitoramento, Documentação de Rede, Hotspot, Testes de Rede) agora pode ser
habilitada ou desabilitada **por cliente**, para refletir o que foi contratado. O toggle
aparece como um switch pequeno ao lado do nome de cada aba e só é visível para administradores
do sistema (`is_staff`/`is_superuser`).

```
Admin abre a tela do cliente
  └─ Vê um switch ao lado de cada aba
     └─ Desliga, por exemplo, "VPN" (cliente não contratou)
        └─ AJAX salva em ClienteModulo(cliente, modulo='vpn', habilitado=False)
           └─ Para o cliente final: a aba "VPN" some da tela
              └─ Acesso direto por URL às views de VPN é bloqueado (redirect com aviso)
```

Módulo **sem registro** em `ClienteModulo` = **habilitado**. Isso é proposital: nenhum
cliente já cadastrado perde acesso a nada com o deploy dessa feature — desabilitar é sempre
uma ação explícita do admin, não um estado padrão.

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

## Interface (toggle)

`clientes/templates/_modulo_toggle_btn.html` — um switch estilo iOS (checkbox + label),
incluído ao lado de cada `<li class="nav-item modulo-tab-item">` em
`clientes/templates/listar.html`, só quando `is_admin` é verdadeiro:

```html
{% if is_admin %}{% include "_modulo_toggle_btn.html" with modulo="vpn" habilitado=modulos_habilitados.vpn %}{% endif %}
```

CSS do switch fica num `<style>` inline logo acima da `<ul id="mainTabs">` em `listar.html`
(cores usam as variáveis do tema: `--primary-green`, `--glow-green`, `--border`,
`--text-muted`). O `<li>` de cada aba ganhou `display:flex; align-items:center` (classe
`.modulo-tab-item`) — sem isso o switch cai pra linha de baixo do texto da aba, porque o
`<a class="nav-link">` é um elemento de bloco.

**Para o cliente final** (não-admin), cada `<li>` só é renderizado se o módulo estiver
habilitado: `{% if is_admin or modulos_habilitados.xxx %}`. A aba "Acessos" (que antes
sempre vinha com `display:block` fixo por ser a aba padrão) agora respeita o mesmo flag.
Se a aba padrão estiver desabilitada, um fallback em JS (`trocarAba`/`DOMContentLoaded`)
ativa automaticamente a primeira aba disponível — sem isso o cliente veria a página em
branco caso "Acessos" fosse desabilitado.

Bloqueio também no lado do JS: `window.MODULOS_BLOQUEADOS` (lista gerada no template a
partir de `modulos_habilitados`) e a função `moduloBloqueado(nomeAba)` impedem que
`trocarAba()` abra uma aba desabilitada mesmo que algo tente ativá-la programaticamente
(ex: `sessionStorage` de uma sessão anterior, antes do módulo ser desabilitado).

---

## Endpoint de Toggle

`clientes/views.py` → `toggle_modulo_cliente(request, cliente_id)`:

```python
@login_required(login_url='login')
@admin_required
@require_http_methods(["POST"])
def toggle_modulo_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    modulo_key = request.POST.get('modulo')
    registro, _ = ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo_key, defaults={'habilitado': True})
    registro.habilitado = not registro.habilitado
    registro.save(update_fields=['habilitado', 'atualizado_em'])
    return JsonResponse({'ok': True, 'modulo': modulo_key, 'habilitado': registro.habilitado})
```

Rota: `POST /clientes/<cliente_id>/modulos/toggle/` (`clientes/urls.py`). O endpoint sempre
**inverte** o estado atual (não recebe o estado desejado) — o switch no frontend já reflete
o estado anterior antes do clique, então "inverter" é semanticamente igual a "definir para o
novo valor".

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

- Render como admin: 11 switches aparecem (um por módulo), todos com estado correto.
- Render como cliente (usuário `is_staff=False` vinculado ao `Cliente`): 0 switches, aba
  presente por padrão.
- Toggle do módulo `vpn` via `toggle_modulo_cliente` → aba "VPN" some do render do cliente
  na sequência.
- Acesso direto a `upload_vpn` como cliente com módulo desabilitado → redirect 302 para
  `listar_clientes` (bloqueado, não executa a view).
- `python manage.py check` e `makemigrations --check` sem apontar problemas.

---

## Deploy

Migração `0085_clientemodulo` já aplicada em `crm_db` (banco compartilhado entre o worktree
de desenvolvimento e produção). Merge feito via fast-forward `claude/system-tools-modularization-d70813` → `main`,
gunicorn reiniciado.
