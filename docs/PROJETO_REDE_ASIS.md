# AS-IS da Infraestrutura de Rede (app `projeto_rede`)

> Implementado em 17/09/2026. Primeira etapa do fluxo **AS-IS → Change Plan TO-BE**.
> Exclusivo do **Administrador** (`usuario.perms.is_admin`).

O sistema reconstrói o estado atual da rede de um cliente a partir de três fontes: o **último backup
de configuração** de cada acesso, a **topologia** desenhada no CRM e o **cadastro** (acessos, função,
modelo e blocos IP). O resultado é um documento no formato do AS-IS da Startnet. Ele pode ser editado
no próprio sistema, versionado e exportado em **PDF** e **DOCX** para apresentar ao cliente.

---

## Onde fica

- **Botão:** Clientes → (cliente) → aba **📚 Documentação de Rede** → **AS-IS da Infraestrutura**
  (só aparece para o Administrador).
- **Lista do cliente:** `/projetos-rede/cliente/<id>/`. Mostra as fontes disponíveis (acessos, SSH,
  com backup, mapas, blocos) e os documentos já gerados.
- **Editor:** `/projetos-rede/documento/<id>/`.

## Fluxo

1. **Gerar AS-IS**: lê os backups, analisa e cria o documento em *Rascunho*, com uma revisão inicial
   ("Geração automática a partir dos backups").
2. **Editar**: a capa e a tabela de controle são editáveis, e cada seção tem título e corpo em rich text.
   Também dá para reordenar, excluir, criar seções e recolocar uma seção automática excluída.
3. **Salvar**: automático 4 s depois da última alteração, ou com **Ctrl+S**. Se outra sessão salvou
   antes, o editor avisa (HTTP 409) e pergunta se deve substituir.
4. **Registrar revisão**: fotografia do documento num marco (ex.: "Revisão técnica com o NOC").
   Sugere a próxima versão (1.0 → 1.1) e grava a nova versão na capa.
5. **Histórico**: lista as revisões. **Restaurar** guarda antes o estado atual como revisão.
6. **Atualizar com backups**: recalcula todas as seções automáticas e mantém a capa e as seções
   criadas à mão. Guarda uma revisão antes.
7. **Recalcular seção** (ícone ↻ na seção): devolve só aquela seção recalculada. Não grava; o
   usuário salva.
8. **Visualizar / PDF / DOCX**: se houver alteração pendente, salva antes de exportar.

## Estrutura do documento gerado

| # | Seção | Origem |
|---|---|---|
| Capa | Empresa, título, subtítulo, tabela de controle (documento, versão, status, ASN, data-base, responsável, classificação), princípio | cadastro + ASN predominante + período dos backups |
| 1 | Controle e finalidade | fontes contadas da coleta |
| 2 | Sumário executivo | texto e tabela por domínio |
| 3 | Topologia e inventário | RRs, PEs (LSR-ID, plataforma, software), demais equipamentos, enlaces da topologia |
| 4 | Underlay, MPLS e controle | OSPF, LDP, RSVP-TE, túneis TE, BFD, MTU por equipamento; RRs e famílias MP-BGP; peers iBGP históricos |
| 5 | Achados estruturais | `AS-IS-NNN` ordenados por severidade |
| 6 | BNG, PPPoE e CGNAT | pools BAS, IPv6 PD, PPPoE via L2VPN até o BNG (VE `l2-terminate`), BNGs remotos, CGNAT dedicado |
| 7 | Trânsito IP e ISP downstreams | upstreams, downstreams (entrega, prefixos, LP, communities), rotas estáticas para clientes |
| 8 | Serviços especiais, VRFs e parceiros | VRFs (RD/RT/PEs), clientes L3VPN, CDN/IX/peering, sessões internas |
| 9 | Transportes e L2VPNs | serviços consolidados por ID, pontas, VLANs, sinalização, MTU |
| 10 | Communities | faixas de prepend (`X:NN0 a X:NN5`) e valores avulsos, com finalidade inferida |
| 11 | Operação, segurança e dívida técnica | telnet/FTP/SSH/SNMP/contas locais por equipamento |
| 12 | Riscos e pendências residuais | derivados dos achados |
| 13 | Status formal | status por domínio |
| Anexo A | Inventário de acessos e fontes | sem credenciais |

Subseções (h3) são numeradas na renderização (`n.m`). Uma seção cujo título começa com "Anexo" fica
fora da numeração.

## Arquitetura

