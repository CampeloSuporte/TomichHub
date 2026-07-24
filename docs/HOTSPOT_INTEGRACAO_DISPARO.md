# Hotspot — Integração Disparo (WhatsApp HSM via Chatmix / Opa Suite)

**Data de Implementação:** 2026-07-23
**Arquivos principais:** `clientes/models.py` (`ClienteIntegracaoDisparo`), `clientes/services.py`
(`ChatmixClient`, `OpaSuiteClient`), `clientes/tasks.py` (`enviar_disparo_hotspot_lead`),
`clientes/hotspot_views.py`, `clientes/templates/listar.html`
**Status:** ✅ Produção — Chatmix e Opa Suite funcionais; ambos podem ficar habilitados ao mesmo
tempo para o mesmo cliente (a task dispara em todos os providers habilitados)

---

## Visão Geral

Quando um lead se cadastra no portal cativo do Hotspot (preenche nome + telefone para liberar o
WiFi), o CRM pode disparar automaticamente uma mensagem de WhatsApp (template HSM aprovado pela
Meta) via API de terceiros. Duas empresas de integração são suportadas: **Chatmix** e
**Opa Suite**.

```
Lead se cadastra no portal do hotspot (nome + telefone)
  └─ HotspotLead.objects.create(...)
     └─ sinal post_save (clientes/models.py)
        └─ enviar_disparo_hotspot_lead.delay(lead.id)   [Celery, background]
           └─ Busca todos os ClienteIntegracaoDisparo(cliente, habilitado=True)
              └─ Nenhum habilitado → ignora silenciosamente
              └─ Para cada provider habilitado (chatmix e/ou opa_suit):
                 ├─ chatmix    → ChatmixClient.enviar_hsm(...)    → POST envios.bulkv2.chatmix.com.br/api
                 └─ opa_suit   → OpaSuiteClient.enviar_template(...) → POST {dominio}/api/v1/template/send
```

A configuração fica na aba **"Integração Disparo"**, ao lado de "Leads", dentro do painel de
detalhe de cada Hotspot (`clientes/templates/listar.html` → `#hsSubTabs`). A configuração é
**por Cliente** (tenant), não por Hotspot individual — um cliente com vários hotspots físicos usa
a mesma conta (Chatmix e/ou Opa Suite) para todos.

Docs oficiais das APIs usadas:
- Chatmix: https://wiki.vmixsolucoes.com.br/chatmix-documentacao/integracoes/integracao-disparo/api-de-disparos-hsm
- Opa Suite: https://api.opasuite.com.br/ (coleção Postman pública — "Templates de mensagem → Enviar template")

---

## Modelo de Dados

`clientes/models.py`:

```python
class ClienteIntegracaoDisparo(models.Model):
    PROVIDER_CHOICES = [
        ('chatmix', 'Chatmix'),
        ('opa_suit', 'Opa Suite'),
    ]

    cliente    = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='integracoes_disparo')
    provider   = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    habilitado = models.BooleanField(default=False)

    api_key     = models.CharField(max_length=255, blank=True, default='')  # Chatmix
    api_token   = models.CharField(max_length=255, blank=True, default='')  # Chatmix (token) / Opa Suite (Bearer)
    api_dominio = models.CharField(max_length=255, blank=True, default='')  # só Opa Suite (multi-tenant por domínio)
    canal_id    = models.CharField(max_length=64, blank=True, default='')   # só Opa Suite (id do canal de comunicação)
    template_id = models.CharField(max_length=64, blank=True, default='')
    variaveis_modelo = models.JSONField(default=_disparo_variaveis_padrao)  # ex: ['{nome}', '{telefone}']

    criado_em     = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cliente', 'provider')
```

- `variaveis_modelo` é uma **lista ordenada** (JSONField) — uma entrada por variável exigida pelo
  template, na mesma ordem, independente do provider. Cada entrada pode ser `{nome}`/`{telefone}`
  (substituído pelo dado do lead) ou um texto fixo (ex: nome do negócio, contato de suporte).
  Templates HSM reais costumam exigir mais de 2 variáveis.
- `api_dominio`/`canal_id` só são usados pelo Opa Suite (ficam vazios para Chatmix, que tem 1
  endpoint fixo e não usa "canal") — ver seção "Por que os providers têm campos diferentes" abaixo.
- `unique_together (cliente, provider)` — um cliente tem no máximo 1 configuração por empresa de
  integração, mas **pode ter as duas habilitadas ao mesmo tempo** (a task dispara em ambas).

### Migrações

