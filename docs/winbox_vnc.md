# WinBox Web (VNC) — Documentação Técnica

## O que é

Sistema que permite abrir o **WinBox 4** (cliente MikroTik) diretamente no browser, sem instalar nada no computador do usuário.

Funciona criando um ambiente X11 isolado no servidor (Xvfb + Openbox + x11vnc) e transmitindo a tela via noVNC (WebSocket → VNC).

---

## Arquitetura

```
Browser (noVNC)
    ↕ WebSocket (/ws/vnc/<id>/?mode=winbox&w=W&h=H)
Django Channels Consumer (VncConsumer)
    ↕ TCP socket (127.0.0.1:PORT)
x11vnc  →  Xvfb (display :N, WxHx24, resolução física via devicePixelRatio)
              ↑
           Openbox (maximiza janelas)
              ↑
           WinBox 4 (conecta ao MikroTik via tunel ou direto)
```

---

## Arquivos Principais

| Arquivo | Função |
|---------|--------|
| `clientes/winbox_vnc.py` | Gerencia Xvfb, Openbox, x11vnc e WinBox |
| `clientes/consumers.py` | `VncConsumer` — bridge WebSocket ↔ VNC TCP |
| `clientes/templates/winbox.html` | Frontend noVNC |
| `clientes/views.py` | `winbox_page()` e `webfig_vnc_page()` |
| `clientes/openbox_rc.xml` | Config Openbox: sem bordas, maximize all |

---

## Fluxo de Inicialização

1. Browser abre `/clientes/winbox/<id>/` (com `?w=W&h=H` opcionais)
2. `winbox.html` conecta WebSocket em `/ws/vnc/<id>/?mode=winbox&w=W&h=H`
3. `VncConsumer` lê `w` e `h` da query string (padrão: 1366×768)
4. `WinboxVNCManager.start()`:
   - Xvfb no primeiro display livre a partir de `:100`
   - Openbox com regra `<maximized>true</maximized>` para todas as janelas
   - x11vnc na primeira porta livre a partir de 5900
   - WinBox 4 passando `host:porta user password` como args
   - `xdotool search --sync --name 'WinBox' windowsize W H windowmove 0 0` em background (garante maximização mesmo sem Openbox)
5. Consumer faz bridge TCP → WebSocket
6. noVNC renderiza o display no browser

---

## Parâmetros de Resolução

O browser envia `?w=` e `?h=` para o WebSocket com as dimensões do viewport, **já multiplicadas
pelo `devicePixelRatio`** (desde 22/07/2026 — ver seção de qualidade de imagem abaixo):

```javascript
// winbox.html
const _dpr = Math.min(window.devicePixelRatio || 1, 2);
const _initW = parseInt(_up.get('w')) || Math.round(document.documentElement.clientWidth * _dpr);
const _initH = parseInt(_up.get('h')) || Math.round(document.documentElement.clientHeight * _dpr);
const wsUrl = `.../ws/vnc/${acessoId}/?mode=winbox&w=${_initW}&h=${_initH}`;
```

O consumer usa esses valores para criar o Xvfb na resolução física do painel do usuário (não
apenas na resolução CSS). Isso vale tanto para `mode=winbox` quanto para `mode=browser` (WebFig) —
`BrowserVNCManager` também aceita `width=`/`height=` no construtor (antes era fixo em 1366×768).

---

## Configuração noVNC (winbox.html)

```javascript
rfb.scaleViewport  = true;   // escala client-side
rfb.resizeSession  = false;  // NÃO pede resize ao servidor (evita desmaximizar WinBox)
rfb.qualityLevel   = 8;      // JPEG quase sem perda (subiu de 6 → 8 em 22/07/2026)
rfb.compressionLevel = 4;    // subiu de 2 → 4 para compensar o ganho de qualidade
```

**Importante:** `resizeSession` deve ficar `false`. Se `true`, o noVNC envia `SetDesktopSize` ao x11vnc após conectar, o que pode desmaximizar o WinBox.

---

## Openbox — Maximização Automática

