# Racks e Conexões Físicas — Documentação Técnica

**App:** `racks/` · **Tela:** `racks/templates/racks/rack_builder.html` + `static/js/rack_builder.js`
**Adicionado em:** 2026-09-24

---

## Visão Geral

A topologia (`docs/topologia.md`) é o desenho **lógico** da rede. Os racks são o inventário
**físico**: em que rack e em que U cada equipamento está, e qual cabo liga qual porta a qual porta.

Pelo editor de topologia:

- **Botão "Racks"** na toolbar (ícone `fa-server`, ao lado de "Importar Hosts") abre a tela de racks
  do cliente. Se houver alteração não salva, pergunta e salva antes — a tela de racks lê a topologia
  **salva**. Não aparece no cenário TO-BE (lá a topologia é hipotética).
- **Bloco "Conexão física"** no painel de propriedades de cada enlace: diz se o enlace já tem cabo, se
  as duas pontas já estão montadas (botão **Criar conexão física**) ou qual ponta falta montar
  (botão **Montar no rack**). Os botões abrem a tela de racks já focada naquele enlace — com o
  formulário do cabo aberto quando dá para criar.

Na tela de racks:

| Área | O que faz |
|---|---|
| Toolbar | Voltar para a topologia (mesmo mapa/sub-mapa de onde veio), abas dos racks com ocupação `usados/total U`, **Criar rack**, alternar **Frente/Traseira** |
| Paleta — **Catálogo** | Tipos genéricos (roteador, switch, OLT, patch panel, DIO, PDU, nobreak, tampa cega…) com altura padrão em U |
| Paleta — **Da topologia** | Equipamentos físicos dos mapas do cliente (raiz e sub-mapas) + hosts do CRM ainda não desenhados, separados em "A montar" e "Já montados" (com rack e U) |
| Palco | O rack desenhado U a U (U1 embaixo, EIA-310), com numeração nos dois trilhos |
| Lateral — **Equipamento** | Propriedades do selecionado (nome, tipo, rack, U, altura, face, profundidade, host do CRM, fabricante/modelo, portas) e os cabos dele |
| Lateral — **Conexões** | Enlaces da topologia com a situação de cada um (filtros Prontos / Pendentes / Com cabo) e a lista de todos os cabos |

### Montar

- **Arrastar** um item da paleta até o rack: a prévia mostra `U10–U11` em verde, ou em vermelho com o
  nome de quem ocupa (`Ocupado: SW-CORE-01`) ou `Passa do topo`. O item "gruda" no cursor pelo topo.
- **Clicar** num item da paleta monta no **U mais alto livre** onde ele cabe (racks se enchem de cima
  para baixo na prática).
- **Arrastar um equipamento já montado** move de U, mantendo o ponto onde foi pego.
- Mover para **outro rack** do cliente é pelo campo "Rack" do painel.
- `Delete` remove o selecionado (confirma e avisa quantos cabos vão junto). `Esc` fecha diálogo/limpa seleção.
- Item da aba "Da topologia" que **já está montado** não arrasta: clicar leva até ele.

### Frente, traseira e profundidade

Cada equipamento tem `face` (frente/traseira) e `profundidade_total`:

- **Profundidade total** (roteador, switch, OLT, servidor, nobreak, bandeja…): ocupa **as duas faces**
  do U. Visto pela face oposta aparece hachurado com ventoinha ("traseira").
- **Raso** (patch panel, DIO, organizador, tampa, PDU): ocupa **só a sua face** — o mesmo U fica livre
  do outro lado. É o que permite uma PDU atrás de um patch panel.

Dois equipamentos colidem quando os U se sobrepõem **e** (algum dos dois é de profundidade total
**ou** estão na mesma face). A face de um item novo é a da vista atual.

### Conexões físicas (cabos)

