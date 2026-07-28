# Changelog — CRM NOC

Todas as mudanças relevantes do projeto são registradas aqui.  
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

---

## [Não publicado] — 2026-07-28 (Geolocalização de IP: token ipinfo.io, correção IPligence, fix Geofeed)

### Adicionado

- **Token de autenticação na ipinfo.io** (`crm/settings.py` — `IPINFO_TOKEN`, `home/views.py` —
  `query_ipinfo`/`q_ipinfo`): as consultas eram anônimas, sujeitas a rate limit baixo e compartilhado
  globalmente. Ver `docs/GEOLOCALIZACAO_IP.md`.
- **Correção automática por e-mail — IPligence** (`home/views.py` — `EMAIL_DESTINOS`,
  `home/templates/geo_consulta.html`): novo destino de correção de geolocalização, mesmo fluxo já
  usado para LACNIC/ARIN. DB-IP.com e IP2Location entraram só como links manuais (portais) — não têm
  mecanismo confiável de automatizar sem risco de falso-positivo. Ver `docs/GEOLOCALIZACAO_IP.md`.
- **URL pública limpa do Geofeed** (`crm/urls.py` — `/geofeed.csv`): a rota original
  (`/homeferramentas/geo/geofeed.csv`) funciona mas é resultado de um bug legado de roteamento; a
  nova rota fica ao lado, sem quebrar a antiga. Ver `docs/GEOLOCALIZACAO_IP.md`.

### Corrigido

- **`geo_blocos_salvar` não atualizava o campo `prefixo` ao editar um bloco existente**
  (`home/views.py`): o `.update(**defaults)` no caminho de edição por `id` não incluía `prefixo` no
  dict `defaults` — editar o prefixo de uma linha já cadastrada em "Blocos do Geofeed" retornava
  sucesso mas não persistia a mudança. Causa raiz de um caso real de suporte (bloco `186.65.76.0/22`
  editado para `/24`, continuava publicando `/22` no `geofeed.csv`). Ver `docs/GEOLOCALIZACAO_IP.md`.

### Removido

- **Submissão automática ao MaxMind Geo/ISP-Org** (`home/views.py` — `geo_atualizar`): o formulário do
  MaxMind passou a rejeitar com HTTP 400 toda submissão automatizada (reproduzido fora do CRM com o
  mesmo payload, sem mudança de nossa parte — provável proteção anti-bot nova do lado deles). Convertido
  para portal manual, mesmo tratamento já dado a DB-IP/IP2Location. Confirmação por e-mail via IMAP
  (`geo_confirmar_maxmind`) não foi afetada. Ver `docs/GEOLOCALIZACAO_IP.md`.

---

## [Não publicado] — 2026-07-27 (Correções: KEX SSH do Backup, WhatsApp Nono Dígito, Timeout RPKI)

### Corrigido

- **KEX SSH do backup restrito ao ZTE** (`clientes/views.py` — `realizar_backup`): o disable de
  `group-exchange-sha256/sha1`/`group16-sha512`/`group18-sha512` (pensado só pra CPU embarcada
  fraca de OLTs ZTE) era aplicado pra **todos** os fabricantes. Um Huawei NE8000 M8 só oferece
  `group-exchange-sha256` como KEX — desabilitar geral zerava o KEX em comum e o backup falhava
  com `Incompatible ssh peer (no acceptable kex algorithm)`. Restrito à flag `is_zte`. Ver
  `docs/backup_automatico.md`.
- **Cobrança WhatsApp com número BR sem o nono dígito** (`financeiro/whatsapp.py`,
  `atendimento/services.py`): números de celular BR podem existir no WhatsApp com ou sem o 9º
  dígito (contas antigas/portadas) e a Evolution API rejeitava com 400 `exists: false` sem nenhuma
  tentativa de variante. `_normalizar_telefone()` ainda rejeitava de propósito (`None`) o formato
  de 8 dígitos, bloqueando o número correto antes mesmo do envio. Agora o envio tenta
  automaticamente a variante alternada do 9º dígito. Ver `docs/FINANCEIRO.md`.
- **Timeout do RIPE Stat pulava o fallback RPKI** (`clientes/views.py` — `validar_rpki`): timeout
  na fonte primária (RIPE Stat) retornava erro na hora em vez de cair no fallback Cloudflare RPKI
  que já existe no código — um timeout pontual bastava pra marcar o bloco como erro sem tentar a
  segunda fonte. Ver `docs/RPKI_IRR.md` (novo).

---

## [Não publicado] — 2026-07-26 (Geolocalização de IP — Múltiplos Blocos/Localizações no Geofeed)

### Adicionado

- **Model `GeofeedBloco`** (`clientes/models.py`): fonte única de verdade do `geofeed.csv` público
  (RFC 8805) — antes o arquivo era montado deduplicando por prefixo dentro do histórico
  `CorrecaoGeoIP` (registro de solicitações de correção), o que era frágil e não permitia editar
  ou remover um bloco já publicado. Migrações `0092_geofeed_bloco` (cria a tabela) e
  `0093_geofeed_bloco_migrar_historico` (popula a partir do prefixo mais recente de cada
  `CorrecaoGeoIP`, sem perder o conteúdo já publicado no deploy).
