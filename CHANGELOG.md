# Changelog — CRM NOC

Todas as mudanças relevantes do projeto são registradas aqui.  
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

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
