# HLD, cenário TO-BE e Change Plan (app `projeto_rede`)

> Implementado em 17/09/2026, como continuação do [AS-IS](PROJETO_REDE_ASIS.md). Exclusivo do Administrador.
> Modelo **reutilizável**: vale para qualquer cliente, parametrizado pelo ASN, pelos prefixos e pelo
> cenário de cada um.

## O fluxo

```
HLD (convenção) ─┐
AS-IS (backups) ─┼─► motor de diferenças (tobe.py) ─► Change Plan TO-BE (waves 0–9)
Cenário TO-BE   ─┤
Mapeamentos     ─┘
```

A tela **Arquitetura de rede** (`/projetos-rede/cliente/<id>/`, botão *AS-IS da Infraestrutura* na aba
Documentação de Rede) mostra os quatro passos, com um bloco para cada:

| Passo | O que é | Onde se edita |
|---|---|---|
| 1. HLD | Convenção estruturada (VRFs/Service IDs, RTs, communities, LP, IX, segurança, endereçamento, RRs, BNGs, underlay, MTU alvo) | Formulário **Convenção** + editor do documento |
| 2. AS-IS | Estado atual dos backups | Editor do documento |
| 3. Cenário TO-BE | Clone de um mapa da topologia editado como a rede deve ficar | Editor de topologia em modo cenário |
| 4. Change Plan | Waves com itens `atual → alvo`, lotes, gates e decisões pendentes | **Fontes e mapeamentos** + editor do documento |

Os três documentos usam o mesmo editor, as mesmas revisões e a mesma exportação PDF/DOCX. O botão
de recálculo muda conforme o tipo: *Atualizar com backups*, *Atualizar pela convenção* ou
*Recalcular o plano*. Seções escritas à mão são sempre preservadas.

## 1. HLD — convenção reutilizável

- **Novo HLD** cria o documento a partir de `convencao.convencao_padrao()`, que é o HLD Startnet v1.0
  parametrizado. Com backups, `sugerir_parametros()` preenche:
  - ASN e o /32 IPv6 (o /40 de infraestrutura é o `ff00::/40` desse bloco);
  - RR01/RR02: quem mais reflete clientes, com NE antes de switch;
  - BNG01/BNG02: BNGs centrais que também são RR.

  O ASN digitado no botão tem prioridade.
- **Convenção** (`/projetos-rede/documento/<id>/convencao/`): campos e tabelas editáveis, com prévia
  da community formatada em cada faixa. Salvar recalcula as seções do HLD e guarda uma revisão antes.
- **Formato das communities pelo ASN**: com ASN de 4 bytes (> 65535) a community padrão
  `ASN:valor` (RFC 1997) não existe.
  - Tudo é escrito como large community `ASN:valor:0` (RFC 8092), e o documento explica o porquê.
  - Blackhole: `ASN:666:0` interno e `65535:666` para operadoras (RFC 7999).
  - RTs continuam `ASN:valor`, o que é válido enquanto o valor couber em 16 bits (a validação avisa se passar).
- **Validações** (`convencao.validar`), exibidas no HLD, no formulário e nas pendências do plano:
  - formato das communities;
  - RT acima de 65535;
  - uso de `198.18.0.0/15` (faixa de benchmark da RFC 2544, listada em bogons).

## 2. Cenário de topologia TO-BE

- **Novo cenário** clona o mapa escolhido para `CenarioTopologia`.
  - O mapa **não** vai para `TopologiaDiagrama`: o editor resolve o mapa principal por
    `pai IS NULL` e o AS-IS lê todos os mapas; um clone ali quebraria os dois.
  - `submap_id` é removido do clone.
- **Editor**: `/projetos-rede/cenario/<id>/` reaproveita `topologia_editor.html` + `topo_main.js` com
  `window.TOPO_CENARIO`:
  - Salvar grava no cenário (`cenario_salvar`); agrupar e sub-mapas ficam desativados;
  - uma faixa avisa que o mapa real não muda.