- **Card "Blocos do Geofeed"** (`home/templates/geo_consulta.html`): tabela editável na tela de
  Geolocalização de IP com botão "+ Adicionar bloco" — cadastra quantos prefixos/localizações
  forem necessários (Prefixo, País, Região, Cidade, Postal Code), cada linha com salvar/remover
  independentes. Antes só era possível publicar 1 bloco por vez, repetindo manualmente todo o
  fluxo de busca + modal de correção para cada prefixo.
- **Endpoints** (`home/views.py`/`home/urls.py`): `geo_blocos_listar` (GET), `geo_blocos_salvar`
  (POST — aceita lista de blocos em uma única requisição) e `geo_blocos_excluir` (POST).
- **Coluna Postal-Code do RFC 8805** (`Prefix,Country,Region,City,Postal-Code`): antes sempre
  vazia — o formato agora inclui o campo quando informado no cadastro de blocos.
- `geo_atualizar` (fluxo de correção via busca de 1 IP, inalterado na UI) agora também grava em
  `GeofeedBloco`, mantendo as duas formas de cadastro na mesma fonte de verdade.

Ver `docs/GEOLOCALIZACAO_IP.md` para detalhes.

---

## [Não publicado] — 2026-07-24 (Hotspot — Cor do Painel/Texto e Tela de Sucesso)

### Adicionado

- **Cor do painel e cor do texto do formulário do hotspot** (`clientes/models.py`,
  `clientes/hotspot_views.py`, `clientes/templates/listar.html`): antes só a cor dos botões e
  do fundo da página eram customizáveis — o card de login em si tinha fundo escuro fixo e texto
  branco fixo, ficando ilegível em painéis claros. Dois campos novos em `HotspotConfig`
  (`cor_painel`, `cor_texto`) com seletor de cor na aba Hotspot, aplicados ao card de login e à
  tela de sucesso. Migrações `0089_merge_20260724_1552` (resolve conflito pré-existente no
  grafo de migrações do app `clientes`), `0090_hotspotconfig_cor_painel`,
  `0091_hotspotconfig_cor_texto`.
- **Tela de sucesso pós-conexão redesenhada** (`clientes/hotspot_views.py::_sucesso_page_html`):
  a tela exibida entre o envio do formulário e a liberação da internet — antes só um spinner
  sobre fundo liso — agora reaproveita a identidade visual do portal (logo, cores, painel),
  com ícone de check animado e saudação com o nome do lead. Mantém o redirect automático (agora
  em 2200ms) e o link de fallback para quando o redirect demora.

Ver `docs/HOTSPOT_CAPTIVE_PORTAL.md` para detalhes de cada item.

---

## [Não publicado] — 2026-07-20 (Editor de Topologia — Design e Efeitos Visuais)

### Adicionado

- **Passe de design completo no editor de Topologia** (`clientes/templates/topologia_editor.html`,
  `static/js/topo_main.js`, `static/js/topo_engine.js`): toolbar com sombra e botão "Salvar" em
  destaque, paleta de dispositivos agrupada por categoria (Rede/Core, Acesso/FTTH, Servidores,
  Outros, Anotações), grid de fundo "blueprint" (pontos + linhas a cada 100px), sheen sutil nos
  nodes, painel de propriedades com transições suaves e campos com anel de foco, nova legenda de
  interfaces (botão "Legenda" na toolbar) e dica de canvas vazio. Puramente visual/aditivo — não
  muda o `dados_json` salvo nem o comportamento de nenhuma ação existente.
- **Ícones de Roteador e Switch redesenhados** (`topo_engine.js`): roteador agora é um círculo
  com 4 setas retas apontando pra fora (estilo AWS "VPC Router"/Cisco "Router"); switch virou uma
  caixa de hardware física com porta uplink redonda + 4 portas RJ45, sem mais setas de
  encaminhamento (uma primeira tentativa com setas de "exchange" foi refeita a pedido, por não
  bater com a referência visual real).
- **Efeitos animados:** brilho nos ícones dos nodes (mais forte no hover/seleção), anel pulsante
  em nodes vinculados a um Acesso do CRM (indica "equipamento real monitorado"), tráfego
  simulado nos links — tracejado correndo + 2 "pacotes" (`<circle>` com `<animateMotion>`)
  viajando do Lado A pro Lado B na velocidade proporcional ao tamanho do link. Botão "Efeitos"
  na toolbar (ligado por padrão) desliga tudo de uma vez para topologias muito grandes.
- **IP de gerência em negrito** abaixo do nome de cada node, com o fundo do rótulo ligeiramente
  mais largo para não cortar IPs longos.

### Corrigido

- **Regressão no rótulo "Interface Lado A/B"**: a correção anterior (sessão passada) só afastava
  o *centro* do texto do node, sem considerar a própria largura — nomes de interface longos (ex.
  `ten-gigabit-ethernet 1/1/5`) ainda ficavam com metade do texto em cima do node em links
  horizontais. Corrigido somando `largura_do_rótulo/2` à distância mínima de afastamento.

Ver `docs/topologia.md` para detalhes de cada item.

---

## [Não publicado] — 2026-07-20 (Editor de Topologia, Backup)

### Adicionado

- **Sugestão de interface a partir do backup no editor de Topologia**
  (`clientes/views.py::interfaces_backup_acesso`, `static/js/topo_main.js`): os campos
  "Interface Lado A/B" do painel de propriedades do link agora são `<input list="...">` ligados
  a um `<datalist>` populado com os nomes de interface (+ descrição, quando o backup tiver) do
  backup mais recente do host em cada ponta do link. Sem backup do host, o campo continua texto
  livre normal, sem sugestões. Parser cobre MikroTik, Juniper e a sintaxe genérica
  Cisco/Huawei/Datacom/ZTE/HP/Dell/Extreme. Ver `docs/topologia.md`.
