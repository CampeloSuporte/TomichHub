# Hotspot — Integração Disparo (WhatsApp HSM via Chatmix)

**Data de Implementação:** 2026-07-23
**Arquivos principais:** `clientes/models.py` (`ClienteIntegracaoDisparo`), `clientes/services.py`
(`ChatmixClient`), `clientes/tasks.py` (`enviar_disparo_hotspot_lead`), `clientes/hotspot_views.py`,
`clientes/templates/listar.html`, `clientes/hotspot_views.py` (formulário público do portal)
**Status:** ✅ Produção (Chatmix); Opa Suit listado na UI como "Em breve" (sem integração real ainda)

---

## Visão Geral

Quando um lead se cadastra no portal cativo do Hotspot (preenche nome + telefone para liberar o
WiFi), o CRM pode disparar automaticamente uma mensagem de WhatsApp (template HSM aprovado pela
Meta) via API de terceiros. A primeira empresa de integração suportada é a **Chatmix**; **Opa
Suit** já aparece na tela como opção, mas ainda sem client/endpoint implementado.

```
Lead se cadastra no portal do hotspot (nome + telefone)
  └─ HotspotLead.objects.create(...)
     └─ sinal post_save (clientes/models.py)
        └─ enviar_disparo_hotspot_lead.delay(lead.id)   [Celery, background]
           └─ Busca ClienteIntegracaoDisparo(cliente, provider='chatmix', habilitado=True)
              └─ Se não existe/desabilitado → ignora silenciosamente
              └─ Se existe → monta variables=... e faz POST em envios.bulkv2.chatmix.com.br/api
```

A configuração fica na aba **"Integração Disparo"**, ao lado de "Leads", dentro do painel de
detalhe de cada Hotspot (`clientes/templates/listar.html` → `#hsSubTabs`). A configuração é
**por Cliente** (tenant), não por Hotspot individual — um cliente com vários hotspots físicos usa
a mesma conta Chatmix para todos.

Doc oficial da API usada:
https://wiki.vmixsolucoes.com.br/chatmix-documentacao/integracoes/integracao-disparo/api-de-disparos-hsm

---

## Modelo de Dados

`clientes/models.py`:

```python
class ClienteIntegracaoDisparo(models.Model):
    PROVIDER_CHOICES = [
        ('chatmix', 'Chatmix'),
        ('opa_suit', 'Opa Suit'),
    ]

    cliente    = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='integracoes_disparo')
    provider   = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    habilitado = models.BooleanField(default=False)

    api_key     = models.CharField(max_length=255, blank=True, default='')
    api_token   = models.CharField(max_length=255, blank=True, default='')
    template_id = models.CharField(max_length=20, blank=True, default='')
    variaveis_modelo = models.JSONField(default=_disparo_variaveis_padrao)  # ex: ['{nome}', '{telefone}']

    criado_em     = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cliente', 'provider')
```

- `variaveis_modelo` é uma **lista ordenada** (JSONField) — uma entrada por variável exigida pelo
  template no Chatmix, na mesma ordem. Cada entrada pode ser `{nome}`/`{telefone}` (substituído
  pelo dado do lead) ou um texto fixo (ex: nome do negócio, contato de suporte). Ver seção
  "Variáveis do Template" abaixo — templates HSM reais costumam exigir mais de 2 variáveis.
- `unique_together (cliente, provider)` — um cliente tem no máximo 1 configuração por empresa de
  integração.

### Migrações

| Migração | O que faz |
|---|---|
| `0086_clienteintegracaodisparo.py` | Cria a tabela (versão inicial, com campo `mensagem_modelo` de texto livre) |
| `0087_remove_clienteintegracaodisparo_mensagem_modelo_and_more.py` | Substitui `mensagem_modelo` (texto livre) por `variaveis_modelo` (lista JSON) — inclui `RunPython` que converte configs já salvas, extraindo `{nome}`/`{telefone}` do texto antigo na mesma ordem em que apareciam (ver "Bug 1" abaixo) |

---

## Onde fica na UI

Sub-aba **"Integração Disparo"** (`#hs-tab-disparo`), irmã de Configuração/Banners/Leads dentro do
painel de detalhe do Hotspot em `clientes/templates/listar.html`. Cada empresa de integração é um
card:

- **Chatmix** (funcional): toggle habilitar/desabilitar (reaproveita a classe CSS
  `.modulo-toggle-switch` já usada no toggle de [Módulos do Cliente](MODULOS_CLIENTE.md)), campos
  Key/Token/ID do Template, lista dinâmica de variáveis (botão "Adicionar variável"/remover linha),
  botão "Salvar" e botão "Enviar teste" (dispara um HSM de verdade para um número informado, sem
  precisar de um lead real).
- **Opa Suit** (placeholder): card com opacidade reduzida, toggle `disabled`, texto "Em breve".

O JS de disparo vive no mesmo bloco `<script>` das outras abas do hotspot, com o prefixo `hsDisparo*`
(`hsCarregarDisparo`, `hsDisparoToggle`, `hsDisparoSalvar`, `hsDisparoTestar`,
`hsDisparoAddVariavelRow`/`hsDisparoColetarVariaveis` para a lista dinâmica). Segue o mesmo padrão
de fetch (`_hsFetch`, JSON body, CSRF via cookie) já usado por `hsCarregarLeads`/`hsCarregarBanners`.