- **A partir do enlace da topologia** (aba Conexões → "Criar cabo", ou o botão do painel do link):
  pontas = equipamentos montados que representam os nodes do enlace; portas = **Interface Lado A/B**
  do enlace; tipo de cabo e conector sugeridos pela **velocidade** (`catalogo.MEIO_DA_IFACE`: 1G/100M →
  UTP/RJ45, 10G+ e SFP → fibra monomodo/LC, GPON/XGS-PON → fibra/SC, microwave → coaxial); etiqueta =
  rótulo do enlace. Tudo editável antes de criar.
- **"Montar aqui"** num enlace pendente monta as pontas que faltam no rack aberto (tipo pelo
  `TIPO_DA_TOPOLOGIA`, no U mais alto livre).
- **Cabo manual**: entre quaisquer dois equipamentos, inclusive de racks diferentes e passivos (patch panel/DIO).
- O campo de porta sugere as interfaces do **backup** do host (mesmo endpoint do painel do link da
  topologia, só as físicas) ou `1..N` para equipamento sem host; continua texto livre.
- **Uma porta, um cabo**: `ge0/0/1` e ` GE0/0/1` são a mesma porta. Porta vazia não bloqueia.
- Portas **numéricas** (`1`, `2`…) acendem no espelho do equipamento na cor do cabo. Nome de interface
  (`ge0/0/7`) não acende — não dá para saber qual quadradinho físico ela é.
- Passar o mouse num cabo realça as duas pontas no rack.

### Situação de um enlace

| Situação | Quando |
|---|---|
| `pendente` | alguma ponta ainda não está montada em rack nenhum (`faltando` lista quais) |
| `pronta` | as duas pontas montadas, sem cabo |
| `criada` | já existe `ConexaoFisica` com o `topologia_link_id` do enlace |

Enlaces com **Internet, IX, nuvem, VM, texto, área ou grupo** numa das pontas são lógicos e não entram
na lista. Enlace que aparece em mais de um mapa (o mesmo `id` na cópia de borda de um sub-mapa) conta
uma vez, pelo mapa raiz.

---

## Modelo de Dados

```
Rack (cliente, nome, local, altura_u, observacoes)
 └─ RackEquipamento (rack, tipo, nome, u_inicial, altura_u, face, profundidade_total,
                     acesso?, topologia_node_id, fabricante, modelo, num_portas)
ConexaoFisica (cliente, ponta_a → RackEquipamento, porta_a, ponta_b, porta_b, meio, conector,
               cor, comprimento_m, identificacao, topologia_link_id, diagrama?)
```

- **Por que tabelas e não o `dados_json` da topologia:** o físico tem regra que o banco precisa segurar
  (dois equipamentos no mesmo U, uma porta com dois cabos, um host montado duas vezes) e um
  equipamento é o mesmo em todos os mapas/sub-mapas.
- **Ligação equipamento ↔ node da topologia:** `acesso` (host do CRM — nodes `crm_<id>`) ou
  `topologia_node_id` (node desenhado à mão). Cada um só pode estar montado **uma vez** por cliente
  (constraint `rack_equip_acesso_unico` + checagem em `services._vinculo`).
- **Ligação cabo ↔ enlace:** `topologia_link_id`, único por cliente (constraint
  `conexao_fisica_link_unico`, parcial — cabos manuais têm o campo vazio).
- Excluir rack leva os equipamentos; excluir equipamento leva os cabos dele (CASCADE). Excluir o host
  do CRM só desvincula (`SET_NULL`).
- Sem `JSONField` (o `crm_db` é SQL_ASCII — ver `projeto_rede.models.JSONTextoField`): tudo é coluna.

## Arquitetura