- **Troca manual do ícone do dispositivo** (`static/js/topo_main.js`): painel de propriedades do
  node ganhou seletor de Ícone/Tipo. Nodes importados do CRM ganham a flag `type_manual` ao ter
  o ícone trocado na mão, para a sincronização automática função→ícone não reverter a escolha do
  usuário na próxima reimportação/recarregamento — com botão para voltar ao modo automático. Ver
  `docs/topologia.md`.
- **Velocidades de interface 20/30/50 Gbps** no editor de Topologia (`static/js/topo_engine.js`).

### Corrigido

- **XSS armazenado no editor de Topologia** (`clientes/templates/topologia_editor.html`): o JSON
  da topologia era injetado no `<script>` de carregamento via `{{ dados_json|safe }}`, sem
  escape — texto livre salvo pelo usuário (ex. um nó "Texto/Legenda") podia fechar a tag
  `<script>` e executar JS arbitrário para quem abrisse aquela topologia depois. Corrigido para
  `JSON.parse("{{ dados_json|escapejs }}")`, mesmo padrão já usado em `topologia_drawio.html`.
- **Atalhos de teclado do editor de Topologia disparavam com foco em `<select>`**
  (`static/js/topo_main.js`): o guard só excluía `INPUT`/`TEXTAREA`; `Delete`/`Backspace` com um
  dropdown de propriedades focado apagava o nó/link selecionado sem intenção. Guard estendido
  para incluir `SELECT`.
- **Rótulo "Interface Lado A/B" escondido atrás do node em links curtos**
  (`static/js/topo_main.js`): posição calculada como % do comprimento do link caía dentro do
  raio visual do próprio node (desenhado por cima na camada SVG) em conexões curtas. Corrigido
  para distância fixa em pixels a partir da borda de cada node.
- **`FileNotFoundError` ao salvar backup de acesso com "/" no campo `tipo`**
  (`clientes/views.py::realizar_backup`): o nome do arquivo de backup só sanitizava espaços;
  `/` num `tipo` como `"BRAS/CGNAT/BORDA - JUNIPER"` virava separador de diretório inexistente
  no `os.path.join()`. Agora qualquer caractere fora de letras/números/`-`/`_` vira `_`. Afeta
  tanto o botão manual quanto o pipeline automático de backup. Ver `docs/backup_automatico.md`.

---

## [Não publicado] — 2026-07-20 (Auditoria de Acessos, Hotspot, Backup)

### Adicionado

- **Auditoria de Acessos** (`clientes/models.py`, `clientes/consumers.py`, `clientes/browser_vnc.py`,
  `clientes/winbox_vnc.py`, `clientes/views.py`, `clientes/admin.py`, `templates/modal_acessos.html`,
  migrações `0080`/`0081`): toda sessão SSH/Telnet/WinBox/WebFig passa a ser registrada —
  usuário do CRM, IP de origem, duração. Para SSH/Telnet grava comandos digitados
  (`AcessoComando`) e transcript completo da tela (ANSI removido). Para WinBox/WebFig via VNC
  grava a tela em `.mp4` via `ffmpeg`. Novo modal "Auditoria de Acessos" na aba de Acessos lista
  sessões, comandos e gravações. WebSocket dos consumers de terminal agora exige usuário
  autenticado (`code=4001` se anônimo). Ver `docs/AUDITORIA_ACESSOS.md`.

### Corrigido

- **Gravação de vídeo de sessões WinBox/WebFig às vezes ficava com 0 bytes**
  (`clientes/winbox_vnc.py`, `clientes/browser_vnc.py`): `stop()` podia ser chamado
  concorrentemente (thread de leitura do VNC + `disconnect()` do WebSocket), enviando dois
  `SIGTERM` ao `ffmpeg` em sequência — no segundo, o processo abortava sem finalizar o `.mp4`.
  Corrigido com trava (`threading.Lock`) tornando `stop()` idempotente. Ver `docs/winbox_vnc.md`.
- **Hotspot — `login.html` não aparecia em profiles recriados via SSH** (`clientes/hotspot_views.py`):
  o RouterOS resolve o `html-directory` do hotspot profile de forma inconsistente entre profiles
  (`<dir>` no profile `default`, `flash/<dir>` em profiles recriados via SSH). O CRM agora grava o
  `login.html` nos dois caminhos possíveis via SFTP e `/tool fetch`. Ver `docs/HOTSPOT_CAPTIVE_PORTAL.md`.
- **Hotspot — tela de status "Hi, guest!" aparecia em vez de liberar a navegação**
  (`clientes/hotspot_views.py`): quando `$(link-orig)` chegava vazio (caso comum, ver bug do
  `<meta refresh>` na sessão anterior), o `dst` do login ficava vazio e o RouterOS mostrava a
  tela de status. Corrigido com destino padrão por sistema operacional (detecção de captive
  portal nativa do Android/iOS/Windows), que fecha o mini-browser automaticamente. Ver
  `docs/HOTSPOT_CAPTIVE_PORTAL.md`.
