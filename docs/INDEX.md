# 📚 Índice de Documentação — CRM NOC

## 🔥 Implementações Recentes

### Sessão 5 — 20/07/2026: Auditoria de Acessos (gravação de sessão) + Correções (Hotspot, Backup)

**O que foi implementado?**
- ✅ Auditoria de Acessos: toda sessão SSH/Telnet/WinBox/WebFig passa a ser registrada — usuário
  do CRM, IP de origem, duração; comandos digitados e transcript completo da tela para SSH/Telnet;
  gravação `.mp4` via `ffmpeg` para sessões gráficas WinBox/WebFig via VNC
- ✅ Novo modal "Auditoria de Acessos" na aba de Acessos (lista sessões, comandos e gravações)
- ✅ WebSocket dos consumers de terminal agora exige usuário autenticado (antes dependia só da view HTTP)
- ✅ Corrigido bug de gravação de vídeo com 0 bytes (`ffmpeg` recebendo `SIGTERM` duplicado)
- ✅ Hotspot: `login.html` gravado em `<dir>/login.html` **e** `flash/<dir>/login.html` (RouterOS
  resolve o `html-directory` do profile de forma inconsistente entre roteadores)
- ✅ Hotspot: destino pós-login por sistema operacional evita tela de status "Hi, guest!" no MikroTik
- ✅ Backup automático: detecção de fabricante mais robusta (combina `fabricante`+`nome`+`tipo`) e
  fix de timeout de KEX SSH (ZTE) também na conexão de backup, não só no terminal interativo

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md)** | Modelos, endpoints, frontend, gravação de tela, transcript/comandos |
| **[terminal_ssh.md](terminal_ssh.md)** | Autenticação obrigatória no WS, constante `_ZTE_PREFERRED_KEX` compartilhada |
| **[winbox_vnc.md](winbox_vnc.md)** | Gravação de tela via `ffmpeg`, fix do `stop()` idempotente |
| **[HOTSPOT_CAPTIVE_PORTAL.md](HOTSPOT_CAPTIVE_PORTAL.md)** | `html-directory` inconsistente entre profiles, destino pós-login por SO |
| **[backup_automatico.md](backup_automatico.md)** | Detecção de fabricante e KEX em `realizar_backup` |
| **[frontend_acessos.md](frontend_acessos.md)** | Botão e modal de auditoria |

---

### Sessão 4 — 16/06/2026: API Key Claude por Grupo + Correções (Agent NOC, Sala Virtual, Hotspot, Financeiro)

**O que foi implementado?**
- ✅ API Key Claude individual por grupo WhatsApp — cada cliente consome seus próprios créditos; agent fica em silêncio se não configurada
- ✅ Sinal óptico Datacom (DmOS): corrigido comando (`show interface transceivers`)
- ✅ Sala Virtual (WebRTC): corrigida queda de áudio após alguns minutos (faltava `onnegotiationneeded`) e candidatos ICE perdidos com várias pessoas na sala
- ✅ Hotspot: entrega do `login.html` via SFTP (substitui `/tool fetch` HTTP, que falhava por DNS/timeout)
- ✅ Financeiro: alerta de cobrança WhatsApp (causa: flag `wa_ativo` desativada) e vínculo fatura↔venda de equipamento nunca funcionava (campo M2M inexistente)
- ✅ Config do Agent NOC: corrigido erro 500 ao salvar API Key (bug de localização pt-BR em campo numérico)

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[agent_noc.md](agent_noc.md)** | API Key por grupo, fix Datacom, fix erro 500 ao salvar config |
| **[ATENDIMENTO.md](ATENDIMENTO.md)** | Sala Virtual — Perfect Negotiation, buffer de candidatos ICE |
| **[HOTSPOT_CAPTIVE_PORTAL.md](HOTSPOT_CAPTIVE_PORTAL.md)** | Entrega de `login.html` via SFTP |
| **[FINANCEIRO.md](FINANCEIRO.md)** | Diagnóstico cobrança WhatsApp, fix vínculo venda de equipamento |

---

