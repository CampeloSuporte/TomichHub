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
   - [Sessão 1](#sessão-1--correções-de-bugs) — Correções de bugs
   - [Sessão 2](#sessão-2--implementação-da-ferramenta-irr) — IRR
   - [Sessão 3](#sessão-3--smtp-global-e-melhorias-irr) — SMTP global
   - [Sessão 4](#sessão-4--campo-ix-member-of-e-placeholders-fictícios) — IX / member-of
   - [Sessão 5](#sessão-5--auto-detecção-de-asn-dos-blocos-rpki) — Auto-detecção ASN
   - [Sessão 6](#sessão-6--pesquisa-looking-glass-gerenciador-de-firmware-e-ui-global) — Looking Glass, Firmware, UI
   - [Sessão 7](#sessão-7--geolocalização-ip-melhorias-lg-e-menu-ferramentas) — Geolocalização IP
   - [Sessão 8](#sessão-8--proxy-web-proxmox-winbox-vnc-e-websocket-proxy) — Proxy Web / Proxmox
   - [Sessão 9](#sessão-9--monitoramento-wireguard-auto-config-zabbix-e-melhorias-visuais) — Monitoramento
   - [Sessão 10](#sessão-10--dashboard-geral-relatório-de-backups-e-scripts-de-automação) — Dashboard, Backups, Scripts
   - [Sessão 11](#sessão-11--módulo-financeiro--redesign-cyberpunk-e-novas-funcionalidades) — Financeiro
   - [Sessão 12](#sessão-12--terminal-web--otimizações-de-latência) — Terminal (latência)

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

Painel geral com estatísticas de chamados, blocos RPKI/IRR inválidos, configurações de sistema (SMTP/IMAP global) e ferramentas de rede (Looking Glass, Geolocalização IP).

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
| `BackupLog` | Histórico de execuções com status, caminho do arquivo e hash SHA-256 do conteúdo |

### Scripts de Automação

| Modelo | Descrição |
|---|---|
| `ScriptCRM` | Script reutilizável com comandos parametrizáveis, fabricante e modo de execução |
| `ScriptExecucaoLog` | Log de cada execução: acesso alvo, parâmetros usados, output, status e timestamps |

### RPKI / IRR

| Modelo | Descrição |
|---|---|
| `BlocoIP` | Bloco IPv4/IPv6 com status de validação RPKI e IRR |
| `ValidacaoRPKI_IRR_Log` | Log de validações |
| `IRRConfig` | Configuração completa do AS para geração de objetos RPSL e envio ao TC |

### Sistema

| Modelo | Descrição |
|---|---|
| `ConfiguracaoSistema` | Singleton com credenciais SMTP e IMAP globais |
| `TopologiaDiagrama` | Estado do editor de topologia (SVG/JSON) |
| `ImagemTopologia` | Imagens de topologia com link DrawIO |

### Geolocalização IP

| Modelo | Descrição |
|---|---|
| `CorrecaoGeoIP` | Registro de correção de geolocalização de um prefixo IP, com destinos, histórico de respostas e status de verificação |

### Firmware

| Modelo | Descrição |
|---|---|
| `FirmwarePasta` | Pasta do sistema de arquivos (hierarquia via FK para pasta pai) |
| `FirmwareArquivo` | Arquivo armazenado com metadados (nome, tamanho, tipo, caminho) |
| `FirmwareCompartilhamento` | Link temporário com token, validade, contador de acessos e credenciais opcionais |

---

## Funcionalidades Principais

### Terminal Web (SSH/Telnet)

- Acesso SSH e Telnet diretamente no navegador via WebSocket
- Suporte a múltiplos fabricantes (MikroTik, Cisco, Huawei, Juniper, etc.)
- Protocolo especial para Huawei (Pexpect)
- Terminal embutido em painel lateral (inline) ou em página dedicada
- Sessões persistem durante a navegação
- **Protocolo binário WebSocket:** teclas enviadas como bytes puros (sem JSON), output recebido como `ArrayBuffer` — elimina overhead de serialização no hot path
- **Renderer WebGL/Canvas:** xterm.js v5 carrega `xterm-addon-webgl` (acelerado por GPU) com fallback para `xterm-addon-canvas` — rendering notavelmente mais rápido que o DOM renderer padrão
- **Leitura de pty com timeout 1ms:** loop de leitura SSH/Telnet verifica disponibilidade a cada 1ms (era 5ms)

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

### Scripts de Automação

- URL: `/clientes/scripts/`
- Biblioteca de scripts reutilizáveis parametrizáveis por fabricante (ZTE, Huawei, Cisco, MikroTik, Datacom, Parks, Genérico)
- Modos de execução: Operacional (show/get), Configuração (config), ZTE Auto-Provisionamento
- Suporte a parâmetros tipados: `text`, `number`, `select` com valores padrão e ajuda contextual
- Loop `#FOR i FROM {X} TO {Y} ... #ENDFOR` para repetição de blocos de comandos
- Execução em qualquer acesso SSH/Telnet via botão na página do cliente
- Histórico completo de execuções com output, status e timestamps
- Gerenciador de scripts exclusivo para `is_staff`: `/clientes/scripts/gerenciar/`
- Script ZTE Auto-Provisionamento inserido como seed via migration 0055

### Financeiro

- Emissão e gestão de faturas
- Controle de consultorias
- Aluguel de blocos IPv4
- Vendas de equipamentos
- Análise de aging (inadimplência)
- Dashboard financeiro com tema cyberpunk
- Seção "Próximas a Vencer": faixas de alerta para faturas vencendo em até 15 dias
- Seção "Top Clientes" visível no modo executivo do dashboard
- Endpoint `GET /financeiro/api/proximas-vencer/?dias=N` para faturas abertas com vencimento iminente

### Wiki

- Artigos em Markdown com sintaxe highlight
- Snippets de código categorizados por fabricante
- Busca por conteúdo, tag e fabricante

### Ferramentas de Rede

**Looking Glass (Pesquisa LG):**

- URL: `/home/ferramentas/lg/`
- Consulta um prefixo IPv4 ou IPv6 em múltiplos coletores BGP públicos simultaneamente
- Fontes: RIPE NCC RIS (API stat.ripe.net), RIPE RIS Whois (riswhois.ripe.net:43)
- Exibe AS paths agrupados por frequência com identificação do país de cada coletor RRC
- Badges IX.br/PTT: coletor RRC15 (São Paulo) identificado com badge `BR — IX.br/PTT`
- Modal de topologia BGP: clique em qualquer AS path para visualizar graficamente o caminho AS → ASN de origem
- Integrado com a aba IRR/RPKI dos clientes via botão "Consultar LG" e query string `?prefixo=`

**Geolocalização IP:**

- URL: `/home/ferramentas/geo/`
- Consulta simultânea em 6 fontes públicas de geolocalização para um IP ou prefixo
- Detecta divergências entre fontes (país, estado, cidade, ASN/org) e destaca em vermelho
- Exibe consenso calculado (maioria das fontes) para cada campo
- Sistema de correção com envio automático para múltiplos destinos:
  - **MaxMind Geo** — preenche e envia o formulário `/en/geoip-location-correction` via scraping
  - **MaxMind ISP/Org** — preenche e envia o formulário `/en/geoip-isp-org-correction` com nome da organização e tipo de correção
  - **LACNIC** — envia e-mail para `hostmaster@lacnic.net` via SMTP global
  - **ARIN** — envia e-mail para `hostmaster@arin.net` via SMTP global
  - **RFC 8805 Geofeed** — gera CSV no formato padrão (`Prefix,Country,Region,City,Postal-Code`) para hospedagem própria
- **Confirmação automática MaxMind**: após envio dos formulários, MaxMind envia e-mail com link de validação; o botão "Confirmar e-mail MaxMind" acessa o IMAP configurado, localiza o(s) link(s) de confirmação e os confirma automaticamente via HTTP
- Histórico de correções (`CorrecaoGeoIP`) com badges coloridos por destino: verde (confirmado), roxo (pendente), vermelho (erro)
- Campo de e-mail de contato pré-preenchido com o usuário SMTP da configuração global

**Gerenciador de Arquivos / Firmware:**

- URL: `/clientes/firmware/`
- Armazenamento hierárquico de firmware e arquivos de configuração de equipamentos
- Upload múltiplo com progresso em tempo real, drag & drop, ícones por tipo de arquivo
- Download remoto via URL: o servidor faz o download diretamente de uma URL HTTP/HTTPS sem passar pelo navegador do usuário
- Sistema de compartilhamento com links temporários em 10 formatos (HTTP, HTTPS, FTP, SFTP, TFTP, Cisco, MikroTik, Huawei, wget, curl)
- Download público via token sem autenticação
- Limite de upload local: 2 GB

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
| RIPE NCC RIS | REST API (stat.ripe.net) | Pesquisa Looking Glass — AS paths por prefixo |
| RIPE RIS Whois | TCP porta 43 (riswhois.ripe.net) | Consulta de AS paths via protocolo WHOIS |
| ip-api.com | REST API (HTTP) | Geolocalização IP — país, estado, cidade, ASN |
| ipinfo.io | REST API (HTTPS) | Geolocalização IP — país, estado, cidade, org |
| ipwhois.app | REST API (HTTPS) | Geolocalização IP — país, estado, cidade, org |
| DB-IP | REST API (HTTPS) | Geolocalização IP — país, estado, cidade |
| RIPE Stat | REST API (stat.ripe.net) | Geolocalização IP — ASN, prefixo anunciado |
| LACNIC RDAP | REST API (rdap.lacnic.net) | Geolocalização IP — informações de bloco LACNIC |
| MaxMind | HTTPS form submit (www.maxmind.com) | Correção de geolocalização e ISP/Org via formulário |
| MaxMind IMAP | IMAP (configuração global) | Confirmação automática de e-mails de validação MaxMind |

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
  ├── faturas/             — PDFs de faturas
  └── firmware/            — Firmware e arquivos de equipamentos (proprietário www-data)
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
| 0049 | `imap_*` em `ConfiguracaoSistema` — credenciais IMAP para confirmação automática |
| 0050 | `FirmwarePasta`, `FirmwareArquivo`, `FirmwareCompartilhamento` — gerenciador de firmware |
| 0051 | `CorrecaoGeoIP` — histórico de correções de geolocalização de prefixos IP |
| 0052 | `BackupLog` — campo `hash_conteudo` (SHA-256), novo status `SEM_MUDANCAS`, `arquivo_path` com default vazio |
| 0053 | `ScriptCRM`, `ScriptExecucaoLog` — sistema de scripts de automação parametrizáveis |
| 0054 | `ScriptCRM.comandos` e `ScriptCRM.parametros` — help_text expandido com documentação de sintaxe |
| 0055 | Seed: insere script ZTE Auto-Provisionamento em Massa via `RunPython` |

---

## Histórico de Alterações (continuação)

### Sessão 6 — Pesquisa Looking Glass, Gerenciador de Firmware e UI global

#### Nova feature: Ferramenta de Pesquisa Looking Glass

**Acesso:** Menu Sistema → Ferramentas → Pesquisa LG

**URL:** `/home/ferramentas/lg/`

Ferramenta que busca um prefixo IPv4 ou IPv6 em múltiplos coletores BGP públicos simultaneamente e exibe os AS paths encontrados agrupados por frequência.

**Fontes consultadas:**

| Fonte | Protocolo |
|---|---|
| RIPE NCC RIS Looking Glass | REST API `stat.ripe.net/data/looking-glass` |
| RIPE Prefix Overview | REST API `stat.ripe.net` |
| RIPE RIS Whois | Conexão raw TCP porta 43 (`riswhois.ripe.net`) |

**Funcionamento:**

- AS paths são agrupados por frequência; o path mais visto é destacado.
- Cada coletor RRC é identificado com bandeira de país: `BR` para o RRC15 (São Paulo) e `INTERNACIONAL` com código do país para os demais.
- Os AS paths agrupados exibem badge `confirmado no BR` (quando presente no RRC15) ou `apenas internacional`.

**Auto-busca por query string:**

Quando acessada com `?prefixo=x.x.x.x/xx` na URL, a ferramenta executa a busca automaticamente. Esse mecanismo é usado pelo botão **"Consultar LG"** presente na aba IRR/RPKI de cada cliente, que abre a ferramenta com o prefixo pré-preenchido.

**Arquivos:**

- Views: `home/views.py` → funções `lg_pesquisa` e `lg_pesquisa_buscar`
- Template: `home/templates/lg_pesquisa.html`
- URLs: `home/urls.py`

---

#### Nova feature: Gerenciador de Arquivos / Firmware

**Acesso:** Menu Sistema → Ferramentas → Arquivos / Firmware

**URL:** `/clientes/firmware/`

Sistema de gerenciamento de arquivos voltado ao armazenamento de firmware de roteadores, switches, OLTs e equipamentos similares.

**Funcionalidades de navegação e upload:**

- Criação de pastas com hierarquia ilimitada e breadcrumb de navegação
- Upload múltiplo com barra de progresso em tempo real (via XHR com `onprogress`)
- Drag & drop de arquivos na área da tabela
- Ícones diferenciados por tipo de arquivo:
  - `.npk`, `.bin`, `.fw` — firmware (ciano)
  - `.zip`, `.gz`, `.tar`, `.rar` — compactados (laranja)
  - `.iso` — ISO (roxo)
  - Configs/texto — cinza
- Exclusão de arquivos e pastas com modal de confirmação temático
- Toast de feedback em todas as operações (criação de pasta, upload, exclusão, revogação de link)
- Modal de criação de pasta usa evento `hidden.bs.modal` para atualizar a lista após a animação, evitando duplo clique
- **Download remoto via URL:** botão "Via URL" na barra superior abre modal onde o usuário cola uma URL HTTP/HTTPS; o servidor faz o download diretamente (streaming com `requests`, sem buffering no navegador), salva na pasta atual e exibe o arquivo imediatamente

**Sistema de compartilhamento com link temporário:**

- Validade configurável em horas com botões rápidos: 1h, 24h, 3d, 7d
- Opção de gerar usuário/senha aleatórios (para uso com FTP/SFTP)
- Gera links prontos em 10 formatos simultâneos:

| Formato | Exemplo de uso |
|---|---|
| HTTP | Download direto via browser |
| HTTPS | Download direto seguro |
| FTP | Clientes FTP |
| SFTP | Clientes SFTP |
| TFTP | Equipamentos com TFTP client |
| Cisco | `copy ftp://...` |
| MikroTik | `/tool fetch` |
| Huawei | `tftp get` |
| wget | Shell Linux |
| curl | Shell Linux |

- Download público via token sem necessidade de login: `/home/ferramentas/firmware/dl/TOKEN/arquivo`
- Botão "Encerrar este link" exibido diretamente no resultado gerado
- Painel de links ativos com contador de acessos e botão Revogar
- Badge verde na tabela mostrando quantos links ativos cada arquivo possui

**Modelos Django** (migration 0050):

| Modelo | Descrição |
|---|---|
| `FirmwarePasta` | Pasta do sistema de arquivos (hierarquia via FK para pasta pai) |
| `FirmwareArquivo` | Arquivo armazenado com metadados (nome, tamanho, tipo, caminho) |
| `FirmwareCompartilhamento` | Link temporário com token, validade, contador de acessos e credenciais opcionais |

**Arquivos:**

- Views: `clientes/firmware_views.py`
- Template: `clientes/templates/firmware.html`
- Armazenamento físico: `/opt/crm/media/firmware/` (proprietário `www-data:www-data`)
- Limite de upload local: **2 GB** (configurado no Nginx)
- Nginx: `location /clientes/firmware/upload/` com `proxy_request_buffering off` e timeout de 600s

---

#### Nova feature: Download de arquivo via URL remota no Gerenciador de Firmware

**Sessão 7 — Implementação da opção "Via URL"**

Adicionada a possibilidade de baixar arquivos diretamente de uma URL HTTP/HTTPS para o gerenciador, sem que o arquivo passe pelo navegador do usuário.

**Endpoint:** `POST /clientes/firmware/upload-url/`

**View:** `firmware_upload_url` em `clientes/firmware_views.py`

**Fluxo:**
1. Usuário cola a URL no modal "Via URL" e clica em "Baixar"
2. Django valida que o scheme é `http` ou `https`
3. O servidor faz `requests.get(url, stream=True)` e escreve o arquivo em disco em chunks de 8 MB
4. É criado um registro `FirmwareArquivo` com metadados (nome, tamanho, MIME type)
5. Modal fecha automaticamente, toast exibe nome e tamanho do arquivo baixado, lista é atualizada

**Tratamento de erros:**
- URL com scheme inválido (ftp, etc.) → erro imediato
- Erro de certificado SSL → mensagem específica
- Falha de conexão / servidor inacessível → mensagem específica
- Timeout (>30s para iniciar resposta) → mensagem específica
- HTTP 4xx/5xx do servidor remoto → exibe o código de status
- Arquivo parcial em caso de exceção → removido automaticamente

**Nome do arquivo:** extraído do path da URL com `os.path.basename` e `urllib.parse.unquote`. Se não tiver extensão, adiciona `.bin`. Colisões de nome são resolvidas com sufixo `(1)`, `(2)`, etc.

**UI** (`clientes/templates/firmware.html`):
- Botão "Via URL" (roxo) na barra superior ao lado do botão Upload
- Modal com campo de URL, suporte à tecla Enter, barra de progresso animada indeterminada enquanto o servidor baixa
- Erros exibidos inline no modal sem fechar (usuário pode corrigir a URL)

**Rota:** `clientes/urls.py` → `path('firmware/upload-url/', fw.firmware_upload_url, name='firmware_upload_url')`

---

#### Nova feature: Sistema global de UI — uiConfirm / uiAlert / uiToast

Todos os `confirm()` e `alert()` nativos do browser foram substituídos por modais e notificações temáticos, integrados ao tema dark do sistema.

**Implementação:** `templates/base.html` — disponível automaticamente em todas as páginas sem imports adicionais.

**Funções disponíveis globalmente:**

```javascript
// Diálogo de confirmação — retorna Promise<boolean>
uiConfirm({
    titulo: 'Texto do título',
    msg:    'Mensagem de corpo',
    icone:  'bi-trash',          // ícone Bootstrap Icons
    iconeCor: '#ff4444',
    btnLabel: 'Excluir',
    btnCor:   'danger'
})

// Alerta informativo — retorna Promise<void>
uiAlert({ titulo, msg, icone, iconeCor })
uiAlert('mensagem simples')     // forma abreviada

// Toast de notificação — sem retorno
uiToast(msg, tipo)              // tipo: 'ok' | 'erro' | 'info'
```

**Templates atualizados** (zero chamadas nativas restantes):

| Template | Substituições |
|---|---|
| `clientes/templates/listar.html` | 43 |
| `financeiro/templates/financeiro/dashboard.html` | 10 |
| `templates/modal_acessos.html` | 5 |
| `wiki/templates/wiki/cadastrar_artigo.html` | 7 |
| `monitoramento/templates/monitoramento/tab_monitoramento.html` | 2 |
| `clientes/templates/backups/listar_backups.html` | 1 |
| `clientes/templates/listar_proxies.html` | 1 |
| `financeiro/templates/faturas/visualizar.html` | 1 |
| `financeiro/templates/consultorias/listar.html` | 1 |
| `wiki/templates/wiki/visualizar_artigo.html` | 1 |
| `clientes/templates/firmware.html` | 12 |

---

#### Correções e melhorias diversas

- **Permissões de mídia:** Diretório `/opt/crm/media/firmware/` criado e configurado com proprietário `www-data:www-data`.
- **Limite de upload Nginx:** Aumentado de 100 MB para **2 GB** (`client_max_body_size 2G`).
- **Botão "Consultar LG"** adicionado na aba IRR/RPKI de cada cliente — abre a ferramenta Looking Glass com o prefixo do bloco pré-carregado via query string.

---

### Sessão 7 — Geolocalização IP, melhorias LG e menu Ferramentas

#### Nova feature: Ferramenta de Geolocalização IP

**Acesso:** Menu Ferramentas → Geolocalização IP (apenas `is_staff`)

**URL:** `/home/ferramentas/geo/`

Ferramenta de consulta e correção de geolocalização de prefixos e endereços IP.

**Fontes de consulta simultâneas:**

| Fonte | API |
|---|---|
| ip-api.com | `http://ip-api.com/json/{ip}` |
| ipinfo.io | `https://ipinfo.io/{ip}/json` |
| MaxMind / RIPE Stat | `https://stat.ripe.net/data/...` |
| DB-IP | `https://api.db-ip.com/v2/free/{ip}` |
| ipwhois.app | `https://ipwhois.app/json/{ip}` |
| LACNIC RDAP | `https://rdap.lacnic.net/rdap/ip/{ip}` |

- Aceita tanto IP individual quanto prefixo CIDR (consulta usando o primeiro IP do bloco)
- Exibe resultados em tabela com colunas: Fonte, País, Estado, Cidade, ASN, Organização
- Campos divergentes entre fontes são destacados em vermelho
- Linha de consenso (maioria das fontes) exibida em destaque

**Sistema de correção:**

Modal de correção acionado pelo botão "Solicitar Correção" com campos pré-preenchidos pelo consenso:

- País (ISO 3166-1 alfa-2), Estado, Cidade, Latitude, Longitude, Organização/ISP
- Destinos selecionáveis via checkboxes: MaxMind Geo, MaxMind ISP/Org, LACNIC, ARIN
- Campos extras para MaxMind ISP/Org: nome da organização e tipo (`ISP`, `Organization`, `Both`)
- Campo de e-mail de contato pré-preenchido com `smtp_user` da `ConfiguracaoSistema`

**Envio por destino (`geo_atualizar`, `home/views.py`):**

| Destino | Método |
|---|---|
| MaxMind Geo | GET página → extrai `csrf_token` → POST `/en/geoip-location-correction` |
| MaxMind ISP/Org | GET página → extrai `csrf_token` → POST `/en/geoip-isp-org-correction` |
| LACNIC | E-mail SMTP para `hostmaster@lacnic.net` |
| ARIN | E-mail SMTP para `hostmaster@arin.net` |
| RFC 8805 Geofeed | Gerado sempre — CSV com `Prefix,Country,Region,City,Postal-Code` |

**Notas importantes sobre o envio MaxMind:**
- O campo `region` do formulário Geo é preenchido dinamicamente por JavaScript no site MaxMind; enviar qualquer valor causa HTTP 400. O sistema envia `region=''` propositalmente.
- O campo `ip_address` aceita notação CIDR completa (ex: `192.0.2.0/24`).
- O sistema detecta respostas de "já submetido anteriormente" e as trata como sucesso informativo.

**Confirmação automática de e-mails MaxMind (`geo_confirmar_maxmind`):**

Após envio dos formulários MaxMind, um e-mail de validação é enviado para o endereço de contato. O botão "Confirmar e-mail MaxMind" no histórico:
1. Conecta ao IMAP configurado em `ConfiguracaoSistema` (`imap_host`, `imap_port`, `imap_user`, `imap_pass`)
2. Busca mensagens `FROM "maxmind.com"` recebidas após a data da correção
3. Extrai todos os links `https://www.maxmind.com/en/confirm?t=...`
4. Faz GET em cada link (trata tanto correção Geo quanto ISP/Org)
5. Confirma sucesso quando o redirect final contém `/confirmed-geoip-correction`
6. Atualiza o campo `email_confirmado` de todas as entradas MaxMind no histórico

**Histórico de correções:**

- Modelo `CorrecaoGeoIP` (migration 0051): prefixo, país, região, cidade, org, lat/lon, destinos enviados (JSONField), solicitante, data, resposta recebida, aplicado
- Listado na própria página de geolocalização com badges coloridos por destino:
  - Verde: confirmado (`RFC 8805 Geofeed`, MaxMind confirmado)
  - Roxo: pendente de confirmação e-mail (MaxMind)
  - Verde sutil: e-mail enviado (LACNIC, ARIN)
  - Vermelho: erro no envio

**Arquivos:**
- View: `home/views.py` → `geo_consulta`, `geo_buscar`, `geo_atualizar`, `geo_confirmar_maxmind`
- Template: `home/templates/geo_consulta.html`
- Modelo: `clientes/models.py` → `CorrecaoGeoIP`
- Migration: `clientes/migrations/0051_correcao_geoip.py`
- URLs: `home/urls.py`

---

#### Melhoria: Looking Glass — badges IX.br/PTT e modal de topologia BGP

**Badges IX.br/PTT:**

- O coletor RRC15 (São Paulo, Brasil) agora exibe badge `BR — IX.br/PTT` em verde
- Os demais coletores exibem `INTERNACIONAL — {código país}` em azul

**Modal de topologia BGP:**

- Clique em qualquer AS path nos resultados abre um modal com visualização gráfica do caminho
- Nós do grafo representam cada ASN; arestas mostram as relações upstream/downstream
- Integração com a pré-existente query string `?prefixo=` para abertura automática

**Arquivos:**
- View: `home/views.py` → `lg_pesquisa`, `lg_pesquisa_buscar`
- Template: `home/templates/lg_pesquisa.html`

---

#### Melhoria: Menu Ferramentas separado (apenas `is_staff`)

O menu de navegação global foi reorganizado:

- **Antes:** Ferramentas (Pesquisa LG, Firmware) eram itens dentro do dropdown "Sistema"
- **Depois:** Dropdown "Ferramentas" separado, com ícone `fa-tools`, contendo:
  - Pesquisa LG
  - Geolocalização IP
  - Arquivos / Firmware

O dropdown Ferramentas é **visível apenas para usuários `is_staff`** (`{% if request.user.is_staff %}`). Usuários comuns não veem nem têm acesso às ferramentas de rede.

**Arquivo:** `templates/base.html`

---

#### Melhoria: ConfiguracaoSistema — campos IMAP (migration 0049)

Campos adicionados ao singleton `ConfiguracaoSistema` para permitir a confirmação automática de e-mails MaxMind:

| Campo | Tipo | Descrição |
|---|---|---|
| `imap_host` | CharField | Servidor IMAP (ex: `imap.gmail.com`) |
| `imap_port` | IntegerField | Porta IMAP (padrão: 993) |
| `imap_user` | CharField | Usuário IMAP (geralmente o mesmo do SMTP) |
| `imap_pass` | CharField | Senha IMAP ou senha de aplicativo |

Esses campos são configurados em **Sistema → Configurações** pelo administrador.

---

### Sessão 8 — Proxy Web: Proxmox, WinBox VNC e WebSocket proxy

#### Bug fix: Proxmox retornava HTTP 401 ao executar ações POST (Start/Stop VM)

**Problema:** Ao clicar em "Start", "Stop" ou qualquer ação POST/PUT/DELETE no Proxmox acessado via proxy HTTP do CRM, o Proxmox retornava erro 401 `Unauthorized`. O cabeçalho `CSRFPreventionToken` (exigido pelo Proxmox VE em todas as requisições mutáveis) chegava no Django como `HTTP_CSRFPREVENTIONTOKEN` — sem prefixo `X_` — e era ignorado pelo loop de forwarding que só encaminhava cabeçalhos `HTTP_X_*`.

**Arquivo:** `clientes/views.py` (função `proxy_web_acesso`)

**Correção:** Adicionado bloco explícito de forwarding do `CSRFPreventionToken` após o loop `HTTP_X_*`:

```python
# Proxmox VE exige CSRFPreventionToken em todas as requisições POST/PUT/DELETE
if 'HTTP_CSRFPREVENTIONTOKEN' in request.META:
    req_headers['CSRFPreventionToken'] = request.META['HTTP_CSRFPREVENTIONTOKEN']
```

---

#### Melhoria: WinBox VNC — noVNC local e resolução adaptativa

**Problema:** O noVNC era carregado via CDN (`cdn.jsdelivr.net`), adicionando latência. Além disso, a resolução não se adaptava ao tamanho do container.

**Solução:**

1. **noVNC servido localmente** — pacote `novnc` instalado em `/opt/crm/static/novnc/` e servido pelo Nginx em `/static/novnc/`. O import no template foi trocado de CDN para `{% static "novnc/core/rfb.js" %}`.

2. **Resolução adaptativa** — adicionado `rfb.resizeSession = true` e `rfb.clipViewport = false`. Listener de `window.resize` com debounce de 300ms chama `rfb._sendDesktopSize(w, h)` para ajustar a resolução do servidor VNC ao tamanho real do container.

**Arquivo:** `clientes/templates/winbox.html`

**Instalação do noVNC (executar uma vez no servidor):**

```bash
sudo apt install novnc -y
cp -r /usr/share/novnc/* /opt/crm/static/novnc/
python manage.py collectstatic --noinput
```

---

#### Bug fix: Shell do Proxmox abria o dashboard do CRM em vez do terminal

**Problema:** Ao clicar em "Shell" no Proxmox, um iframe era criado dinamicamente pelo Proxmox ExtJS com `iframe.src = '/?console=shell&...'`. O proxy injetava JavaScript para interceptar mudanças de `src` em `HTMLImageElement`, `HTMLScriptElement`, `HTMLAnchorElement` etc., mas **não** em `HTMLIFrameElement`. O `location = /` do Nginx redirecionava para `/auth/login/`, exibindo o dashboard do CRM dentro do iframe.

**Arquivo:** `clientes/proxy_engine.py` (bloco `_fixProp` na injeção JavaScript)

**Correção:** Adicionado `_fixProp(HTMLIFrameElement.prototype,'src')` ao bloco de patching de propriedades do proxy inject. Assim, quando o Proxmox define `iframe.src = '/?console=...'`, o setter interceptado reescreve o caminho para o túnel correto.

---

#### Nova feature: Proxy WebSocket para terminal Proxmox (e outras interfaces web)

**Problema:** Após corrigir a navegação do Shell, o terminal Proxmox não conectava porque usa WebSockets (`wss://crm.host/api2/json/nodes/X/qemu/Y/vncwebsocket?...`) que não eram roteados pelo proxy HTTP.

**Solução:** Implementação completa de proxy WebSocket via Django Channels.

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `clientes/consumers.py` | Nova classe `WebSocketProxyConsumer` |
| `clientes/routing.py` | Nova rota `ws/proxy/<acesso_id>/<porta>/<scheme>/<path>` |
| `clientes/proxy_engine.py` | Interceptor `window.WebSocket` no JavaScript injetado |

**`WebSocketProxyConsumer`** (`clientes/consumers.py`):

- `connect()` — aceita WS do browser, extrai `acesso_id/porta/scheme/path` da URL, cria túnel SSH via `TunnelPortCache` se IP privado, conecta TCP ao equipamento, envolve em SSL se `https`, executa handshake WebSocket manual (RFC 6455), inicia thread `_forward_from_device`
- `receive()` — encaminha frames do browser → dispositivo (com masking cliente→servidor)
- `_forward_from_device()` — encaminha frames do dispositivo → browser, trata ping/pong
- `_ws_handshake()` — realiza HTTP Upgrade manual, retorna `(socket, leftover_bytes)`
- `_ws_recv_frame()` e `_ws_send_frame()` — codificação/decodificação completa de frames WebSocket (masking, payload variável 7/16/64 bits)

**Interceptor `window.WebSocket`** (proxy inject em `proxy_engine.py`):

Quando o JavaScript da página faz `new WebSocket('wss://equipamento/api2/...')`, o interceptor reescreve a URL para:

```
wss://crm.host/ws/proxy/{acesso_id}/{porta}/{scheme}/{path}
```

Usa `B.split('/')` para extrair `acesso_id`, `porta` e `scheme` do `proxy_base` — evita uso de regex com `\d` dentro de f-strings Python (ver fix abaixo).

---

#### Bug fix: `re.error: bad escape \d at position 7213`

**Problema:** O interceptor WebSocket originalmente usava regex JavaScript `/^\/clientes\/acessos\/(\d+)\/web\/(\d+)\/(https?)/` dentro de uma f-string Python. O Python 3.12 levanta `re.error: bad escape \d` quando a string de substituição (`repl`) do `re.sub` contém `\d`.

**Correção:** Substituídas todas as expressões regulares JavaScript na string de substituição `re.sub` por código JavaScript equivalente usando `B.split('/')`:

```javascript
var _parts = B.split('/');
if (_parts.length >= 7 && _parts[1] === 'clientes' && _parts[2] === 'acessos') {
    var _aid = _parts[3], _aprt = _parts[5], _asch = _parts[6];
    // ...
}
```

---

### Sessão 9 — Monitoramento: WireGuard, auto-config Zabbix e melhorias visuais

#### Bug fix: Erro "não há proxy SSH ativo" para clientes com VPN WireGuard

**Problema:** Clientes que usam WireGuard como VPN (sem túnel SSH configurado) recebiam a mensagem:

> `Zabbix tem IP privado (172.18.x.x) mas não há proxy SSH ativo para este cliente. Configure na aba 'Túneis SSH'.`

**Causa:** A função `_get_config_com_tunel()` em `monitoramento/views.py` não verificava se o IP era acessível via WireGuard antes de lançar a exceção.

**Arquivo:** `monitoramento/views.py` (função `_get_config_com_tunel`)

**Correção:** Adicionada verificação via `vpn_cobre_ip()` (importado de `clientes.views`) antes de lançar a exceção. Se o WireGuard cobre o IP do Zabbix, retorna `(config, None)` para conexão direta sem túnel SSH:

```python
if not proxy:
    # Verificar se VPN WireGuard cobre este IP (fallback sem túnel SSH)
    try:
        from clientes.views import vpn_cobre_ip
        from clientes.models import Cliente as _Cliente
        _cli = _Cliente.objects.get(id=cliente_id)
        if vpn_cobre_ip(_cli, zbx_host):
            return config, None  # conecta diretamente via WireGuard
    except Exception as _e:
        logger.debug(f"[ZBX] vpn_cobre_ip check falhou: {_e}")
    raise Exception("Zabbix tem IP privado ...")
```

A função `vpn_cobre_ip` verifica se existe uma `VPNWireGuard` ativa com `peer_no_servidor=True` e com rotas que cobrem o IP do Zabbix.

---

#### Nova feature: Auto-preenchimento da config Zabbix a partir dos acessos do cliente

**Problema:** Para configurar o Zabbix API, o usuário precisava saber e digitar manualmente a URL, usuário e senha. Clientes que já têm um `Acesso` cadastrado com tipo "zabbix" possuem esses dados no sistema.

**Novo endpoint:** `GET /monitoramento/zabbix/autoconfig/?id=<cliente_id>`

**View:** `autoconfig_zabbix` em `monitoramento/views.py`

**Lógica:**
1. Busca `Acesso.objects.filter(cliente_id=cliente_id, tipo__icontains='zabbix').first()`
2. Monta a URL: `{protocolo}://{host}:{porta}` (HTTPS por padrão; HTTP se protocolo for `HTTP`)
3. Retorna `url`, `usuario`, `senha`, `tipo` e `host`

**Resposta de exemplo:**

```json
{
  "encontrado": true,
  "url": "https://172.18.5.10:443",
  "usuario": "Admin",
  "senha": "zabbix",
  "tipo": "ZABBIX SERVER",
  "host": "172.18.5.10"
}
```

**UI:** Botão **"Auto-preencher"** adicionado no rodapé esquerdo do modal Zabbix API. Ao clicar, preenche automaticamente os campos URL, usuário e senha. Exibe feedback de sucesso com o nome e IP do host encontrado, ou erro se não houver host "zabbix" nos acessos.

**Arquivos:** `monitoramento/views.py`, `monitoramento/urls.py`, `monitoramento/templates/monitoramento/tab_monitoramento.html`

---

#### Melhoria: Design dos gráficos de monitoramento

Melhorias visuais no monitor de tráfego da aba Monitoramento.

| Elemento | Antes | Depois |
|---|---|---|
| Altura do canvas | `180px` | `240px` |
| Cards — fundo | Cor sólida `#0d1829` | Gradiente sutil `#0e1b30 → #0a1422` |
| Cards — hover | Sem animação | `translateY(-1px)` + sombra mais forte |
| Valores de stats | `font-size: 0.82rem` | `font-size: 0.9rem` |
| Grid — coluna mínima | `500px` | `540px` |
| Canvas — fundo | `#080f1a` | `#060d18` com borda topo sutil |
| Stats row — padding | `10px 16px` | `12px 16px` |

**Arquivo:** `monitoramento/templates/monitoramento/tab_monitoramento.html`

---

### Sessão 10 — Dashboard geral, Relatório de Backups e Scripts de Automação

#### Redesign: Dashboard geral (`quadro_geral`)

**URL:** `/homegeral`

O dashboard principal foi completamente reescrito com tema cyberpunk.

**Layout e componentes:**

- **8 cards de estatísticas** em grid responsivo: Total Clientes, Backups Hoje, Backups com Erro, Blocos RPKI Inválidos, Blocos IRR Inválidos, VPNs Ativas, Proxies Ativos, Monitoramentos
- **Gráfico de barras (Chart.js):** histórico de backups dos últimos 14 dias — barras verdes (sucesso) e vermelhas (erro) sobrepostas
- **Tabela "Últimos Backups":** 10 registros mais recentes com badges coloridos por status (SUCESSO/ERRO/SEM MUD./PARCIAL)
- **Tabela "Top Clientes com Mais Backups":** ranking dos 5 clientes com mais backups no período
- **Tabela RPKI Inválidos:** blocos com status Invalid com link para a aba do cliente
- **Tabela IRR Pendentes:** clientes com `irr_status` diferente de `ok`
- Link de navegação para cada cliente via `{% url 'listar_clientes' %}?id=<id>`

**Arquivo:** `home/templates/quadro_geral.html`

---

#### Nova feature: Relatório de Backups

**Acesso:** Menu Ferramentas → Relatório de Backups

**URL:** `/home/relatorio/backups/`

Página com histórico paginado de todos os backups executados, com filtros e resumo.

**Filtros disponíveis:**

| Filtro | Parâmetro GET |
|---|---|
| Cliente | `?cliente=<id>` |
| Status | `?status=SUCESSO|ERRO|SEM_MUDANCAS|PARCIAL` |
| Data início | `?data_ini=YYYY-MM-DD` |
| Data fim | `?data_fim=YYYY-MM-DD` |

**Resumo:** chips com contadores de Total, OK, Erro e Sem Mudanças (acima da tabela).

**Tabela:** Data/Hora, Cliente (link), Host, Template, Status (badge), Duração (segundos), Hash SHA-256 (truncado).

**Paginação:** 50 registros por página com navegação preservando todos os filtros ativos.

**Arquivos:**
- View: `home/views.py` → `relatorio_backups`
- Template: `home/templates/relatorio_backups.html`
- URL: `home/urls.py` → `path('relatorio/backups/', ..., name='relatorio_backups')`
- Menu: `templates/base.html` — link "Relatório de Backups" no dropdown Ferramentas

---

#### Nova feature: Sistema de Scripts de Automação

**Acesso:** Botão "Scripts" na página de qualquer acesso SSH/Telnet do cliente

**URLs:**

```
GET  /clientes/scripts/                     — listar scripts disponíveis (JSON)
GET  /clientes/scripts/<id>/               — detalhes do script (JSON)
POST /clientes/scripts/executar/            — executar script em um acesso
GET  /clientes/scripts/historico/<acesso_id>/ — histórico de execuções
GET  /clientes/scripts/gerenciar/           — painel de gestão (is_staff)
POST /clientes/scripts/salvar/              — criar novo script
POST /clientes/scripts/salvar/<id>/        — editar script existente
POST /clientes/scripts/deletar/<id>/       — remover script
```

**Modelo `ScriptCRM`** (migration 0053):

| Campo | Tipo | Descrição |
|---|---|---|
| `nome` | CharField | Nome do script |
| `descricao` | TextField | Descrição e documentação |
| `fabricante` | CharField | `zte`, `huawei`, `cisco`, `mikrotik`, `datacom`, `parks`, `generico` |
| `modo_execucao` | CharField | `operacional`, `configuracao`, `zte_auto_prov` |
| `comandos` | TextField | Comandos, um por linha. Suporta `{PARAM}` e `#FOR i FROM {X} TO {Y} ... #ENDFOR` |
| `parametros` | JSONField | Lista de objetos `{nome, label, tipo, default, obrigatorio, ajuda, opcoes}` |
| `ativo` | BooleanField | Script visível ou arquivado |
| `criado_por` | FK User | Autor do script |

**Modelo `ScriptExecucaoLog`** (migration 0053):

| Campo | Tipo | Descrição |
|---|---|---|
| `script` | FK ScriptCRM | Script executado |
| `acesso` | FK Acesso | Equipamento alvo |
| `usuario` | FK User | Quem executou |
| `parametros_usados` | JSONField | Snapshot dos valores informados |
| `output` | TextField | Saída completa do terminal |
| `status` | CharField | `executando`, `sucesso`, `erro`, `parcial` |
| `iniciado_em` / `finalizado_em` | DateTimeField | Timestamps de execução |

**Sintaxe de comandos:**

```
# Variável simples:
interface {INTERFACE}
description {DESCRICAO}

# Loop (expande X a Y):
#FOR i FROM {VLAN_INI} TO {VLAN_FIM}
 vlan {i}
#ENDFOR
```

**Gerenciador** (`/clientes/scripts/gerenciar/`):
- Exclusivo para `is_staff`
- CRUD completo de scripts com editor de parâmetros dinâmico
- Contador total de execuções
- Template: `clientes/templates/scripts/gerenciar.html`

**Script seed ZTE** (migration 0055):

Script "ZTE — Autorizar ONUs em Massa (Auto)" inserido automaticamente. Modo `zte_auto_prov` executa o motor de auto-provisionamento da plataforma que detecta ONUs não autorizadas na PON informada e as provisiona em sequência.

**Arquivos:** `clientes/script_views.py`, `clientes/templates/scripts/gerenciar.html`, migrations 0053–0055

---

#### Melhoria: `BackupLog` — hash de conteúdo e status "Sem Mudanças" (migration 0052)

| Alteração | Detalhe |
|---|---|
| Novo campo `hash_conteudo` | SHA-256 do conteúdo do backup; permite detectar backups sem alteração real |
| Novo status `SEM_MUDANCAS` | Executado com sucesso mas conteúdo idêntico ao backup anterior (hash igual) |
| `arquivo_path` com `default=''` | Evita `NOT NULL` constraint em registros sem arquivo gerado |

---

### Sessão 11 — Módulo Financeiro — Redesign cyberpunk e novas funcionalidades

#### Redesign: Dashboard financeiro

**URL:** `/financeiro/`

O dashboard financeiro foi completamente reescrito com tema cyberpunk, preservando toda a lógica JavaScript e os modais Bootstrap existentes.

**Modo executivo (staff) — novos componentes:**

- **KPI Grid (4 cards):** Faturamento do mês, Recebido, Em aberto, Vencido — com barras de acento coloridas e glow no hover
- **"Próximas a Vencer" strip:** faixa laranja com chips clicáveis para faturas abertas vencendo em até 15 dias; cada chip leva ao cliente específico
- **Top Clientes:** seção com ranking dos maiores clientes por faturamento (dado que existia via API mas nunca aparecia na UI)
- **Gráfico de Faturamento:** barras verdes (pago) + vermelhas (aberto) por mês via Chart.js
- **Tabela Aging:** barras de progresso coloridas por faixa de atraso
- **Tabelas de inadimplentes e pagadores tardios:** styling cyberpunk (`cy-table`)

**Modo cliente — componentes:**

- 4 cards de resumo (Consultorias, Aluguéis, Total Recebido, Saldo Aberto)
- Abas Bootstrap restyled com bordas cyberpunk

**CSS introduzido:**

`.kpi-card`, `.cy-card`, `.vencer-strip`, `.aging-row`, `.cy-table`, `.btn-cy`, `.badge-cy` e variantes (`.badge-ok`, `.badge-err`, `.badge-warn`, `.badge-info`)

**Modais Bootstrap preservados** com mesmos IDs (exigidos pelo JavaScript existente) e restyled com fundo escuro e bordas ciano.

**Toast notifications** restyled com glow effect e animação `slideIn`.

**Arquivo:** `financeiro/templates/financeiro/dashboard.html`

---

#### Nova feature: Endpoint "Próximas a Vencer"

**Endpoint:** `GET /financeiro/api/proximas-vencer/?dias=N`

**View:** `api_proximas_vencer` em `financeiro/views.py`

Retorna faturas com status `ABERTA` com `data_vencimento` entre hoje e `hoje + N dias` (padrão: 15).

**Resposta:**

```json
{
  "sucesso": true,
  "faturas": [
    {
      "id": 42,
      "numero": "FAT-2026-042",
      "cliente": "Empresa XYZ",
      "cliente_id": 7,
      "valor": 1500.00,
      "vencimento": "25/05/2026",
      "dias_restantes": 7
    }
  ],
  "total": 1
}
```

**Arquivos:** `financeiro/views.py`, `financeiro/urls.py`

---

### Sessão 12 — Terminal Web — Otimizações de latência

Três melhorias independentes para reduzir a latência percebida ao digitar no terminal SSH/Telnet.

#### Protocolo binário WebSocket (maior impacto)

**Problema:** Cada tecla digitada trafegava como JSON: `{"action":"command","command":"a"}` (~31 bytes), exigindo serialização no frontend e parse no backend. O output do terminal chegava como `{"type":"output","data":"..."}`, com `json.dumps()` escapando todos os caracteres ANSI.

**Solução:**

- **Input (cliente → servidor):** `terminal.onData` agora envia `_enc.encode(input)` (bytes puros via `socket.send(ArrayBuffer)`); o consumer recebe via `bytes_data` e chama `enviar_comando()` diretamente, sem `json.loads`
- **Output (servidor → cliente):** `send_output()` chama `self.send(bytes_data=text.encode('utf-8'))` em vez de montar JSON manualmente; o frontend detecta `e.data instanceof ArrayBuffer` e decodifica com `_dec.decode(e.data)` — sem `JSON.parse`
- `socket.binaryType = 'arraybuffer'` definido no frontend para garantir entrega como `ArrayBuffer` (sem conversão `Blob`)
- Mensagens de controle (connect, connected, error) continuam em JSON texto — protocolo misto: binário para dados, texto para controle

**Arquivos modificados:**
- `clientes/consumers.py` — `receive()` com `bytes_data`, `send_output()` binário
- `clientes/templates/terminal.html` — `onData`, `onmessage`, `binaryType`

---

#### Renderer WebGL/Canvas (xterm.js v5)

**Problema:** A opção `rendererType: 'canvas'` é ignorada silenciosamente no xterm.js v5 (foi removida nessa versão). Sem nenhum addon de renderer carregado, o xterm usava o DOM renderer — o mais lento das três opções.

**Solução:** Carregados os addons de renderer via CDN:

```html
<script src="xterm-addon-canvas@0.7.0/lib/xterm-addon-canvas.min.js"></script>
<script src="xterm-addon-webgl@0.18.0/lib/xterm-addon-webgl.min.js"></script>
```

Ao abrir cada terminal, tenta carregar WebGL (acelerado por GPU) com fallback automático para Canvas:

```javascript
try {
    const webgl = new WebglAddon.WebglAddon();
    webgl.onContextLoss(() => webgl.dispose());
    terminal.loadAddon(webgl);
} catch(_) {
    try { terminal.loadAddon(new CanvasAddon.CanvasAddon()); } catch(__) {}
}
```

A opção `rendererType: 'canvas'` foi removida do construtor `new Terminal({...})`.

**Arquivo:** `clientes/templates/terminal.html`

---

#### Timeout do select() reduzido: 5ms → 1ms

**Problema:** O loop de leitura do pty SSH e do socket Telnet usava `select.select([fd], [], [], 0.005)` — no pior caso, dados que chegassem logo após o select iniciar uma nova espera aguardavam até 5ms antes de serem detectados.

**Solução:** Timeout reduzido para `0.001` (1ms) nos dois loops:

```python
# Antes
r, _, _ = select.select([fd], [], [], 0.005)

# Depois
r, _, _ = select.select([fd], [], [], 0.001)
```

Aplica-se tanto ao loop SSH (`read_ssh_output`) quanto ao loop Telnet (`read_telnet_output`).

**Arquivo:** `clientes/consumers.py`
