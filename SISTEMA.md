# CRM Tomich — Documentação do Sistema

> Sistema de gerenciamento de clientes para provedores de internet (ISP), com foco em gestão de redes, acessos, IPAM, VPN, RPKI/IRR, backups, financeiro e monitoramento.

---

## Sumário

1. [Visão Geral](#visão-geral)
2. [Stack Tecnológica](#stack-tecnológica)
3. [Arquitetura](#arquitetura)
4. [Aplicações (Apps Django)](#aplicações-apps-django)
5. [Modelos de Dados](#modelos-de-dados)
6. [Funcionalidades Principais](#funcionalidades-principais)
7. [Integrações Externas](#integrações-externas)
8. [Infraestrutura e Deploy](#infraestrutura-e-deploy)
9. [Histórico de Alterações](#histórico-de-alterações)

---

## Visão Geral

O CRM Tomich é uma aplicação web Django voltada para provedores de internet que centraliza:

- Gerenciamento de clientes e seus equipamentos de rede
- Acesso remoto via terminal SSH/Telnet diretamente no navegador
- Gerenciamento de VPNs (OpenVPN e WireGuard)
- IPAM nativo (VLANs, prefixos, sub-redes, IPs, túneis VPN)
- Validação RPKI e atualização de registros IRR no TC (NIC.br)
- Backup automatizado de configurações de equipamentos
- Monitoramento via Zabbix
- Gestão financeira (faturas, consultorias, aluguel de IPv4, vendas)
- Base de conhecimento (Wiki)
- Topologia de rede interativa

---

## Stack Tecnológica

| Componente | Tecnologia |
|---|---|
| Framework web | Django 5.2.7 |
| Banco de dados | PostgreSQL (`crm_db`) |
| Cache | Redis (`redis://127.0.0.1:6379/1`) |
| Fila de tarefas | Celery + Redis broker (`redis://localhost:6379/0`) |
| Tarefas agendadas | Django-Celery-Beat |
| WebSockets | Django Channels (ASGI) |
| Servidor HTTP | Gunicorn (sync workers) |
| Servidor ASGI | Daphne (WebSocket/async) |
| Frontend | Bootstrap 5 + CSS customizado (tema dark) |
| Automação de rede | Netmiko, Paramiko, Pexpect |
| Linguagem | Python 3.12 |
| Fuso horário | America/Sao_Paulo |
| Idioma | pt-BR |

---

## Arquitetura

```
Nginx (reverse proxy)
  ├── Gunicorn  → Django (requisições HTTP síncronas)
  └── Daphne   → Django Channels (WebSockets — terminal SSH/Telnet)

Django
  ├── PostgreSQL  (banco principal)
  ├── Redis       (cache + broker Celery)
  └── Celery      (backups, tarefas assíncronas)

Acesso a equipamentos de rede
  ├── ProxyServer (túnel SSH) → equipamento na rede do cliente
  ├── Terminal WebSocket      → SSH/Telnet via navegador
  └── Winbox VNC             → GUI MikroTik via navegador
```

### Fluxo de acesso remoto a equipamentos

1. O cliente possui equipamentos acessíveis apenas dentro de sua rede privada.
2. Um **ProxyServer** (túnel SSH reverso) é configurado entre o servidor CRM e um host na rede do cliente.
3. O CRM encaminha conexões SSH/Telnet/HTTP pelo túnel, tornando o equipamento acessível via navegador.
4. O terminal WebSocket usa Django Channels para manter a sessão interativa em tempo real.

---

## Aplicações (Apps Django)

### `clientes` — App principal

Gerencia tudo relacionado a clientes e suas redes: acessos, equipamentos, VPN, IPAM, backups, RPKI/IRR, topologia, chamados.

**URLs principais:** `/clientes/`

### `home` — Dashboard e administração

Painel geral com estatísticas de chamados, blocos RPKI/IRR inválidos e configurações de sistema (SMTP global).

**URLs principais:** `/home/`

### `financeiro` — Gestão financeira

Faturas, consultorias, aluguel de blocos IPv4, vendas de equipamentos, análise de aging.

**URLs principais:** `/financeiro/`

### `wiki` — Base de conhecimento

Artigos em Markdown, snippets de código, filtros por fabricante e tags.

**URLs principais:** `/wiki/`

### `monitoramento` — Monitoramento via Zabbix

Integração com API Zabbix, topologias de monitoramento, status em tempo real.

**URLs principais:** `/monitoramento/`

### `usuario` — Autenticação

Registro e autenticação de usuários.

**URLs principais:** `/auth/`

### `funcao_equipamento` e `modelo_equipamento`

Cadastro de funções (Roteador, Switch, Firewall, OLT...) e modelos de equipamentos.

---

## Modelos de Dados

### Clientes e Infraestrutura

| Modelo | Descrição |
|---|---|
| `Cliente` | Empresa cliente com CNPJ, endereço, contatos |
| `Acesso` | Credenciais de acesso a equipamento (SSH, Telnet, HTTP, Winbox) |
| `ProxyServer` | Túnel SSH para acesso a redes privadas de clientes |
| `Documento` | Arquivos anexados ao cliente |

### Chamados

| Modelo | Descrição |
|---|---|
| `Categoria` | Categorias de chamados |
| `Chamado` | Ticket de suporte com prioridade, departamento, status |
| `ComentarioChamado` | Comentários em chamados (interno/externo) |
| `ComentarioAcesso` | Comentários em acessos de equipamentos |

### VPN

| Modelo | Descrição |
|---|---|
| `ArquivoVPN` | Arquivos de configuração VPN (OpenVPN, WireGuard) |
| `VPNServidorConfig` | Configuração global do servidor WireGuard (singleton) |
| `VPNWireGuard` | Configuração WireGuard por cliente |
| `OpenVPNConfig` | Configuração servidor OpenVPN no MikroTik |
| `OpenVPNUsuario` | Usuários/peers OpenVPN |

### IPAM (Nativo)

| Modelo | Descrição |
|---|---|
| `IPAMVlan` | VLANs (número, nome, status) |
| `IPAMPrefixo` | Prefixos IP (/24, /22, etc.) com utilização |
| `IPAMSubRede` | Sub-redes com gateway |
| `IPAMEndereco` | Endereços IP individuais com hostname, MAC, status |
| `IPAMVpnDoc` | Documentação de túneis VPN (IPSec, GRE, L2TP, MPLS, WireGuard, OpenVPN) |
| `DocumentacaoRedeConfig` | Config de integração PHP IPAM / NetBox |

### Backups

| Modelo | Descrição |
|---|---|
| `BackupTemplate` | Templates de comandos por fabricante (Cisco, Huawei, MikroTik...) |
| `BackupLog` | Histórico de execuções com status e caminho do arquivo |

### RPKI / IRR

| Modelo | Descrição |
|---|---|
| `BlocoIP` | Bloco IPv4/IPv6 com status de validação RPKI e IRR |
| `ValidacaoRPKI_IRR_Log` | Log de validações |
| `IRRConfig` | Configuração completa do AS para geração de objetos RPSL e envio ao TC |

### Sistema

| Modelo | Descrição |
|---|---|
| `ConfiguracaoSistema` | Singleton com credenciais SMTP globais |
| `TopologiaDiagrama` | Estado do editor de topologia (SVG/JSON) |
| `ImagemTopologia` | Imagens de topologia com link DrawIO |

---

## Funcionalidades Principais

### Terminal Web (SSH/Telnet)

- Acesso SSH e Telnet diretamente no navegador via WebSocket
- Suporte a múltiplos fabricantes (MikroTik, Cisco, Huawei, Juniper, etc.)
- Protocolo especial para Huawei (Pexpect)
- Terminal embutido em painel lateral (inline) ou em página dedicada
- Sessões persistem durante a navegação

### Winbox e Acesso Web

- VNC proxy para interface gráfica do MikroTik (Winbox) no navegador
- Proxy HTTP/HTTPS para WebFig e outras interfaces web de equipamentos
- Tunelamento por porta específica

### Backups Automatizados

- Templates de comandos configuráveis por fabricante
- Execução agendada via Celery Beat
- Histórico com download dos arquivos
- Suporte a MikroTik, Cisco, Huawei, Datacom, Juniper, entre outros

### IPAM Nativo

- Gestão de VLANs, prefixos, sub-redes e IPs individuais
- Controle de utilização de blocos
- Documentação de túneis VPN (7 tipos)
- Importação de IPs via arquivo
- Integração opcional com PHP IPAM e NetBox via túnel SSH

### VPN

**OpenVPN:**
- Configuração automática de servidor no MikroTik
- Geração de certificados e scripts de instalação
- Gestão de usuários/peers com download de configs

**WireGuard:**
- Configuração do servidor (IP, porta, DNS)
- Geração automática de script de configuração por peer
- QR Code para dispositivos móveis

### RPKI / IRR

**Validação RPKI:**
- Cadastro de blocos IPv4/IPv6 com ASN
- Verificação do status de validação (Valid, Invalid, Unknown)
- Dashboard com blocos inválidos

**Atualização IRR (TC/NIC.br):**
- Configuração completa do AS por cliente (ASN, rotas IPv4/IPv6, AS-sets, IX)
- Consulta WHOIS automática ao NIC.br/RADB para pré-preencher os campos
- Auto-detecção do ASN a partir dos blocos RPKI já cadastrados
- Geração de todos os objetos RPSL: `person`, `mntner`, `route-set`, `route`, `route6`, `as-set`, `aut-num`
- Preview do e-mail antes do envio
- Envio via SMTP global para `auto-dbm@bgp.net.br` com assunto `IRR Route Update`
- Suporte a `member-of` para participação em IX (PTT Metro, etc.)

### Monitoramento

- Integração com API Zabbix (autenticação por token ou usuário/senha)
- Topologias de monitoramento com status em tempo real
- Exibição de hosts e itens do Zabbix

### Financeiro

- Emissão e gestão de faturas
- Controle de consultorias
- Aluguel de blocos IPv4
- Vendas de equipamentos
- Análise de aging (inadimplência)
- Dashboard financeiro

### Wiki

- Artigos em Markdown com sintaxe highlight
- Snippets de código categorizados por fabricante
- Busca por conteúdo, tag e fabricante

### Chamados

- Ciclo completo: Aberto → Em Andamento → Aguardando → Resolvido → Fechado
- Prioridade (Normal, Alta, Urgente) e departamento
- Comentários internos e externos
- Categorias configuráveis

---

## Integrações Externas

| Sistema | Protocolo | Finalidade |
|---|---|---|
| TC / NIC.br | SMTP + WHOIS (porta 43) | Atualização de registros IRR |
| RADB | WHOIS (porta 43) | Consulta alternativa de objetos RPSL |
| Zabbix | REST API (HTTP) | Monitoramento de equipamentos |
| PHP IPAM | HTTP via túnel SSH | Documentação de rede (opcional) |
| NetBox | REST API via token | Documentação de rede (opcional) |
| DrawIO | Embed iframe | Editor de topologia de rede |
| MikroTik | API RouterOS / Winbox | Configuração de VPN, acesso remoto |

---

## Infraestrutura e Deploy

### Serviços systemd

| Serviço | Função |
|---|---|
| `gunicorn.service` | Servidor HTTP Django (workers síncronos) |
| `daphne.service` | Servidor ASGI para WebSockets (terminal) |
| `celery.service` | Worker de tarefas assíncronas |
| `celerybeat.service` | Agendador de tarefas |
| `redis.service` | Broker + cache |
| `postgresql.service` | Banco de dados |

### Diretório de mídia

```
/opt/crm/media/
  ├── documentos/          — Arquivos de clientes
  ├── backups/             — Backups de equipamentos
  │   └── cliente_{id}/acesso_{id}/
  ├── vpn/                 — Configs VPN
  ├── topologias/          — Imagens de topologia
  └── faturas/             — PDFs de faturas
```

### Variáveis de ambiente relevantes

Configuradas em `crm/settings.py`:

- `DATABASES` — PostgreSQL local
- `CELERY_BROKER_URL` — `redis://localhost:6379/0`
- `CACHES` — Redis `redis://127.0.0.1:6379/1`
- `CHANNEL_LAYERS` — In-memory (WebSocket)
- `MEDIA_ROOT` — `/opt/crm/media/`

---

## Histórico de Alterações

### Sessão 1 — Correções de bugs

#### Bug: Aba VPN carregava infinitamente na primeira visita

**Problema:** O DOMContentLoaded restaurava a aba ativa do sessionStorage mas não chamava `ovpnCarregarLista()` para a aba VPN.

**Arquivo:** `clientes/templates/listar.html`

**Correção:** Adicionado case `vpn` no bloco de restauração do sessionStorage com delay de 300ms para aguardar renderização do DOM.

---

#### Bug: Prefixos IPAM não apareciam em Documentação de Rede

**Problema:** A view `ipam_prefixos_listar` usava `.sort()` no Python misturando redes IPv4 e IPv6. O módulo `ipaddress` não permite comparar `IPv4Network` com `IPv6Network`, lançando `TypeError` → Django retornava HTML 500 → `JSON.parse` falhava no frontend.

**Arquivo:** `clientes/ipam_views.py` (linhas ~99 e ~456)

**Correção:** Ordenação por tupla `(version, network)` para que IPv4 e IPv6 sejam agrupados separadamente antes de comparar:

```python
data.sort(key=lambda x: (
    ipaddress.ip_network(x['prefixo'], strict=False).version,
    ipaddress.ip_network(x['prefixo'], strict=False)
))
```

Mesmo fix aplicado em `ipam_subredes_listar`.

---

#### Bug: Modal OpenVPN escurecia a tela sem interação

**Problema:** Modais Bootstrap dentro de `container-fluid` com possíveis transformações CSS ficavam presos no contexto de stacking do container. O backdrop aparecia no `body` mas o modal ficava preso internamente, bloqueando toda interação.

**Arquivo:** `clientes/templates/listar.html`

**Correção:**
- `document.body.appendChild(el)` antes de exibir cada modal (move para o body)
- `bootstrap.Modal.getOrCreateInstance(el)` no lugar de `new bootstrap.Modal(el)` para evitar instâncias duplicadas

Aplicado nas funções: `ovpnAbrirModal`, `ovpnVerLogs`, `ovpnUsuarioAbrirModal`.

---

### Sessão 2 — Implementação da ferramenta IRR

#### Nova feature: Atualização de registros IRR via e-mail

Implementação completa de uma ferramenta para atualizar objetos RPSL no registro TC (NIC.br) enviando e-mail para `auto-dbm@bgp.net.br`.

**Novo modelo `IRRConfig`** (`clientes/models.py`, migration 0046):

Campos: `asn`, `as_name`, `empresa_descr`, `nic_hdl`, `irr_password`, `auth_bcrypt`, `email_contato`, `email_abuse`, `website`, `person_name`, `address`, `phone`, `ipv4_rotas`, `ipv6_rotas`, `route_set_members`, `upstream_asns`, `customer_asns`, `geo_*`

Properties automáticas: `mntner` → `MAINT-AS{asn}`, `as_full` → `AS{asn}`

**Novos endpoints** (`clientes/urls.py`):

```
GET  /<cliente_id>/irr/config/     — retorna config salva
POST /<cliente_id>/irr/salvar/     — salva config
GET  /<cliente_id>/irr/preview/    — preview do e-mail RPSL
POST /<cliente_id>/irr/enviar/     — envia e-mail ao TC
GET  /<cliente_id>/irr/consultar/  — consulta WHOIS e retorna objetos parsados
```

**Geração de objetos RPSL** (`_irr_gerar_corpo` em `clientes/views.py`):

Gera o corpo completo do e-mail com os objetos: `password`, `person`, `mntner`, `route-set`, `route` (IPv4), `route6` (IPv6), `as-set`, `aut-num`.

**Consulta WHOIS** (`irr_consultar_whois`):

Consulta `whois.nic.br` → `irr.nic.br` → `whois.radb.net` (fallback). Faz queries para `aut-num`, `route`, `route6` e `mntner`. Retorna dados parsados + raw RPSL completo.

**Interface** (`clientes/templates/listar.html`):

Card IRR na aba RPKI/IRR com sub-abas:
- **Dados Gerais:** ASN, AS Name, NIC-HDL, senhas, contatos, endereço, geolocalização
- **Rotas:** IPv4, IPv6, route-set members
- **AS-Sets:** Upstreams, Customers, participação em IX (member-of)

Botões: Consultar IRR, Preview E-mail, Enviar Atualização

Modal **"Estrutura IRR Atual"** — exibe os objetos RPSL existentes no servidor WHOIS (aut-num, mntner, routes, route6) em formato texto para referência, separados por linha divisória.

Modal **"Preview E-mail"** — exibe o corpo do e-mail RPSL gerado com opção de copiar ou enviar diretamente.

---

### Sessão 3 — SMTP global e melhorias IRR

#### Nova feature: Painel de configuração SMTP global (admin)

**Problema:** SMTP estava configurado por cliente no `IRRConfig`. O correto é ter uma configuração global usada por todos.

**Novo modelo `ConfiguracaoSistema`** (`clientes/models.py`, migration 0047):

```python
class ConfiguracaoSistema(models.Model):
    smtp_host    = CharField
    smtp_port    = IntegerField (default 587)
    smtp_user    = CharField
    smtp_pass    = CharField
    smtp_from    = EmailField
    smtp_use_tls = BooleanField (default True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
```

Campos SMTP removidos do `IRRConfig` nesta mesma migration.

**Nova view** `configuracoes_sistema` (`home/views.py`):
- `@admin_required` — apenas staff
- GET: renderiza formulário com dados atuais
- POST (JSON): atualiza campos; senha só atualizada se enviada

**Nova view** `smtp_testar` (`home/views.py`):
- POST com `{"destino": "email@exemplo.com"}`
- Envia e-mail de teste usando credenciais salvas

**Novos endpoints** (`home/urls.py`):

```
GET/POST /home/configuracoes/              — painel SMTP
POST     /home/configuracoes/smtp-testar/  — teste de envio
```

**Menu Sistema** (`templates/base.html`):

Adicionado link "Configurações" visível apenas para `is_staff`.

**`irr_enviar` atualizado** (`clientes/views.py`):

Passou a usar `ConfiguracaoSistema.get()` para obter credenciais SMTP. Respeita o flag `smtp_use_tls`.

---

#### Melhoria: Modal com estrutura IRR atual no WHOIS

**Problema:** O botão "Consultar IRR" apenas auto-preenchia os campos sem mostrar como o registro está atualmente no servidor.

**Alteração:** Adicionado modal `#modalIrrWhois` que exibe o RPSL raw retornado pelo servidor WHOIS, dividido em seções: `aut-num`, `mntner`, `route (IPv4)`, `route6 (IPv6)`. Limite aumentado de 2000 para 8000 caracteres por seção.

**Remoção:** Sub-aba SMTP do formulário IRR removida (configuração migrada para painel global).

---

### Sessão 4 — Campo IX (member-of) e placeholders fictícios

#### Nova feature: Participação em IX (member-of no aut-num)

**Novo campo** `ix_members` no `IRRConfig` (migration 0048):

```python
ix_members = JSONField(default=list)
# Ex: ["AS-PTTMetro-SP", "AS65001:AS-ANNOUNCEMENTS", "AS-PTTMetro-ATM4-SP"]
```

**Interface** (`clientes/templates/listar.html`):

Nova seção "Participação em IX" na aba AS-Sets com lista dinâmica (adicionar/remover entradas), destacada em amarelo (`#ffaa00`).

**Geração RPSL** (`_irr_gerar_corpo`):

Quando há IX configurados, insere no `aut-num`:

```
remarks:        Participante IX:
remarks:        ...
member-of:      AS-PTTMetro-SP
member-of:      AS65001:AS-ANNOUNCEMENTS
remarks:        ...
```

**Placeholders atualizados** — valores de exemplo trocados por nomes fictícios:

| Campo | Antes | Depois |
|---|---|---|
| ASN | `272418` | `65001` |
| AS Name | `INFORLIMA` | `PROVEDOR-NET` |
| Descrição | `INFORLIMA TELECOM` | `Provedor Net Telecomunicações` |
| NIC-HDL | `JOLJE19-NICBR` | `JOAOS1-NICBR` |
| Person Name | `Lucas Campelo` | `João Silva` |
| Cidade | `Cansancao` | `São Paulo` |

---

#### Bug fix: `irr_config_get` referenciava campos smtp removidos

**Problema:** A view `irr_config_get` ainda tentava acessar `cfg.smtp_host`, `cfg.smtp_port`, `cfg.smtp_user`, `cfg.smtp_from` que foram removidos do model `IRRConfig` na migration 0047. Causaria `AttributeError` ao carregar a config.

**Correção:** Campos smtp removidos do retorno de `irr_config_get` e da lista `campos_simples` em `irr_config_salvar`. Campo `ix_members` adicionado ao retorno e ao `campos_json`.

---

### Sessão 5 — Auto-detecção de ASN dos blocos RPKI

#### Nova feature: Pré-preenchimento automático ao abrir aba IRR

**Problema:** Clientes que já tinham blocos IP com ASN cadastrado na validação RPKI/IRR precisavam preencher o ASN manualmente antes de consultar o WHOIS.

**Alteração em `irr_config_get`** (`clientes/views.py`):

Quando não existe `IRRConfig` para o cliente, a view agora consulta `BlocoIP.objects.filter(cliente=cliente).exclude(asn='')` e retorna o primeiro ASN encontrado no campo `asn_sugerido`.

**Alteração em `irrCarregarConfig`** (`clientes/templates/listar.html`):

```javascript
if (d.existe) {
    irrPreencherForm(d);              // config salva → preenche formulário
} else if (d.asn_sugerido) {
    el.value = d.asn_sugerido;        // preenche campo ASN
    await irrConsultarWhois();        // consulta WHOIS automaticamente
}
```

Flag `_irrConfigCarregado` garante que a consulta ocorre apenas na primeira abertura da aba, evitando re-consultas ao trocar de aba.

---

#### Bug fix: IndexError em `irr_consultar_whois`

**Problema:** Em alguns registros WHOIS o campo `changed` vinha vazio ou sem espaços. O código `parse_field(autnum_raw, 'changed').split()[0]` lançava `IndexError: list index out of range`, retornando HTML 500 no lugar de JSON.

**Arquivo:** `clientes/views.py`, função `irr_consultar_whois`

**Correção:**

```python
# Antes (quebrava):
dados['email_contato'] = parse_field(autnum_raw, 'e-mail') or parse_field(autnum_raw, 'changed').split()[0]

# Depois (seguro):
changed_parts = parse_field(autnum_raw, 'changed').split()
dados['email_contato'] = parse_field(autnum_raw, 'e-mail') or (changed_parts[0] if changed_parts else '')
```

---

## Migrations (clientes)

| Migration | O que adicionou |
|---|---|
| 0001 | Modelos iniciais |
| 0002–0029 | Expansões incrementais (VPN, docs, backups, proxies, blocos IP) |
| 0030 | `BlocoIP` — validação RPKI/IRR |
| 0031–0037 | Topologia (SVG, DrawIO, editor) |
| 0038 | `ComentarioAcesso` |
| 0039 | `DocumentacaoRedeConfig` (PHP IPAM / NetBox) |
| 0040 | `TopologiaDiagrama` |
| 0041 | `VPNServidorConfig`, `VPNWireGuard` |
| 0042 | IPAM nativo (Vlan, Prefixo, SubRede, Endereco, VpnDoc) |
| 0043 | `pool_cheia` em `IPAMPrefixo` |
| 0044–0045 | `OpenVPNConfig`, `OpenVPNUsuario` |
| 0046 | `IRRConfig` — configuração IRR por cliente |
| 0047 | `ConfiguracaoSistema` — SMTP global singleton |
| 0048 | `ix_members` em `IRRConfig` — participação em IX |