- **Backup automático — detecção de fabricante falhava com modelo cadastrado errado**
  (`clientes/views.py::realizar_backup`): detecção usava só `modelo.nome`; passou a combinar
  `modelo.fabricante` + `modelo.nome` + `acesso.tipo`. Também adicionado
  `disabled_algorithms={'kex': [...]}` para evitar timeout de KEX em equipamentos ZTE durante o
  backup (mesmo problema já corrigido no terminal interativo). Ver `docs/backup_automatico.md`.

---

## [Não publicado] — 2026-06-16 (Agent NOC, Sala Virtual, Hotspot, Financeiro)

### Adicionado

- **API Key Claude individual por grupo WhatsApp** (`clientes/models.py`,
  `home/views.py`, `home/templates/agent_grupos.html`, `home/agent_engine.py`):
  cada grupo WhatsApp vinculado ao Agent NOC pode agora ter sua própria chave
  Anthropic, consumindo os créditos do próprio cliente em vez da chave global do
  sistema. Sem chave configurada, o agent fica em **silêncio total** naquele grupo
  (nenhuma mensagem de erro é enviada). Campo nunca exibe a chave real na UI — só o
  status configurada/não configurada — e o valor é mantido se o campo for deixado
  em branco ao salvar. Ver `docs/agent_noc.md`.

### Corrigido

- **Agent NOC não buscava sinal óptico em equipamentos Datacom (DmOS)**
  (`home/agent_engine.py`): o comando usado (`show interface <iface> transceiver`)
  não existe no DmOS; o correto é `show interface transceivers` (plural, sem
  interface). O agent agora executa esse comando automaticamente ao identificar uma
  interface física Datacom e filtra a saída para a interface relevante. Ver
  `docs/agent_noc.md`.
- **Sala Virtual de atendentes (WebRTC) — áudio cai sozinho após alguns minutos**
  (`atendimento/templates/atendimento/sala_virtual.html`): faltava o listener
  `onnegotiationneeded`, então a tentativa de recuperação via `restartIce()` nunca
  surtia efeito de fato. Implementado o padrão Perfect Negotiation (papéis
  polite/impolite determinísticos) e buffer de candidatos ICE recebidos antes da
  conexão estar pronta (corrige também o caso de "3 pessoas se ouvem, uma não" ao
  entrar várias pessoas ao mesmo tempo). Ver `docs/ATENDIMENTO.md`.
- **Hotspot — entrega do `login.html` ao MikroTik falhava** (`clientes/hotspot_views.py`):
  `/tool fetch` via HTTP falhava por DNS e depois por timeout de conexão em redes
  restritas; passou a usar SFTP pelo canal SSH já aberto, com fetch como fallback.
  Também corrigido `expected end of command` por falta de aspas em parâmetros
  RouterOS. Ver `docs/HOTSPOT_CAPTIVE_PORTAL.md`.
- **Alerta de cobrança via WhatsApp "não enviava"**: não era bug — a flag
  `ConfiguracaoFinanceira.wa_ativo` estava desativada (padrão de fábrica). A task
  agendada (`financeiro.tasks.enviar_alertas_whatsapp`, seg–sex 8:30) sempre rodava e
  sempre pulava silenciosamente.
- **Mensagem de cobrança de venda de equipamento não informava qual serviço**
  (`financeiro/models.py`, `financeiro/views.py`, `financeiro/whatsapp.py`): `Fatura`
  nunca teve de fato o campo M2M `vendas_equipamentos` que o código já tentava usar
  (`hasattr` sempre `False`), então a venda nunca era vinculada à fatura. Adicionado
  o campo (migração `0019_fatura_vendas_equipamentos`), corrigida a montagem da
  mensagem para incluir parcelas/data de início, e religadas retroativamente as 55
  faturas já existentes sem vínculo. Ver `docs/FINANCEIRO.md`.
- **Config do Agent NOC (API Key) não salvava — erro 500 silencioso**
  (`home/templates/agent_config.html`, `home/views.py`): localização pt-BR
  (`USE_L10N=True`) renderizava `0.2` como `0,2` no campo numérico de temperatura,
  invalidando o `<input type="number">` no navegador e quebrando o salvamento
  inteiro (incluindo a API Key) com `ValueError` no backend. Corrigido o
  template (`stringformat`) e tornado o backend resiliente a campos numéricos
  vazios. Ver `docs/agent_noc.md`.

---

## [Não publicado] — 2026-06-16 (VPN WireGuard — Isolamento por cliente)

### Corrigido

