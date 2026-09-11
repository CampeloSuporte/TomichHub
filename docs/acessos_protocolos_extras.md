# Protocolos extras de acesso no host

## O que é

Cada host (`clientes.Acesso`) tem um acesso **padrão**, com o protocolo e a porta do cadastro. A aba
**+** do card cadastra **outros protocolos no mesmo IP**, com as mesmas credenciais. Exemplo: um
equipamento com HTTP como padrão e SSH extra na porta 22. Quando há mais de uma forma de entrar, o
botão **Acessar** abre no meio do card um painel para escolher por onde acessar.

| Parte | Onde |
|---|---|
| Modelo | `AcessoProtocolo` (migração `0115_acessoprotocolo`); `Acesso.porta_ssh()`, `Acesso.aplicar_protocolo_extra()`, `Acesso.host_eh_privado` |
| Cadastro | `adicionar_protocolo_acesso`, `remover_protocolo_acesso` (`clientes/views.py`) |
| Terminal SSH/Telnet | `SSHConsumer.receive` / `conectar_acesso` (`clientes/consumers.py`), `terminal.html` |
| RDP | `WinboxVNCConsumer.conectar_vnc` (`?pid=`), `winbox.html` |
| Web HTTP/HTTPS | `proxy_web_acesso` + `_falha_proxy_web` (views), `abrirWebProxyComFallback` (`static/js/terminal_tab_manager.js`) |
| Backup | `realizar_backup` (views) |
| Card | `clientes/templates/listar.html` (card e bloco "PROTOCOLOS EXTRAS DO HOST") |
| Testes | `clientes/tests_acesso_protocolos.py` |

## Modelo

| Campo | Descrição |
|---|---|
| `acesso` | FK para `Acesso`, `related_name='protocolos_extras'`, `CASCADE` |
| `protocolo` | `SSH`, `TELNET`, `HTTP`, `HTTPS` ou `RDP` (`AcessoProtocolo.PROTOCOLOS`) |
| `porta` | 1 a 65535 |
| `criado_em` | automático |

`unique_together = (acesso, protocolo, porta)`. O modelo não guarda IP nem credencial: tudo vem do
`Acesso`. Winbox não entra aqui porque já tem campo próprio (`Acesso.winbox`). No admin, os extras
aparecem inline no `Acesso`.

## Cadastro pela aba "+"

A aba **+** ao lado de "Padrão" abre no card, sem modal, uma linha com **Hostname/IP** (só leitura,
é o IP do padrão), **Porta**, **Protocolo** e o botão **+**.

- A porta é sugerida pelo protocolo (22, 23, 80, 443, 3389) enquanto o campo estiver vazio ou com a
  sugestão de outro protocolo. Host web começa sugerindo SSH; host SSH/Telnet começa sugerindo HTTPS.
- **Enter** salva e **Esc** fecha.
- `POST /clientes/acessos/<id>/protocolos/adicionar/` (`protocolo`, `porta`) devolve JSON. Responde
  400 para protocolo fora da lista, porta fora de 1 a 65535, protocolo e porta iguais aos do padrão,
  e protocolo e porta já cadastrados no host.
- Os extras aparecem na linha **Outros acessos** (`SSH 22 ×`). O × chama
  `POST /clientes/acessos/protocolos/<id>/remover/`.
- Permissão: `@modulo_habilitado_required('acessos')` e `pode_acessar_acesso`, as mesmas regras de
  quem vê o host. Sessão expirada vira 302 para o login, e o JS mostra "Sessão expirada ou sem
  permissão".
- A busca de acessos exibe **cópias** dos cards. Por isso o JS acha formulário, chips e painel a
  partir do card clicado (`closest('.card')`), nunca por `id`. O × dos chips criados na hora é um
  atributo `onclick`, que sobrevive à cópia.

## Acessar: escolha no meio do card

O painel aparece quando o host tem **protocolo extra ou porta Winbox**. O card inteiro escurece e as
opções ficam no centro:

- o padrão (`SSH · porta 22002 · padrão`);
- cada protocolo extra;
- **Winbox** (Web 4.2) e **Winbox 3.43** (legado, `/clientes/winbox/<id>/?v=3`), quando o host tem
  porta Winbox.

Clique na parte escurecida, no **×** ou **Esc** fecham o painel. O foco vai para a primeira opção, e
o painel só existe no DOM enquanto está aberto. Host sem extra e sem Winbox continua abrindo direto
no **Acessar**.

A barra de ícones do card não tem mais `</>` (terminal), Winbox, 3.43 nem interface web (proxy):
tudo sai pelo **Acessar**. Os dois botões grandes de Winbox Web embaixo do Acessar também saíram.

## Rota de cada protocolo

A regra é a do acesso padrão: **IP privado passa pelo CRM, IP público vai direto.**

| Protocolo | IP privado | IP público |
|---|---|---|
| SSH / Telnet | terminal do CRM via proxy SSH ou OpenVPN do cliente | terminal do CRM direto |
| RDP | `/clientes/rdp/<id>/?pid=<extra>` via proxy SSH ou OpenVPN | a mesma página, direto |
| HTTP / HTTPS | proxy web do CRM; se falhar, direto no navegador | direto no navegador |
| Winbox | Winbox Web (4.2 ou 3.43) via proxy ou OpenVPN | Winbox Web direto |