| Migração | O que faz |
|---|---|
| `0086_clienteintegracaodisparo.py` | Cria a tabela (versão inicial, com campo `mensagem_modelo` de texto livre) |
| `0087_remove_clienteintegracaodisparo_mensagem_modelo_and_more.py` | Substitui `mensagem_modelo` (texto livre) por `variaveis_modelo` (lista JSON) — inclui `RunPython` que converte configs já salvas, extraindo `{nome}`/`{telefone}` do texto antigo na mesma ordem em que apareciam (ver "Bug 1" abaixo) |
| `0088_clienteintegracaodisparo_api_dominio_and_more.py` | Adiciona `api_dominio`/`canal_id` (Opa Suite), amplia `template_id` de 20 para 64 caracteres (Chatmix usa ID numérico curto; Opa Suite usa ObjectId Mongo de 24 caracteres) e corrige o label do choice `opa_suit` de "Opa Suit" para "Opa Suite" |

---

## Onde fica na UI

Sub-aba **"Integração Disparo"** (`#hs-tab-disparo`), irmã de Configuração/Banners/Leads dentro do
painel de detalhe do Hotspot em `clientes/templates/listar.html`. Cada empresa de integração é um
card:

- **Chatmix**: toggle habilitar/desabilitar (reaproveita a classe CSS `.modulo-toggle-switch` já
  usada no toggle de [Módulos do Cliente](MODULOS_CLIENTE.md)), campos Key/Token/ID do Template,
  lista dinâmica de variáveis (botão "Adicionar variável"/remover linha), botão "Salvar" e botão
  "Enviar teste" (dispara um HSM de verdade para um número informado, sem precisar de um lead
  real).
- **Opa Suite**: mesmo layout, mas com campos Domínio da conta/Token (Bearer)/ID do Canal/ID do
  Template + a mesma lista dinâmica de variáveis.

Cada card é independente — um cliente pode ter só Chatmix, só Opa Suite, ou os dois habilitados
ao mesmo tempo (nesse caso todo lead novo dispara duas mensagens, uma por provider).

O JS de disparo vive no mesmo bloco `<script>` das outras abas do hotspot, com o prefixo `hsDisparo*`
(`hsCarregarDisparo`, `hsDisparoToggle`, `hsDisparoSalvar`, `hsDisparoTestar`,
`hsDisparoAddVariavelRow`/`hsDisparoColetarVariaveis` para a lista dinâmica). Segue o mesmo padrão
de fetch (`_hsFetch`, JSON body, CSRF via cookie) já usado por `hsCarregarLeads`/`hsCarregarBanners`.

### Painel de ajuda embutido (card Chatmix)

Botão **"Onde acho Key/Token/ID do Template?"** (`hsDisparoToggleAjudaChatmix()`) dentro do card
Chatmix abre/fecha um painel (`#hsd-chatmix-ajuda`) com um mini-guia visual recriando as telas do
Chatmix — não são screenshots reais, são mockups em HTML/CSS que reproduzem a estrutura das telas:

1. **Chaves para acesso**: mostra que o campo **Canais** aceita seleção múltipla (Ctrl/Cmd) e que
   Key/Token só aparecem depois de marcar **"Ver opções avançadas"**. Destaca que o canal errado
   selecionado nessa chave é a causa mais comum do erro `"Template nao encontrado"` (ver Bug 4).
2. **Mensagens → Mensagens Templates → Acessar**: mostra que o ID do template fica no final da
   URL do navegador ao abrir o template (ex: `.../templates/21606`).
3. **Sugestão de corpo de mensagem** pronta para colar no template no Chatmix (boas-vindas +
   oferta), com botão **"Copiar"** (`hsDisparoCopiarSugestao()`, via `navigator.clipboard`). O
   texto usa `{{1}}` (sintaxe Meta/WhatsApp, inserida pelo botão "+ Variável" no editor deles) para
   a única variável (nome) — o operador só precisa configurar 1 linha (`{nome}`) em "Variáveis do
   template" para bater com esse modelo.

**Detalhe de implementação:** escrever `{{1}}` literal no template Django quebraria, porque `{{ }}`
é a sintaxe de variável do próprio Django (`{{1}}` seria interpretado como literal inteiro `1`,
perdendo as chaves). A sugestão de mensagem usa
`{% templatetag openvariable %}1{% templatetag closevariable %}` para escapar isso e imprimir
`{{1}}` como texto puro — confirmado renderizando o fragmento isolado com `Template(...).render()`.

---

## Endpoints

Todos em `clientes/hotspot_views.py`, protegidos por `@login_required` (mesmo nível das outras
telas de administração do hotspot — não há `@admin_required` extra aqui, igual às demais views de
`hotspot_views.py`):