- **Conecta ISP perdeu acesso às redes internas após exclusão de outra VPN**
  (`clientes/vpn_manager.py`): `remover_peer()` apagava do kernel rotas
  (`ip route del <rede> dev wg0`) sem checar se **outro** cliente ainda
  dependia da mesma rota. Em 14/06, excluir a VPN do cliente 41 (Sartor
  Internet) apagou as rotas compartilhadas `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `198.18.0.0/15` em `wg0`, das quais Conecta ISP (e outros
  clientes legados) ainda dependiam — o túnel UDP continuava de pé, só o
  roteamento interno parou. `remover_peer()` agora verifica
  (`_outro_peer_usa_rede()`) se algum outro `VPNWireGuard` ativo ainda
  declara a mesma rede antes de remover a rota.
- Rotas compartilhadas de `wg0` restauradas manualmente e persistidas em
  `/etc/wireguard/wg0.conf`.

### Adicionado

- **Interfaces isoladas por cliente** (`clientes/vpn_manager.py`,
  `clientes/views.py`): toda VPN WireGuard criada a partir de agora ganha
  sua própria interface dedicada (`wg5`, `wg6`, ...; porta e `/30` próprios)
  em vez de compartilhar `wg0` — elimina por completo a classe de bug em que
  criar/excluir a VPN de um cliente afeta as rotas de outro. Novas funções:
  `alocar_proxima_interface()`, `criar_interface_isolada()`,
  `adicionar_peer_isolado()`, `remover_interface_isolada()`,
  `vpn_e_isolada()`. `gerar_script_mikrotik()` agora gera o script com a
  porta/sub-rede corretas conforme o tipo de interface do cliente.
  Clientes legados (ids 3, 7, 8, 9, todos em `wg0`) não foram migrados —
  migração requer reconfigurar o WireGuard em cada MikroTik remotamente,
  registrado como recomendação futura.
- **Documentação** (`docs/vpn_wireguard.md`): arquitetura, causa raiz do
  incidente, limitação conhecida de faixas amplas idênticas entre clientes,
  e guia de diagnóstico rápido de roteamento.

---

## [Não publicado] — 2026-06-13 (Monitor de Tráfego com Abas + Hotspot Captive Portal)

### Adicionado

- **Sistema de abas no Monitor de Tráfego**
  (`monitoramento/templates/monitoramento/tab_monitoramento.html`,
  `monitoramento/views.py`):
  A aba de monitoramento ganhou uma barra de abas independentes. Cada aba tem seu próprio
  conjunto de painéis de gráficos Zabbix. Funcionalidades:
  - Criar nova aba (botão "+ Nova aba") — abre automaticamente input de renomeação
  - Trocar de aba — destrói instâncias Chart.js anteriores para liberar memória/CPU
  - Renomear aba por duplo-clique no nome ou pelo menu de contexto (clique direito)
  - Fechar aba pelo botão × ou pelo menu de contexto; bloqueado quando há apenas 1 aba
  - Badge com contador de gráficos por aba
  - Persistência no banco no formato `{ "tabs": [...] }` com compatibilidade retroativa
    (formato antigo de lista plana é migrado automaticamente para aba "Geral")
  - Chave localStorage migrada de `grph_charts_v2_<id>` para `grph_tabs_v1_<id>`

- **Menu de contexto (clique direito) nas abas do Monitor de Tráfego**
  (`monitoramento/templates/monitoramento/tab_monitoramento.html`):
  Clique direito em qualquer aba exibe menu com "Renomear aba" e "Fechar aba".
  O menu é posicionado junto ao cursor, respeita os limites da janela e fecha ao clicar fora.

### Corrigido

- **Hotspot captive portal não redirecionava para login antes de liberar internet**
  (`clientes/hotspot_views.py`): Quatro causas raiz identificadas e corrigidas:

  1. **JS bloqueado em mini-browsers** — `_gerar_login_html` agora usa
     `<meta http-equiv="refresh">` como redirecionamento primário (funciona sem JS).
     O `window.location.replace()` é mantido como secundário e um link `<a>` como
     último recurso.

  2. **Injeção HTML via `&` em URLs** — `$(link-login)` e `$(link-orig)` do MikroTik
     contêm `&` que quebravam atributos `value="..."`. Corrigido com
     `html.escape(..., quote=True)` em todas as variáveis inseridas em HTML.

  3. **Mixed content bloqueava POST do formulário** — `scheme` estava hardcoded como
     `'http'`. Quando o portal era acessado via HTTPS, o browser bloqueava o POST.
     Corrigido com `scheme = 'https' if request.is_secure() else 'http'`.

  4. **`link` vazio quando meta-refresh era usado** — O redirect via `<meta>` não passa
     parâmetros na URL, deixando o campo `link` do POST vazio. Adicionado fallback:
     `raw_link = link if link else f'http://{h.gateway}/login'`.

### Documentação

- `docs/monitoramento.md` — Atualizado com sistema de abas, nova API, variáveis de estado
  e histórico de alterações
- `docs/HOTSPOT_CAPTIVE_PORTAL.md` — Criado: fluxo completo do captive portal, 4 bugs
  corrigidos, compatibilidade com mini-browsers, configuração nginx e walled garden
- `docs/INDEX.md` — Atualizado com sessão 3, novos arquivos e histórico

---

## [Não publicado] — 2026-06-03 (WinBox Web VNC — Correções)

### Corrigido

- **`WinboxVNCManager.__init__()` — parâmetros `width` e `height` faltando**
  (`clientes/winbox_vnc.py`): O consumer passava `width=` e `height=` ao construtor
  mas o `__init__` não declarava esses parâmetros, causando `NameError` e impedindo
  qualquer sessão WinBox de iniciar. Parâmetros adicionados com defaults `width=1366, height=768`.

- **WinBox abrindo minúsculo no browser** (`clientes/winbox_vnc.py`):
  Flag `-ncache 10` no x11vnc fazia o servidor reportar ao noVNC uma tela 10× mais alta
  que a real (ex: 1400×8000 em vez de 1400×800). O noVNC escalava todo o conteúdo para
  caber nessa altura virtual, fazendo o WinBox aparecer como um quadradinho minúsculo no
  topo da tela. Removidos os flags problemáticos: `-ncache`, `-noscr`, `-xrandr`, `-threads`,
  `-nowireframe`. x11vnc restaurado ao comando simples e estável.

- **`rfb.resizeSession = true` desmaximizando o WinBox** (`clientes/templates/winbox.html`):
  O noVNC com `resizeSession=true` enviava `SetDesktopSize` ao x11vnc após conectar,
  podendo causar resize do Xvfb e desmaximizar o WinBox. Definido como `false` pois
  o Xvfb já é criado com as dimensões exatas do viewport do usuário.

### Adicionado

- **Maximização via `xdotool`** (`clientes/winbox_vnc.py`):
  Após iniciar o WinBox, um processo background aguarda a janela aparecer
  (`xdotool search --sync --name 'WinBox'`) e a redimensiona para a resolução correta,
  servindo como fallback ao Openbox.

- **`xdotool` e `wmctrl`** instalados no servidor como dependências do WinBox VNC.

- **Documentação** (`docs/winbox_vnc.md`): Documentação técnica completa do módulo
  WinBox Web VNC, incluindo arquitetura, fluxo de inicialização, problemas conhecidos
  e como testar manualmente.

---

## [Não publicado] — 2026-06-02 (Notificações de Chamados em Aberto no CRM)

### Adicionado

- **Notificador global de chamados em aberto** (`templates/base.html`):
  Sistema de notificação em tempo real via WebSocket para usuários fora do módulo de
  atendimento (em páginas como Clientes, Financeiro, Monitoramento, etc.).
  Ao chegar um novo chamado em aberto (conversa sem atendente), aparece um toast vermelho
  no canto inferior direito com animação de pulso, nome do grupo e link direto para a conversa.
  Badge vermelho com contador aparece no botão "Atendimento" da barra de navegação global.
  Som de alerta duplo e notificação do navegador (se permissão concedida) também são emitidos.
  Apenas para `is_staff`. Reconecta automaticamente com backoff exponencial em caso de queda.
  Arquivos: `templates/base.html` (container `#globalTicketToasts`, estilos `.gtkt-*`,
  script com `connect()`, `showToast()`, `updateBadge()`, `dismissToast()`).

