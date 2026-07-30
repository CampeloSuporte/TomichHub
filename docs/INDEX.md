# 📚 Índice de Documentação — CRM NOC

## 🔥 Implementações Recentes

### Sessão 19 — 30/07/2026: Fix — Autofill do Chrome Preenchendo Campos de Pesquisa

**O que foi diagnosticado e corrigido?**
- ✅ Ao entrar na tela do cliente, o Chrome sugeria/preenchia automaticamente logins salvos
  (ex: senha do relatado "greentelecom") no campo de pesquisa da aba Acessos e no campo de
  pesquisa de Backups — nenhum dos dois tinha `autocomplete="off"`/`name`, e o Chrome os
  reconhecia como campo de login.
- ✅ Causa raiz do login salvo indevidamente: o formulário de cadastro/edição de Túnel Proxy
  tem um par "Usuário" + "Senha" lado a lado sem nenhum atributo de autocomplete — padrão
  clássico que faz o Chrome oferecer "salvar senha?" e gravar como login do site. Mesmo problema
  nos campos "Usuário" dos modais de Acesso/VPN.
- ℹ️ O Turnstile "marcando sozinho" sem clique no login **não é bug**: é o modo "Managed" do
  Cloudflare Turnstile passando silenciosamente para sessões que não parecem suspeitas. A
  validação de segurança real continua no backend (`usuario/views.py` — `_verificar_turnstile`,
  que confere o token via `siteverify` antes de liberar o login).

**Importante:** a correção evita que o Chrome volte a salvar esses campos como login, mas **não
apaga** logins já salvos indevidamente — isso precisa ser removido manualmente em
`chrome://settings/passwords` em cada PC afetado.

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[frontend_acessos.md](frontend_acessos.md)** | Seção "Autofill do Chrome nos Campos de Pesquisa" |

---

### Sessão 18 — 27/07/2026: Correções — KEX SSH do Backup, WhatsApp Nono Dígito, Timeout RPKI

**O que foi diagnosticado e corrigido?**
- ✅ Backup falhando com `Incompatible ssh peer (no acceptable kex algorithm)` num Huawei NE8000
  M8 (roteador de borda/BGP) — o disable de KEX pesado, pensado só pra OLTs ZTE de CPU fraca,
  estava sendo aplicado pra todos os fabricantes e zerava o KEX em comum com esse equipamento.
  Restrito à flag `is_zte`.
- ✅ Cobrança via WhatsApp rejeitada pela Evolution API (`exists: false`) — número de celular BR
  cadastrado com o 9º dígito só existe no WhatsApp sem ele (conta antiga/portada). Envio agora
  tenta a variante alternada automaticamente; removida uma trava que rejeitava esse formato de
  número antes mesmo de tentar enviar.
- ✅ Validação RPKI marcando bloco como erro num timeout pontual do RIPE Stat, sem tentar o
  fallback Cloudflare RPKI que já existe no código — corrigido pra cair no fallback como qualquer
  outra falha da fonte primária.

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[backup_automatico.md](backup_automatico.md)** | Seção "KEX para equipamentos de CPU limitada" |
| **[FINANCEIRO.md](FINANCEIRO.md)** | Seção "Cobrança via WhatsApp — Número BR com/sem o Nono Dígito" |
| **[RPKI_IRR.md](RPKI_IRR.md)** | Novo — validação RPKI/IRR completa + fix do timeout |

---

### Sessão 17 — 26/07/2026: Geolocalização de IP — Múltiplos Blocos/Localizações no Geofeed

**O que foi implementado?**
- ✅ Novo model `GeofeedBloco` como fonte única do `geofeed.csv` público (RFC 8805) — antes o
  arquivo dependia de deduplicar por prefixo dentro do histórico `CorrecaoGeoIP`, sem suporte a
  editar/remover um bloco já publicado
- ✅ Card "Blocos do Geofeed" na tela de Geolocalização de IP: tabela editável com "+ Adicionar
  bloco" — cadastra quantos prefixos e localizações forem necessários, cada linha com
  salvar/remover independentes (antes só dava pra publicar 1 bloco por vez, repetindo o fluxo de
  busca + modal de correção manualmente para cada prefixo)
- ✅ Endpoints `geo_blocos_listar`/`geo_blocos_salvar` (aceita lote de blocos em 1 requisição)/
  `geo_blocos_excluir`
