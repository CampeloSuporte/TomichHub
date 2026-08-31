# Hosts liberados por Usuário — recorte de acesso dentro do mesmo cliente

**Data de implementação:** 2026-08-31
**Arquivos principais:** `usuario/models.py` (`UsuarioAcesso`), `usuario/perms.py`
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
**"Hosts liberados"** com os hosts do cliente ao qual ele está vinculado:

```
Admin abre Sistema → Usuário → edita o login "leivy" (cliente Startnet Provedor)
  └─ Seção "Hosts liberados" lista os 46 hosts do cliente, todos marcados
     └─ Desmarca 43, salva
        └─ Grava UsuarioAcesso(usuario=leivy, acesso=<host>) para os 3 marcados
           └─ Quando leivy loga: aba Acessos mostra 3 hosts, o combo do terminal
              mostra 3, os backups são os desses 3
              └─ URL direta de um host bloqueado responde 403
              └─ Outro login do mesmo cliente, sem restrição, continua vendo os 46
```

**Sem nenhum registro em `UsuarioAcesso` = vê todos os hosts do cliente.** Mesma escolha do
`UsuarioModulo`: ninguém perde acesso no deploy, e restringir é sempre ação explícita do
admin. Vale só para o **portal do cliente final** — Administrador, Consultor e Operador
respondem pelo cliente/instância inteiros e nunca são filtrados por host.

---

## Modelo

```python
class UsuarioAcesso(models.Model):          # usuario/models.py
    usuario = FK(User, related_name='acessos_permitidos')
    acesso  = FK('clientes.Acesso', related_name='usuarios_permitidos')
    criado_em = DateTimeField(auto_now_add=True)
    unique_together = ('usuario', 'acesso')
```

Migração: `usuario/migrations/0012_usuarioacesso.py`. É uma **lista de permitidos**, não de
bloqueados — de propósito: para um login já restrito, um host cadastrado depois nasce
**invisível**, e não liberado por descuido.

`acessos_permitidos_ids(user)` devolve o `set` de ids ou **`None`** quando não há restrição —
`None` é "vê tudo", diferente de `set()` (que a função nunca devolve).

---

## Regras de gravação (`_sincronizar_acessos_usuario`)

| Situação no form | O que grava | Por quê |
|---|---|---|
| Todos os hosts marcados | **Apaga** os registros | "Sem restrição" é a ausência de registro — e é o que faz host novo do cliente já nascer visível para esse login |
| Alguns marcados | Um registro por host marcado | O recorte propriamente dito |
| **Nenhum** marcado | Não mexe, e avisa na tela | "Zero host" é indistinguível de "sem restrição" nessa tabela; gravar liberaria tudo, o oposto do clique. Para tirar hosts do login, desmarque a ferramenta **Acessos** |
| POST sem `acessos_form_present` | Não mexe | Mesmo marcador de seção do `_sincronizar_modulos_usuario`: form incompleto (ou o modal de cadastro) nunca apaga seleção de ninguém |
| Id de host de outro cliente | Descartado | O POST é intersectado com os hosts do cliente vinculado |

O vínculo login ↔ cliente continua sendo feito na tela **Clientes** (campo "Usuário" ou
"Usuários adicionais"). Enquanto não existe vínculo não há o que escolher: o modal de
cadastro traz só o aviso explicando isso, e a seção do modal de edição mostra o mesmo texto.

---

## Onde a restrição é aplicada

Duas funções em `usuario/perms.py`, no mesmo lugar de sempre — nada de checagem solta:

| Função | Uso |
|---|---|
| `pode_acessar_acesso(user, acesso)` | Ações sobre **um** host. É `pode_acessar_cliente` + o recorte por host |
| `filtrar_acessos_visiveis(user, qs)` | Telas e APIs que **listam** hosts. Não substitui o filtro por cliente — aplique os dois |

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

- Lista rolável com um checkbox por host — `tipo`, IP, protocolo e função.
- Botões **Marcar todos** / **Desmarcar todos**.
- Texto de ajuda dizendo o que "todos marcados" significa, e avisando quando o login está
  restrito hoje.

A lista é buscada **sob demanda** ao abrir o modal, em `GET /auth/usuarios/<id>/hosts/`
(`views.hosts_usuario`, protegida por `pode_gerenciar_usuarios_required` + o escopo de
`usuarios_gerenciaveis_por`). Embutir os hosts de cada usuário no HTML da lista fazia a
página saltar de **254 KB para 596 KB** — dezenas de logins de portal, dezenas de hosts cada,
para alimentar um modal que abre um usuário por vez. Sob demanda a página voltou a 260 KB e a
lista vem sempre atual.

Enquanto a resposta não chega — e quando o login não tem cliente vinculado — o marcador
`acessos_form_present` fica `disabled`, então um "Salvar" apressado não tem como apagar a
seleção existente.

---

## Testes

`usuario.tests.HostsLiberadosPortalTest` (9 testes):

- Sem registro → vê os 3 hosts do cliente; `pode_acessar_acesso` verdadeiro.
- Seleção parcial → lista e permissão caem para o host marcado; o outro dá `False`.
- Marcar todos → volta a zero registro, e um host criado **depois** já é acessível.
- Host de outro cliente no POST → descartado.
- Nada marcado → mantém a seleção anterior (e avisa).
- POST sem o marcador de seção → não mexe.
- Back-office (admin) → nunca filtrado por host.
- Painel do cliente logado como o portal → só o host liberado aparece no HTML.
- `GET /clientes/acessos/buscar/<id>/` → 200 no liberado, **403** no bloqueado.

Suíte do app: 17 testes, OK.

Conferência no banco real, com o login `leivy` (cliente Startnet Provedor, 46 hosts):
restrito a 3 hosts, a aba Acessos passou a mostrar 3, `/clientes/terminal/acessos/` devolveu
3, `buscar`/`comentários`/`proxy web` responderam 403 no host bloqueado e 200/302 no
liberado, e o WinBox 403 × 200. Depois do teste o login foi devolvido ao estado original
(sem restrição).
