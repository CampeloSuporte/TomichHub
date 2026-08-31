# Hosts liberados por Usuário — recorte de acesso dentro do mesmo cliente

**Data de implementação:** 2026-08-31
**Arquivos principais:** `usuario/models.py` (`UsuarioAcesso`, `UsuarioFuncao`), `usuario/perms.py`
(`pode_acessar_acesso`, `filtrar_acessos_visiveis`), `usuario/views.py`
(`_hosts_do_usuario`, `_sincronizar_acessos_usuario`, `hosts_usuario`),
`usuario/templates/cadastrar_usuario.html`, `clientes/views.py`
**Status:** ✅ Produção

---

## Visão Geral

[MODULOS_CLIENTE.md](MODULOS_CLIENTE.md) recorta o portal do cliente **por aba**
(`UsuarioModulo`): esse login vê Backups, aquele não. Faltava o recorte **por host**: o
cliente tem 46 equipamentos cadastrados e quer que um login de portal alcance só três deles.

Agora, em **Sistema → Usuário**, editar um login do tipo *Cliente* mostra a seção
**"Hosts liberados"**, com três modos de acesso:

| Modo | O que grava | Quando usar |
|---|---|---|
| **Todos os hosts do cliente** (padrão) | nada — ausência de registro | sem restrição |
| **Somente as funções marcadas** | `UsuarioFuncao` | "esse login só cuida das OLTs" |
| **Somente os hosts marcados** | `UsuarioAcesso` | lista fechada de equipamentos |

```
Admin abre Sistema → Usuário → edita o login "leivy" (cliente Startnet Provedor)
  └─ Seção "Hosts liberados" → modo "Somente as funções marcadas"
     └─ Lista as funções que os hosts DESSE cliente usam: BRAS (14), SWITCH L3 (15),
        CGNAT (2), ROTEADOR PE (4)...  → marca BRAS, salva
        └─ Grava UsuarioFuncao(usuario=leivy, funcao=BRAS)
           └─ Quando leivy loga: aba Acessos mostra os 14 BRAS, o combo do terminal
              mostra 14, os backups são os desses 14
              └─ URL direta de um host de outra função responde 403
              └─ BRAS cadastrado amanhã aparece sozinho, sem reeditar o usuário
              └─ Outro login do mesmo cliente, sem restrição, continua vendo os 46
```

**Sem registro em nenhuma das duas tabelas = vê todos os hosts do cliente.** Mesma escolha do
`UsuarioModulo`: ninguém perde acesso no deploy, e restringir é sempre ação explícita do
admin. Vale só para o **portal do cliente final** — Administrador, Consultor e Operador
respondem pelo cliente/instância inteiros e nunca são filtrados por host.

### Função é regra; host é lista

A diferença que importa na hora de escolher o modo:

- **Função**: host novo com uma função liberada **entra sozinho**. É o jeito certo de dizer
  "esse técnico cuida das OLTs" — o cadastro de uma OLT nova não exige reeditar ninguém.
- **Host**: a seleção é um retrato. Host novo **não** entra até alguém marcá-lo.

Host **sem função cadastrada** nunca entra pelo modo função (não casa com regra nenhuma) — o
modal avisa quantos hosts do cliente estão nessa situação. Para liberar um deles, use o modo
por host.

---

## Modelos

```python
class UsuarioAcesso(models.Model):          # usuario/models.py
    usuario = FK(User, related_name='acessos_permitidos')
    acesso  = FK('clientes.Acesso', related_name='usuarios_permitidos')
    unique_together = ('usuario', 'acesso')

class UsuarioFuncao(models.Model):
    usuario = FK(User, related_name='funcoes_permitidas')
    funcao  = FK('funcao_equipamento.Funcao_equipamento', related_name='usuarios_permitidos')
    unique_together = ('usuario', 'funcao')
```

Migrações: `usuario/migrations/0012_usuarioacesso.py` e `0013_usuariofuncao.py`.

`UsuarioAcesso` é uma **lista de permitidos**, não de bloqueados — de propósito: para um login
restrito host a host, um equipamento cadastrado depois nasce **invisível**, e não liberado por
descuido. `UsuarioFuncao` é o oposto por natureza (é regra), e é justamente por isso que o
modo existe separado, em vez de um botão que só marcaria os checkboxes daquela função.