- ✅ Coluna Postal-Code do RFC 8805 (antes sempre vazia) passa a ser preenchida quando informada
- ✅ Migração de dados preserva os prefixos já publicados a partir do histórico existente

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[GEOLOCALIZACAO_IP.md](GEOLOCALIZACAO_IP.md)** | Arquitetura completa, endpoints, migrations, como usar, testes |

---

### Sessão 16 — 24/07/2026: Hotspot — Integração Disparo: Opa Suite Canal/Template Trocados

**O que foi diagnosticado?**
- ✅ Teste de disparo do Opa Suite retornava `HTTP 404: "Communication channel not found"` mesmo
  com domínio/token corretos
- ✅ Causa: os campos **Canal** e **Template** da configuração estavam com valores trocados/errados
  — um `_id` de canal WhatsApp válido estava salvo no campo Template, e o campo Canal tinha um
  valor (`uej2uHCH`) que não batia com nenhum registro real da conta
- ✅ Diagnóstico feito consultando a própria API do Opa Suite (`GET /api/v1/canal-comunicacao/` e
  `GET /api/v1/template`, com o Bearer token já salvo no CRM) para listar os IDs reais da conta e
  comparar com o configurado — sem precisar acessar o painel do Opa Suite manualmente
- ✅ `canal_id` corrigido direto no banco (confirmado via API); operador confirmou e ajustou o
  Template correto pela própria tela do CRM — teste funcionou
- ✅ Nenhuma mudança de código — erro de preenchimento na configuração, não bug do CRM

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md)** | Seção "Bug 5 — Opa Suite: Canal e Template trocados", dica em "Configurar o Opa Suite" |

---

### Sessão 15 — 23/07/2026: Hotspot — Integração Disparo: Painel de Ajuda Visual (Chatmix)

**O que foi implementado?**
- ✅ Botão "Onde acho Key/Token/ID do Template?" no card Chatmix — abre um mini-guia visual
  (mockups HTML/CSS, não screenshots reais) recriando as telas do Chatmix: "Chaves para acesso"
  (destacando o campo Canais com seleção múltipla e o checkbox "Ver opções avançadas") e
  "Mensagens → Mensagens Templates" (mostrando o ID do template no final da URL)
- ✅ Sugestão de corpo de mensagem pronta (boas-vindas + oferta de desconto) com botão "Copiar"
  (`navigator.clipboard`), usando `{{1}}` (sintaxe Meta/WhatsApp) para a variável de nome
- ✅ Diagnosticados (não são bugs do CRM): `"success":true, status:"queue"` mas mensagem não
  chega = template pendente de aprovação da Meta; `"Template nao encontrado"` mesmo o template
  existindo = Key/Token sem o canal certo marcado (campo **Canais** multi-seleção na Chatmix)
- ✅ Detalhe técnico: `{{1}}` literal quebraria o parser de templates do Django (`{{ }}` é a
  sintaxe de variável dele) — resolvido com `{% templatetag openvariable/closevariable %}`

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md)** | Seções "Painel de ajuda embutido (card Chatmix)", "Bug 4 — Diagnósticos de teste que não são bug do CRM", "Exemplo de corpo de template" |

---

### Sessão 14 — 23/07/2026: Módulos por Usuário — de Cliente (empresa) pra Login individual

**O que mudou?**
- ✅ Descoberto que a seleção de ferramentas deveria ser por **login individual** (`User`),
  não por empresa (`Cliente`) — dois usuários da mesma empresa podem precisar ver coisas
  diferentes (ex: financeiro não vê VPN, técnico de rede vê)
- ✅ Removido `ClienteModulo` (model, migração `0086_delete_clientemodulo`, checkboxes em
  `cadastrar_cliente.html`)
- ✅ Novo model `UsuarioModulo` (`usuario/models.py`), primeira migração do app `usuario`
- ✅ Checkboxes "Ferramentas habilitadas" movidos para **Sistema → Usuário**
  (`cadastrar_usuario.html`), visíveis só quando o tipo selecionado é "Cliente"
  (administradores sempre veem tudo)
- ✅ `modulo_habilitado_required` simplificado: checa `request.user` diretamente, sem
  precisar resolver qual `Cliente` está por trás da URL
- ✅ `listar_clientes` calcula `modulos_habilitados` a partir do usuário logado, não do
  cliente sendo visualizado

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[MODULOS_CLIENTE.md](MODULOS_CLIENTE.md)** | Reescrito — arquitetura atual (por usuário), histórico das 2 versões anteriores descartadas |

---

### Sessão 13 — 23/07/2026: Módulos do Cliente — Seleção movida para o Cadastro/Edição