- **Toast visual diferenciado para chamados em aberto** (`atendimento/templates/atendimento/base.html`):
  Dentro do módulo de atendimento, toasts de *novo chamado em aberto* (conversa não assumida)
  agora têm estilo vermelho distinto do toast de mensagem normal (azul/cinza).
  Estilo `msg-toast-ticket`: borda vermelha, fundo vermelho translúcido, animação de pulso
  `ticketPulse`. Ícone de sino vermelho no lugar das iniciais do grupo.
  Label "NOVO CHAMADO EM ABERTO" em vermelho acima do nome do grupo.
  Toasts de mensagens da própria conversa assumida continuam com estilo original.
  Arquivos: `atendimento/templates/atendimento/base.html` (CSS `.msg-toast-ticket`,
  `.msg-toast-icon-ticket`, `.msg-toast-label`, `@keyframes ticketPulse`;
  função `showToast(groupName, msgText, convId, initials, isNewTicket)`).

### Alterado

- **`showToast()` na base do atendimento** (`atendimento/templates/atendimento/base.html`):
  Assinatura alterada de `showToast(groupName, msgText, convId, initials)` para
  `showToast(groupName, msgText, convId, initials, isNewTicket)`. Chamada atualizada
  para passar `isUnassigned` como quinto argumento, ativando o estilo vermelho apenas
  em chamados sem atendente.

- **Botão "Atendimento" na navegação global** (`templates/base.html`):
  Adicionados `id="globalAtendBtn"` e `style="position:relative;"` para permitir
  anexar o badge numérico vermelho via JavaScript sem alterar layout.

---

## [Não publicado] — 2026-06-01 (sessão 4 — Módulo Financeiro)

### Adicionado

- **Sistema de Recorrência de Despesas** (`financeiro/models.py`, `financeiro/views.py`,
  `financeiro/templates/`, migrations `0004-0005`): Novo sistema para despesas recorrentes
  com suporte a UNICA/MENSAL/BIMESTRAL/TRIMESTRAL/SEMESTRAL/ANUAL.
  Campos adicionados em `Despesa`: `recorrencia`, `meses_recorrencia`, `ocorrencia_atual`,
  `status` (PENDENTE/PAGO), `data_pagamento`.
  Endpoint `POST /financeiro/api/despesa/{id}/pagar/` marca como pago e auto-gera próxima
  ocorrência. Interface com checkbox "Recorrência" e campo "Total de meses (vazio=indefinido)".
  Exibição visual: "2/12 mensal" em cor roxa. (docs: `docs/FINANCEIRO.md`)

- **Sistema de Privacidade para Despesas** (`financeiro/models.py`, `financeiro/views.py`,
  migrations `0006`): Campo `privada` em Despesa. Despesas privadas visíveis apenas para
  criador, públicas para todos. Checkbox "🔒 Privada (apenas você vê)" nos modais.
  Indicador visual com ícone de cadeado roxo. Filtro automático em `api_listar_despesas`
  via `Q(privada=False) | Q(privada=True, criado_por=request.user)`.

- **Sistema de Privacidade para Faturas** (`financeiro/models.py`, `financeiro/views.py`,
  migrations `0007`): Campo `privada` em Fatura. Faturas privadas visíveis apenas para staff.
  Checkbox "🔒 Privada (apenas você vê)" no modal "Nova Fatura Manual".
  Indicador visual com ícone de cadeado roxo.
  Validação: `api_visualizar_fatura` retorna 403 para não-staff ao acessar privada.