| Arquivo | Papel |
|---|---|
| `racks/catalogo.py` | **Única fonte** dos tipos (altura, cor, ícone, portas, profundidade, face padrão), meios de cabo, conectores, `TIPO_DA_TOPOLOGIA` e `MEIO_DA_IFACE`. Vai inteiro para a tela em `catalogo_para_tela()` — tipo novo entra aqui e aparece na paleta sem mexer no JS |
| `racks/services.py` | Todas as regras: `conflitos`/`validar_posicao`/`primeiro_u_livre`, montar/mover (com `select_for_update` no rack contra dois arrastes simultâneos), porta ocupada, `dispositivos`, `links_topologia`, `criar_conexao_do_link`, e `estado()` (o JSON inteiro da tela). Recusas saem como `ErroRack` com mensagem pronta para a tela |
| `racks/views.py` | Só HTTP. Decorator `_api(escrita)` responde JSON 401/403 (sem o 302 do login) e converte `ErroRack` em 400 |
| `static/js/rack_builder.js` | Classe `RackBuilder`. Toda ação faz POST e recebe `estado` de volta, que substitui o local e redesenha — a tela nunca fica fora de sincronia. A checagem de colisão do JS (`_motivoConflito`) é só para a cor da prévia; quem decide é o backend |
| `clientes/topologia_tipos.py` | Mapeamento função/tipo do host → tipo do device, extraído de `topologia_hosts` para os racks classificarem hosts ainda não desenhados com o mesmo critério do "Importar Hosts" |

A tela carrega também `static/js/topo_engine.js` (só dados) para usar o `TOPO_IFACES` do editor nos
rótulos de velocidade — uma tabela só.

`static/js/rack_builder.js` está reincluído no `.gitignore` (`!static/js/rack_builder.js`), como os JS
do editor de topologia.

## Permissões

Iguais às da topologia: ferramenta `topologia` habilitada (`modulo_habilitado_required`, e o mesmo
critério em JSON nas rotas AJAX) + `pode_acessar_cliente` (Consultor/Operador só na própria
instância). Login do portal em modo somente leitura (`acessos_somente_leitura`) vê tudo e não altera
nada (rotas de escrita dão 403; a tela esconde os controles).

## URLs

| Método | URL | Descrição |
|---|---|---|
| `GET` | `/racks/cliente/<id>/` | Tela (`?diagrama=` volta para esse mapa, `?link=` foca um enlace, `?rack=` abre um rack, `?embed=1` preservado) |
| `GET` | `/racks/cliente/<id>/estado/` | Estado inteiro: racks+equipamentos, cabos, dispositivos, enlaces |
| `GET` | `/racks/cliente/<id>/link/?link=<id>` | Situação de um enlace (painel do link na topologia) |
| `POST` | `/racks/cliente/<id>/racks/criar/` | Cria rack |
| `POST` | `/racks/rack/<id>/editar/` · `/excluir/` | Edita (recusa reduzir a altura cortando equipamento) / exclui |
| `POST` | `/racks/rack/<id>/equipamentos/criar/` | Monta (sem `u_inicial` = U mais alto livre) |
| `POST` | `/racks/equipamento/<id>/editar/` · `/excluir/` | Edita/move (inclusive `rack_id`) / remove |
| `POST` | `/racks/cliente/<id>/conexoes/criar/` | Cabo manual |
| `POST` | `/racks/cliente/<id>/conexoes/do-link/` | Cabo a partir do enlace `link_id` (campos opcionais sobrescrevem o sugerido) |
| `POST` | `/racks/conexao/<id>/editar/` · `/excluir/` | Edita / exclui cabo |

Toda rota de escrita devolve `{ok: true, estado: {...}}` ou `{ok: false, erro: "..."}`.

## Testes

- `racks/tests.py` — regras de espaço (faces, profundidade, topo, reduzir altura), vínculo único,
  situação dos enlaces, criação a partir do enlace, porta ocupada, CASCADE, permissões da API e o
  mapeamento de `topologia_hosts` depois da extração.
- `racks/tests_navegador.py` — ponta a ponta no Chrome headless (driver de
  `projeto_rede/tests_navegador.py`): botão da topologia → criar rack → arrastar com mouse real →
  prévia vermelha de conflito → criar cabo do enlace → painel do link na topologia. Com
  `RACK_SCREENSHOTS=<pasta>` salva as telas.