### Sessão 3 — 13/06/2026: Monitor de Tráfego com Abas + Hotspot Captive Portal

**O que foi implementado?**
- ✅ Sistema de abas no Monitor de Tráfego (criar, renomear, fechar, trocar)
- ✅ Menu de contexto (clique direito) nas abas com opções de renomear e fechar
- ✅ Renomeação inline por duplo-clique no nome da aba
- ✅ Backend atualizado para formato `{ "tabs": [...] }` com compatibilidade retroativa
- ✅ Hotspot captive portal: 4 bugs corrigidos (JS bloqueado, HTML injection, mixed content, link vazio)

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[monitoramento.md](monitoramento.md)** | Sistema de abas, API, variáveis de estado, comportamento JS |
| **[HOTSPOT_CAPTIVE_PORTAL.md](HOTSPOT_CAPTIVE_PORTAL.md)** | Bugs corrigidos, fluxo de autenticação, compatibilidade mini-browsers |

---

### Sessão 2 — 10/06/2026: Despesas Avançado + Hotspot Banda + Contratos Digitais

**O que foi implementado?**
- ✅ Parcelamento de despesas (1x–12x) substituindo campo Recorrência
- ✅ Página dedicada `/financeiro/despesas/` com bulk actions e filtros
- ✅ Correção de bugs: `uiConfirm` indefinido, método POST/DELETE
- ✅ Contratos de aluguel IPv4 com assinatura digital (canvas + PDF + PIL)
- ✅ Hotspot: controle de banda por IP via DHCP Queue Simple MikroTik

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[DESPESAS_AVANCADO.md](DESPESAS_AVANCADO.md)** | Parcelamento, página dedicada, bulk actions, bugs |
| **[CONTRATOS_ASSINATURA_DIGITAL.md](CONTRATOS_ASSINATURA_DIGITAL.md)** | Contratos de aluguel com assinatura digital |
| **[HOTSPOT_CONTROLE_BANDA.md](HOTSPOT_CONTROLE_BANDA.md)** | Queue Simple por IP via DHCP Lease Script |
| **[IMPLEMENTACOES_JUNHO_2026.md](IMPLEMENTACOES_JUNHO_2026.md)** | Checklist completo (sessões 1 e 2) |

---

### Sessão 1 — 01/06/2026: Recorrências e Privacidade

**O que foi implementado?**
- ✅ Despesas com auto-recorrência (mensal, trimestral, anual, etc)
- ✅ Privacidade para 5 modelos financeiros (Despesa, Fatura, Consultoria, Aluguel, Venda)
- ✅ Layout melhorado (sem sobreposição de texto)

**Onde está documentado?**

| Documentação | Tema | Público-alvo |
|--------------|------|--------------|
| **[FINANCEIRO.md](FINANCEIRO.md)** | Visão geral completa do módulo | Todos |
| **[DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md)** | Como funciona o sistema de recorrências | Devs que trabalham com recorrências |
| **[PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md)** | Como funciona privacidade | Devs que trabalham com privacidade |
| **[IMPLEMENTACOES_JUNHO_2026.md](IMPLEMENTACOES_JUNHO_2026.md)** | Checklist, arquivos modificados, commits | PMs, Arquitetos |

**Comece aqui:** 👉 [FINANCEIRO.md](FINANCEIRO.md)

---

## 📖 Documentação Existente

### Modelos Financeiros

- **[FINANCEIRO.md](FINANCEIRO.md)** — Módulo completo
  - Recorrência de despesas
  - Privacidade (5 modelos)
  - API endpoints
  - Interface de usuário
  - Instalação

- **[DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md)** — Recorrências em detalhe
  - Arquitetura e fluxos
  - Cálculo de datas
  - Exemplos reais
  - Testes
  - Tratamento de erros

- **[PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md)** — Privacidade em detalhe
  - Controle de acesso
  - Filtros de listagem
  - Casos de uso
  - Testes de segurança
  - Permissões

### Outros Módulos