O arquivo `clientes/openbox_rc.xml` configura o Openbox para maximizar todas as janelas e remover bordas:

```xml
<openbox_config>
  <applications>
    <application class="*">
      <decor>no</decor>
      <maximized>true</maximized>
    </application>
  </applications>
</openbox_config>
```

O `xdotool search --sync` serve como fallback caso o Openbox ainda não tenha aplicado a regra quando a janela aparece.

---

## Dependências no Servidor

```bash
apt-get install xvfb openbox x11vnc xdotool wmctrl
```

Binário do WinBox 4: `/opt/crm/static/winbox4/WinBox` (ELF 64-bit)

---

## Problemas Conhecidos e Soluções

### WinBox abre pequeno no browser

**Causa:** Flag `-ncache N` no x11vnc. O `-ncache 10` faz o x11vnc reportar ao noVNC uma tela **10× mais alta** que a real (ex: 1400×8000 em vez de 1400×800). O noVNC escala todo o conteúdo para caber, fazendo o WinBox aparecer minúsculo no topo.

**Solução:** Nunca usar `-ncache` no x11vnc para sessões WinBox. O comando correto é:
```python
["x11vnc", "-display", f":{display}", "-nopw", "-listen", "127.0.0.1",
 "-xkb", "-rfbport", str(port), "-shared", "-forever", "-quiet"]
```

**Flags problemáticas a evitar:** `-ncache`, `-noscr`, `-xrandr` (xrandr pode causar resize que desmaximiza o WinBox).

---

### `WinboxVNCManager.__init__() got an unexpected keyword argument 'width'`

**Causa:** O consumer passa `width=` e `height=` ao construtor, mas o `__init__` não declarava esses parâmetros.

**Solução:** Assinatura correta:
```python
def __init__(self, host, port, user, password,
             winbox_path="/opt/crm/static/winbox4/WinBox",
             width=1366, height=768):
```

---

### Sessão não inicia (fica no splash "Preparando WinBox")

**Causas possíveis:**
1. Parâmetros faltando no `__init__` (ver acima)
2. Xvfb não instalado
3. Display X11 em uso (checar `/tmp/.X11-unix/`)
4. Porta VNC em uso

**Diagnóstico:**
```bash
journalctl -u daphne -n 30 | grep -E "VNC|Error|winbox"
```

---

## Gravação de Tela (Auditoria) — Adicionado em 2026-07-20

Toda sessão WinBox/WebFig via VNC pode ser gravada em `.mp4` para auditoria — ver
[AUDITORIA_ACESSOS.md](AUDITORIA_ACESSOS.md) para a documentação completa (modelos, endpoints,
frontend). Resumo do que muda em `winbox_vnc.py`/`browser_vnc.py`:

- `WinboxVNCManager`/`BrowserVNCManager` ganham `record_path=` no construtor; se preenchido, um
  processo `ffmpeg -f x11grab` é iniciado ~1.5s após o WinBox/navegador subir (evita disputa de
  CPU durante o carregamento inicial da UI) e roda com `nice -n 15 ionice -c 3` (prioridade baixa,
  cede CPU/IO pro Wine/WinBox durante toda a sessão, não só no início).
- Largura/altura são arredondadas pra número par (`& ~1`) antes de montar Xvfb e ffmpeg — o
  `libx264`/`yuv420p` exige dimensões pares; como a resolução vem do painel do navegador do
  cliente (frequentemente ímpar), sem isso o ffmpeg falhava ao abrir o encoder e gravava um
  `.mp4` de 0 bytes (sem moov atom, não reproduz na auditoria).
- **`stop()` agora é idempotente** (`threading.Lock` + flag `_stopped`): antes, `limpar_recursos()`
  podia ser chamado duas vezes concorrentemente (thread de leitura do VNC + thread de
  `disconnect()` do WebSocket), enviando **dois `SIGTERM`** ao `ffmpeg`. No segundo `SIGTERM`, o
  `ffmpeg` aborta sem finalizar o `.mp4` (trailer nunca escrito), gerando arquivo de **0 bytes**
  mesmo em sessões de horas. O timeout de `p.wait()` após `terminate()` também subiu de 2s → 5s
  só para o `ffmpeg` (precisa de mais tempo que os outros processos para o mux do mp4).