- **Painel do nó (bloco TO-BE)**, guardado em `node.tobe`:
  - estado: manter / novo / remover;
  - papéis: RR01, RR02, PE, P, BNG01–03, BRAS-POP, CGNAT-EXTERNO, BORDA, CE…, podendo ser **vários**
    (o NE8000 de JNA é RR01 **e** BNG01);
  - loopback alvo e BNG de destino.
- **Painel do enlace**, guardado em `link.tobe`: estado, papel (principal / alternativo / último
  recurso), MTU alvo, custo OSPF e lote da Wave 1.
- **No canvas**: nó novo com borda verde tracejada, nó a remover esmaecido com borda vermelha, crachá
  com os papéis e enlaces novos/removidos/alternativos com traço próprio.
- Tudo que se desenha dentro do cenário (nó ou enlace) nasce como **novo**.

## 3. Change Plan TO-BE

### Criação e mapeamentos

**Novo Change Plan** (`tobe_novo`): escolha o HLD, o AS-IS de referência e o cenário (todos opcionais).
Depois de gerar, abre **Fontes e mapeamentos** (`tobe_mapeamentos`), que já vem com sugestões e as
pendências do plano:

| Mapeamento | Sugestão automática | Uso |
|---|---|---|
| VRF legada → catálogo | INTERNET→VRF-INTERNET, B2C/ACESSO→VRF-ACCESS, B2B→VRF-BUSINESS, CGNAT→VRF-CGNAT, gerência/câmeras→VRF-INFRA, BLOQUEIO/PORTAL→**eliminar**, demais→**L3VPN** | Wave 4/5/9 |
| Contratante de cada L2VPN | Nome do serviço sem prefixos (`TrVlan430.`, `L2L-`, interfaces…); a palavra que se repete em vários serviços vence (INVINET) | Wave 5 (`TR-<CONTRATANTE>-L2-<SEQ>`) |
| Papéis-alvo por equipamento | RR01/RR02 e BNG01/BNG02 sugeridos; vazio = sem papel. O cenário tem prioridade | Waves 3, 6, 7 |
| BNG de destino por POP | — | Wave 6 |
| Community legada → HLD | Blackhole→666, internas→21000, clientes→21001, CDN→20100/30300, prepend→30200 | Wave 4 |
| Serviços críticos | `INVINET, SICREDI` | Prioridade 4 na Wave 5 |

Valor salvo vence a sugestão, inclusive quando está vazio. Aplicar recalcula o plano.

### O que o motor gera por wave (`tobe.gerar_plano`)

| Wave | Itens |
|---|---|
| 0 | Equipamentos sem backup ou com backup antigo; enlaces sem interface/IP; equipamentos novos do cenário |
| 1 | Inventário de MTU observado por camada (backbone, MPLS MTU, VSI/PW, PPP MTU/MRU/MSS); um item por enlace de backbone com a MTU das duas pontas; lotes; decisão pendente do LLD se não houver MTU alvo |
| 2 | Processo/área OSPF, router-id ≠ LSR-ID, `import-route` no IGP, `ospf ldp-sync` ausente, BFD ausente ou com timers fora do padrão, P2P fora de /30, loopback alvo do cenário ou fora do bloco core, RSVP-TE a justificar, enlaces novos ou com custo |
| 3 | iBGP RR01↔RR02; PE/BNG sem sessão com RR01 e RR02 ou sem as famílias do HLD; sessões PE-a-PE; RRs extras; peers históricos |
| 4 | De-para de VRFs com RD `LSR-ID:ID` e RT do catálogo por PE; eliminação de VRFs de bloqueio; de-para de communities; policies fora de `RP-AS…` |
| 5 | Cada L2VPN (exceto PPPoE) → `TR-…-L2-NNN`, ID 7001+ e RT 17001+, por prioridade; cada VRF-L3VPN → `TR-…-L3-NNN`, ID 6001+ |
| 6 | POPs com BNG remoto → BNG de destino; PPPoE transportado → VRF-ACCESS; pool CGNAT fora do bloco do BNG (JNA usa `100.80.0.0/18`, que é do BNG02); tamanho de PD; BNG fora da VRF-ACCESS |
| 7 | Card de serviço por BNG (detecta `service-instance-group`); cada CGNAT externo com pools a reproduzir e BNG do mesmo POP |
| 8 | Cada sessão eBGP de Internet → UPLINKnn/CONTENTnn/IXnn/DOWNSTREAM, community da identidade, LP da classe, nome de policy e o que falta (bogons, max-prefix, policy) |
| 9 | CGNATs externos, BNGs remotos migrados, VRFs substituídas, LDP remote-peers, túneis TE, nós/enlaces marcados para remoção, communities legadas |