```
coleta.py      → lê BackupLog/arquivo, TopologiaDiagrama, BlocoIP (somente leitura)
extratores.py  → Huawei VRP e MikroTik RouterOS → dict AS-IS (sem segredos)
analise.py     → funções puras: papéis, classificação eBGP, agregações, achados, riscos
composicao.py  → seções HTML (subconjunto controlado, tudo com escape)
sanitizar.py   → lista de permissões do HTML vindo do editor
exportacao.py  → HTML de impressão, PDF (Chrome headless), DOCX (python-docx)
views.py       → páginas (admin_required) e API JSON (_admin_api → 401/403 em JSON)
```

Para os demais fabricantes (Cisco, Juniper, Datacom, ZTE…) não há extrator dedicado. O documento usa
o `clientes/backup_parser.py` genérico: L2VPN, IPs, BGP e OSPF básicos.

### Extração (Huawei)

- **BGP com contexto de address-family e VRF**: peers globais habilitados por AF (`vpnv4`, `vpnv6`,
  `l2vpn-ad-family`); peers de VRF declarados dentro de `ipvX-family vpn-instance`; `group` declarado
  no topo **ou dentro da AF da VRF**; herança de `enable`, `reflect-client`, `route-policy`,
  `ip-prefix`/`ipv6-prefix` do grupo; `undo default ipv4-unicast`; `undo peer X enable`; `ignore`.
- **Route-policy completa**: `if-match` (ip-prefix, ipv6 prefix-list, community-filter, as-path),
  `apply local-preference`, `apply community` (+ `additive`/`none`), `apply as-path … additive`.
- **Prefix-lists v4 e v6** com `greater-equal`/`less-equal`.
- MPLS (`mpls te`, `mpls rsvp-te`, BFD de TE/RSVP), LDP e `remote-peer`, OSPF por processo/VRF,
  MTU e `mpls mtu` por interface, túneis TE (`explicit-path`), `tunnel-policy`.
- BNG: `ip pool … bas local`, `ipv6 prefix … delegation`, sub-interfaces de VE `l2-terminate` ligadas a
  VSI (PPPoE transportado), subvisão `bas`, domínios e grupos RADIUS (nomes apenas).
- Rotas estáticas com VRF, próximo salto e descrição.
- Gerência: telnet (explícito ou por `protocol inbound` na VTY), FTP, STelnet, versão SNMP, **contagem**
  de communities SNMP e de contas locais.

### Extração (MikroTik)

Identidade, versão, modelo, bonding, VLANs, pools, servidores PPPoE, RADIUS, NAT (netmap/src-nat,
faixas públicas e privadas, regras com `to-ports` = determinístico), BGP v6 (`peer`) e v7
(`connection`), OSPF, LDP/VPLS, rota default e serviços desabilitados. O export do RouterOS 7 abrevia
chaves com o mesmo prefixo da anterior (`remote.address=X .as=Y` = `remote.as=Y`); `_kv` expande isso.

### Classificação das sessões eBGP

Feita pela **política**, não pela descrição:

| Recebe | Anuncia | Classe |
|---|---|---|
| full/default | prefixos próprios / por community | Upstream (trânsito) |
| prefixos específicos | full/default | ISP downstream |
| nenhuma | full/default | ISP downstream (sessão só de anúncio) |
| prefixos específicos | prefixos específicos ou remove communities | Peering / troca de tráfego |

ASN privado → *Interno* (CGNAT/BNG) ou CDN pela descrição. Descrição com CDN/GGC/Globo/Netflix… →
*Parceiro de conteúdo*; PTT/IX → *IX / PTT*. Sessão em VRF que não é de Internet → *Cliente L3VPN*.
Só quando a política não decide, a descrição (UPSTREAM/DOWNSTREAM/CLIENTE) é usada. Cada linha guarda
a base da inferência. Uma descrição que contradiz a política vira o achado *Descrição divergente*.

Downstreams são agrupados por (equipamento, ASN), juntando IPv4 e IPv6. O mesmo ASN com entrega
diferente em POPs diferentes vira o risco "perfis distintos" (caso WEBNET Juruena × AFT).

### Achados automáticos

MTU heterogênea no backbone · backbone MPLS em MTU 1500 · BFD parcial · RSVP-TE com poucos
consumidores · sinalização L2VPN mista (LDP + BGP) · peers iBGP históricos · eBGP público sem
política · sessões desativadas · descrição divergente · **prefix-list DEFAULT que entrega mais que a
default** · RD/RT fora do ASN · communities com prefixo diferente do ASN de 4 bytes · telnet/FTP ·
serviços padrão do RouterOS · SNMP v2c · cobertura de backup · backups com mais de 7 dias · L2VPN com
ponta desconhecida · erros de grafia recorrentes · NTP ausente.

## Validação com a Startnet (cliente 90, backups de 17/09/2026)

41 backups de 51 acessos: coleta e análise em ~1,8 s, PDF em 1,6 s (1,5 MB), DOCX em 68 KB.
A geração reproduz o documento de referência:

- RRs em JNA e AFT (e também AYP e NE40-VS02, que têm `reflect-client`), 18 PEs com LSR-ID.
- INVINET/JNA: *Full routing IPv4*, LP 1000, communities `27648:669, :460, :4010, :6015, :7014`.
- NETULTRA AFT: full v4 / default v6; Monte Verde: default v4; GTR_NET em Monte Verde; CASTILHO e WWC em Colniza;
  EVOLUTION por rota estática `201.218.163.32/27 via 172.24.70.22`.
- WEBNET com perfis distintos em Juruena e AFT (sessão multihop só de anúncio).
- PPPoE via L2VPN até AFT (700–713, 717, 720 e 721, igual à seção 6.2 do documento manual), CGNAT `RTR-CGN-AFT-CEN-01` com 65 regras netmap
  determinísticas, `100.80.64.0/19` → `201.218.163.128/25`, BGP AS65532.
- VRF INVINET com RD/RT legado `27248:27063`; faixas de communities `27648:450–455`, `460–465`,
  `1010–1015`, `2010–2015`, `5010–5015`, `6010–6015`, `7010–7015` com a regra de prepend.

Divergência encontrada **a favor do sistema**: o documento manual registra MUNDONET e WEBNET em
Juruena como "Default v4/v6", mas a prefix-list `DEFAULT-V6` desse PE é `:: 0 less-equal 48`, ou seja,
**full routing IPv6**. O AS-IS gerado mostra a entrega real e abre o achado correspondente.

## Decisões e armadilhas

- **`crm_db` é SQL_ASCII.** O `JSONField` do Django serializa acentos como `í` e o `jsonb` recusa
  esses escapes nessa codificação (`unsupported Unicode escape sequence`). `metadados`, `secoes` e
  `coleta` usam `JSONTextoField`: JSON guardado num `TextField` com `ensure_ascii=False`, como o
  `TopologiaDiagrama.dados_json`. Não filtre por conteúdo desses campos.
- **`base.html` não tem `{% csrf_token %}`.** As duas telas incluem o token no próprio template; sem
  isso o POST só funcionaria se o cookie `csrftoken` já existisse por acaso.
- **Segurança do conteúdo.** Extratores não devolvem senha, chave, shared-key nem nome de community
  SNMP (só contagens). Todo texto de configuração passa por `escape` na composição. O HTML do editor
  é limpo no cliente (colar) e no servidor (`limpar_html`, lista de permissões), e é limpo de novo na
  renderização. O cabeçalho do PDF (`@page`) usa `_css_str`, não `escapejs`.
- **PDF**: Chrome headless (`--print-to-pdf`, perfil e `HOME` temporários, timeout de 90 s). Cabeçalho
  e rodapé usam margin boxes do `@page`, suportados desde o Chrome 131. O HTML não depende de rede.
- **DOCX**: no XML do Word, `tcBorders` precisa vir antes de `shd` na célula; na ordem inversa, o Word
  acusa "conteúdo ilegível". O sumário é estático (não depende de o Word atualizar campos); o rodapé
  usa os campos `PAGE`/`NUMPAGES`.
- **Coleta nunca apaga `BackupLog`.** Arquivo ausente vira lacuna ("Registro sem arquivo").
- **Dependência nova:** `python-docx==1.2.0` (instalado em `/opt/crm/venv`).

## Testes

- `projeto_rede/tests.py` (25): extratores Huawei/MikroTik com configs sintéticas, classificação,
  achados, famílias de community, escape, sanitização, numeração, DOCX válido, escape do cabeçalho
  CSS, permissões (Consultor 403/302, anônimo 401 em JSON), gerar → salvar (sanitizado, 409 em
  conflito) → revisão → recalcular → exportar → atualizar mantendo seções manuais → restaurar → excluir.
- `projeto_rede/tests_navegador.py` (1): o fluxo inteiro num Chrome headless real. Controle por
  `--remote-debugging-pipe` (fds 3/4) e `Emulation.setFocusEmulationEnabled`; as requisições da página
  são respondidas pelo Python via `Fetch.fulfillRequest`, porque o Chrome deste host não abre socket.
  É pulado sem Chrome.

```bash
/opt/crm/venv/bin/python manage.py test projeto_rede
```

## Próxima etapa: Change Plan TO-BE

O modelo já prevê isso: `DocumentoRede.tipo = 'change_plan'` e `documento_base` aponta para o AS-IS de
origem. O editor, as revisões e a exportação são genéricos; falta a composição das seções do TO-BE
(waves, pré-requisitos, rollback, janelas) a partir dos achados e riscos do AS-IS.