- Falha ao iniciar o `ffmpeg` (ex: não instalado) não derruba a sessão — só desliga a gravação
  com log de aviso.

### Ícones do WinBox 3.43 com fundo preto

**Causa:** Xvfb rodando a `16bpp`. O WinBox 3.43 (via Wine) faz alpha-blending dos ícones via GDI,
e isso quebra nesse depth — cada ícone renderiza com um quadrado preto atrás em vez de fundo
transparente. Reproduzido comparando `16bpp` vs `24bpp` no mesmo equipamento/sessão: só o `16bpp`
apresenta o defeito. **Não tem relação com CPU/gravação** (hipótese antiga, descartada).

**Solução:** Xvfb sobe em `24bpp` (`xvfb_cmd` em `winbox_vnc.py`), não `16bpp`.

---

### Imagem borrada / texto sem nitidez em telas HiDPI — Corrigido em 22/07/2026

**Causa:** O Xvfb era criado com a resolução em **pixels CSS** do viewport
(`document.documentElement.clientWidth/clientHeight`), ignorando o `devicePixelRatio` do
navegador. Em qualquer tela HiDPI (Retina, Windows com escala >100%), isso gerava um framebuffer
com metade (ou um terço) da resolução física real, e o noVNC esticava esse conteúdo via CSS
(`scaleViewport`) para preencher a tela — resultado: texto e ícones borrados, como assistir vídeo
480p em tela 4K.

**Solução:** `winbox.html` agora multiplica a largura/altura do viewport pelo `devicePixelRatio`
antes de enviar `w`/`h` ao WebSocket (cap em 2x para não estourar CPU/gravação em telas 3x). O
Xvfb passa a renderizar em resolução física, e o `scaleViewport` reduz de volta ao tamanho CSS —
efeito equivalente a supersampling, deixando o conteúdo nítido mesmo sem o WinBox ser DPI-aware.
Junto disso, `qualityLevel` subiu de `6` para `8` (ver seção "Configuração noVNC" acima) para
reduzir o artefato de compressão JPEG no texto.

**Efeito colateral aceito:** em telas 2x, o Xvfb/WinBox/ffmpeg passam a operar em ~4x mais pixels
(2x largura × 2x altura). A gravação de auditoria já roda com `nice -n 15 ionice -c 3` (ver seção
de Gravação de Tela), o que absorve esse custo extra sem competir com o Wine/WinBox pela CPU.

---

### WinBox Web não abre para clientes que só têm VPN (sem ProxyServer SSH) — Corrigido em 04/08/2026

**Sintoma:** `WinboxVNCConsumer` falhava direto com `"Nenhum proxy SSH ativo para <cliente>"` — a
sessão nunca chegava a subir o Xvfb/WinBox, mesmo com o IP do equipamento perfeitamente alcançável
pelo servidor.

**Causa:** `get_active_proxy()` (`clientes/consumers.py`) levanta exceção sempre que não existe um
`ProxyServer` (túnel SSH) ativo pro cliente — sem checar se o IP privado já está coberto por um
túnel do próprio cliente, cuja rota já existe no kernel via a interface da VPN (mesmo mecanismo que
`proxy_web_acesso` — o proxy HTTP, ver `docs/proxy_web_acessos.md` — já usava via `vpn_cobre_ip`).
Clientes que dependem só de VPN (sem SSH proxy cadastrado) — na época, o Conecta ISP com uma VPN
WireGuard cobrindo `10.0.0.0/8` e nenhum `ProxyServer` — não conseguiam abrir WinBox Web nem WebFig
via VNC de jeito nenhum. (O WireGuard foi removido em 14/08/2026; hoje o mesmo fallback vale para o
túnel OpenVPN.)