- **Papéis inferidos x decididos**: sem papel explícito, o PE/P/BNG-remoto vem do AS-IS e aparece como
  "(inferido do AS-IS)". Só um BRAS-POP **marcado** por você fica mantido como "BRAS de POP pequeno".
- **Lotes da Wave 1**: o do enlace, quando definido. Senão, a distância em saltos até RR01/RR02;
  alternativos e de último recurso vão para o lote final.
- **MTU do enlace**: a interface é localizada **pelo IP do enlace** (Vlanif, subinterface), com
  alternativa pelo nome. Subinterface herda a MTU da porta; Vlanif não. A porta física aparece à parte,
  com `jumboframe`.

### Documento

As seções (`composicao_tobe.SECOES`) seguem o Change Plan Startnet:
- controle e governança, visão das waves com gate principal, papéis-alvo e decisões pendentes;
- Wave 0 a 9 (texto padrão do programa + tabela de itens);
- comunicação, checklist, critério global e referências (HLD, AS-IS, cenário e nomes dos arquivos de
  backup usados).

A capa usa o destaque **PRIORIDADE CRÍTICA** (o título do destaque agora é editável em qualquer
documento).

## Decisões e armadilhas

- **Numeração no DOCX**: listas numeradas usam número escrito no texto. O estilo "List Number" do Word
  compartilha uma numeração no documento inteiro, o que gerava "11., 27., 33." no exemplo original.
- **Bug corrigido no extrator**: o IP de interface era guardado como endereço de **rede**
  (`172.24.64.0/29`). Agora é `ip_interface` (`172.24.64.1/29`), o que também melhora o mapa
  IP→equipamento do AS-IS.
- **Extrator Huawei** passou a ler `ospf ldp-sync`, `ospf cost`, timers de BFD (interface e
  `bfd all-interfaces`), `import-route` no OSPF, `ppp mru`, `tcp adjust-mss` e `jumboframe`.
- **`topo_main.js` (`?v=48`)**: tudo de cenário fica atrás de `TOPO_CENARIO`; o editor normal não muda.
- **Migração** `projeto_rede/0002_hld_tobe_cenario`: campo `dados` em `DocumentoRede`, tipo `hld` e
  tabela `CenarioTopologia`. É aditiva; documentos existentes seguem válidos.

## Validação com a Startnet (17/09/2026)

HLD → cenário (JNA = RR01+BNG01, AFT = RR02+BNG02, CGNAT AFT a remover) → Change Plan, com os dados
reais e numa transação desfeita. O plano teve 346 itens:

- **Wave 1**: 20 enlaces e o inventário de MTU 1500/2000/9000/9198, MPLS MTU 1590 em Cotriguaçu, VSI
  1550 e PPP MTU/MRU 1500 com MSS 1440, os mesmos valores do documento manual.
- **Wave 7**: card já configurada em JNA (`service-instance-group CGNAT`).
- **Wave 8**: UPLINK01–04, CONTENT01–02 e IX01, com LP e max-prefix faltando.

Tempos: plano em ~1,2 s, PDF do plano em 1,6 s (1,2 MB), HLD em 0,8 s.

## Testes

- `projeto_rede/tests_tobe.py` (16): convenção (formatos, RT, /40 IPv6, escape), motor com e sem
  cenário (papéis múltiplos, estados, lotes, loopback, underlay, RR, VRF, borda, BNG, CGNAT,
  mapeamentos), numeração do DOCX e o fluxo HTTP HLD → convenção → cenário → plano → mapeamentos,
  com permissões.
- `projeto_rede/tests_navegador.py::CenarioNavegadorTest`: o editor de topologia normal intacto e o modo
  cenário num Chrome real (painel TO-BE, crachá, estilos, gravação só no cenário).