**O que mudou?**
- ✅ Removidos os switches de toggle inline nas abas de `listar.html` (feedback: melhor
  selecionar tudo no mesmo lugar onde o cliente é cadastrado)
- ✅ Nova seção "Ferramentas habilitadas" nos modais de Cadastro e Edição de cliente
  (`cadastrar_cliente.html`), um checkbox por módulo
- ✅ `cadastrar_cliente`/`editar_cliente` (views) gravam `ClienteModulo` a partir dos
  checkboxes marcados, com marcador oculto `modulos_form_present` pra nunca desabilitar
  tudo por um form incompleto
- ✅ Removido o endpoint AJAX `toggle_modulo_cliente` (não usado mais)
- ✅ Continua igual: aba some da tela do cliente quando o módulo está desabilitado, e o
  bloqueio de backend (`modulo_habilitado_required`) nos 89 endpoints

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[MODULOS_CLIENTE.md](MODULOS_CLIENTE.md)** | Seção "Interface — Seleção no Cadastro/Edição do Cliente" |

---

### Sessão 12 — 23/07/2026: Hotspot — Integração Disparo: Opa Suite

**O que foi implementado?**
- ✅ Segunda empresa de integração funcional: **Opa Suite** (endpoint
  `POST {dominio}/api/v1/template/send`, auth `Bearer <token>`), lendo a coleção Postman pública
  em https://api.opasuite.com.br/
- ✅ Novo cliente `OpaSuiteClient` (`clientes/services.py`) — mesmo padrão do `ChatmixClient`,
  mas multi-tenant por domínio próprio (`api_dominio`) e exigindo `canal_id` (ID do canal de
  comunicação que faz o envio)
- ✅ Modelo `ClienteIntegracaoDisparo` ganhou `api_dominio`/`canal_id`; `template_id` ampliado de
  20 para 64 caracteres (Opa Suite usa ObjectId Mongo, 24 caracteres, maior que o ID numérico
  curto do Chatmix)
- ✅ `enviar_disparo_hotspot_lead` (Celery) generalizada para disparar em **todos** os providers
  habilitados do cliente, não só Chatmix — um cliente pode ter Chatmix e Opa Suite habilitados ao
  mesmo tempo
- ✅ Card "Opa Suite" na UI virou funcional (era um placeholder "Em breve"): campos Domínio/Token/
  Canal/Template + a mesma lista dinâmica de variáveis já usada no Chatmix
- ✅ Correção de nomenclatura: "Opa Suit" → "Opa Suite" (nome correto da empresa, conforme a
  própria documentação deles)

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md)** | Seções "Serviços — ChatmixClient e OpaSuiteClient", "Por que os providers têm campos diferentes", "Configurar o Opa Suite" |

---

### Sessão 11 — 23/07/2026: Hotspot — Integração Disparo (WhatsApp HSM via Chatmix)

**O que foi implementado?**
- ✅ Nova aba "Integração Disparo" ao lado de "Leads" no painel do Hotspot — dispara automaticamente
  uma mensagem WhatsApp (HSM) quando um novo lead se cadastra no portal cativo
- ✅ Novo model `ClienteIntegracaoDisparo` (por cliente × empresa de integração): Chatmix funcional,
  Opa Suit listado como "Em breve"
- ✅ `ChatmixClient` (`clientes/services.py`) e task Celery `enviar_disparo_hotspot_lead`
  (`clientes/tasks.py`), disparada via sinal `post_save` em `HotspotLead`
- ✅ Lista dinâmica de variáveis do template (adicionar/remover linha) — corrige limitação inicial
  que só suportava 2 variáveis (`{nome}`/`{telefone}`), enquanto templates HSM reais podem exigir
  qualquer quantidade
- ✅ Fix: `success: false` da Chatmix com HTTP 200 era tratado como envio bem-sucedido
- ✅ Fix: formulário do portal cativo agora exige o 9º dígito do telefone (sem ele, o número fica
  incompleto para o WhatsApp)
- ✅ Botão "Enviar teste" — valida key/token/template sem precisar de um lead real

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md)** | Modelo de dados, endpoints, `ChatmixClient`, disparo automático via Celery, bugs corrigidos, passo a passo de configuração |

---

### Sessão 10 — 23/07/2026: Módulos do Cliente — Habilitar/Desabilitar Ferramentas por Contrato

**O que foi implementado?**
- ✅ Novo model `ClienteModulo`: habilita/desabilita, por cliente, cada ferramenta da tela
  (Acessos, Backups, VPN, Topologia, Túneis SSH, Documentos, RPKI/IRR, Monitoramento,
  Documentação de Rede, Hotspot, Testes de Rede)
