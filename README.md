# TomichHub — CRM NOC para Provedores de Internet

Sistema web completo de gerenciamento de clientes voltado a provedores de internet (ISPs).
Centraliza acesso remoto a equipamentos, IPAM, VPN, backups, RPKI/IRR, financeiro, monitoramento e muito mais — tudo em uma única interface no navegador.

---

## Sumário

- [Visão Geral](#visão-geral)
- [Stack Tecnológica](#stack-tecnológica)
- [Arquitetura](#arquitetura)
- [Módulos do Sistema](#módulos-do-sistema)
  - [Clientes e Equipamentos](#clientes-e-equipamentos)
  - [Terminal Web SSH/Telnet](#terminal-web-sshtelnet)
  - [WinBox e Acesso Web](#winbox-e-acesso-web)
  - [Hotspot MikroTik](#hotspot-mikrotik)
  - [Backups Automatizados](#backups-automatizados)
  - [IPAM Nativo](#ipam-nativo)
  - [VPN — OpenVPN](#vpn--openvpn)
  - [RPKI e IRR](#rpki-e-irr)
  - [Scripts de Automação](#scripts-de-automação)
  - [Monitor de Tráfego](#monitor-de-tráfego)
  - [Financeiro](#financeiro)
  - [Atendimento e Chamados](#atendimento-e-chamados)
  - [Agent NOC (IA)](#agent-noc-ia)
  - [Wiki](#wiki)
  - [Ferramentas de Rede](#ferramentas-de-rede)
- [Integrações Externas](#integrações-externas)
- [Instalação](#instalação)
- [Configuração Inicial](#configuração-inicial)
- [Comandos do Dia a Dia](#comandos-do-dia-a-dia)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Documentação Técnica](#documentação-técnica)
- [Troubleshooting](#troubleshooting)

---

## Visão Geral

O TomichHub é uma plataforma NOC desenvolvida em Django com foco em provedores de internet. Substituindo múltiplas ferramentas separadas, ele oferece:

- Gerenciamento completo de clientes e sua infraestrutura de rede
- Acesso remoto SSH/Telnet/Winbox diretamente no navegador
- IPAM nativo (VLANs, prefixos, IPs, túneis VPN)
- Automação de backups de configurações de equipamentos
- Gestão e automação de VPNs OpenVPN
- Validação e atualização de RPKI/IRR junto ao NIC.br
- Monitor de tráfego em tempo real via Zabbix com múltiplas abas
- Módulo financeiro completo (faturas, contratos, LOA, WhatsApp, PIX)
- Agent NOC com inteligência artificial para análise e diagnóstico
- Hotspot captive portal com cadastro de usuários

---

## Stack Tecnológica

| Componente | Tecnologia |
|---|---|
| Framework web | Django 5.2.7 |
| Banco de dados | PostgreSQL |
| Cache / Broker | Redis |
| Fila de tarefas | Celery + Celery Beat |
| WebSockets | Django Channels (ASGI) |
| Servidor HTTP | Gunicorn (workers síncronos) |
| Servidor ASGI | Daphne (WebSockets) |
| Frontend | Bootstrap 5 + tema dark customizado |
| Gráficos | Chart.js |
| Terminal web | xterm.js v5 (renderer WebGL/Canvas) |
| VNC no browser | noVNC |
| Automação de rede | Netmiko, Paramiko, Pexpect |
| Relatórios/PDFs | ReportLab, FPDF2, Pillow |
| Linguagem | Python 3.12 |
| Fuso horário | America/Sao_Paulo |

---

## Arquitetura

```
┌─────────────────────────────────────────────────────┐
│                      Nginx                          │
│  HTTP → HTTPS redirect                              │
│  HTTPS → Gunicorn (socket Unix)                     │
│  /ws/  → Daphne (WebSocket)                         │
│  /static/, /media/ → arquivos locais                │
└───────────────────┬─────────────────────────────────┘
                    │
        ┌───────────┴──────────┐
        │                      │
   Gunicorn               Daphne
   (requisições HTTP)     (WebSockets — terminal)
        │                      │
        └───────────┬──────────┘
                    │
              Django App
         ┌──────────┼──────────┐
         │          │          │
    PostgreSQL    Redis     Celery
    (banco)       (cache)   (backups, tarefas)

Acesso a equipamentos de clientes:
  CRM → ProxyServer (túnel SSH) → rede privada do cliente → equipamento
```

### Fluxo de acesso remoto

1. Equipamentos do cliente ficam acessíveis apenas dentro da rede privada do cliente
2. Um **ProxyServer** (host na rede do cliente) mantém um túnel SSH reverso para o CRM
3. O CRM encaminha conexões SSH/Telnet/HTTP pelo túnel, sem expor o equipamento diretamente

---

## Módulos do Sistema

### Clientes e Equipamentos

Cadastro completo de clientes com todas as informações necessárias para um provedor:

- Dados empresariais (CNPJ, endereço, contatos, e-mail, WhatsApp)
- **Acessos** — credenciais por equipamento: SSH, Telnet, HTTP, WinBox, API RouterOS
- **ProxyServer** — configuração do túnel SSH por cliente
- Histórico de comentários por acesso
- Upload de documentos
- Controle de chamados vinculados

### Terminal Web SSH/Telnet

Acesso SSH e Telnet diretamente no navegador, sem instalar nenhum cliente:

- Suporte a múltiplos fabricantes: MikroTik, Cisco, Huawei, Juniper, Datacom, ZTE, Parks
- Protocolo especial para Huawei via Pexpect (trata prompts não-padrão)
- **Protocolo binário WebSocket:** teclas enviadas como bytes puros — sem overhead JSON
- **Renderer WebGL/Canvas:** xterm.js com aceleração de GPU (fallback Canvas)
- Terminal inline no painel do cliente ou em página dedicada
- Sessões persistem durante a navegação
- Suporte a equipamentos legados com algoritmos SSH antigos (group14, KEX sha1)

### WinBox e Acesso Web

- **WinBox VNC:** abre a interface gráfica do MikroTik no navegador via Xvfb + noVNC
- **WebFig:** proxy HTTP/HTTPS para a interface web de qualquer equipamento
- Acesso por porta específica com tunelamento automático pelo ProxyServer

### Hotspot MikroTik

Portal cativo integrado ao MikroTik para provedores de hotspot:

- **Captive portal:** redireciona novos usuários para tela de cadastro (CPF/Telefone)
- Compatível com mini-browsers iOS e Android (usa `<meta http-equiv="refresh">` sem depender de JS)
- **Controle de banda por IP:** Queue Simple via DHCP Lease Script (ativo/inativo por MAC)
- Configuração de limite de banda (download/upload) por plano do hotspot
- Configuração do gateway, DHCP server e script de banda via interface web

**Fluxo do captive portal:**
```
Usuário conecta ao WiFi
  → MikroTik intercepta → /clientes/hotspot/login-html/<id>/
  → CRM gera redirect para /clientes/hotspot/portal/<id>/
  → Usuário preenche CPF/Telefone
  → CRM autentica via API MikroTik → libera acesso
```

### Backups Automatizados

- Templates de comandos configuráveis por fabricante (Cisco, Huawei, MikroTik, Datacom, Juniper...)
- Agendamento via Celery Beat (diário, semanal, por cliente)
- Histórico com download dos arquivos de configuração
- Hash SHA-256 para detectar alterações entre backups
- Relatório consolidado de status (`/home/ferramentas/relatorio-backups/`)

### IPAM Nativo

Gerenciamento completo de endereçamento IP sem ferramentas externas:

| Recurso | Descrição |
|---|---|
| VLANs | Número, nome, status (ativa/inativa/reservada) |
| Prefixos | Blocos IPv4/IPv6 com % de utilização |
| Sub-redes | Com gateway, VLAN e descrição |
| Endereços IP | Individuais com hostname, MAC, status, notas |
| Túneis VPN | IPSec, GRE, L2TP, MPLS, WireGuard, OpenVPN, VXLAN |

Recursos adicionais:
- Importação de IPs via arquivo
- Integração opcional com **PHP IPAM** e **NetBox** via túnel SSH
- Agrupamento automático de blocos /24 pai

### VPN — OpenVPN

> O WireGuard foi removido em 14/08/2026 — ver [docs/vpn_wireguard.md](docs/vpn_wireguard.md).

**Túnel OpenVPN (CRM é o servidor, MikroTik do cliente é o client):**
- Instância dedicada por túnel (porta, interface `tun-crm-N` e `/29` próprios)
- PKI própria da CRM e bootstrap de um comando no MikroTik
- Validação de conflito de redes entre clientes
- Detalhes em [docs/tunel_openvpn_mikrotik.md](docs/tunel_openvpn_mikrotik.md)

**OpenVPN Server no MikroTik do cliente (acesso remoto do NOC):**
- Configuração automática do servidor no MikroTik via Netmiko
- Geração de certificados CA, server e cliente
- Scripts de instalação para Windows, Linux e Android
- Gestão de usuários/peers com download de arquivos `.ovpn`

### RPKI e IRR

**Validação RPKI:**
- Cadastro de blocos IPv4/IPv6 com ASN do cliente
- Verificação do status: Valid, Invalid, NotFound
- Dashboard com lista de blocos inválidos e ações corretivas

**Atualização IRR (TC/NIC.br):**
- Configuração completa do AS por cliente
- Consulta WHOIS automática ao NIC.br/RADB para pré-preencher campos
- Auto-detecção do ASN a partir dos blocos RPKI cadastrados
- Geração automática de objetos RPSL:
  `person`, `mntner`, `route-set`, `route`, `route6`, `as-set`, `aut-num`
- Preview do e-mail antes do envio
- Envio via SMTP global para `auto-dbm@bgp.net.br`
- Suporte a `member-of` para participação em IX (PTT Metro, etc.)
- Integração com **Looking Glass** para verificar propagação do prefixo

### Scripts de Automação

Biblioteca de scripts reutilizáveis e parametrizáveis:

- Fabricantes suportados: ZTE, Huawei, Cisco, MikroTik, Datacom, Parks, Genérico
- Modos de execução: Operacional (show/get), Configuração, ZTE Auto-Provisionamento
- Parâmetros tipados: `text`, `number`, `select` com valores padrão e ajuda contextual
- Loop `#FOR i FROM {X} TO {Y} ... #ENDFOR` para repetição de blocos
- Execução em qualquer acesso SSH/Telnet do cliente
- Histórico completo de execuções com output e status
- Gerenciador exclusivo para `is_staff`: `/clientes/scripts/gerenciar/`
- Script de auto-provisionamento ZTE inserido automaticamente na migration

### Monitor de Tráfego

Dashboard de gráficos em tempo real via Zabbix API:

- **Sistema de abas independentes** — cada aba com seu próprio conjunto de painéis
- Criar, renomear (duplo-clique ou clique direito), fechar abas
- Adicionar gráficos escolhendo host Zabbix + item In + item Out
- Auto-detecção de itens "Bits received" e "Bits sent"
- Atualização automática a cada 15 segundos
- Períodos configuráveis: 1h, 3h, 6h, 12h, 24h
- Modo fullscreen por gráfico
- Estatísticas em tempo real: Download atual, Upload atual, Pico do período
- Configuração persistida no banco (compartilhada entre usuários do cliente)
- Configuração da Zabbix API por cliente (URL, token ou usuário/senha, auto-preenchimento)

### Financeiro

Módulo completo de gestão financeira para provedores:

| Recurso | Descrição |
|---|---|
| Faturas | Emissão, controle de status, aging de inadimplência |
| Consultorias | Registro e controle de serviços prestados |
| Aluguel IPv4 | Contratos de aluguel de blocos com assinatura digital (LOA) |
| Vendas | Registro de vendas de equipamentos |
| Despesas | Controle com parcelamento (1x–12x) e bulk actions |
| LOA | Carta de Autorização com assinatura digital via canvas (PDF gerado com Pillow) |
| WhatsApp | Envio de cobranças via Evolution API |
| PIX | Suporte a chaves PIX nos dados de cobrança |
| Dashboard | Tema dark com próximas a vencer, top clientes, aging |
| Recorrência | Despesas que se repetem automaticamente (mensal, trimestral, anual...) |
| Privacidade | Controle de visibilidade por item (privado/público por usuário/staff) |

### Atendimento e Chamados

- Ciclo completo: Aberto → Em Andamento → Aguardando → Resolvido → Fechado
- Prioridade: Normal, Alta, Urgente
- Departamentos e categorias configuráveis
- Comentários internos (apenas equipe) e externos (visíveis ao cliente)
- Sistema de tarefas e lembretes pessoais
- Relatório por assunto e categoria
- Notificações em tempo real (toast + badge) para chamados sem atendente

### Agent NOC (IA)

Assistente de inteligência artificial integrado ao CRM:

- Chat com IA para análise de problemas de rede
- Acesso a ferramentas: terminal SSH, consulta de equipamentos, logs
- Grupos de discussão com múltiplos participantes
- Integração com WhatsApp via Evolution API
- **API Key Claude individual por grupo WhatsApp** — cada cliente consome os próprios créditos Anthropic; o agent fica em silêncio em grupos sem chave configurada
- Controle de consumo de tokens com custo estimado em USD e BRL
- Histórico de conversas e relatórios de uso

### Wiki

Base de conhecimento interna:

- Artigos em Markdown com syntax highlighting
- Snippets de código categorizados por fabricante
- Busca por conteúdo, tag e fabricante
- Acesso rápido pelo menu lateral

### Ferramentas de Rede

**Looking Glass:**
- Consulta de prefixos IPv4/IPv6 em múltiplos coletores BGP públicos (RIPE NCC RIS)
- AS paths agrupados por frequência com identificação do país de cada coletor
- Badge `BR — IX.br/PTT` para o coletor de São Paulo
- Modal de topologia BGP visual para cada AS path
- Integrado com a aba IRR/RPKI dos clientes

**Geolocalização IP:**
- Consulta simultânea em 6 fontes (ip-api, ipinfo, ipwhois, DB-IP, RIPE Stat, LACNIC RDAP)
- Detecção automática de divergências entre fontes
- Envio de correções para MaxMind (Geo + ISP/Org), LACNIC e ARIN
- Confirmação automática de e-mails de validação MaxMind via IMAP
- Geração de Geofeed RFC 8805 em CSV
- Histórico de correções por prefixo

**Gerenciador de Arquivos / Firmware:**
- Armazenamento hierárquico de firmwares e arquivos de configuração
- Upload múltiplo com progresso em tempo real, drag & drop
- Download remoto via URL (o servidor faz o download sem passar pelo navegador)
- Sistema de compartilhamento com links temporários em 10 formatos:
  HTTP, HTTPS, FTP, SFTP, TFTP, Cisco TFTP, MikroTik, Huawei TFTP, wget, curl
- Limite de upload: 2 GB

---

## Integrações Externas

| Sistema | Protocolo | Finalidade |
|---|---|---|
| TC / NIC.br | SMTP + WHOIS :43 | Atualização de registros IRR |
| Zabbix | REST API | Monitor de tráfego |
| PHP IPAM / NetBox | HTTP via túnel SSH | IPAM externo (opcional) |
| MikroTik RouterOS | SSH + API | VPN, Hotspot, Backups |
| RIPE NCC RIS | REST API | Looking Glass — AS paths |
| ip-api / ipinfo / ipwhois / DB-IP / RIPE / LACNIC | REST API | Geolocalização IP |
| MaxMind | HTTPS form submit | Correção de geolocalização |
| Evolution API | REST API | WhatsApp — Agent NOC + cobrança |
| DrawIO | iframe embed | Editor de topologia |

---

## Instalação

> Documentação completa em [INSTALACAO.md](INSTALACAO.md).

### Requisitos

| Recurso | Mínimo |
|---|---|
| SO | Ubuntu 22.04 LTS ou 24.04 LTS |
| CPU | 2 vCPUs |
| RAM | 4 GB |
| Disco | 40 GB |
| Python | 3.12 |

### Resumo de instalação

```bash
# 1. Dependências do sistema
sudo apt update && sudo apt install -y \
    python3.12 python3.12-venv python3.12-dev \
    postgresql redis-server nginx \
    git curl build-essential libpq-dev \
    novnc openssh-client sshpass expect certbot python3-certbot-nginx

# 2. Banco de dados
sudo -u postgres psql -c "CREATE USER crm_user WITH PASSWORD 'SUA_SENHA';"
sudo -u postgres psql -c "CREATE DATABASE crm_db OWNER crm_user;"

# 3. Clonar e configurar
cd /opt && git clone https://github.com/CampeloSuporte/TomichHub.git crm
cd /opt/crm
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 4. Configurar crm/settings.py (SECRET_KEY, ALLOWED_HOSTS, DATABASES)

# 5. Migrações e arquivos estáticos
python manage.py migrate
python manage.py collectstatic --noinput
mkdir -p /opt/crm/static/novnc
cp -r /usr/share/novnc/* /opt/crm/static/novnc/

# 6. Serviços systemd (gunicorn, daphne, celery)
# Ver INSTALACAO.md — Seção 10

# 7. Nginx
# Ver INSTALACAO.md — Seção 11

# 8. SSL
sudo certbot --nginx -d seu.dominio.com.br

# 9. Superusuário
python manage.py createsuperuser
```

---

## Configuração Inicial

Após a instalação, acesse o sistema e configure na ordem:

### 1. SMTP e IMAP

**Sistema → Configurações**

Necessário para envio de e-mails (IRR, notificações) e confirmação automática MaxMind.

| Campo | Descrição |
|---|---|
| SMTP Host | Ex: `smtp.gmail.com` |
| SMTP Porta | 587 (TLS) ou 465 (SSL) |
| SMTP Usuário | E-mail de envio |
| SMTP Senha | Senha ou App Password |
| IMAP Host | Para confirmação automática MaxMind |
| IMAP Usuário | Mesmo e-mail do SMTP |

### 2. Usuários

**Sistema → Usuários**

- Marcar `is_staff` para usuários que precisam acessar Ferramentas, Scripts e área financeira
- Usuários sem `is_staff` veem apenas a área de clientes e chamados do próprio cliente

### 3. Zabbix (por cliente)

**Monitoramento → Zabbix API** (ícone na toolbar do monitor)

- URL base do Zabbix (sem `/api_jsonrpc.php`)
- Token de API (Zabbix 5.4+) ou usuário/senha
- Botão "Auto-preencher" detecta automaticamente se houver host "zabbix" nos acessos

### 4. Hotspot (por cliente com hotspot)

**Cliente → Hotspot**

- Gateway do hotspot
- DHCP Server name
- Limite de banda (se controle de banda ativado)
- URL do portal é gerada automaticamente: `/clientes/hotspot/login-html/<id>/`

---

## Comandos do Dia a Dia

```bash
# Atualizar código
cd /opt/crm
git pull origin main
source venv/bin/activate
pip install -r requirements.txt       # se houver novas dependências
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn daphne celery

# Reiniciar serviços (após mudança de código ou configuração)
sudo systemctl restart gunicorn daphne celery

# Ver logs em tempo real
sudo journalctl -u gunicorn -f
sudo journalctl -u daphne -f
sudo journalctl -u celery -f

# Status de todos os serviços
sudo systemctl status gunicorn daphne celery redis postgresql nginx

# Shell Django
cd /opt/crm && source venv/bin/activate
python manage.py shell

# Backup do banco
sudo -u postgres pg_dump crm_db > backup_$(date +%Y%m%d).sql

# Restaurar banco
sudo -u postgres psql crm_db < backup_20260101.sql
```

---

## Estrutura do Projeto

```
/opt/crm/
├── crm/                          # Configurações Django
│   ├── settings.py               # Configurações gerais
│   ├── urls.py                   # URLs raiz
│   ├── celery.py                 # Configuração Celery
│   ├── wsgi.py                   # WSGI (Gunicorn)
│   └── asgi.py                   # ASGI (Daphne/WebSockets)
│
├── clientes/                     # App principal
│   ├── models.py                 # Cliente, Acesso, ProxyServer, IPAM, VPN, Backup...
│   ├── views.py                  # Views gerais
│   ├── hotspot_views.py          # Captive portal e controle de banda
│   ├── ipam_views.py             # IPAM nativo
│   ├── firmware_views.py         # Gerenciador de arquivos/firmware
│   ├── script_views.py           # Scripts de automação
│   ├── openvpn_tunnel_manager.py # Túnel OpenVPN por cliente (PKI + instâncias)
│   ├── proxy_engine.py           # Proxy HTTP para equipamentos
│   ├── winbox_vnc.py             # WinBox via VNC
│   ├── consumers.py              # WebSocket (terminal SSH/Telnet)
│   └── tasks.py                  # Tarefas Celery (backups)
│
├── financeiro/                   # Módulo financeiro
│   ├── models.py                 # Fatura, Despesa, AluguelIPv4, LOA, Venda...
│   ├── views.py                  # Dashboard, CRUD, LOA, WhatsApp
│   ├── whatsapp.py               # Integração Evolution API
│   ├── tasks.py                  # Tarefas de cobrança
│   └── migrations/               # 18 migrações
│
├── monitoramento/                # Monitor de tráfego
│   ├── models.py                 # ZabbixConfig, MonitorDashConfig, Topologia
│   ├── views.py                  # API Zabbix, dash carregar/salvar
│   └── templates/monitoramento/  # tab_monitoramento.html (gráficos com abas)
│
├── home/                         # Dashboard geral e ferramentas
│   ├── views.py                  # Dashboard, LG, GeoIP, Firmware
│   ├── agent_engine.py           # Agent NOC com IA
│   └── templates/                # quadro_geral.html, ferramentas
│
├── atendimento/                  # Chamados e tarefas
├── wiki/                         # Base de conhecimento
├── usuario/                      # Autenticação
├── funcao_equipamento/           # Tipos de equipamento
├── modelo_equipamento/           # Modelos de equipamento
│
├── templates/                    # Templates globais
│   └── base.html                 # Layout principal com sidebar
├── static/                       # Arquivos estáticos
├── media/                        # Uploads (firmwares, PDFs, VPNs, backups)
├── docs/                         # Documentação técnica
├── venv/                         # Ambiente virtual Python
├── manage.py
├── requirements.txt
├── INSTALACAO.md                 # Guia de instalação detalhado
├── SISTEMA.md                    # Documentação técnica completa
├── CHANGELOG.md                  # Histórico de alterações
└── README.md                     # Este arquivo
```

---

## Documentação Técnica

| Arquivo | Conteúdo |
|---|---|
| [INSTALACAO.md](INSTALACAO.md) | Guia completo de instalação do zero |
| [SISTEMA.md](SISTEMA.md) | Documentação técnica detalhada (modelos, APIs, arquitetura) |
| [CHANGELOG.md](CHANGELOG.md) | Histórico de todas as alterações |
| [AGENT_NOC.md](AGENT_NOC.md) | Documentação do Agent NOC com IA |
| [docs/agent_noc.md](docs/agent_noc.md) | Agent NOC — API Key por grupo, monitor de tokens, fix Datacom |
| [docs/ATENDIMENTO.md](docs/ATENDIMENTO.md) | Módulo de atendimento — tickets, tarefas, Sala Virtual (WebRTC) |
| [docs/INDEX.md](docs/INDEX.md) | Índice de toda a documentação |
| [docs/monitoramento.md](docs/monitoramento.md) | Monitor de tráfego com sistema de abas |
| [docs/HOTSPOT_CAPTIVE_PORTAL.md](docs/HOTSPOT_CAPTIVE_PORTAL.md) | Captive portal e bugs corrigidos |
| [docs/HOTSPOT_CONTROLE_BANDA.md](docs/HOTSPOT_CONTROLE_BANDA.md) | Controle de banda via DHCP Queue Simple |
| [docs/FINANCEIRO.md](docs/FINANCEIRO.md) | Módulo financeiro completo |
| [docs/CONTRATOS_ASSINATURA_DIGITAL.md](docs/CONTRATOS_ASSINATURA_DIGITAL.md) | LOA com assinatura digital |
| [docs/DESPESAS_AVANCADO.md](docs/DESPESAS_AVANCADO.md) | Parcelamento e bulk actions de despesas |
| [docs/DESPESA_RECORRENCIA.md](docs/DESPESA_RECORRENCIA.md) | Recorrência automática de despesas |
| [docs/PRIVACIDADE_FINANCEIRA.md](docs/PRIVACIDADE_FINANCEIRA.md) | Controle de privacidade por item |
| [docs/winbox_vnc.md](docs/winbox_vnc.md) | WinBox Web via VNC no navegador |
| [docs/terminal_ssh.md](docs/terminal_ssh.md) | Terminal SSH/Telnet web |
| [docs/backup_automatico.md](docs/backup_automatico.md) | Sistema de backup automático |
| [docs/ipam.md](docs/ipam.md) | IPAM nativo |
| [docs/topologia.md](docs/topologia.md) | Topologia de rede interativa |

---

## Troubleshooting

### 502 Bad Gateway

```bash
sudo systemctl status gunicorn
sudo systemctl restart gunicorn daphne
```

> Nunca use `pkill -f gunicorn` — o socket fica órfão. Use sempre `systemctl restart`.

### WinBox/VNC não carrega

```bash
# Verificar noVNC
ls /opt/crm/static/novnc/
# Se vazio:
mkdir -p /opt/crm/static/novnc
cp -r /usr/share/novnc/* /opt/crm/static/novnc/
```

### Terminal não conecta

```bash
sudo systemctl status daphne
sudo journalctl -u daphne -n 50
# Verificar se o ProxyServer do cliente está ativo
```

### Celery não executa tarefas

```bash
sudo systemctl status celery
sudo journalctl -u celery -n 50 -f
# Verificar se o Redis está rodando
redis-cli ping   # deve retornar PONG
```

### Hotspot não redireciona para o portal

Verificar:
1. O campo "Login Page" no MikroTik aponta para `http://<IP-CRM>/clientes/hotspot/login-html/<ID>/`
2. O nginx não está redirecionando esse path para HTTPS (veja o bloco `location ~ ^/clientes/hotspot/` no nginx)
3. O Walled Garden do MikroTik permite acesso pré-autenticação ao IP do CRM

### Monitor de tráfego sem dados

1. Verificar a configuração da Zabbix API (botão "Zabbix API" na toolbar do monitor)
2. Usar o botão "Testar" para validar a conexão
3. Verificar se os itens selecionados têm histórico no Zabbix (período 1h)

### Migrations falhando

```bash
cd /opt/crm && source venv/bin/activate
python manage.py showmigrations | grep "\[ \]"   # migrações pendentes
python manage.py migrate --run-syncdb
```

---

## Licença

Software proprietário — uso restrito a instâncias autorizadas.

---

**Desenvolvido por:** CampeloSuporte  
**Última atualização:** 2026-06-13