"Privado" segue o `ipaddress.is_private` do Python: `Acesso.host_eh_privado` no servidor e
`_hostEhPrivado` no JS, com o mesmo critério. Host com caminho fixo (`198.18.1.13/zabbix`) é
avaliado pelo IP.

### SSH e Telnet de protocolo extra

1. O card chama `acessarProtocoloExtra`, que abre o terminal com `protocolo_id` no `acessoData`.
2. O `terminal.html` manda `protocolo_id` no `connect` do WebSocket. A aba mostra o sufixo `[SSH 22]`.
3. `SSHConsumer.conectar_acesso` chama
   `acesso.aplicar_protocolo_extra(pid, permitidos=('SSH', 'TELNET'))`, que troca porta e protocolo
   **só em memória**, sem salvar. Todo o caminho existente (proxy, OpenVPN, direto, Huawei, Parks,
   SSH legado) continua lendo `acesso.porta`.
4. No extra, SSH ou Telnet é o que foi cadastrado. No padrão, o protocolo continua deduzido pela
   porta (`detect_protocol`), como sempre foi.

Na sessão compartilhada, a chave no registro é `(acesso_id, protocolo_id)` no extra e `acesso_id` no
padrão (`_SharedTerminalSession.chave`). Assim, quem entra na sessão do padrão não cai no shell da
porta extra. O **link externo** só existe no acesso padrão, porque o `TerminalLinkExterno` guarda só
o host; pedir link numa sessão de extra devolve erro explícito.

### Web com IP privado: proxy primeiro, direto se falhar

Vale para o padrão e para os extras HTTP/HTTPS com IP privado (`abrirWebProxyComFallback`):

1. A aba nova abre **no clique**, com "Conectando via proxy do CRM a host:porta…". Depois de um
   `await` o navegador bloquearia o popup.
2. O JS faz `fetch` em `/clientes/acessos/<id>/web/<porta>/<scheme>/`.
3. Resposta do **equipamento**, com qualquer status (200, 401, 404…), é proxy funcionando: a aba vai
   para o proxy.
4. É **falha do proxy** quando a resposta traz o header `X-CRM-Proxy-Falha: 1`, quando há erro de
   rede ou quando passam 15 s. Nesses casos a mesma aba vai para `http(s)://host:porta` direto.

Páginas de erro do próprio proxy, que levam o header (`_falha_proxy_web`):

| Status | Motivo |
|---|---|
| 400 | IP privado sem `ProxyServer` ativo e sem túnel OpenVPN cobrindo o IP |
| 502 | sem resposta do equipamento (proxy ou rota até ele fora) |
| 500 | erro interno no `ProxyEngine` |

Os 15 s ficam abaixo do pior caso do servidor (handshake SSH com o proxy até 15 s, mais 8 s de
conexão), para o direto chegar antes do 502. A conexão direta só funciona se o PC do operador
alcançar a rede do cliente, por exemplo com VPN no próprio PC.

### RDP de protocolo extra

O card abre `/clientes/rdp/<id>/?pid=<extra>`. O `winbox.html` repassa o `pid` na URL do WebSocket
VNC, e o `WinboxVNCConsumer` aplica a porta do extra (`permitidos=('RDP',)`) antes de montar o túnel.
Título e splash da página seguem o modo: "RDP · IP" e "Preparando acesso RDP". Antes o RDP também
mostrava "Preparando WinBox".

## Backup: sempre pelo SSH do host

`realizar_backup` usa `Acesso.porta_ssh()`:

1. protocolo padrão SSH: porta padrão;
2. senão, o primeiro SSH extra (menor `id`);
3. sem SSH em lugar nenhum: comportamento antigo (porta padrão, ou 22).

Quando o SSH vem do extra, o log do backup mostra `🔑 SSH do protocolo extra: porta N`, e o
relatório registra `Host: ip:porta` com a porta usada de fato. O caminho proxy/OpenVPN/direto não
muda.

Outras rotinas SSH (Agent NOC, scripts, OLT PON, `platform_ssh_exec`) continuam usando só o acesso
padrão.

## Diagnóstico

- **"Protocolo de acesso não encontrado neste host" no terminal**: o extra foi removido com a aba
  aberta, e o reconectar reenvia o `protocolo_id` antigo. Reabra pelo **Acessar**.
- **Web caiu no direto sem esperar**: abra `/clientes/acessos/<id>/web/<porta>/<scheme>/` numa aba e
  leia a página de erro do proxy (motivo e status). No DevTools, essa resposta traz
  `X-CRM-Proxy-Falha: 1`.
- **Extras de um host** (shell do Django):

```python
from clientes.models import AcessoProtocolo
AcessoProtocolo.objects.filter(acesso_id=1126).values('id', 'protocolo', 'porta')
```

- **Porta que o backup vai usar**: `Acesso.objects.get(id=1126).porta_ssh()`.