| Endpoint | Método | Descrição |
|---|---|---|
| `/clientes/<cliente_id>/hotspot/disparo/` | GET | Retorna config de todos os providers (Chatmix + Opa Suite) para o cliente |
| `/clientes/<cliente_id>/hotspot/disparo/salvar/` | POST | Salva credenciais/domínio/canal/template/variáveis de um provider |
| `/clientes/<cliente_id>/hotspot/disparo/toggle/` | POST | Inverte `habilitado` (mesmo padrão do `toggle_modulo_cliente`) |
| `/clientes/<cliente_id>/hotspot/disparo/testar/` | POST | Envia um disparo de teste com os dados salvos, sem lead real (branch por `provider` no backend) |

---

## Serviços — `ChatmixClient` e `OpaSuiteClient`

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

class OpaSuiteClient:
    def __init__(self, dominio, token): ...  # Authorization: Bearer <token>
    def enviar_template(self, numero, canal_id, template_id, variaveis, timeout=20):
        payload = {
            'contato': {'canalCliente': numero},
            'template': {'_id': template_id, 'variaveis': list(variaveis)},
            'canal': canal_id,
        }
        # POST {dominio}/api/v1/template/send
        ...
```

Funções auxiliares (compartilhadas pelos dois providers):

- `normalizar_numero_whatsapp(numero)` — normaliza qualquer telefone BR (com ou sem `+55`, com ou
  sem formatação) para `+55DDDNÚMERO`. **O `+55` é sempre implícito** — nem o lead no portal, nem
  o operador no teste do CRM, precisam digitá-lo.
- `montar_variaveis_mensagem(variaveis_modelo, lead)` — renderiza cada entrada de
  `variaveis_modelo`, substituindo `{nome}`/`{telefone}` pelos dados do lead (texto fixo passa
  direto); remove `|` do resultado (delimitador do `variables=` do Chatmix — inofensivo para o
  Opa Suite, que usa JSON puro e não tem esse delimitador).

### Formato da API — Chatmix

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
- Resposta de sucesso pode vir com HTTP 200 e `"success": false` no corpo — ver "Bug 2".

### Formato da API — Opa Suite

```
POST {dominio}/api/v1/template/send
Authorization: Bearer <token>