`acessos_permitidos_ids(user)` e `funcoes_permitidas_ids(user)` devolvem o `set` de ids ou
**`None`** quando não há registro — `None` é "não restringe por aqui", diferente de `set()`
(que as funções nunca devolvem).

**As duas tabelas se somam** (união) em `pode_acessar_acesso`: com registro nas duas, o login
vê os hosts das funções liberadas **mais** os hosts liberados individualmente. A tela grava um
modo de cada vez — trocar de modo limpa a tabela do outro — mas a permissão não depende disso,
então uma união montada direto no banco (ou por uma tela futura) funciona sem tocar em perms.

---

## Regras de gravação (`_sincronizar_acessos_usuario`)

| Situação no form | O que grava | Por quê |
|---|---|---|
| Modo **todos** | **Apaga** as duas tabelas | "Sem restrição" é a ausência de registro — e é o que faz host novo do cliente já nascer visível para esse login |
| Modo **função**, com funções marcadas | Um `UsuarioFuncao` por função (e limpa `UsuarioAcesso`) | O recorte por função; os três modos são exclusivos na tela |
| Modo **host**, com hosts marcados | Um `UsuarioAcesso` por host (e limpa `UsuarioFuncao`) | O recorte host a host |
| Modo **host** com todos os hosts marcados | **Apaga** os registros | Equivale a "todos", e é gravado como tal |
| **Nada** marcado (função ou host) | Não mexe, e avisa na tela | "Zero host" é indistinguível de "sem restrição" nessas tabelas; gravar liberaria tudo, o oposto do clique. Para tirar hosts do login, desmarque a ferramenta **Acessos** |
| POST sem `acessos_form_present` | Não mexe | Mesmo marcador de seção do `_sincronizar_modulos_usuario`: form incompleto (ou o modal de cadastro) nunca apaga seleção de ninguém |
| Id de host ou função de outro cliente | Descartado | O POST é intersectado com os hosts (e as funções em uso) do cliente vinculado |

O vínculo login ↔ cliente continua sendo feito na tela **Clientes** (campo "Usuário" ou
"Usuários adicionais"). Enquanto não existe vínculo não há o que escolher: o modal de
cadastro traz só o aviso explicando isso, e a seção do modal de edição mostra o mesmo texto.

---

## Onde a restrição é aplicada

Duas funções em `usuario/perms.py`, no mesmo lugar de sempre — nada de checagem solta:

| Função | Uso |
|---|---|
| `pode_acessar_acesso(user, acesso)` | Ações sobre **um** host. É `pode_acessar_cliente` + o recorte (host liberado **ou** host de função liberada) |
| `filtrar_acessos_visiveis(user, qs)` | Telas e APIs que **listam** hosts — mesmo critério, como `Q(id__in=...) | Q(funcao_id__in=...)`. Não substitui o filtro por cliente: aplique os dois |

Os ~26 pontos que faziam `pode_acessar_cliente(request.user, acesso.cliente)` passaram a
chamar `pode_acessar_acesso(request.user, acesso)` — `clientes/views.py` (buscar acesso,
backup manual, WinBox, RDP, WebFig, ping, traceroute, comentários, auditoria de sessão, proxy
web, interfaces/VLANs/L2VPN do backup, OLT PON), `clientes/script_views.py`,
`clientes/bgp_views.py`, `home/views.py` e o **WebSocket do terminal SSH**
(`clientes/consumers.py`). Como todos já resolviam o `Acesso` antes de checar, a troca é
mecânica e não muda nada para o back-office.

Listagens filtradas com `filtrar_acessos_visiveis`:

- `listar_clientes` — aba **Acessos**, o combo de **Função** e o aviso de backup com erro
  (senão o host bloqueado apareceria pela borda).
- `listar_acessos_terminal` (`/clientes/terminal/acessos/`) — alimenta o Terminal SSH, o
  WinBox e os combos de host de outras telas.
- `listar_backups_cliente` — o arquivo de backup **é** a configuração do equipamento, então
  segue o mesmo recorte.

### Limite conhecido