- ✅ Switch de toggle ao lado de cada aba em `listar.html`, visível só para admin (`is_staff`/`is_superuser`)
- ✅ Módulo sem registro = habilitado (clientes já cadastrados não perdem acesso a nada)
- ✅ Para o cliente final: aba some quando o módulo é desabilitado, com fallback automático
  de aba padrão caso "Acessos" seja desabilitado
- ✅ Decorator `modulo_habilitado_required` bloqueia acesso direto por URL (não só a aba
  visual) em 89 endpoints de `clientes/views.py`
- ✅ Fix de layout: menu do usuário-cliente colado no botão "Voltar ao Dashboard" (faltava `w-100`)

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[MODULOS_CLIENTE.md](MODULOS_CLIENTE.md)** | Modelo de dados, UI, endpoint de toggle, decorator, lacunas conhecidas (Hotspot/IPAM/Monitoramento sem bloqueio de backend) |

---

### Sessão 9 — 23/07/2026: Tela de Login do Hotspot — Sobrenome, Termos/LGPD e Dedup de Leads

**O que foi implementado?**
- ✅ Campo "Nome completo" separado em "Nome" + "Sobrenome" (dois campos lado a lado)
- ✅ Campo "Data de nascimento" removido do formulário de login do hotspot
- ✅ Checkbox obrigatório de aceite dos Termos de Uso/Política de Privacidade (LGPD), com modal
  de 3 abas (Resumo/Privacidade/Termos); aceite gravado em `HotspotLead.termos_aceitos`
- ✅ Deduplicação de leads: mesmo hotspot + mesmo telefone ou mesmo nome completo
  (case-insensitive) já cadastrado não gera um novo `HotspotLead`
- ✅ Ajustes visuais nos campos do formulário (contraste, espaçamento, estado de erro)
- ✅ Admin: coluna "Nasc." da listagem de leads virou "Termos" (✓ Aceito/—); CSV export ajustado

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[HOTSPOT_CAPTIVE_PORTAL.md](HOTSPOT_CAPTIVE_PORTAL.md)** | Sobrenome, aceite de Termos/LGPD, dedup de leads |

---

### Sessão 8 — 20/07/2026: Correções em Gravação WinBox, Proxy Web + Wiki de Artigos

**O que foi implementado?**
- ✅ Gravação de tela WinBox: corrigido `.mp4` de 0 bytes quando a resolução do viewport do
  cliente é ímpar (`libx264` exige dimensões pares)
- ✅ Ícones do WinBox 3.43 com fundo preto + lentidão pra interagir: causa raiz era o Xvfb rodando
  a 16bpp (não disputa de CPU, como se supunha antes) — corrigido para 24bpp
- ✅ `ffmpeg` da gravação agora sobe com `nice`/`ionice` (prioridade baixa), cedendo CPU/IO pro
  Wine/WinBox durante toda a sessão, não só no carregamento inicial
- ✅ Proxy web de acessos (`proxy_web_acesso`): corrigido 404 em redirects HTTP relativos do
  equipamento (ex: Zabbix `Location: zabbix.php?...`) que viravam concatenação sem barra com o
  path do proxy
- ✅ Wiki de Artigos Técnicos: anexo de PDF por artigo (upload/troca/remoção, visualizador com
  zoom no painel do Terminal), templates de listagem (categoria/tag/fabricante) unificados em um
  só, busca também pelo conteúdo do artigo, registro completo no Django Admin

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md)** | Seção "Correções — 20/07/2026 (tarde)": vídeo 0 bytes, ícone preto (16bpp), nice/ionice, redirect relativo no proxy |
| **[winbox_vnc.md](winbox_vnc.md)** | Xvfb 24bpp, dimensões pares, nice/ionice no ffmpeg |
| **[WIKI_ARTIGOS.md](WIKI_ARTIGOS.md)** | PDF anexado, templates unificados, admin, remoção do CRUD de blocos de código |

---

### Sessão 7 — 20/07/2026: Editor de Topologia — Design, Ícones e Efeitos Visuais

**O que foi implementado?**
- ✅ Passe de design completo: toolbar, paleta agrupada por categoria, grid "blueprint", sheen
  nos nodes, painel de propriedades com transições, legenda de interfaces (botão na toolbar)
- ✅ Ícones de Roteador (círculo + 4 setas, estilo AWS/Cisco) e Switch (caixa física com portas
  RJ45 + uplink) redesenhados a partir de referências visuais reais — 2 iterações