---

## Endpoints

Todos em `clientes/hotspot_views.py`, protegidos por `@login_required` (mesmo nível das outras
telas de administração do hotspot — não há `@admin_required` extra aqui, igual às demais views de
`hotspot_views.py`):

| Endpoint | Método | Descrição |
|---|---|---|
| `/clientes/<cliente_id>/hotspot/disparo/` | GET | Retorna config de todos os providers (Chatmix + Opa Suit) para o cliente |
| `/clientes/<cliente_id>/hotspot/disparo/salvar/` | POST | Salva key/token/template/variáveis de um provider |
| `/clientes/<cliente_id>/hotspot/disparo/toggle/` | POST | Inverte `habilitado` (mesmo padrão do `toggle_modulo_cliente`) |
| `/clientes/<cliente_id>/hotspot/disparo/testar/` | POST | Envia um HSM de teste com os dados salvos, sem lead real |

---

## Serviço — `ChatmixClient`

`clientes/services.py`, seguindo o mesmo padrão do `EvolutionAPIClient` (`atendimento/services.py`):
`requests.Session()`, timeout explícito, retorno em tupla `(ok, detalhe)`.

```python
CHATMIX_API_URL = 'https://envios.bulkv2.chatmix.com.br/api'

class ChatmixClient:
    def __init__(self, key, token): ...
    def enviar_hsm(self, numero, variaveis, template_id, timeout=20):
        mensagem = 'variables=' + '|'.join(variaveis) + '||template=' + str(template_id)
        payload = {'key': self.key, 'token': self.token, 'numero': numero, 'mensagem': mensagem}
        ...
```

Funções auxiliares:

- `normalizar_numero_whatsapp(numero)` — normaliza qualquer telefone BR (com ou sem `+55`, com ou
  sem formatação) para `+55DDDNÚMERO`. **O `+55` é sempre implícito** — nem o lead no portal, nem
  o operador no teste do CRM, precisam digitá-lo.
- `montar_variaveis_mensagem(variaveis_modelo, lead)` — renderiza cada entrada de
  `variaveis_modelo`, substituindo `{nome}`/`{telefone}` pelos dados do lead (texto fixo passa
  direto); remove `|` do resultado (delimitador do formato `variables=`).

### Formato da API (doc Chatmix)

```
POST https://envios.bulkv2.chatmix.com.br/api
{
  "key": "...", "token": "...", "numero": "+5511999999999",
  "mensagem": "variables=valor1|valor2|valor3||template=181"
}
```

- `|` separa variáveis; `||` separa variáveis de configurações; `template=ID` define o template.
- O ID do template é o número no final da URL em Mensagens → Templates no Chatmix
  (ex: `.../templates/181` → ID `181`).

---

## Disparo automático (sinal + Celery)

`clientes/models.py`:

```python
@receiver(post_save, sender=HotspotLead)
def disparar_integracao_lead(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from .tasks import enviar_disparo_hotspot_lead
        enviar_disparo_hotspot_lead.delay(instance.id)
    except Exception:
        logging.getLogger(__name__).exception('Falha ao enfileirar disparo de integração p/ lead %s', instance.id)
```

**Por que `try/except` ao redor do `.delay()`:** os dois pontos que criam `HotspotLead`
(`hotspot_portal_conectar` e `hotspot_lead_pixel`, em `clientes/hotspot_views.py`) são endpoints
públicos e críticos do captive portal — o usuário precisa conseguir conectar no WiFi mesmo que o
broker Celery/Redis esteja fora do ar. Uma falha ao enfileirar a task nunca pode derrubar o
cadastro do lead nem travar a liberação de acesso.

`clientes/tasks.py` → `enviar_disparo_hotspot_lead(lead_id)` (`@shared_task(bind=True,
max_retries=2, default_retry_delay=30)`):

1. Busca o `HotspotLead` (se não existir mais, `status: ignorado`).
2. Busca `ClienteIntegracaoDisparo(cliente=lead.hotspot.cliente, provider='chatmix',
   habilitado=True)` — se não existir ou estiver desabilitado, `status: ignorado` (nenhum erro,
   nenhum envio; é o comportamento esperado enquanto o operador ainda está configurando).
3. Normaliza o telefone, monta as variáveis, chama `ChatmixClient.enviar_hsm(...)`.
4. Em falha: se for erro `HTTP 4xx` (config errada — credenciais/template/variáveis), **não
   reenfileira** (retry não resolve um erro de configuração, só gasta as tentativas). Em qualquer
   outra falha (rede, 5xx), reenfileira até 2 vezes.

---

## Bugs Corrigidos

### Bug 1 — Quantidade fixa de variáveis (só nome/telefone)

**Sintoma:** Chatmix respondia `HTTP 400: "Template requer 4 variaveis, mas apenas 2 foram
fornecidas"`.