- **[AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md)** — Auditoria de Acessos (sessões SSH/WinBox)
  - Modelos `AcessoSessao`/`AcessoComando`
  - Transcript e comandos digitados (SSH/Telnet)
  - Gravação de tela via `ffmpeg` (WinBox/WebFig)
  - Endpoints e modal de auditoria

- **[HOTSPOT_CONTROLE_BANDA.md](HOTSPOT_CONTROLE_BANDA.md)** — Hotspot: controle de banda por IP
  - Queue Simple ativado via DHCP Lease Script
  - Script RouterOS com escaping correto para SSH
  - Preview em tempo real na interface
  - Como verificar no MikroTik

- **[monitoramento.md](monitoramento.md)** — Dashboard de monitoramento com abas
  - Sistema de abas independentes por cliente
  - Persistência de configuração no banco
  - Menu de contexto, renomeação inline
  - API e variáveis de estado do módulo GRAPH

- **[HOTSPOT_CAPTIVE_PORTAL.md](HOTSPOT_CAPTIVE_PORTAL.md)** — Captive portal MikroTik
  - Fluxo de autenticação completo
  - 4 bugs corrigidos (JS, HTML injection, mixed content, link vazio)
  - Compatibilidade com mini-browsers iOS/Android
  - Configuração nginx e walled garden

- **[backup_automatico.md](backup_automatico.md)** — Sistema de backup automático
  - Habilitação automática
  - Detecção de modelo
  - Templates

- **[envio_credenciais_email.md](envio_credenciais_email.md)** — Envio periódico de credenciais
  - Task Celery
  - Geração de PDF
  - Agendamento

- **[vpn_wireguard.md](vpn_wireguard.md)** — VPN WireGuard por cliente
  - Arquitetura wg0 legado vs. interfaces isoladas (wg5+)
  - Incidente Conecta ISP (rotas compartilhadas apagadas) e correção
  - Limitação de faixas amplas idênticas entre clientes
  - Diagnóstico rápido de roteamento

- **[winbox_vnc.md](winbox_vnc.md)** — WinBox Web via VNC no browser
  - Arquitetura Xvfb + Openbox + x11vnc + noVNC
  - Fluxo de inicialização
  - Problemas conhecidos (ncache, resizeSession, width/height)
  - Como testar manualmente

- **[frontend_acessos.md](frontend_acessos.md)** — Gerenciamento de acessos
  - Exportação de PDF
  - Visibilidade de senhas
  - Gerador aleatório

---

## 🚀 Guias Rápidos

### Para Desenvolvedores

**Quero entender o módulo financeiro**
1. Leia: [FINANCEIRO.md](FINANCEIRO.md) (5 min)
2. Se trabalhar com recorrências: [DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md)
3. Se trabalhar com privacidade: [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md)

**Quero adicionar um novo tipo de item financeiro (como Despesa, Fatura)**
1. Leia: [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md) (Seção "Arquitetura")
2. Copie o padrão de um modelo existente
3. Adicione campo `privada = models.BooleanField(default=False)`
4. Crie migration
5. Implemente API com filtro apropriado
6. Adicione checkbox em template
7. Adicione ao admin.py

**Quero entender como funciona a privacidade**
👉 [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md)

**Quero entender como funciona a recorrência**
👉 [DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md)

### Para Product Managers

**Quero saber o que foi implementado**
👉 [IMPLEMENTACOES_JUNHO_2026.md](IMPLEMENTACOES_JUNHO_2026.md)

**Quero um resumo técnico**
👉 [FINANCEIRO.md](FINANCEIRO.md) (Seção "Estrutura de Modelos")

**Quero saber próximos passos**
👉 [IMPLEMENTACOES_JUNHO_2026.md](IMPLEMENTACOES_JUNHO_2026.md) (Seção "Próximos Passos")

### Para Arquitetos

**Quero entender a arquitetura**
1. [FINANCEIRO.md](FINANCEIRO.md) — Visão geral
2. [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md) — Padrão de controle de acesso
3. [DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md) — Padrão de auto-geração

**Quero o checklist completo**
👉 [IMPLEMENTACOES_JUNHO_2026.md](IMPLEMENTACOES_JUNHO_2026.md)