{
  "contato": {"canalCliente": "+5511999999999"},
  "template": {"_id": "624c358355802dbdd2eb944a", "variaveis": ["valor1", "valor2"]},
  "canal": "622f8e310d7149ee66bb654c"
}
```

Resposta de sucesso documentada:
```json
{"status": "success", "code": 200, "data": {"message": "Template has been succesfully sent.", "messageSentId": "..."}}
```

### Por que os providers têm campos diferentes

| Campo | Chatmix | Opa Suite |
|---|---|---|
| Endpoint base | Fixo global (`envios.bulkv2.chatmix.com.br`) | **Por conta** — cada cliente Opa Suite tem seu próprio domínio (`api_dominio`) |
| Autenticação | `key` + `token` no corpo JSON | `Authorization: Bearer <token>` (só `api_token`, `api_key` fica vazio) |
| Identificador do canal | Não existe — 1 conta = 1 canal implícito | Obrigatório (`canal_id`) — uma conta Opa Suite pode ter vários canais de comunicação (WhatsApp/Telegram/etc), então o envio precisa dizer qual usar |
| ID do template | Numérico curto (ex: `181`) | ObjectId Mongo, 24 caracteres hex (ex: `624c358355802dbdd2eb944a`) — por isso `template_id` foi ampliado para `max_length=64` na migração `0088` |
| Formato de `variables` | String posicional (`variables=v1\|v2\|\|template=ID`) | Array JSON (`template.variaveis: [v1, v2]`) |

Onde conseguir cada dado no Opa Suite: **Token** → cadastro de usuários, criando um perfil de
permissões do tipo "API". **Canal** → listagem de canais de comunicação (endpoint `GET
/api/v1/canal-comunicacao/`, ou direto no painel). **Template `_id`** → tela de Templates de
Mensagem (endpoint `GET /api/v1/template` para listar, ou o `_id` visível ao abrir o template).

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
2. Busca **todas** as `ClienteIntegracaoDisparo(cliente=lead.hotspot.cliente, habilitado=True)` —
   se nenhuma, `status: ignorado` (nenhum erro, nenhum envio; é o comportamento esperado enquanto
   o operador ainda está configurando).
3. Para **cada** config habilitada (podem ser Chatmix e Opa Suite ao mesmo tempo): normaliza o
   telefone, monta as variáveis, e chama o client certo (`ChatmixClient.enviar_hsm(...)` ou
   `OpaSuiteClient.enviar_template(...)`) conforme `config.provider`. Se os campos obrigatórios
   daquele provider não estiverem preenchidos, pula com `status: ignorado` para aquele provider
   específico (sem travar os outros).
4. Em falha de qualquer provider: se for erro `HTTP 4xx` (config errada — credenciais/template/
   variáveis/canal), **não reenfileira aquele resultado** (retry não resolve um erro de
   configuração, só gasta as tentativas). Se **algum** provider teve falha não-4xx (rede, 5xx), a
   task inteira é reenfileirada (até 2 vezes) — os providers que já enviaram com sucesso não são
   reenviados na prática, pois o retry reprocessa a lista inteira; para o volume/latência baixos
   desse fluxo isso é aceitável, mas é bom saber que um retry pode reenviar um provider que já
   tinha ido bem se outro falhou.

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

### Bug 4 — Diagnósticos de teste que não são bug do CRM

Dois sintomas encontrados durante os testes reais que **não** eram falha do sistema — documentados
aqui porque são fáceis de confundir com bug:

- **`"success":true, "status":"queue"` mas a mensagem não chega:** a Chatmix aceitou e enfileirou
  o envio (resposta genuína, sem o problema do Bug 2), mas a entrega real depende da Meta ter
  **aprovado o template**. Enquanto "Pendente", a Meta bloqueia a entrega mesmo com a Chatmix
  aceitando o envio na fila. Conferir o status do template em Mensagens → Templates no Chatmix.
- **`"Template nao encontrado"` mesmo o template existindo** (confirmado pela URL do painel): a
  causa mais comum é a **Key/Token usada não ter o canal certo marcado**. No Chatmix, cada "Chave
  de acesso" (Integrações → Chaves para acesso) tem um campo **Canais** com seleção múltipla
  (Ctrl/Cmd) — se o canal de WhatsApp onde o template está cadastrado não estiver marcado nessa
  chave específica, a API não o encontra. Verificado no banco (`ClienteIntegracaoDisparo.template_id`)
  que o valor salvo batia exatamente com o ID informado, descartando corrupção de dado no CRM.

Esses dois casos motivaram o painel de ajuda visual descrito em "Onde fica na UI → Painel de ajuda
embutido" — com destaque justamente para o campo Canais.

### Bug 5 — Opa Suite: Canal e Template trocados (`"Communication channel not found"`)

**Sintoma:** teste de envio retornava `HTTP 404: {"error":"NOT_FOUND_ERROR","message":"Communication
channel not found."}`.

**Causa:** os valores salvos em **Canal** e **Template** estavam invertidos/confundidos. Ao contrário
do Chatmix (onde o ID do template aparece direto na URL do painel), o Opa Suite não expõe o `_id`
real (ObjectId Mongo) de forma óbvia na interface — o operador havia colado o `_id` de um **canal**
de comunicação no campo Template, e um valor de 8 caracteres (`uej2uHCH`, formato que não bate com
nenhum ObjectId Mongo de 24 caracteres) no campo Canal, que não correspondia a nada na conta.

**Diagnóstico:** como as credenciais (domínio + token) já estavam salvas no CRM, foi possível
consultar a própria API do Opa Suite diretamente (`GET {dominio}/api/v1/canal-comunicacao/` e
`GET {dominio}/api/v1/template`, com o Bearer token) para listar os canais e templates **reais** da
conta e comparar com o que estava configurado — confirmando que:
- o valor salvo em "Template" (`63f61c53f4752cb43ee88f77`) era na verdade o `_id` de um canal
  WhatsApp ativo ("whatsapp api ... dialog360");
- o valor salvo em "Canal" (`uej2uHCH`) não batia com nenhum dos 10 templates existentes na conta.

**Correção:** `canal_id` foi corrigido para o `_id` do canal confirmado via API; o operador então
localizou o template correto entre os listados e atualizou o campo Template pela própria tela do
CRM. Nenhuma mudança de código foi necessária — foi um erro de preenchimento na configuração, mas
vale documentar porque o padrão de diagnóstico (consultar os endpoints de listagem do provider
usando as próprias credenciais salvas) é reaproveitável para qualquer erro futuro de "não
encontrado" nesta integração, com qualquer provider.

**Dica para configurar Canal/Template do Opa Suite sem adivinhar:** se a interface não deixar claro
qual é o `_id` real de um canal ou template, uma chamada `GET {dominio}/api/v1/canal-comunicacao/`
ou `GET {dominio}/api/v1/template` (com o Bearer token gerado no cadastro de usuário) retorna a
lista completa com os `_id`s corretos — mais confiável do que tentar inferir pela URL do navegador.

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

O próprio painel de ajuda do card Chatmix (botão "Onde acho Key/Token/ID do Template?") já traz
uma sugestão pronta com botão "Copiar" — uma mensagem de boas-vindas + oferta, usando 1 variável
(`{{1}}` = nome):

```
🎉 Parabéns, {{1}}!

Você acabou de se conectar ao nosso Wi-Fi Grátis e foi selecionado para ganhar um SUPER DESCONTO
na contratação da internet da Conecta ISP! 🚀

✅ Internet 100% fibra óptica
⚡ Alta velocidade e estabilidade
💰 Oferta exclusiva por tempo limitado

📲 Responda esta mensagem e descubra o desconto especial que preparamos para você!
```

(troque "Conecta ISP" pelo nome do negócio do cliente sendo configurado). Para templates com mais
variáveis, a sintaxe é a mesma (`{{1}}`–`{{N}}`, do editor de templates da Meta/Chatmix, inseridas
automaticamente pelo botão "+ Variável" no editor deles) — só ajustar a quantidade de linhas em
"Variáveis do template" no CRM para bater.

### Configurar o Opa Suite

1. Mesma aba, card **Opa Suite**.
2. **Domínio da conta**: URL base do seu Opa Suite (ex: `https://minhaempresa.opasuite.com.br`).
3. **Token**: gerado em Cadastro de Usuários → criar um usuário com perfil de permissões do tipo
   "API" → o token fica disponível nesse cadastro.
4. **ID do Canal**: liste os canais de comunicação (`GET /api/v1/canal-comunicacao/`, ou no
   painel do Opa Suite) e copie o `_id` do canal WhatsApp que vai fazer o envio.
5. **ID do Template**: liste os templates (`GET /api/v1/template`, ou abrindo o template no
   painel) e copie o `_id` (formato ObjectId Mongo, 24 caracteres).

**Cuidado para não trocar Canal com Template** (ver Bug 5): diferente do Chatmix, o painel do Opa
Suite não deixa o `_id` óbvio na tela/URL — prefira sempre confirmar os dois valores chamando os
endpoints de listagem acima com o token, em vez de copiar algo direto da interface.
6. Ajustar as **variáveis** (mesma lógica do Chatmix), Salvar, Enviar teste, e só então habilitar
   o toggle.

**Nota:** por padrão o Opa Suite **bloqueia** o envio de template para um contato que já está em
atendimento em andamento (a API tem um parâmetro `allowSendingToStartedCustomerService` para
liberar isso, mas o CRM não expõe esse campo hoje — se for necessário, é uma extensão pequena no
`OpaSuiteClient.enviar_template` e no card de configuração).

---

## Limitações Conhecidas

- Configuração é **por Cliente**, não por Hotspot — todos os hotspots do mesmo cliente
  compartilham a mesma conta (Chatmix e/ou Opa Suite). Se um cliente precisar de contas
  diferentes por hotspot, o modelo precisaria de FK para `HotspotConfig` em vez de `Cliente`
  (não implementado).
- Sem retry infinito: falhas de configuração (HTTP 4xx) não são reenfileiradas — o operador
  precisa corrigir e reenviar manualmente (ou aguardar o próximo lead).
- Quando os dois providers estão habilitados e só um falha com erro transitório (rede/5xx), o
  retry reprocessa **os dois** — o provider que já tinha enviado com sucesso pode receber uma
  segunda mensagem no reenvio. Baixo risco na prática (falha transitória + 2 providers habilitados
  ao mesmo tempo é uma combinação rara), mas é uma limitação conhecida do design atual.
- Sem persistência de log de disparos enviados/falhados por lead (fica só no log do Celery/logger
  padrão do Django) — não há tela de histórico de envios no CRM.
- Opa Suite: o parâmetro `allowSendingToStartedCustomerService` da API (permite reenviar template
  para contato já em atendimento) não é exposto na configuração do CRM.

---

## Deploy

Migrações `0086`, `0087` e `0088` aplicadas em `crm_db` (banco compartilhado entre os worktrees de
desenvolvimento e produção). Merge feito via fast-forward `claude/chatmix-integration-config-1bcbba`
→ `main`, `gunicorn` e `celery` reiniciados a cada alteração (`services.py`/`tasks.py` exigem
restart do `celery` também, não só do `gunicorn`, já que o worker mantém os módulos Python
carregados em memória).