- ✅ Efeitos animados: brilho nos ícones, anel pulsante em nodes do CRM, tráfego simulado nos
  links (tracejado + pacotes viajando de Lado A pro Lado B), botão "Efeitos" pra desligar tudo
- ✅ IP de gerência em negrito abaixo do node
- ✅ Corrigida regressão no rótulo "Interface Lado A/B" (largura do texto não entrava na conta
  da distância mínima até o node — nomes de interface longos ainda ficavam cortados)

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[topologia.md](topologia.md)** | Design, ícones, efeitos animados, IP em negrito, fix de regressão |

---

### Sessão 6 — 20/07/2026: Editor de Topologia (interfaces do backup, ícone manual) + fix de backup

**O que foi implementado?**
- ✅ Campos "Interface Lado A/B" do editor de topologia agora sugerem, via `<datalist>`, as
  interfaces + descrição extraídas do backup mais recente do host em cada ponta do link — sem
  backup, o campo continua texto livre normal
- ✅ Painel de propriedades do node ganhou seletor de **Ícone/Tipo**, com trava (`type_manual`)
  para a troca manual não ser revertida pela sincronização automática função→ícone do CRM
- ✅ Novas velocidades de interface: 20 Gbps, 30 Gbps, 50 Gbps
- ✅ 3 bugs corrigidos no editor: XSS armazenado via `dados_json|safe` no carregamento da página,
  atalhos de teclado disparando com foco em `<select>`, rótulo "Interface Lado A/B" escondido
  atrás do node em links curtos
- ✅ Backup: corrigido `FileNotFoundError` ao salvar backup de acesso com `/` no campo `tipo`
  (nome de arquivo sanitizado corretamente)

**Onde está documentado?**

| Documentação | Tema |
|--------------|------|
| **[topologia.md](topologia.md)** | Sugestão de interface via backup, troca manual de ícone, novas velocidades, bugs corrigidos |
| **[backup_automatico.md](backup_automatico.md)** | Fix do nome de arquivo com `/` no tipo do acesso |

---

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

- **[topologia.md](topologia.md)** — Editor visual de topologia de rede (SVG)
  - Sugestão de interface a partir do backup (`<datalist>` com nome + descrição)
  - Troca manual de ícone com trava contra a sincronização automática do CRM
  - Tipos de dispositivo, tipos/velocidades de interface, waypoints
  - Bugs corrigidos (XSS, atalhos com `<select>` focado, rótulo escondido atrás do node)

- **[HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md)** — Hotspot: disparo automático de WhatsApp (Chatmix + Opa Suite)
  - Model `ClienteIntegracaoDisparo`, `ChatmixClient`/`OpaSuiteClient`, task Celery `enviar_disparo_hotspot_lead` (dispara em todos os providers habilitados)
  - Lista dinâmica de variáveis do template (N variáveis, não só nome/telefone)
  - Tabela comparativa Chatmix × Opa Suite (endpoint, auth, canal, formato do ID de template)
  - Bugs corrigidos: `success:false` com HTTP 200, telefone sem o 9º dígito
  - Passo a passo de configuração de cada provider (credenciais/domínio/canal/template/variáveis/teste)

- **[WIKI_ARTIGOS.md](WIKI_ARTIGOS.md)** — Wiki de artigos técnicos
  - PDF anexado ao artigo (upload/troca/remoção, visualizador com zoom no Terminal)
  - Templates de listagem (categoria/tag/fabricante) unificados
  - Busca também pelo conteúdo, dedupe de categoria por slug
  - Registro completo no Django Admin

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
├─ frontend_acessos.md
├─ topologia.md .......................... 📌 Editor visual de topologia de rede (SVG)
├─ WIKI_ARTIGOS.md ........................ 📌 Wiki de artigos técnicos (PDF, busca, admin)
└─ MODULOS_CLIENTE.md ..................... 📌 Habilitar/desabilitar ferramentas por cliente
└─ HOTSPOT_INTEGRACAO_DISPARO.md .......... 📌 Hotspot: disparo automático de WhatsApp (Chatmix + Opa Suite)
└─ GEOLOCALIZACAO_IP.md ................... 📌 Geolocalização de IP, correção e Geofeed público (múltiplos blocos)
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

### "Como configurar o disparo de WhatsApp (Chatmix) para leads do Hotspot?"
→ [HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md) — Seção "Como Configurar (passo a passo)"