**Fix:** `WinboxVNCConsumer.conectar_vnc()` (modo `winbox`/`browser`, WinBox via VNC) e
`conectar_winbox()` (modo `winbox_nativo`, TCP passthrough direto) agora: buscam `ProxyServer`
ativo primeiro; se não existir, chamam `vpn_cobre_ip(acesso.cliente, host)` — se a VPN cobre o IP,
conectam direto (sem túnel); só levantam a exceção original se nem proxy nem VPN cobrirem o host.

```python
proxy = ProxyServer.objects.filter(cliente=acesso.cliente, ativo=True).first()
if proxy:
    local_port = self._criar_tunel_paramiko(proxy, host, porta)
    target_host, target_port = '127.0.0.1', local_port
else:
    from .views import vpn_cobre_ip
    if not vpn_cobre_ip(acesso.cliente, host):
        raise Exception(f"Nenhum proxy SSH ativo nem VPN cobrindo {host} para {acesso.cliente.nome_empresa}")
    target_host, target_port = host, porta
```

**Pendente:** o mesmo bug (`get_active_proxy()` sem fallback de VPN) ainda existe em três outros
métodos de `clientes/consumers.py` — Terminal SSH (`connect_ssh_via_proxy`), OLT Parks
(`connect_ssh_parks_proxy`, via `pexpect`+`ProxyCommand`, não usa `_criar_tunel_paramiko`) e Telnet
(`connect_telnet_via_proxy`) — todos afetam qualquer cliente só-VPN da mesma forma. Não corrigidos
ainda porque cada um usa o objeto `proxy` de um jeito diferente (não é o mesmo copy-paste simples),
precisam de mais cuidado e teste por protocolo.

### Acesso RDP abria terminal SSH em vez da área de trabalho — Corrigido em 20/08/2026

Clicar em "Acessar" num acesso com protocolo **RDP** (ex.: `SRV-AGRONELORE`) abria
`/clientes/terminal/?cliente=<id>` e o CRM tentava conectar por SSH — nunca chegava no
`/clientes/rdp/<id>/`.

Causa: existem duas implementações de `acessarEquipamento()` no projeto, e a que a listagem de
clientes carrega é a de `static/js/terminal_tab_manager.js` — que tratava `HTTP/HTTPS` e `WINBOX`,
mas **não** `RDP`; qualquer outro protocolo caía no ramo final "SSH, Telnet, etc" e ia para o
terminal. (A outra, `static/js/acessar_equipamento.js`, já tinha o caso RDP, mas nenhum template a
inclui — é código morto.)

Correção: ramo explícito para `RDP` em `terminal_tab_manager.js`, abrindo `/clientes/rdp/<id>/` em
janela própria, no mesmo padrão do WinBox Web.

> Ao mexer nesse fluxo, edite **`static/js/terminal_tab_manager.js`** — é o arquivo realmente
> servido (`STATIC_ROOT` é o próprio `static/`) e o único incluído por `clientes/templates/listar.html`.

---

## Modos Suportados

| Modo | URL | Descrição |
|------|-----|-----------|
| `winbox` | `/clientes/winbox/<id>/` | WinBox 4 nativo via VNC |
| `browser` | `/clientes/webfig/<id>/` | Navegador web apontando para WebFig/HTTP |
| `rdp` | `/clientes/rdp/<id>/` | Área de trabalho remota (xfreerdp) via VNC |

---

## Como Testar Manualmente

```bash
# Como www-data (igual ao daphne)
sudo -u www-data env -i HOME=/var/www PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin bash -c '
  DISPLAY=:199
  Xvfb :199 -screen 0 1366x768x24 -nolisten tcp &
  sleep 0.5
  DISPLAY=:199 openbox --config-file /opt/crm/clientes/openbox_rc.xml &
  sleep 0.5
  DISPLAY=:199 x11vnc -display :199 -nopw -listen 127.0.0.1 -rfbport 5999 -shared -forever -quiet -nonap -threads -wait 10 &
  sleep 0.5
  DISPLAY=:199 /opt/crm/static/winbox4/WinBox &
  sleep 5
  DISPLAY=:199 wmctrl -lG  # deve mostrar WinBox em 0 0 1366 768
  kill $(jobs -p)
'
```

---

**Última atualização:** 20/08/2026  
**Autor:** CampeloSuporte