---

## 🗂️ Organização de Arquivos

```
docs/
├─ INDEX.md (este arquivo)
├─ FINANCEIRO.md ......................... 📌 Módulo financeiro (completo)
├─ DESPESA_RECORRENCIA.md ................ 📌 Recorrências (detalhado)
├─ DESPESAS_AVANCADO.md .................. 📌 Parcelamento, bulk actions, bugs
├─ PRIVACIDADE_FINANCEIRA.md ............. 📌 Privacidade (detalhado)
├─ CONTRATOS_ASSINATURA_DIGITAL.md ....... 📌 Contratos com assinatura digital
├─ HOTSPOT_CONTROLE_BANDA.md ............. 📌 Hotspot: DHCP Queue Simple por IP
├─ HOTSPOT_CAPTIVE_PORTAL.md ............. 📌 Hotspot: captive portal e bugs corrigidos
├─ IMPLEMENTACOES_JUNHO_2026.md .......... 📌 Checklist e resumo executivo
├─ AUDITORIA_ACESSOS.md .................. 📌 Auditoria de sessões (comandos, transcript, vídeo)
├─ monitoramento.md ...................... 📌 Monitor de tráfego com sistema de abas
├─ backup_automatico.md
├─ envio_credenciais_email.md
├─ winbox_vnc.md
├─ terminal_ssh.md
└─ frontend_acessos.md
```

---

## ⚡ Resumo das Features

### 1️⃣ Recorrência de Despesas (Junho 2026)

**O que é?**
- Despesas que se repetem automaticamente
- Suporta: Mensal, Bimestral, Trimestral, Semestral, Anual
- Auto-gera próxima ocorrência ao marcar como pago

**Como usar?**
```
Criar Despesa → Selecionar "Recorrência: Mensal" → Total: 12 meses → Salvar
```

**Documentação:** [DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md)

### 2️⃣ Privacidade (Junho 2026)

**O que é?**
- Despesas: privadas (criador só vê) ou públicas (todos veem)
- Faturas/Consultorias/Aluguéis/Vendas: privadas (staff só) ou públicas (todos)

**Como usar?**
```
Criar item → Marcar checkbox 🔒 Privada → Salvar
```

**Documentação:** [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md)

### 3️⃣ Layout Melhorado (Junho 2026)

**O que mudou?**
- Coluna "Vencimento" expandida para 180px
- Eliminou sobreposição com "Nome"

**Documentação:** [FINANCEIRO.md](FINANCEIRO.md) (Seção "Melhorias de Layout")

---

## 📊 Estatísticas de Implementação

| Métrica | Valor |
|---------|-------|
| Modelos afetados | 5 (Despesa, Fatura, Consultoria, AluguelIPv4, VendaEquipamento) |
| Campos adicionados | 10 (5 em Despesa, 1 em cada outro) |
| Migrações criadas | 6 (0005-0010) |
| APIs modificadas | 10 |
| Linhas de documentação | 3.100+ |
| Arquivos .md criados | 4 |
| Commits registrados | 6 |

---

## 🔗 Links Úteis

### Código-fonte
- Models: `/opt/crm/financeiro/models.py` (linhas 513+)
- Views: `/opt/crm/financeiro/views.py`
- Templates: `/opt/crm/financeiro/templates/financeiro/dashboard.html`
- Admin: `/opt/crm/financeiro/admin.py`
- Migrations: `/opt/crm/financeiro/migrations/0005-0010`

### Acesso
- **Admin:** http://localhost:3000/admin/
- **Dashboard:** http://localhost:3000/
- **API:** POST/GET `/financeiro/api/despesa/*` etc

### Referências
- Django Models: https://docs.djangoproject.com/en/stable/ref/models/fields/
- Django QuerySet: https://docs.djangoproject.com/en/stable/ref/models/querysets/
- Django Permissions: https://docs.djangoproject.com/en/stable/topics/auth/

---

## 🆘 Precisa de Ajuda?

### "Como adiciono privacidade a um novo modelo?"
→ [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md) — Seção "Modelos Implementados"