- **Sistema de Privacidade para Consultorias, Aluguéis e Vendas**
  (`financeiro/models.py`, `financeiro/views.py`, migrations `0008-0010`):
  Mesmo padrão de privacidade aplicado a: `Consultoria`, `AluguelIPv4`, `VendaEquipamento`.
  Todos com campo `privada` (default=False), checkbox nos modais, indicador visual 🔒.
  Controle de acesso: staff vê privadas, usuários veem apenas públicas.

### Alterado

- **Layout de Listagem de Despesas** (`financeiro/templates/financeiro/dashboard.html`):
  CSS Grid ajustado para evitar sobreposição de nome na data de vencimento.
  Colunas finais: `2.5fr 80px 90px 180px 120px` (Nome | Recorrência | Valor | Vencimento | Status).
  Vencimento expandido de 120px para 180px.
- **Admin Panel** (`financeiro/admin.py`): Adicionado display e filtro de `privada`
  em DespesaAdmin, FaturaAdmin, ConsultoriaAdmin, AluguelIPv4Admin, VendaEquipamentoAdmin.

### Corrigido

- **Sobreposição de nome em vencimento**: Aumentado grid-column de vencimento
  de 120px → 140px → 180px em iterações sucessivas.
- **Nome incorreto em migração 0009**: `model_name='alugueipv4'` corrigido para
  `model_name='aluguelipv4'`.
- **Servidor 502 Bad Gateway**: Gunicorn reiniciado após migrações.

---

## [Não publicado] — 2026-05-27 (sessão 3)

### Adicionado

- **Dashboard de Monitoramento — persistência no banco** (`monitoramento/models.py`,
  `monitoramento/views.py`, `monitoramento/urls.py`, migration `0002`):
  Novo modelo `MonitorDashConfig` (OneToOne com Cliente, JSONField `dados`).
  Endpoints `GET /monitoramento/dash/carregar/` e `POST /monitoramento/dash/salvar/`.
  O frontend agora carrega do backend na inicialização e salva a cada alteração.
  Migração automática do localStorage para o banco na primeira abertura da aba.
  **Problema resolvido:** gráficos adicionados por um usuário agora aparecem para todos
  os usuários com acesso ao mesmo cliente. (docs: `docs/monitoramento.md`)

- **Senha Root — controle de visibilidade** (`clientes/templates/listar.html`,
  `templates/modal_acessos.html`, `clientes/views.py`): campo `senha_adm` (Senha Root)
  ocultado para usuários do tipo cliente em quatro locais: card de acesso, modal Novo
  Acesso, modal Editar Acesso, modal Duplicar Acesso. A API `buscar_acesso` também retorna
  `senha_adm=''` para não-staff. Visível apenas para `is_staff` ou `is_superuser`.

- **Gerador de senha aleatória** (`templates/modal_acessos.html`): botão 🎲 adicionado
  ao lado dos campos Senha e Senha Admin nos modais Novo Acesso, Editar Acesso e Duplicar
  Acesso. Gera 16 caracteres via `crypto.getRandomValues`, exibe em texto, copia para
  clipboard automaticamente e confirma com ✓ verde por 1,5s.

- **Exportação de credenciais em PDF** (`clientes/views.py`, `clientes/urls.py`,
  `clientes/templates/listar.html`): botão dropdown "Exportar Senhas" no cabeçalho da
  página do cliente (visível apenas para `is_superuser`). Duas opções:
  - *Sem Senha Root* — A4 retrato, 7 colunas, arquivo `*_sem_root.pdf`
  - *Com Senha Root* — A4 paisagem, 8 colunas, arquivo `*_com_root.pdf`
  Endpoint: `GET /clientes/<id>/senhas/pdf/?root=0|1`.
  PDF gerado via ReportLab. (docs: `docs/frontend_acessos.md`)

- **Envio periódico de PDF com credenciais** (`clientes/tasks.py`, `crm/celery.py`):
  task `enviar_pdf_credenciais` gera PDF A4 paisagem com todos os clientes e acessos
  e envia a cada 2 dias para `campelosuporte.ti@gmail.com`, `noc@tomich.com.br` e
  `danilo@tomich.com.br`. Usa a configuração SMTP do sistema (`ConfiguracaoSistema`).
  (docs: `docs/envio_credenciais_email.md`)

### Alterado

- **Habilitação automática de backup — exclusão de VM e Hipervisor**
  (`clientes/tasks.py`): `habilitar_backups_automaticos` agora exclui acessos com função
  contendo `vm`, `hipervisor` ou `hypervisor` (case-insensitive) ao habilitar backup e ao
  corrigir templates. Adicionada varredura de limpeza: a cada execução remove `backup_habilitado`,
  `backup_automatico` e `backup_template` de qualquer acesso com função VM/Hipervisor já
  marcado. Resultado da primeira varredura: 112 equipamentos removidos (511 → 399).
  (docs: `docs/backup_automatico.md`)

---

## [Não publicado] — 2026-05-26 (sessão 2)

### Adicionado

- **Ícones de topologia por função** (`static/js/topo_engine.js`): novos tipos de dispositivo
  `cgnat` (ícone NAT com setas many-to-one, laranja) e `vm` (caixas empilhadas, roxo).
  Mapeamento automático de função → tipo no endpoint `topologia/hosts/`:
  CGNAT/CG-NAT → `cgnat`; BRAS/BNG → `router`; VM/KVM/VMware/VPS → `vm`.
  Ícones existentes de Borda/Border/Core já mapeavam para `router`.