### "Chatmix disse que enviou mas a mensagem não chegou no WhatsApp, por quê?"
→ [HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md) — Seção "Bugs Corrigidos" (Bug 2: `success:false` com HTTP 200) e "Limitações Conhecidas" (template pendente de aprovação da Meta)

### "Opa Suite retorna 'Communication channel not found', o que fazer?"
→ [HOTSPOT_INTEGRACAO_DISPARO.md](HOTSPOT_INTEGRACAO_DISPARO.md) — Seção "Bugs Corrigidos" (Bug 5: Canal e Template trocados) — confirme os IDs reais chamando `GET /api/v1/canal-comunicacao/` e `GET /api/v1/template` com o token, em vez de confiar no que aparece no painel

---

## 📅 Histórico

| Data | O quê | Documentação |
|------|-------|--------------|
| 29/07/2026 | Exportação de Senhas: novo formato TXT (além do PDF); fix PDF cortando nas laterais no modo "Sem Senha Root" (tabela mais larga que a página) | frontend_acessos.md |
| 26/07/2026 | Geolocalização de IP: novo model `GeofeedBloco` como fonte única do geofeed.csv, card "Blocos do Geofeed" para cadastrar múltiplos prefixos/localizações de uma vez, coluna Postal-Code preenchida | GEOLOCALIZACAO_IP.md |
| 24/07/2026 | Hotspot: Integração Disparo — Opa Suite retornava "Communication channel not found"; diagnóstico via API própria revelou Canal/Template trocados na configuração; corrigido, teste funcionou | HOTSPOT_INTEGRACAO_DISPARO.md |
| 23/07/2026 | Módulos: de `ClienteModulo` (empresa) para `UsuarioModulo` (login individual) — seleção movida pra Sistema → Usuário | MODULOS_CLIENTE.md |
| 23/07/2026 | Módulos do Cliente: seleção movida do toggle inline nas abas para checkboxes no cadastro/edição do cliente | MODULOS_CLIENTE.md |
| 23/07/2026 | Hotspot: Integração Disparo — painel de ajuda visual no card Chatmix (Key/Token, ID do Template, sugestão de mensagem com botão Copiar); diagnóstico de "template pendente" e "canal errado na chave" | HOTSPOT_INTEGRACAO_DISPARO.md |
| 23/07/2026 | Hotspot: Integração Disparo — Opa Suite funcional (`OpaSuiteClient`, `api_dominio`/`canal_id`), task generalizada p/ disparar em todos os providers habilitados, fix nomenclatura "Opa Suit"→"Opa Suite" | HOTSPOT_INTEGRACAO_DISPARO.md |
| 23/07/2026 | Hotspot: Integração Disparo (WhatsApp HSM via Chatmix) disparado automaticamente no cadastro do lead; lista dinâmica de variáveis do template; fix `success:false` com HTTP 200; fix telefone sem o 9º dígito | HOTSPOT_INTEGRACAO_DISPARO.md |
| 23/07/2026 | Módulos do Cliente: toggle por ferramenta (Acessos/Backups/VPN/Topologia/etc), admin-only, bloqueio de backend; fix menu do cliente colado no "Voltar ao Dashboard" | MODULOS_CLIENTE.md |
| 23/07/2026 | Hotspot: login com sobrenome, checkbox de aceite Termos/LGPD (modal), dedup de leads por telefone/nome | HOTSPOT_CAPTIVE_PORTAL.md |
| 20/07/2026 | Gravação WinBox (fix vídeo 0 bytes, ícone preto/16bpp, nice/ionice); proxy web (fix redirect relativo); Wiki de Artigos (PDF, templates unificados, admin) | AUDITORIA_ACESSOS.md, winbox_vnc.md, WIKI_ARTIGOS.md |
| 20/07/2026 | Editor de Topologia: passe de design, ícones de Router/Switch redesenhados, efeitos animados (brilho, pulso, tráfego simulado), IP em negrito, fix de regressão no rótulo Lado A/B | topologia.md |
| 20/07/2026 | Editor de Topologia: interfaces do backup no Lado A/B, troca manual de ícone, velocidades 20/30/50G; fix XSS, atalhos com `<select>`, rótulo escondido; fix backup com `/` no tipo | topologia.md, backup_automatico.md |
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

**Última atualização:** 26/07/2026  
**Versão:** 2.1  
**Mantidor:** CampeloSuporte
- [Notificações de Chamados em Aberto](notificacoes_chamados.md) — Toast e badge em tempo real para chamados sem atendente (dentro e fora do atendimento)