### "Como adiciono recorrência a um novo modelo?"
→ [DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md) — Seção "Migrações"

### "Qual API usa privacidade?"
→ [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md) — Seção "API Endpoints"

### "Como o filtro de privacidade funciona?"
→ [PRIVACIDADE_FINANCEIRA.md](PRIVACIDADE_FINANCEIRA.md) — Seção "Controle de Acesso"

### "Como criar uma despesa recorrente?"
→ [DESPESA_RECORRENCIA.md](DESPESA_RECORRENCIA.md) — Seção "Fluxo de Funcionamento"

### "Qual é o checklist de implementação?"
→ [IMPLEMENTACOES_JUNHO_2026.md](IMPLEMENTACOES_JUNHO_2026.md) — Seção "Checklist de Implementação"

### "Como configurar uma API Key Claude individual por cliente/grupo?"
→ [agent_noc.md](agent_noc.md) — Seção "API Key Claude por Grupo WhatsApp"

### "Por que o agent não responde em um grupo WhatsApp?"
→ [agent_noc.md](agent_noc.md) — Seção "API Key Claude por Grupo WhatsApp" (sem chave configurada = silêncio)

### "Como pegar o sinal óptico de um equipamento Datacom?"
→ [agent_noc.md](agent_noc.md) — Seção "Sinal Óptico Datacom (DmOS)"

### "Por que o áudio da sala virtual cai depois de um tempo?"
→ [ATENDIMENTO.md](ATENDIMENTO.md) — Seção "Sala Virtual de Atendentes — WebRTC"

### "Por que o alerta de cobrança WhatsApp não está sendo enviado?"
→ [FINANCEIRO.md](FINANCEIRO.md) — Seção "Cobrança via WhatsApp — Diagnóstico e Correção"

---

## 📅 Histórico

| Data | O quê | Documentação |
|------|-------|--------------|
| 20/07/2026 | Auditoria de Acessos (comandos, transcript, gravação de vídeo); auth obrigatória no WS; fix vídeo 0 bytes; hotspot `flash/<dir>` e destino pós-login por SO; backup (fabricante + KEX) | AUDITORIA_ACESSOS.md, terminal_ssh.md, winbox_vnc.md, HOTSPOT_CAPTIVE_PORTAL.md, backup_automatico.md, frontend_acessos.md |
| 16/06/2026 | API Key Claude por grupo; fix Datacom; Sala Virtual WebRTC; Hotspot SFTP; Financeiro (cobrança + vínculo venda) | agent_noc.md, ATENDIMENTO.md, HOTSPOT_CAPTIVE_PORTAL.md, FINANCEIRO.md |
| 13/06/2026 | Monitor de tráfego com abas; hotspot captive portal (4 bugs) | monitoramento.md, HOTSPOT_CAPTIVE_PORTAL.md |
| 10/06/2026 | Parcelamento, bulk actions, contratos digitais, hotspot banda | DESPESAS_AVANCADO.md, CONTRATOS_ASSINATURA_DIGITAL.md, HOTSPOT_CONTROLE_BANDA.md |
| 01/06/2026 | Recorrência + Privacidade (5 modelos) | FINANCEIRO.md, DESPESA_RECORRENCIA.md, PRIVACIDADE_FINANCEIRA.md, IMPLEMENTACOES_JUNHO_2026.md |
| 27/05/2026 | Dashboard persistência, Backup automático | monitoramento.md, backup_automatico.md |
| 26/05/2026 | Terminal SSH, IPAM, Agent NOC | (docs anteriores) |

---

## ✅ Status

- **Módulo Financeiro:** 🟢 Pronto para Produção
- **Documentação:** 🟢 Completa
- **Testes:** 🟢 Aprovados
- **Migrations:** 🟢 Aplicadas

---

**Última atualização:** 20/07/2026  
**Versão:** 1.4  
**Mantidor:** CampeloSuporte
- [Notificações de Chamados em Aberto](notificacoes_chamados.md) — Toast e badge em tempo real para chamados sem atendente (dentro e fora do atendimento)