A auto-documentação do IPAM (`ipam_analisar_backups`) varre os backups de **todos** os hosts
do cliente. Ela não lista host nem dá acesso a equipamento — alimenta a documentação de rede,
que é do cliente como um todo — mas um login restrito pode ver ali IP/VLAN de um host que não
enxerga na aba Acessos. Se isso incomodar, o filtro é o mesmo `filtrar_acessos_visiveis`.

---

## Interface

`usuario/templates/cadastrar_usuario.html`, modal de **edição**, seção "Hosts liberados"
(mesma regra de visibilidade dos módulos: só aparece com o tipo *Cliente* selecionado):

- Rádio com os três modos, cada um com uma linha explicando a diferença; o modo gravado hoje
  vem pré-selecionado (`modo` no JSON: `todos` / `funcao` / `host`).
- Modo **função**: lista rolável com as funções que os hosts **daquele cliente** usam, cada uma
  com a contagem de hosts. A lista global de `Funcao_equipamento` é da plataforma inteira e
  encheria a tela de opção que não casa com host nenhum do cliente.
- Modo **host**: lista rolável com um checkbox por host — `tipo`, IP, protocolo e função.
- **Marcar todos** / **Desmarcar todos** em cada uma das duas listas.
- Rodapé com o nome do cliente e, quando existem, quantos hosts estão **sem função** (esses não
  entram pelo modo função).

A lista é buscada **sob demanda** ao abrir o modal, em `GET /auth/usuarios/<id>/hosts/`
(`views.hosts_usuario`, protegida por `pode_gerenciar_usuarios_required` + o escopo de
`usuarios_gerenciaveis_por`). Embutir os hosts de cada usuário no HTML da lista fazia a
página saltar de **254 KB para 596 KB** — dezenas de logins de portal, dezenas de hosts cada,
para alimentar um modal que abre um usuário por vez. Sob demanda a página voltou a 260 KB e a
lista vem sempre atual.

Enquanto a resposta não chega — e quando o login não tem cliente vinculado — o marcador
`acessos_form_present` fica `disabled` e o rádio some, então um "Salvar" apressado não tem como
apagar a seleção existente nem escolher modo sobre lista vazia.

---

## Testes

`usuario.tests.HostsLiberadosPortalTest` (9 testes) — recorte host a host:

- Sem registro → vê os 3 hosts do cliente; `pode_acessar_acesso` verdadeiro.
- Seleção parcial → lista e permissão caem para o host marcado; o outro dá `False`.
- Marcar todos → volta a zero registro, e um host criado **depois** já é acessível.
- Host de outro cliente no POST → descartado.
- Nada marcado → mantém a seleção anterior (e avisa).
- POST sem o marcador de seção → não mexe.
- Back-office (admin) → nunca filtrado por host.
- Painel do cliente logado como o portal → só o host liberado aparece no HTML.
- `GET /clientes/acessos/buscar/<id>/` → 200 no liberado, **403** no bloqueado.

`usuario.tests.FuncoesLiberadasPortalTest` (10 testes) — recorte por função:

- Liberar OLT → só as duas OLTs ficam visíveis; o BRAS dá `False`.
- OLT criada **depois** já é acessível (a regra vale para host novo).
- Host sem função não entra nem com todas as funções marcadas.
- Trocar para o modo host limpa as funções; modo "todos" limpa as duas tabelas.
- Função de outro cliente no POST → descartada.
- Nenhuma função marcada → mantém a seleção anterior.
- União: registro nas duas tabelas → OLTs **mais** o host avulso liberado.
- Painel do cliente e `buscar/<id>` respeitando a função (200 na OLT, **403** no BRAS).
- JSON do modal com `modo='funcao'`, contagem por função e `hosts_sem_funcao`.

Suíte do app: 27 testes, OK.

Conferência no banco real, com o login `leivy` (cliente Startnet Provedor, 46 hosts):

- Restrito a 3 hosts (modo host): a aba Acessos passou a mostrar 3, `/clientes/terminal/acessos/`
  devolveu 3, `buscar`/`comentários`/`proxy web` responderam 403 no host bloqueado e 200/302 no
  liberado, e o WinBox 403 × 200.
- Restrito à função **BRAS** (modo função): 14 hosts visíveis, todos com função BRAS no combo do
  terminal, e 403 num host de outra função.

Depois dos testes o login foi devolvido ao estado original (sem restrição).
