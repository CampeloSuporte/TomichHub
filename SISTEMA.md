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
   - [Sessão 13](#sessão-13--firmware-progress-ws-ftp-fix-design-modais-e-agent-noc) — Firmware WS, FTP, Design, Agent NOC
   - [Sessão 14](#sessão-14--firmware-url-fix-evolution-api-agent-grupos-e-bug-uiconfirm) — Firmware URL, Evolution API, Agent Grupos, uiConfirm
   - [Sessão 15](#sessão-15--agent-noc-whatsapp-terminal-e-correções-de-infraestrutura) — Agent NOC: WhatsApp funcional, terminal funcional, SSH legado, permissões
   - [Sessão 16](#sessão-16--multi-tenant-consultor-e-operador) — Multi-tenant: Consultor e Operador
   - [Sessão 17](#sessão-17--dashboard-da-instância-pra-consultor-e-operador) — Dashboard da instância

---

## Visão Geral

O CRM Tomich é uma aplicação web Django voltada para provedores de internet que centraliza:

- Gerenciamento de clientes e seus equipamentos de rede
- Acesso remoto via terminal SSH/Telnet diretamente no navegador
- Gerenciamento de VPNs (OpenVPN)
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

### `usuario` — Autenticação e Multi-tenant

Registro e autenticação de usuários, e o sistema de papéis multi-tenant (Administrador / Consultor / Operador / portal do Cliente final) — ver [Sessão 16](#sessão-16--multi-tenant-consultor-e-operador). `usuario/perms.py` é o ponto único de verdade para papel e escopo de instância, usado pelos decorators do núcleo (`clientes`, `monitoramento`, `ipam`, `hotspot`, `bgp`, `scripts`).

**URLs principais:** `/auth/`

### `seguranca` — Proteção contra invasão

Bloqueio por força bruta no login (3 senhas erradas trancam a conta por 5 min; 10 falhas trancam o IP por 15 min), ponte com o **fail2ban** (jails `sshd` e `crm-login`), middleware que barra SQL injection / path traversal / XSS, e o painel **Sistema → Segurança**. A checagem de bloqueio roda antes do `authenticate()` — durante o bloqueio nem a senha certa entra. Ver [docs/SEGURANCA.md](docs/SEGURANCA.md).

**URLs principais:** `/seguranca/`

### `funcao_equipamento` e `modelo_equipamento`

Cadastro de funções (Roteador, Switch, Firewall, OLT...) e modelos de equipamentos.

---

## Modelos de Dados

### Multi-tenant (app `usuario`)

| Modelo | Descrição |
|---|---|
| `PerfilUsuario` | Papel do usuário de back-office: `admin` / `consultor` / `operador`. Sem registro + `is_staff=True` = admin legado (compatibilidade retroativa) |
| `Instancia` | "Conta" de um Consultor — Clientes e Operadores pertencem a uma Instancia |
| `InstanciaFerramenta` | Ferramentas do núcleo liberadas pelo Administrador para a Instancia (default desabilitado) |

Ver [Sessão 16](#sessão-16--multi-tenant-consultor-e-operador) para o desenho completo.

### Segurança (app `seguranca`)

| Modelo | Descrição |
|---|---|
| `TentativaLogin` | Log append-only de toda tentativa de autenticação (sucesso e falha), com usuário, IP, motivo e navegador |
| `BloqueioLogin` | Contador de falhas + janela de bloqueio, por conta (`tipo='conta'`) ou por IP (`tipo='ip'`) |
| `EventoSeguranca` | Requisição barrada pelo filtro de injeção: tipo, assinatura, rota, campo e trecho do payload |
| `AcaoSeguranca` | Auditoria do painel — quem desbloqueou/baniu o quê, quando e de qual IP |

Os banimentos do fail2ban **não** têm modelo: a fonte da verdade é o `fail2ban-client`, porque é ele que fala com o firewall. Ver [docs/SEGURANCA.md](docs/SEGURANCA.md).

### Clientes e Infraestrutura

| Modelo | Descrição |
|---|---|
| `Cliente` | Empresa cliente com CNPJ, endereço, contatos. `instancia` (FK, nullable) — vazio = cliente da plataforma (só o Administrador vê) |
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
| `VPNOpenVPN` | Túnel OpenVPN por cliente (instância dedicada) |
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

**Túnel OpenVPN por cliente (CRM como servidor):**
- Instância systemd dedicada por túnel (porta, `tun-crm-N` e `/29` próprios)
- PKI própria e bootstrap de um comando no MikroTik
- WireGuard removido em 14/08/2026 (ver `docs/vpn_wireguard.md`)

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

- URL: `/home/ferramentas/lg/` — três abas: **Looking Glass**, **Filtro IRR (bgpq4)** e **AS-SET**
- Consulta um prefixo IPv4 ou IPv6 em múltiplos coletores BGP públicos simultaneamente
- Fontes: RIPE NCC RIS (API stat.ripe.net), RIPE RIS Whois (riswhois.ripe.net:43)
- Exibe AS paths agrupados por frequência com identificação do país de cada coletor RRC
- Badges IX.br/PTT: coletor RRC15 (São Paulo) identificado com badge `BR — IX.br/PTT`
- Modal de topologia BGP: clique em qualquer AS path para visualizar graficamente o caminho AS → ASN de origem
- Integrado com a aba IRR/RPKI dos clientes via botão "Consultar LG" e query string `?prefixo=`
- **Aba Filtro IRR (bgpq4)**: gera o prefix-list/route-filter de um ASN ou as-set no formato do fabricante (Cisco IOS/XR, Junos, Huawei VRP/XPL, MikroTik v6/v7, Nokia, Arista, BIRD, OpenBGPD, JSON, lista simples), com fontes IRR, max-length, agregação e download do arquivo completo
- **Aba AS-SET**: membros diretos, as-sets aninhados clicáveis, ASNs do fechamento recursivo com nome, contagem de prefixos v4/v6 e o objeto em cada base IRR (RADB, LACNIC, TC, RIPE…) com aviso quando divergem
- Detalhes: [docs/CONSULTA_IRR_ASSET.md](docs/CONSULTA_IRR_ASSET.md)

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
| NTT IRR (rr.ntt.net) | IRRd/WHOIS porta 43 + bgpq4 | Filtro IRR por fabricante e expansão de as-set (Pesquisa LG) |
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
| `fail2ban.service` | Blacklist de IP no firewall — jails `sshd` (porta **22002**, não a 22) e `crm-login` |

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
- `SEGURANCA_*` — limites do bloqueio de login e do filtro de injeção (ver [docs/SEGURANCA.md](docs/SEGURANCA.md))

### Arquivos de configuração fora do repositório

| Caminho | Função |
|---|---|
| `/etc/fail2ban/jail.d/crm.local` | Jails `sshd` e `crm-login` |
| `/etc/fail2ban/filter.d/crm-login.conf` | Filtro do log de login do CRM — o formato da linha é um contrato com `seguranca/services.py` |
| `/etc/sudoers.d/crm-fail2ban` | Deixa o `www-data` chamar só os verbos do `fail2ban-client` usados pelo painel |
| `/etc/logrotate.d/crm-seguranca` | Rotação de `/var/log/crm/auth.log` |
| `/var/log/crm/auth.log` | Uma linha por falha de login, lida pela jail `crm-login` |

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

- Views: `home/views.py` → funções `lg_pesquisa`, `lg_pesquisa_buscar`, `lg_irr_filtro` e `lg_as_set`
- Consultas IRR/bgpq4: `home/irr_tools.py`
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

---

### Sessão 13 — Firmware Progress WS, FTP Fix, Design Modais e Agent NOC

**Data:** 19/05/2026

---

#### 1. Progresso de download em tempo real via WebSocket (Gerenciador de Firmware)

Quando um link compartilhado é acessado (por uma OLT, equipamento ou usuário externo), os admins com a página de firmware aberta passam a ver um modal/painel com o progresso do download em tempo real.

**Arquitetura:**

- **Consumer WebSocket** `FirmwareDownloadConsumer` (`clientes/consumers.py`) — admins conectam em `ws/firmware/downloads/`; o consumer entra no grupo `firmware_downloads` no channel layer
- **Rota** adicionada em `clientes/routing.py`: `ws/firmware/downloads/`
- **Streaming com rastreamento** em `firmware_views.py` — `firmware_download` foi reescrito de `FileResponse` para `StreamingHttpResponse` com um generator que:
  - Emite `download_start` (nome, tamanho, IP do solicitante) ao iniciar
  - Emite `download_progress` (pct, bytes_sent) a cada 2 MB transferidos
  - Emite `download_complete` no bloco `finally` (garante envio mesmo se a conexão cair)
  - Usa `_fw_channel_send()` — wrapper `async_to_sync(channel_layer.group_send)` seguro de chamar de contexto síncrono

**Helper centralizado:**
```python
def _fw_channel_send(event_type: str, data: dict):
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(
            'firmware_downloads',
            {'type': 'download_event', 'event_type': event_type, **data},
        )
```

**Frontend (`clientes/templates/firmware.html`):**
- Conecta WebSocket em `fwDlConectar()` com reconexão automática após 4 s
- Painel flutuante `#fwDlOverlay` com lista de downloads ativos, barra de progresso por download, IP do solicitante e nome do arquivo
- Badge flutuante `#fwDlBadge` (canto inferior direito) visível quando o painel está fechado, mostrando contagem de downloads ativos
- Downloads concluídos ficam verdes e se auto-removem após 6 s

**Arquivos modificados:**
- `clientes/consumers.py` — classe `FirmwareDownloadConsumer`
- `clientes/routing.py` — nova rota WS
- `clientes/firmware_views.py` — `_fw_channel_send()`, `firmware_download()` reescrito
- `clientes/templates/firmware.html` — modal + JavaScript WS

---

#### 2. Correção de permissão no upload de firmware (Via URL)

**Problema:** O worker do Gunicorn (`www-data`) não tinha permissão para criar diretórios ou arquivos em `/opt/crm/media/firmware/` (dono `root:tftp`), causando `[Errno 13] Permission denied`.

**Solução:**
```bash
usermod -aG tftp www-data          # www-data entra no grupo tftp
chmod -R g+w /opt/crm/media/firmware/  # grupo com permissão de escrita recursiva
systemctl restart gunicorn         # sessão do processo herda novo grupo
```

---

#### 3. Correção de login FTP nos links compartilhados

**Problema:** Usuários FTP temporários (`fw_XXXXXX`) gerados nos links compartilhados não conseguiam fazer login. O `useradd` falhava silenciosamente porque `www-data` não tinha permissão para executá-lo, e o `chpasswd` confirmava: `user "fw_xzijyq" does not exist`.

**Causa raiz:** Gunicorn roda como `www-data`, que não tem privilégio para criar usuários do sistema.

**Solução:** Criado `/etc/sudoers.d/crm-firmware-ftp` com regras restritas:
```
www-data ALL=(ALL) NOPASSWD: /usr/sbin/useradd -r -d /opt/crm/media/firmware -s /usr/sbin/nologin -M --no-user-group fw_*
www-data ALL=(ALL) NOPASSWD: /usr/sbin/userdel fw_*
www-data ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd
```

As funções `_ftp_criar_usuario()` e `_ftp_remover_usuario()` em `firmware_views.py` foram atualizadas para chamar os comandos com `sudo`. Testado: `ftplib.FTP().login('fw_teste', 'Senha...')` retornou `LOGIN OK`.

**Arquivos modificados:**
- `/etc/sudoers.d/crm-firmware-ftp` (novo)
- `clientes/firmware_views.py` — `_ftp_criar_usuario()`, `_ftp_remover_usuario()`

---

#### 4. Redesign: modais de Cadastro, Edição e Cópia de Acessos

Os três modais de acesso (`modalAcesso`, `modalEditarAcesso`, `modalDuplicarAcesso`) estavam usando a paleta verde Matrix antiga enquanto o restante da plataforma já havia migrado para o tema cyberpunk cyan.

**Mudanças em `static/css/style.css`:**

| Elemento | Antes | Depois |
|---|---|---|
| `.modal-acesso` fundo | `#001a0d` | `var(--card-bg)` |
| Borda do modal | `2px solid #00ff41` | `1px solid var(--border)` |
| `.modal-title` fonte/cor | Courier New `#00ff41` | Orbitron + `var(--cyan)` |
| `.modal-body-acesso` | `#000d06` | `var(--dark-bg)` |
| Inputs fundo/texto/borda | verde escuro / `#4d8c6f` | `var(--card-bg)` / `var(--text-light)` / cyan dim |
| Inputs `:focus` | borda `#00ff41` | borda `var(--cyan)` + glow `rgba(0,245,255,.08)` |
| `btn-submit-modal` | fundo `#00ff41` / texto preto | fundo cyan translúcido / texto cyan |
| `btn-cancel-modal` | fundo verde escuro | transparente + borda cyan dim |
| `.search-select-*` | cores verdes Matrix | cores cyan da plataforma |
| Scrollbar | verde | cyan translúcido |

Adicionado seletor `appearance: auto` nos `select` para exibir a seta nativa estilizada via SVG inline cyan.

---

#### 5. Bug fix: checkboxes não apareciam marcados nos modais de acesso

**Problema:** Ao clicar em "Habilitar backup" ou "Executar backup automaticamente", o checkbox não mostrava visualmente o estado marcado.

**Causa raiz:** O seletor CSS `#modalEditarAcesso input { background: var(--card-bg) !important }` capturava todos os `<input>`, incluindo `type="checkbox"`, sobrescrevendo o `background-color` do estado `:checked` nativo do browser.

**Correção em `static/css/style.css`:**
- Todos os seletores de input dos três modais passaram a usar `:not([type="checkbox"]):not([type="radio"])` — excluindo checkboxes do override
- Adicionada regra específica para checkboxes restaurando aparência nativa e definindo `accent-color: var(--cyan)`:
```css
#modalEditarAcesso input[type="checkbox"],
#modalAcesso input[type="checkbox"],
#modalDuplicarAcesso input[type="checkbox"] {
    appearance: auto !important;
    -webkit-appearance: auto !important;
    accent-color: var(--cyan) !important;
    background: transparent !important;
    border: none !important;
}

**Arquivo modificado:** `static/css/style.css`

---

### Sessão 14 — Firmware URL fix, Evolution API, Agent Grupos e Bug uiConfirm

#### 1. Fix: download por URL não respeitava nome do arquivo (OneDrive / RFC 5987)

**Problema:** Ao tentar baixar um firmware via URL do OneDrive no formato `download.aspx?SourceUrl=.../MA5800.bin`, o arquivo era salvo como `download.aspx` porque o código extraía o nome somente do `path` da URL, ignorando a query string. Além disso, o header `Content-Disposition` no formato RFC 5987 (`filename*=UTF-8''...`) não era reconhecido pelo regex antigo.

**Causa raiz em `clientes/firmware_views.py`:**
- `os.path.basename(parsed.path)` → `download.aspx` (nome do script, não do arquivo)
- Regex `filename=["\']?([^"\';\r\n]+)` não captura `filename*=UTF-8''...`

**Solução:** Duas novas funções auxiliares:

```python
def _fw_inferir_nome_url(url):
    # Se o basename for extensão de script (.aspx, .php, etc.),
    # varre os parâmetros de query: SourceUrl, source, file, filename, name, f, dl, path
    # e usa o basename do primeiro parâmetro que contenha um nome com extensão

def _fw_nome_do_content_disposition(cd):
    # 1) Tenta RFC 5987: filename*=charset''encoded_name  (prioridade)
    # 2) Fallback: filename="..." ou filename=...
```

O `_fw_download_worker` foi simplificado para usar ambas as funções. O `firmware_upload_url` passou a usar `_fw_inferir_nome_url` para o `nome_hint` exibido na UI imediatamente.

**Arquivos modificados:**
- `clientes/firmware_views.py` — novas funções `_fw_inferir_nome_url`, `_fw_nome_do_content_disposition`; refatoração de `firmware_upload_url` e `_fw_download_worker`

---

#### 2. Fix: Evolution API — URL inacessível (hostname Docker interno)

**Problema:** A URL cadastrada `http://evolution-api_evolution-api:8080` é um hostname interno Docker (convenção `{projeto}_{serviço}`). O CRM roda em bare-metal sem Docker e não consegue resolver esse nome.

**Diagnóstico:** Varredura da rede `179.48.68.64/27` confirmou que a Evolution API está acessível em `https://evolution-api-evolution-api.mqezv6.easypanel.host` (EasyPanel).

**Correção:** URL atualizada diretamente no banco via script Python. A API key `429683C4...` também estava incorreta (401 Unauthorized) — o usuário deve atualizar para a `AUTHENTICATION_API_KEY` correta definida nas variáveis de ambiente do EasyPanel.

**Arquivo modificado:** banco de dados (`EvolutionAPIConfig.url`)

---

#### 3. Fix: TemplateSyntaxError — filtro `selectattr` não existe no Django

**Problema:** `TemplateSyntaxError: Invalid filter: 'selectattr'` ao acessar `/home/agent/grupos/`. O template usava `{{ grupos|selectattr:'cliente'|list|length }}` — filtro Jinja2 que não existe no sistema de templates Django.

**Correção:** Os três contadores foram movidos para a view `agent_grupos`:

```python
return render(request, 'agent_grupos.html', {
    'grupos': grupos,
    'clientes': clientes,
    'total_vinculados': sum(1 for g in grupos if g.cliente_id),
    'total_ativos':     sum(1 for g in grupos if g.ativo),
    'total_sem':        sum(1 for g in grupos if not g.cliente_id),
})
```

Template atualizado para usar `{{ total_vinculados }}`, `{{ total_ativos }}`, `{{ total_sem }}` diretamente.

**Arquivos modificados:**
- `home/views.py` — `agent_grupos`
- `home/templates/agent_grupos.html` — stats strip

---

#### 4. UX: campo de vínculo de cliente com busca/autocomplete (Agent Grupos)

**Problema:** O campo "Cliente vinculado" no modal de edição de grupos usava um `<select>` simples — com muitos clientes ficava difícil de usar.

**Solução:** Campo substituído por search-select customizado:
- Input de texto com placeholder "🔍 Digite para buscar o cliente..."
- Dropdown com filtro em tempo real por nome e CNPJ com highlighting dos termos
- Navegação por teclado (↑ ↓ Enter Esc)
- Fechar ao clicar fora
- Campo `<input type="hidden">` para armazenar o `id` selecionado
- Opção "Sem vínculo" sempre no topo

**Implementação:** CSS (`.cs-wrap`, `.cs-dropdown`, `.cs-option`) e JS (`csFilter`, `csSelect`, `csKeydown`, `csOpen`, `csClose`) adicionados inline no template. Lista de clientes serializada em `CS_CLIENTES` a partir do contexto Django.

**Arquivos modificados:**
- `home/templates/agent_grupos.html` — modal campo cliente

---

#### 5. Bug fix: tela ficando escura ao confirmar ações (uiConfirm backdrop preso)

**Problema:** Em várias páginas (especialmente na aba IRR ao clicar "Enviar Atualização"), a tela ficava com overlay escuro permanente após fechar o modal de confirmação.

**Causa raiz:** `uiConfirm` e `uiAlert` eram implementados como modais Bootstrap (`bootstrap.Modal`). O Bootstrap gerencia um `div.modal-backdrop` via animações CSS. Quando dois modais interagiam (ex: `modalIrrPreview` fechando via `data-bs-dismiss` enquanto `uiConfirm` abria), o Bootstrap acumulava backdrops ou não removia o último, deixando o overlay preso.

**Solução:** `uiConfirm` e `uiAlert` reescritos como **overlays puros** — sem Bootstrap Modal, sem backdrop div, sem animação de fade:

```html
<!-- Antes: <div class="modal fade" data-bs-backdrop="static" ...> -->
<!-- Depois: -->
<div id="uiModalConfirm" style="display:none; position:fixed; inset:0;
     background:rgba(0,0,0,.78); z-index:99995; backdrop-filter:blur(3px);
     align-items:center; justify-content:center;">
```

```javascript
// Antes: bootstrap.Modal.getOrCreateInstance(...).show() / .hide()
// Depois:
overlay.style.display = 'flex';   // abrir
overlay.style.display = 'none';   // fechar — instantâneo, sem backdrop residual
```

A Promise resolve imediatamente ao clicar no botão — sem event listeners adicionais, sem race condition.

**Arquivos modificados:**
- `templates/base.html` — HTML dos dois modais + funções `uiConfirm`, `uiAlert`; removidas funções auxiliares `_uiHideAndClean` e `_uiCleanup` que não são mais necessárias

---

### Sessão 15 — Agent NOC: WhatsApp funcional, terminal funcional, SSH legado, permissões

#### 1. Fix: `FieldDoesNotExist` — campo `tokens_usados` inexistente em `AgentSessao`

**Problema:** `_atualizar_tokens` tentava fazer `.update(tokens_usados=total)` na model `AgentSessao`, mas esse campo não existe — tokens são registrados por interação em `AgentLog`.

**Correção:** Removida a chamada `.update()` desnecessária. A notificação de tokens ao cliente via `notify_cb` foi mantida.

**Arquivo:** `home/agent_engine.py` — método `_atualizar_tokens`

---

#### 2. Fix: Evolution API — payload sendText formato errado (v1 vs v2)

**Problema:** O payload enviado usava formato v1 (`{"textMessage": {"text": "..."}}`), mas a Evolution API v2 espera `{"number": "...", "text": "..."}`. Resultado: HTTP 400 em todas as respostas do agente via WhatsApp.

**Correção:** Atualizado payload em `_evolution_send` no `agent_engine.py`.

```python
# Antes (v1)
payload = {"number": jid, "options": {"delay": 200}, "textMessage": {"text": texto}}
# Depois (v2)
payload = {"number": jid, "text": texto}
```

**Arquivo:** `home/agent_engine.py` — função `_evolution_send`

---

#### 3. Fix: Terminal Agent NOC — WebSocket conectando com dupla barra (`ws/agent//`)

**Problema:** `_getAcessoAtivoId()` usava `window._termTabs` que nunca é populado (terminais ficam em `terminalManager.terminals`). Resultado: `acessoId` sempre vazio → URL `/ws/agent//` → Daphne retornava `No route found for path 'ws/agent//'`.

**Correção:**
- `_getAcessoAtivoId()` reescrita para ler de `terminalManager.terminals.get(mgr.activeTerminal)`
- URL construída sem barra dupla quando `acessoId` é vazio
- Adicionado `?cliente=ID` na URL do WebSocket (lido da query string da página)
- Consumer lê `?cliente=` do `scope['query_string']` como fallback quando não há `acesso_id`

**Arquivos:** `clientes/templates/terminal.html`, `home/agent_consumer.py`

---

#### 4. Fix: Terminal Agent NOC — "Não foi possível identificar o cliente"

**Problema:** Usuários staff sem cliente vinculado à conta e sem acesso ativo aberto recebiam erro ao conectar ao Agent NOC pelo terminal.

**Correção:** Consumer lê `?cliente=ID` da query string do WebSocket como primeira alternativa antes de tentar `Cliente.objects.get(usuario=user)`.

**Arquivo:** `home/agent_consumer.py` — método `_inicializar_sessao`

---

#### 5. Fix: `SynchronousOnlyOperation` — `str(acesso)` em contexto async

**Problema:** `_tool_execute_command` chamava `str(acesso)` que aciona `Acesso.__str__()` → `self.cliente.nome_empresa` → lazy load de FK em contexto async → Django bloqueia.

**Correção:**
- `_get_acesso` atualizado para `select_related('modelo', 'cliente')` — carrega o cliente junto
- Substituído `str(acesso)` por `f"{acesso.tipo} - {acesso.host}"` em todos os pontos

**Arquivo:** `home/agent_engine.py`

---

#### 6. Fix: `Funcao_equipamento` sem atributo `nome`

**Problema:** Código usava `a.funcao.nome` mas o modelo `Funcao_equipamento` tem o campo `descricao`, não `nome`. Ocorria em dois lugares: `_build_system_prompt` e `_tool_list_hosts`.

**Correção:** Substituídos todos os usos de `.nome` por `.descricao` no `agent_engine.py`.

**Arquivo:** `home/agent_engine.py`

---

#### 7. Fix: Aprovação WhatsApp sempre usava fabricante `generico`

**Problema:** O callback `aprovacao_wa` em `views.py` sempre chamava `_is_safe_command(comando, 'generico')` independente do equipamento real. Mikrotik RouterOS (`/interface print`, `ip address add`) nunca passava na verificação genérica.

**Correção:** `aprovacao_wa` busca o modelo do host pelo `acesso_id` e usa o fabricante correto na verificação.

**Arquivo:** `home/views.py` — função `aprovacao_wa`

---

#### 8. Novo: Sistema de permissões em 3 níveis para WhatsApp

**Implementação:** Adicionado `OPERATIONAL_COMMANDS` dict por fabricante e função `_is_operational_command()`:

| Nível | O que permite |
|-------|--------------|
| `leitura` | Apenas comandos read-only (`SAFE_COMMANDS`) |
| `operacional` | Leitura + escrita não-destrutiva (add/remove/enable/disable de IPs, rotas, interfaces, filas) |
| `admin` | Tudo exceto `BLOCKED_COMMANDS` (reboot/format/erase/factory reset) |

**Arquivo:** `home/agent_engine.py` — `OPERATIONAL_COMMANDS`, `_is_operational_command`; `home/views.py` — `aprovacao_wa`

---

#### 9. Melhoria: Lista safe de comandos Mikrotik expandida

**Problema:** Agent enviava `interface print` (sem `/`), mas a lista aceitava apenas `^/interface print`. Resultado: comandos RouterOS legítimos eram rejeitados.

**Correção:** Lista Mikrotik expandida para aceitar comandos com e sem barra inicial (`^/?`), e ~20 novos comandos adicionados (`system resource print`, `ip route print`, `queue print`, `bridge print`, `ppp active print`, etc.).

**Arquivo:** `home/agent_engine.py` — `SAFE_COMMANDS['mikrotik']`

---

#### 10. Melhoria: System prompt dinâmico por nível de permissão

**Problema:** System prompt dizia "apenas comandos read-only no WhatsApp" para todos os grupos, fazendo o modelo recusar escritas mesmo em grupos `admin`.

**Correção:** System prompt gerado dinamicamente com instrução específica por nível:
- `admin` → "execute QUALQUER comando exceto reboot/format/erase/factory reset"
- `operacional` → "execute leitura + configurações não-destrutivas"
- `leitura` → "execute apenas read-only"

**Arquivo:** `home/agent_engine.py` — `_build_system_prompt`

---

#### 11. Melhoria: System prompt inclui fabricante, modelo e função de cada host

**Problema:** Hosts listados no prompt tinham apenas nome/IP/protocolo. O modelo não sabia o fabricante e usava comandos errados (ex: `show` em vez de `display` para Huawei).

**Correção:** Cada host agora exibe `função=BRAS | modelo=HUAWEI NE8000 M4 | fabricante=huawei`. Adicionada seção de referência de sintaxe por fabricante.

**Arquivo:** `home/agent_engine.py` — `_build_system_prompt`, função `_host_line`

---

#### 12. Melhoria: System prompt com instrução explícita para usar ferramentas

**Adicionado:** Regra explícita "quando pedido para acessar host, use `execute_command` IMEDIATAMENTE — não responda com texto dizendo que não pode". Instrução para sempre incluir output bruto em bloco de código.

**Arquivo:** `home/agent_engine.py` — `_build_system_prompt`

---

#### 13. Nova ferramenta: `get_terminal_output`

**Funcionalidade:** Permite ao agente capturar autonomamente o conteúdo visível do terminal SSH/Telnet aberto no browser, sem necessidade de o operador clicar em "Colar output".

**Fluxo:**
1. Agente chama `get_terminal_output` quando detecta mensagens como "analisa esse log", "o que aparece no terminal"
2. Consumer envia `{type: "request_terminal_output"}` ao browser via WebSocket
3. JS lê o buffer xterm.js do terminal ativo via `terminalManager.terminals`
4. Browser responde com `{type: "terminal_output_response", content: "..."}`
5. Consumer resolve o Future e retorna o conteúdo ao agente

**Arquivos:** `home/agent_engine.py` (tool definition + `_tool_get_terminal_output`), `home/agent_consumer.py` (`_solicitar_terminal_output`, handler `terminal_output_response`), `clientes/templates/terminal.html` (`_agentGetTerminalBuffer`, handler `request_terminal_output`)

---

#### 14. Fix: `_agentGetTerminalBuffer` sempre retornava vazio

**Problema:** A função usava `window._termTabs || []` que nunca é populado. Os terminais ficam em `terminalManager.terminals` (Map).

**Correção:** Reescrita para iterar `terminalManager.terminals`, priorizando o terminal ativo (`mgr.activeTerminal`).

**Arquivo:** `clientes/templates/terminal.html`

---

#### 15. Fix: Sessão WhatsApp não atualizava ao trocar cliente do grupo

**Problema:** Ao mudar o cliente vinculado a um grupo WhatsApp, a sessão antiga (com cliente anterior) continuava sendo reutilizada por até 2h (timeout), mostrando hosts do cliente errado.

**Correção:** Filtro de busca de sessão agora inclui `cliente=grupo.cliente`. Ao criar nova sessão, sessões ativas do mesmo JID com cliente diferente são encerradas automaticamente.

**Arquivo:** `home/views.py` — `_processar_wa_mensagem_async`

---

#### 16. Refatoração: Agent usa infraestrutura SSH da plataforma

**Problema:** `_ssh_exec_sync` no agent usava paramiko básico sem suporte a algoritmos legados. Equipamentos Huawei (NE8000, OLTs) usam `diffie-hellman-group1-sha1` desabilitado por padrão no paramiko 4.x → erro de conexão silencioso.

**Solução:** Criada função `platform_ssh_exec(acesso, comando)` em `clientes/consumers.py` que reutiliza:
- Mesmos flags SSH da plataforma (`KexAlgorithms`, `HostKeyAlgorithms`, `Ciphers`, `MACs` legados)
- Detecção automática de proxy (IP privado → proxy paramiko direct-tcpip)
- `pexpect` para conexões diretas (mesma autenticação interativa do terminal)
- `screen-length 0 temporary` automático para equipamentos Huawei
- Suporte a Huawei via VRP, Mikrotik RouterOS, Linux, Cisco IOS, ZTE

`agent_engine.py` agora delega para `platform_ssh_exec` — sem reimplementação de lógica SSH.

**Arquivos:** `clientes/consumers.py` (funções `platform_ssh_exec`, `_pexpect_exec`, `_paramiko_proxy_exec`), `home/agent_engine.py` (removido paramiko direto)

---

#### 17. Fix: Aba Monitoramento sumiu do painel de clientes

**Problema:** O `{% include 'monitoramento/tab_monitoramento.html' %}` foi removido do `listar.html`. O menu ainda exibia o link "🖧 Monitoramento" mas o `<div id="tab-monitoramento">` não existia no DOM.

**Correção:** Recolocado o include entre as abas Credenciais e Documentação.

**Arquivo:** `clientes/templates/listar.html`

---

### Sessão 16 — Multi-tenant: Consultor e Operador

**Objetivo:** comercializar a plataforma para consultores de rede — cada um cadastra e gerencia seus próprios clientes, isolados dos clientes de outros consultores, sem acesso a Financeiro/Atendimento/Wiki/dashboards administrativos. O Administrador escolhe, por instância, quais ferramentas do núcleo cada consultor pode usar. O Operador é um funcionário do consultor (ou do admin) com acesso quase igual, exceto que não gerencia usuários nem escolhe ferramentas da instância.

#### 1. Decisão de design: `is_staff` não muda de significado

Consultor e Operador continuam com `is_staff=False` — exatamente como o portal do cliente final tinha antes. Isso evita reabrir dezenas de checks `is_staff`/`admin_required` espalhados por Financeiro, Atendimento, Wiki, `home`, catálogos de equipamento e Firmware, que continuam tratando `is_staff=True` como bypass total de administrador. O papel real do usuário passa a vir de um novo modelo `PerfilUsuario` (`role`: `admin`/`consultor`/`operador`), consultado através de `usuario/perms.py` — não de `is_staff` cru. Contas administradoras existentes continuam funcionando sem qualquer migração de dados: na ausência de `PerfilUsuario`, `is_staff=True` cai de volta para papel `admin` (compatibilidade retroativa).

#### 2. Modelos novos (`usuario/models.py`)

- `Instancia` — a "conta" de um Consultor (`nome`, `ativo`, `criado_por`).
- `PerfilUsuario` — `usuario` (OneToOne), `role` (`admin`/`consultor`/`operador`), `instancia` (FK, null só para admin), `criado_por`.
- `InstanciaFerramenta` — `instancia`, `ferramenta` (14 chaves: `acessos, backups, vpn, topologia, tuneis, documentos, rpki_irr, monitoramento, hotspot, ipam, scripts, bgp, testes_rede, lg, geoip, firmware`), `habilitado` (**default `False`** — o oposto do `UsuarioModulo` existente: aqui é o Administrador concedendo acesso a um revendedor pago, não um toggle opcional por login).
- `Cliente.instancia` (FK nullable, `clientes/models.py`) — cliente sem instância = "da plataforma", só o Administrador vê (preserva o comportamento anterior sem precisar de data migration).
- `Cliente.objects.visiveis_para(user)` — escopo central de visibilidade usado em todas as telas de back-office (admin → tudo; consultor/operador → só a própria instância).

`UsuarioModulo` (o toggle por login existente, usado pelo portal do cliente final) não mudou de propósito — continua um sistema paralelo, agora combinado com `InstanciaFerramenta` (ver item 5).

#### 3. `usuario/perms.py` — ponto único de verdade

Toda checagem de papel/escopo no núcleo passa por aqui: `get_role`, `is_admin`/`is_consultor`/`is_operador`/`is_backoffice`, `get_instancia`, `pode_gerenciar_usuarios`, `pode_gerenciar_ferramentas_instancia`, `ferramenta_habilitada(user, key)`, `pode_acessar_cliente(user, cliente)`, `usuarios_gerenciaveis_por(user)`.

#### 4. Decorators (`clientes/decorators.py`)

- `backoffice_required` (novo) — admin/consultor/operador; usado nas telas do núcleo que Consultor/Operador também acessam (ex.: `cadastrar_cliente`).
- `admin_required`/`superuser_required` — inalterados, continuam exclusivos do Administrador (o que já barra Consultor/Operador de Financeiro, Atendimento, Wiki, catálogos de equipamento, Firmware antigo, etc., sem precisar tocar nesses apps).
- `cliente_can_view_cliente` — delega para `pode_acessar_cliente`.
- `modulo_habilitado_required(key)` — bypass só para admin; Consultor/Operador checam `InstanciaFerramenta`; portal do cliente final passa por `portal_pode_usar_ferramenta` (ver item 5). Já estava aplicado em ~86 pontos de `clientes/views.py` (acessos, backups, vpn, topologia, túneis, rpki_irr, documentos, testes_rede) — a mudança central cobriu a maior parte do núcleo sem editar view por view.
- `ferramenta_instancia_required(key)` (novo) — mesma lógica, aplicado em `monitoramento/views.py`, `clientes/ipam_views.py`, `clientes/hotspot_views.py`, `clientes/bgp_views.py`, `clientes/script_views.py`, `clientes/firmware_views.py` e nas views de LG/GeoIP em `home/views.py` — views que antes só tinham `@login_required`, sem nenhum controle de ferramenta.

#### 5. Portal do cliente final: teto da instância do Consultor

Bug encontrado em teste manual: um Consultor sem a ferramenta Hotspot liberada via `InstanciaFerramenta` via a aba Hotspot aparecer mesmo assim — tanto no próprio painel quanto no painel do cliente que ele cadastrou (o `UsuarioModulo` do cliente final, com default ligado, nunca conferia o teto da instância). Corrigido com duas funções em `usuario/perms.py`:

- `modulos_habilitados_dict_para_listagem(user, cliente)` — dict usado por `listar.html` pra decidir quais abas mostrar, unificando admin (tudo), consultor/operador (`InstanciaFerramenta` da própria instância) e portal do cliente final (`UsuarioModulo` capado pela `InstanciaFerramenta` da instância do Consultor dono do cliente).
- `portal_pode_usar_ferramenta(user, key)` — mesma lógica no backend, usada pelos decorators.

Inclui o mapeamento `'documentacao' → 'ipam'` (nomes históricos diferentes pro mesmo recurso — a aba "Documentação de Rede" do `UsuarioModulo` é o backend de IPAM).

No template (`clientes/templates/listar.html`), as 13 chamadas que decidiam visibilidade de aba/ferramenta (`{% if is_admin or modulos_habilitados.X %}`) foram trocadas para `is_admin_puro` (só Administrador de fato) — `is_admin` continua significando "é da equipe" (admin/consultor/operador) e segue controlando os outros ~4 pontos de UI que não são sobre ferramentas (ex.: campo de senha admin).

#### 6. Gestão de usuários (`usuario/views.py`, `usuario/templates/cadastrar_usuario.html`)

Seletor de 4 papéis no lugar do checkbox único `is_staff`: **Cliente** / **Operador** / **Consultor** / **Administrador** — as duas últimas opções só aparecem para quem já é Administrador. Regras reforçadas no servidor (não só escondidas na UI):

- Consultor só cria `role=operador`, sempre na própria instância — mesmo manipulando o POST diretamente.
- Ao criar/editar um Consultor, o Administrador vê checkboxes de `InstanciaFerramenta` (mesmo padrão visual dos checkboxes de `UsuarioModulo` já existentes).
- `cadastrar_usuario`/`editar_usuario` passam a listar só `usuarios_gerenciaveis_por(request.user)` em vez de `User.objects.all()`.

#### 7. Navegação (`templates/base.html`, `usuario/context_processors.py`)

Novo context processor `perfil_context` expõe `is_admin_bo`/`is_consultor_bo`/`is_operador_bo`/`is_backoffice_bo` e `ferramentas_habilitadas`/`ferramentas_menu_bo_visivel`. Os blocos hoje gated só por `is_staff` (Financeiro, Atendimento, Wiki, catálogos, Agent NOC) não precisaram mudar — já ficam de fora para Consultor/Operador. Item de menu "Clientes" (e "Usuários", só pra quem gerencia usuários) adicionado pra back-office não-admin; dropdown "Ferramentas" ganhou uma versão enxuta mostrando só LG/GeoIP/Firmware quando liberados pra instância.

#### 8. Bugs encontrados em teste manual pós-deploy (e corrigidos na mesma sessão)

1. **Busca de cliente do menu vazava entre instâncias** — `buscar_clientes_chamado` (`clientes/views.py`) usava `Cliente.objects.filter(...)` sem nenhum escopo; um Consultor via nos resultados clientes de outra instância (o clique já era bloqueado por `pode_acessar_cliente`, mas a lista não deveria nem mostrar o nome). Trocado para `Cliente.objects.visiveis_para(request.user)`.
2. **Endpoints do IPAM identificados só por objeto (`vlan_id`/`prefixo_id`/`subrede_id`/`ip_id`/`vpn_id`)** não passavam pelo helper `_cliente()` (que já validava posse) — um Consultor/Operador com IPAM liberado podia mutar VLAN/prefixo/sub-rede/IP/VPN de outra instância adivinhando o id. Fechado com `_checar_obj_cliente` aplicado nos 15 endpoints afetados.
3. **Aba/ferramenta não liberada pra instância aparecia mesmo assim** — ver item 5 acima (bug do Hotspot reportado pelo usuário).
4. **Wiki não tinha nenhum gate de `is_staff`** — reachable por qualquer usuário autenticado, incluindo (antes desta sessão) o próprio portal do cliente final. Adicionado `@admin_required` nas 12 views de `wiki/views.py`, cumprindo a premissa de que Wiki continua exclusivo do Administrador nesta fase.

**Fora do escopo desta sessão** (fica pra uma fase 2, caso decidam abrir pra Consultor/Operador): Financeiro, Atendimento, Wiki e os dashboards administrativos globais (`quadro_geral` e relatórios cross-cliente) continuam exclusivos do Administrador. Firmware é biblioteca global compartilhada (sem FK de cliente/instância) — liberar a ferramenta dá acesso ao mesmo acervo que o Administrador vê, não uma cópia isolada por instância.

**Arquivos principais:** `usuario/models.py`, `usuario/perms.py` (novo), `usuario/views.py`, `usuario/context_processors.py` (novo), `usuario/templates/cadastrar_usuario.html`, `clientes/decorators.py`, `clientes/models.py`, `clientes/views.py`, `clientes/api_views.py`, `clientes/ipam_views.py`, `clientes/hotspot_views.py`, `clientes/bgp_views.py`, `clientes/script_views.py`, `clientes/firmware_views.py`, `monitoramento/views.py`, `home/views.py`, `wiki/views.py`, `templates/base.html`, `crm/settings.py`.

---

### Sessão 17 — Dashboard da instância pra Consultor e Operador

**Pedido do usuário:** "cria um dashboard igual o dash da plataforma para a instância do consultor e seus operadores".

**Implementação:** nova view `quadro_instancia` (`home/views.py`, URL `quadro_instancia` → `/homeinstancia`, mesmo padrão de mount sem separador usado por `quadro_geral`/`/homegeral`) — réplica das queries de `quadro_geral` (stats de clientes/hosts, backups de hoje, gráfico dos últimos 14 dias, últimos 10 backups, top 5 clientes por hosts, blocos RPKI/IRR inválidos), mas toda escopada via `Cliente.objects.visiveis_para(request.user)` em vez de `Cliente.objects.all()`/`BackupLog.objects.all()` etc. Consultor e todos os seus Operadores compartilham a mesma instância, então veem exatamente os mesmos números — não há dashboard por login, só por instância.

Em vez de duplicar os ~300 linhas de HTML/CSS/JS do template, `quadro_geral.html` foi parametrizado com três variáveis de contexto (com default pra não quebrar a view do Administrador, que não precisou mudar a lógica, só ganhou `mostrar_relatorio_backups: True` explícito):

- `dash_titulo` — "Dashboard" (admin) vs. nome da instância (consultor/operador)
- `dash_subtitulo` — "Visão geral do sistema" vs. "Visão geral da sua instância"
- `mostrar_relatorio_backups` — esconde os 2 links pro relatório de backups (`relatorio_backups`, admin-only) na versão da instância, já que não existe uma versão escopada desse relatório ainda

`quadro_instancia` é gated por `backoffice_required` (admin/consultor/operador) e redireciona Administrador de volta pro `quadro_geral` internamente (não tem instância própria). Também trata o caso extremo de um `PerfilUsuario` consultor/operador sem `instancia` (não deveria acontecer via fluxo normal de criação, mas se acontecer, desloga em vez de tentar redirecionar pra `login`, que bateria de novo em `redirect_user_by_role` e entraria em loop).

**Navegação:** `redirect_user_by_role` (`usuario/views.py`) agora manda Consultor/Operador pra `quadro_instancia` no login, em vez de `cadastrar_cliente` — mesmo padrão do Administrador, que sempre caiu em `quadro_geral`. O botão "Dashboard" do menu (`templates/base.html`) que antes apontava pra `cadastrar_cliente` nesse caso agora aponta pro dashboard; "Clientes" continua acessível pelo dropdown "Sistema".

**Arquivos:** `home/views.py` (`quadro_instancia`, + imports `redirect`/`messages` que faltavam nesse arquivo), `home/urls.py`, `home/templates/quadro_geral.html`, `usuario/views.py` (`redirect_user_by_role`), `templates/base.html`.