- **Atualização automática de ícones em topologias salvas** (`static/js/topo_main.js`):
  ao abrir uma topologia salva, o método `_refreshCrmNodeTypes()` consulta o backend e
  atualiza o tipo/ícone dos nós CRM sem mover posições, garantindo que mudanças de
  mapeamento reflitam em diagramas existentes.

- **Remoção de host da topologia**: já estava implementado (`_deleteSelected()`) —
  botão "Remover" no painel de propriedades e tecla `Delete`/`Backspace`.

- **Portfólio de modelos de equipamento** (`modelo_equipamento`): 225 modelos carrier-grade
  inseridos para Huawei (52), Cisco (38), Fiberhome (16), Datacom (20), Intelbras (18),
  Mikrotik (37). Modelos Juniper (46), TP-Link (8) e VSOL (16) adicionados posteriormente
  totalizando **287 modelos**. Fabricantes normalizados para grafia consistente.

- **Auto-detecção de modelo via backup** (`clientes/tasks.py`, `clientes/models.py`,
  migration `0064`): campo `modelo_auto_em` adicionado em `Acesso`. Task
  `detectar_modelos_via_backup` lê o arquivo de backup mais recente de cada host SSH,
  extrai o model string via regex (RouterOS, Cisco IOS/XE/XR, Huawei VRP, ZTE, Datacom,
  A10) e faz match contra `Modelo_equipamento`. Resultado: 91 modelos detectados
  automaticamente (99% de cobertura). Roda a cada 3 dias via Celery Beat.

- **Habilitação automática de backup** (`clientes/tasks.py`): task
  `habilitar_backups_automaticos` habilita `backup_habilitado`, `backup_automatico` e
  seleciona o template correto para todos os acessos SSH com modelo. Regras:
  ZTE/Parks → template Cisco; Huawei OLTs (MA5xxx) → template OLT Huawei;
  demais → match por fabricante. 511 equipamentos com backup ativo.

- **Rotina de backup completa** (`clientes/tasks.py`,
  `clientes/management/commands/rotina_backup.py`): task `rotina_backup_completa`
  encadeia detecção de modelo + habilitação de backup. Management command
  `python manage.py rotina_backup` executa o pipeline com saída formatada e suporta
  flags `--forcar-modelo` e `--apenas-templates`. Agendado diariamente às 01h.

### Alterado

- **Versão dos arquivos JS de topologia**: `topo_engine.js` e `topo_main.js`
  atualizados para `v=8` para invalidar cache do browser.

- **Normalização de fabricantes** no banco `modelo_equipamento`: registros com
  `HUAWEI`, `CISCO`, `MIKROTIK`, `DATACOM`, `INTELBRAS`, `FIBERHOME`, `ZTE Corporation`
  normalizados para grafia título (`Huawei`, `Cisco`, etc.).

---

## [Não publicado] — 2026-05-26

### Corrigido

- **Terminal SSH** (`clientes/consumers.py`): reordenação dos KexAlgorithms para colocar
  `diffie-hellman-group14-sha256` antes de `diffie-hellman-group16-sha512`, eliminando
  timeout em equipamentos ZTE com CPU lenta (DH 4096-bit causava atraso crítico no handshake).

- **Modais de Acesso** (`static/js/main.js`): modais (`modalAcesso`, `modalEditarAcesso`,
  `modalDuplicarAcesso`) agora são movidos para `document.body` no momento de abertura,
  corrigindo bug de posicionamento causado por containers ancestrais com `position: relative`
  ou `transform` interferindo no `position: fixed`.

### Adicionado

- **IPAM — Agrupamento /24** (`clientes/ipam_views.py`): a função `_get_or_create_prefixo_pai`
  passou a criar também um registro `IPAMSubRede` (status `reservado`) para o bloco /24 pai,
  além do `IPAMPrefixo` container já existente. Resultado: blocos /24 aparecem tanto na aba
  de Prefixos quanto na aba de Sub-redes do IPAM.

- **Filtro inline de Acessos** (`clientes/templates/listar.html`): campo de busca em tempo
  real por nome (tipo) e por endereço IP (host / host_ipv6), substituindo o antigo botão
  "Filtrar Acessos". Inclui contador de resultados e restauração da visão em abas ao limpar.

- **Monitor de Tokens — Agent NOC** (`home/views.py`, `home/urls.py`,
  `home/templates/agent_config.html`): nova view `agent_token_stats` com endpoint
  `GET /agent/config/token-stats/?periodo=<24h|7d|30d|all>` que devolve consumo de tokens,
  custo estimado em USD e BRL (cotação em tempo real via AwesomeAPI) e histórico diário dos
  últimos 14 dias. Interface exibida no painel de configuração do Agent NOC.

### Alterado

- **CSS Modal Overlay** (`static/css/style.css`): `.modal-overlay` usa `inset: 0` em vez de
  `top/left/width/height` individuais e `overflow-y: auto`; `.modal-acesso` utiliza
  `margin: 40px auto` para centralização correta independente do contêiner pai.

---

## Versões anteriores

| Commit     | Descrição                                         |
|------------|---------------------------------------------------|
| `314b907e` | Agent NOC melhorado, plataforma estável           |
| `8751731e` | Implementado Agent NOC inicial                    |
| `4c90ccc1` | Terminal melhorado e redesign geral               |
| `d248e753` | Pesquisa LG, gerenciador de firmware, UI temática |
| `4559c99e` | IRR automatizado pela plataforma                  |