**Causa:** a primeira versão tinha um único campo de texto livre (`mensagem_modelo`) e o sistema
tentava *inferir* as variáveis procurando `{nome}`/`{telefone}` na frase — sempre limitado a essas
duas, mas templates HSM reais no Chatmix podem exigir qualquer quantidade de variáveis (nome da
empresa, contato de suporte, etc).

**Correção:** campo `variaveis_modelo` (lista JSON) substituiu o texto livre. A tela de config
virou uma lista dinâmica (adicionar/remover linha), uma por variável exigida pelo template, na
mesma ordem — cada linha pode ser `{nome}`, `{telefone}` ou texto fixo. Migração `0087` com
`RunPython` converteu as configs já salvas, extraindo os placeholders do texto antigo.

### Bug 2 — `success: false` com HTTP 200 era tratado como sucesso

**Sintoma:** teste de envio reportava "enviado com sucesso" no CRM, mas a mensagem nunca chegava
no WhatsApp.

**Causa:** `ChatmixClient.enviar_hsm` só checava o status HTTP (`raise_for_status()`); a Chatmix
pode responder **HTTP 200** e ainda assim sinalizar falha no corpo JSON (`"success": false`) —
por exemplo template pendente de aprovação da Meta, número sem WhatsApp, etc.

**Correção:** o cliente agora faz `parse` do JSON de resposta e trata `success is False` como
falha mesmo com HTTP 200, propagando a mensagem de erro real (`data.get('message')`). A tela de
teste no CRM também passou a mostrar sempre a resposta completa da API (antes só mostrava
"Teste enviado!" genérico em caso de sucesso).

### Bug 3 — Lead sem o 9º dígito quebra o número no WhatsApp

**Sintoma:** número normalizado (`normalizar_numero_whatsapp`) ficava incompleto para leads que
digitaram telefone no formato antigo (10 dígitos, sem o 9), e a Chatmix não entrega a mensagem
nesse caso.

**Correção:** o formulário público do portal cativo (`clientes/hotspot_views.py` →
`_portal_page_html`, função `onSubmit()`) agora valida no submit que o telefone tem exatamente
**11 dígitos** (DDD + 9 + 8 números) antes de liberar a conexão — mesmo padrão visual de erro
(borda vermelha) já usado nos outros campos obrigatórios, com uma dica fixa abaixo do campo
("Com o 9: (DD) 9XXXX-XXXX").

---

## Como Configurar (passo a passo)

1. Abrir o cliente → aba **Hotspot** → selecionar um hotspot → sub-aba **Integração Disparo**.
2. No card **Chatmix**: preencher **Key** e **Token** (menu "Chaves para Acesso" no Chatmix,
   selecionando o canal WhatsApp).
3. Preencher o **ID do Template** (número no final da URL do template, em Mensagens → Templates
   no Chatmix).
4. Ajustar a lista de **variáveis** para bater exatamente com a quantidade e ordem que o template
   exige — uma linha por variável, usando `{nome}`/`{telefone}` ou texto fixo.
5. Clicar em **Salvar**.
6. Clicar em **Enviar teste**, informando só o número nacional com o 9 (ex: `74988737970` — o
   `+55` é automático). Conferir a resposta completa exibida na tela.
7. Só depois de confirmar que o teste chegou de verdade no WhatsApp, ligar o **toggle** do card
   — a partir daí, todo novo lead cadastrado nesse hotspot dispara a mensagem automaticamente.

### Exemplo de corpo de template para cadastrar no Chatmix

Ver seção "Variáveis do Template" — exemplo com 4 variáveis (nome, telefone, nome do negócio,
contato de suporte), usando a sintaxe `{{1}}`–`{{4}}` do editor de templates da Meta/Chatmix
(inseridas automaticamente pelo botão "+ Variável" no editor deles).

---

## Limitações Conhecidas

- **Opa Suit** aparece na UI mas não tem client/endpoint implementado — `hotspot_disparo_salvar`
  recusa qualquer `provider != 'chatmix'` com erro claro ("Esta integração ainda não está
  disponível").
- Configuração é **por Cliente**, não por Hotspot — todos os hotspots do mesmo cliente
  compartilham a mesma conta Chatmix. Se um cliente precisar de contas diferentes por hotspot,
  o modelo precisaria de FK para `HotspotConfig` em vez de `Cliente` (não implementado).
- Sem retry infinito: falhas de configuração (HTTP 4xx) não são reenfileiradas — o operador
  precisa corrigir e reenviar manualmente (ou aguardar o próximo lead).
- Sem persistência de log de disparos enviados/falhados por lead (fica só no log do Celery/logger
  padrão do Django) — não há tela de histórico de envios no CRM.

---

## Deploy

Migrações `0086` e `0087` aplicadas em `crm_db` (banco compartilhado entre os worktrees de
desenvolvimento e produção). Merge feito via fast-forward `claude/chatmix-integration-config-1bcbba`
→ `main`, `gunicorn` e `celery` reiniciados a cada alteração (`services.py`/`tasks.py` exigem
restart do `celery` também, não só do `gunicorn`, já que o worker mantém os módulos Python
carregados em memória).
