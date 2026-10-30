# WinBox Web (VNC) — Documentação Técnica

## O que é

Sistema que permite abrir o **WinBox 4** (cliente MikroTik) diretamente no browser, sem instalar nada no computador do usuário.

O mesmo padrão serve o **WebFig** (navegador no servidor) e o **acesso RDP** (área de trabalho
remota via `xfreerdp`) — muda só o binário executado dentro do Xvfb. Ver "Modos Suportados".

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
rfb.resizeSession  = false;  // x11vnc não suporta SetDesktopSize; resize vai por frame de texto (ver abaixo)
rfb.qualityLevel   = 8;      // JPEG quase sem perda (subiu de 6 → 8 em 22/07/2026) — só navegador/RDP
rfb.compressionLevel = 4;    // zlib do Tight (2 a 9 deram o mesmo tamanho na tela do WinBox)
```

No modo WinBox o `winbox.html` tira o pseudo-encoding de qualidade (-32..-23) da lista que o noVNC
manda, e o x11vnc responde com **Tight sem perdas** (sem JPEG). Ver "Fluidez" abaixo.

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

O UNM2000 Web tem dependências próprias (ver a seção dele).

Binário do WinBox 4: `/opt/crm/static/winbox4/WinBox` (ELF 64-bit)

---

## RDP de protocolo extra e textos por modo — Adicionado em 11/09/2026

- `rdp_page` aceita `?pid=<AcessoProtocolo>`. O `winbox.html` repassa o `pid` na URL do WebSocket e
  o `WinboxVNCConsumer` usa a porta desse RDP extra
  (`aplicar_protocolo_extra(pid, permitidos=('RDP',))`).
- Título e splash seguem o `vnc_mode`: `RDP · IP` e "Preparando acesso RDP", `WebFig · IP` e
  "Preparando WebFig", `WinBox · IP` e "Preparando WinBox". Antes o RDP também mostrava "Preparando
  WinBox", o que parecia um Winbox Web aberto no lugar do RDP.
- No card, Winbox Web 4.4 e 3.43 saem pela escolha do **Acessar** ([acessos_protocolos_extras.md](acessos_protocolos_extras.md)).

## UNM2000 Web — Adicionado em 30/10/2026

Abre o cliente do UNM2000 (FiberHome) no navegador. Protocolo **UNM2000** no cadastro do acesso
(`host` = IP do servidor UNM; usuário e senha = login do UNM; a porta não é usada pelo cliente).
Página `/clientes/unm/<id>/`, modo `unm` do `WinboxVNCConsumer`, classe `UnmVNCManager`
(`clientes/unm_vnc.py`), que herda Xvfb, x11vnc, resize e gravação do `WinboxVNCManager`.

**Cliente.** Port em Java 21, sem Wine ([unm2000-linux-port](https://github.com/Reinoldo-Ozy/unm2000-linux-port) v1.3.1):

```bash
apt-get install openjdk-21-jre redsocks socat sshpass unrar bsdiff
systemctl disable --now redsocks   # cada sessão sobe o próprio
curl -O http://<ip-do-unm>:8080/webstart/unm2000_Client_en.exe
sudo bash install.sh unm2000_Client_en.exe   # instala em /opt/unm2000
```

**Rede.** O cliente fala ZeroC Ice em dezenas de portas informadas pelo servidor (18 na Sartor), então
o túnel de porta única (`_criar_tunel_paramiko`) não serve. Em IP privado:

```
UNM2000 -> iptables REDIRECT -> redsocks -> socat -> socket UNIX | socat -> ssh -D -> proxy do cliente
           |-------- namespace da sessão (só loopback) --------|   |------- host ---------|
```

- `unshare -Urn --pid --fork --kill-child`: roda como `www-data`, **sem root nem sudo**. Dentro, o
  processo é "root" só naquele namespace, o bastante para o `iptables`.
- O namespace não tem interface além do loopback: a única saída é o socket UNIX. Não há como escapar do
  proxy nem alcançar a rede do servidor, e sessões de clientes diferentes não se enxergam.
- Rota padrão no `lo` com origem `10.255.0.1`: sem a rota o `connect()` dá *unreachable* antes do
  `REDIRECT` ("Socket errors" no cliente); sem a origem, a resposta se perde.
- O namespace de PID faz o kernel matar tudo junto, mesmo se o daphne cair sem passar por `stop()`.
- IP público: o cliente sai direto, sem namespace. IP privado sem `ProxyServer` ativo: erro claro (via
  VPN ainda não foi implementado).

**Telas iniciais.** A home é nova a cada sessão (`/tmp/unm_*`, apagada no `stop()`), com
`-J-Duser.home` para isolar também as preferências Java, que iriam para `/var/www/.java`. Por isso o
cliente sempre pede a pasta de trabalho, num diálogo escondido atrás da tela de abertura.
`_automatizar_telas` esconde a abertura, responde e preenche o login com os atalhos do diálogo
(`Alt+U/P/S/L`), que não dependem da posição da janela. A senha vai pela entrada padrão do `xdotool`,
fora da lista de processos. Sem senha no cadastro, o login fica na tela para digitar.

**Openbox.** `openbox_rc_unm.xml` maximiza só a janela principal (`title="UNM2000*" type="normal"`). Avisos
também têm o título "UNM2000", daí o `type`; NE Manager e diálogos mantêm a borda para mover e fechar.

**Limitações.** O que a conta do UNM pode fazer, a sessão pode: `admin` vê "Delete NE" e afins, então use
um usuário de perfil restrito no cadastro. Cerca de 900 MB por sessão. O contador global de alarmes da
janela principal fica em 0 (o do NE Manager funciona). Painéis com JxBrowser não abrem.
A tela de alarmes correntes sem filtro carrega centenas de milhares de linhas pelo proxy.

## Resize em tempo real e WinBox 4.4 — Adicionado em 22/10/2026

Antes, ampliar a janela/painel só esticava no navegador o framebuffer criado na abertura (imagem
borrada): o Xvfb não muda de tamanho depois de criado (`xrandr` não cresce a tela nem aceita
`--newmode`) e o `rfb._sendDesktopSize` que o `winbox.html` chamava nem existe no noVNC.

Agora (só no modo WinBox; RDP e WebFig seguem escalando):

- O `winbox.html` manda também `sw`/`sh` (monitor × `devicePixelRatio`). O Xvfb nasce desse
  tamanho (mínimo 1920×1080, teto 3840×2160) e o x11vnc mostra só o recorte `-clip WxH+0+0`.
- O Openbox de cada sessão usa uma cópia de `openbox_rc.xml` com `<margins>` = tela − recorte, então
  a janela maximizada ocupa exatamente o que o noVNC enxerga.
- Ao redimensionar (debounce de 250 ms; o `devicePixelRatio` é recalculado, então zoom do navegador
  também conta), o front manda um **frame de texto** no mesmo WebSocket:
  `{"type":"resize","w":W,"h":H}`. O RFB do noVNC é todo binário, então o `WinboxVNCConsumer.receive`
  intercepta e chama `WinboxVNCManager.resize()`.
- `resize()` aplica numa thread (rajadas aplicam só o último tamanho): reescreve as margens,
  `openbox --reconfigure` e `x11vnc -R clip:WxH+0+0 -Q clip`. O x11vnc manda `DesktopSize` e o
  noVNC ajusta o canvas, ficando 1:1 de novo.
- **`-R` sozinho não serve**: é assíncrono por propriedade X e um comando seguinte (ou um `-Q`
  separado) sobrescreve o anterior antes de o x11vnc ler — o recorte ficava um passo atrás. `-R` e
  `-Q` na mesma chamada rodam em sequência e devolvem o recorte aplicado (confere e reenvia 1×).
  Cada troca leva ~0,7 s. **O x11vnc roda sem `-threads`**: ver a seção de 27/10/2026 abaixo.
- A gravação (ffmpeg) captura a tela inteira do Xvfb, já que o recorte muda no meio da sessão.
- O WinBox 4 tem janela mínima de 1280×720: abaixo disso parte da janela fica fora do recorte (já
  era assim antes).

## x11vnc sem `-threads`: tela não aparecia — Corrigido em 27/10/2026

**Sintoma:** WinBox Web (ex.: acesso 1226, WinBox 3) abria a página e ficava em "Conectando"/tela
preta; recarregar não resolvia. No log do daphne, logo depois do "Iniciando ambiente", aparecia
`x11vnc :100 não confirmou clip:...`.

**Causa:** no x11vnc 0.9.16 (libvncserver), trocar o `-clip` por `-R` enquanto um cliente recebe
quadros **com `-threads` ligado** trava o x11vnc para sempre: ele para de mandar quadros e nem
responde mais ao `-Q` (o `x11vnc -Q` desiste em ~3,5 s com "could not connect"). O `winbox.html`
manda `enviarTamanho()` no `connect`, e o painel quase sempre mede uns pixels diferente do tamanho
inicial (1400 → 1398), então **toda** sessão fazia a troca nos primeiros segundos e congelava.

Evidência na sessão do 1226: o WinBox estava conectado e desenhado no Xvfb (título
`CCR-TRV-GTW-4 - WinBox v6.49.13`), o WebSocket do noVNC continuava aberto, mas o socket do x11vnc
tinha mandado só 1501 bytes e parou exatamente no resize; as threads ficavam paradas em futex.
Reproduzido isolado (Xvfb + x11vnc + cliente RFB mínimo, troca de clip 0,5 s, 3 s ou 6 s depois de
conectar): **com `-threads` trava sempre; sem ele, o `DesktopSize` chega em ~0,65 s e os quadros
continuam.** `-R nothreads` em tempo de execução trava igual, então não dá para desligar só na hora
do resize. O "~3 s por troca" anotado em 22/10 era esse timeout, não o tempo da troca.

**Correção:** `-threads` saiu da linha do x11vnc (`winbox_vnc.py`; o teste de latência agora exige a
ausência). Custo medido (mudança na tela → quadro no cliente, 30 amostras): mediana 24 → 29 ms,
p90 27 → 38 ms.

WinBox 4 atualizado de 4.2 para **4.4** (build de 14/09/2026, `upgrade.mikrotik.com/routeros/winbox/LATEST.4`).
O binário é gitignored: depois do pull em outro servidor, baixar
`https://download.mikrotik.com/routeros/winbox/4.4/WinBox_Linux.zip` e extrair em
`/opt/crm/static/winbox4/`. Backup da 4.2 em `static/winbox4/WinBox.bak-pre-4.4`.

## Fluidez / lentidão — Ajustado em 23/10/2026

Queixa: "o WinBox Web está lento" (Cgnat AFL, acesso 1413, Startnet, via proxy). Medido no servidor:

| Trecho | Medida | Gargalo? |
|---|---|---|
| CPU do container (10 núcleos) | ~60% ocioso, `/proc/pressure/cpu` some avg10 ≈ 2%. O load 40+ é do host (LXC) | não |
| Sessões simultâneas | 1 a 2 no pico (hoje); muitas sessões de 0,1 min = abrir/fechar | não |
| Xvfb / x11vnc / WinBox | ~3% / ~3% / <3% de um núcleo cada | não |
| ffmpeg (auditoria) | 26–33% de um núcleo em 1920×1080, 8 fps, `nice 15`; 4K (HiDPI) custa ~4× | não, com folga hoje |
| x11vnc: codificar quadro inteiro | ~20–30 ms | não |
| Navegador do usuário → servidor | ~41 ms RTT | fixo |
| Servidor → proxy `tunnel_zabbix-startnet` (24.152.31.3:666) | ~45 ms RTT; abrir canal até o equipamento ~100 ms | fixo |
| Proxy → equipamento (172.24.67.122) | ~7 ms, sem perda, MTU 1428 passa | não |

Cada ação do WinBox que fala com o equipamento paga ~93 ms de ida e volta antes de qualquer
ajuste. O que dava para tirar:

1. **Nagle no túnel até o proxy.** O `_ProxyPool` compartilha uma conexão SSH por proxy, e só o
   caminho do terminal ligava `TCP_NODELAY`; o túnel do WinBox (`_criar_tunel_paramiko`), o IPAM e
   outros, não. Com Nagle, a 2ª mensagem pequena de uma rajada espera o ACK do proxy. Medido (eco
   pelo canal SSH até o proxy): 1 mensagem 42 ms nos dois casos; **3 mensagens seguidas 84 → 50 ms**.
   Agora `_ProxyPool.put()` desliga o Nagle em toda conexão que entra no pool.
2. **JPEG numa tela de cores chapadas.** O noVNC sempre manda o pseudo-encoding de qualidade e o
   x11vnc então usa JPEG. Quadro inteiro com a lista real do noVNC: **WinBox 3 80 → 25 KB**,
   WinBox 4 109 → 70 KB, mesmo tempo de codificação, e sem borrão no texto. O x11vnc 0.9.16 não tem
   opção para desligar JPEG, então o `winbox.html` filtra o pedido de qualidade (só no modo WinBox;
   navegador/RDP têm foto e seguem em JPEG q8). Conferido no fluxo: o libvncserver 0.9.14 só usa
   fill/copy/palette no Tight sem perdas — nunca o filtro *gradient*, que o decoder do noVNC não
   implementa.
3. **Polling do x11vnc.** `-wait 10` → `-wait 5 -defer 5` (padrão do `-defer` é 20 ms). Tecla →
   atualização no WinBox 4 (40 amostras × 2): **mediana 37 → 29 ms, p90 52 → 38 ms**, CPU do x11vnc
   igual. (O `-threads` que estava junto saiu em 27/10/2026 porque travava o resize; ver abaixo.)

Sem efeito medido e por isso não mexido: `TCP_NODELAY` no socket daphne → x11vnc (loopback, as
medições com e sem deram igual) e o `compressionLevel` (2, 4 e 9 deram o mesmo tamanho).

Para repetir as medições: cliente RFB mínimo em Python contra a porta do x11vnc da sessão
(`ss -tlnp | grep x11vnc`), pedindo quadro inteiro com cada lista de encodings e mandando
keydown/keyup com espera da próxima `FramebufferUpdate`.

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

### Sessão não inicia (fica no splash "Preparando WinBox", "Preparando acesso RDP" ou "Preparando WebFig")

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

### RDP abria só tela preta (`/sec:tls` forçado) — Corrigido em 20/08/2026

Depois que o botão passou a abrir `/clientes/rdp/<id>/`, a sessão subia mas o noVNC mostrava
**tela preta**: o `RdpVNCManager` chamava o `xfreerdp` com `/sec:tls`, e Windows Server com NLA
obrigatório (padrão desde o 2012) recusa TLS puro:

```
[WARN][com.freerdp.core.nego] - Error: HYBRID_REQUIRED_BY_SERVER [0x00000005]
[ERROR][com.freerdp.core] - ERRCONNECT_SECURITY_NEGO_CONNECT_FAILED [0x0002000C]
```

O `xfreerdp` morria em ~100 ms, mas Xvfb e x11vnc continuavam de pé — o browser conectava no VNC
e transmitia fielmente um display vazio.

Correção em `clientes/rdp_vnc.py`:

- **Sem `/sec:...`**: o FreeRDP negocia sozinho (NLA → TLS → RDP legado), servindo tanto servidor
  novo quanto servidor antigo sem NLA. Não reintroduza o flag.
- **stderr do `xfreerdp` deixou de ir para `DEVNULL`**: é lido num thread (`_drenar_stderr`), fica
  no log do daphne e alimenta o diagnóstico. Também evita o pipe encher e travar o cliente.
- **Falha vira erro na tela, não tela preta**: `start()` espera até 2 s; se o cliente RDP morreu
  nesse intervalo, `_motivo_falha_rdp()` traduz o stderr ("Usuário ou senha inválidos", "O servidor
  exige NLA…", "Não foi possível abrir a conexão TCP…") e a exceção sobe pro `VncConsumer`, que
  manda a mensagem pro browser.

Erros de Kerberos (`krb5_parse_name … default realm`) e `fuse: device not found` aparecem mesmo em
sessão saudável — o FreeRDP cai pra NTLM e segue; não são causa de falha. Junto com o encerramento
normal (`fsig_term_handler`, `ERRCONNECT_CONNECT_CANCELLED`), eles vão pro log em **DEBUG**
(`_ERROS_BENIGNOS_RDP`); no log do daphne, `ERROR` de `xfreerdp` é falha de verdade. Toda linha
continua guardada em memória e alimentando o diagnóstico, independente do nível de log.

Validado em produção em 20/08/2026 no `SRV-AGRONELORE` (Grupo Agronelore, 192.168.0.1:3389 pelo
túnel do ProxyServer): área de trabalho do Windows renderizando no noVNC.

### Colar do PC local dentro da sessão não funcionava (WinBox e RDP) — Corrigido em 02/09/2026

Copiar um texto **fora** do navegador (ou em outra aba) e dar `Ctrl+V` dentro da renderização do
WinBox/RDP não colava nada. Duas causas independentes, uma em cada ponta:

**1. Navegador — a leitura do clipboard local dependia só de `navigator.clipboard.readText()`**

O canvas do noVNC apenas **encaminha a tecla** pro host remoto; quem cola é o app lá dentro, contra
o clipboard X11 da sessão. Então o texto precisa chegar antes, via `ClientCutText`
(`rfb.clipboardPasteFrom()`). O código lia o clipboard local com `navigator.clipboard.readText()` e,
se a promise falhasse, desistia em silêncio — e ela falha em muita gente:

- o **Firefox nunca expôs `readText()`** para páginas comuns;
- no **Chrome** a permissão `clipboard-read` pode estar negada (e, dentro de `<iframe>`, exige
  `allow="clipboard-read; clipboard-write"` — a aba WinBox do terminal já traz esse atributo).

Agora a leitura sai do **evento `paste` do próprio navegador**, que não pede permissão nenhuma e
existe em todos eles. O `keydown` de `Ctrl+V`/`Shift+Insert` é interceptado em fase de **captura**
no `document`, move o foco para um `<textarea>` invisível (`#wb-paste-sink`) e deixa a ação padrão
seguir — o navegador entrega o `paste` nele e o texto é lido de `clipboardData`. `readText()` virou
só plano B, num timeout de 150 ms.

Detalhes que não podem ser mexidos sem quebrar de novo:

- **Nunca chamar `preventDefault()` nesse `keydown`**: o evento `paste` é a ação padrão dele.
  Só `stopPropagation()`/`stopImmediatePropagation()`, pra segurar a tecla (se ela chegasse agora
  no app remoto, ele colaria o conteúdo *antigo* do clipboard da sessão).
- O `#wb-paste-sink` precisa ser focável de verdade — `display:none`/`visibility:hidden` não
  recebem foco e o `paste` nunca chegaria. Ele fica em `left:-9999px` com `opacity:0`.
- O foco volta pro canvas num `setTimeout(…, 0)`; parado no sink, o `keyup` do Ctrl não chegaria no
  noVNC e o modificador ficaria **preso** na sessão remota.
- O `Ctrl+V` remoto é sintetizado inteiro (Control down → v down → v up → Control up), porque o Ctrl
  físico pode ter sido solto durante a espera e o noVNC ignora release de tecla que ele não
  registrou como pressionada.
- Clipboard local vazio ou ilegível ainda reenvia a tecla segurada, pra colar o que já estiver no
  clipboard da sessão em vez de o `Ctrl+V` do usuário sumir.

**2. Servidor — o RDP subia sem o canal de clipboard**

Mesmo com o texto na seleção X11 do Xvfb, ele não tinha como atravessar até o Windows: o
`RdpVNCManager` chamava o `xfreerdp` **sem** o canal `cliprdr`. Agora passa `+clipboard` (forma
aceita pelo FreeRDP 2 e 3). O FreeRDP 3 liga por padrão, mas o binário 2.x do fallback não.

Do lado do WinBox não foi preciso nada no servidor: o `x11vnc` já define **PRIMARY e CLIPBOARD** ao
receber `ClientCutText` (é o padrão — `-nosetclipboard`/`-nosetprimary` desligariam), e o WinBox
nativo/Wine lê o `CLIPBOARD`.

O atraso entre empurrar o texto e sintetizar o `Ctrl+V` é de **80 ms** no WinBox/WebFig e **250 ms**
no RDP — no RDP o FreeRDP ainda precisa negociar o `cliprdr` com o Windows depois que o `x11vnc`
assume a seleção X11.

O painel manual (botão 📋) continua existindo como alternativa e ganhou um botão **"Colar na
sessão"**, que empurra o texto e dispara o `Ctrl+V` remoto de uma vez.

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
  DISPLAY=:199 x11vnc -display :199 -nopw -listen 127.0.0.1 -rfbport 5999 -shared -forever -quiet -nonap -wait 10 &
  sleep 0.5
  DISPLAY=:199 /opt/crm/static/winbox4/WinBox &
  sleep 5
  DISPLAY=:199 wmctrl -lG  # deve mostrar WinBox em 0 0 1366 768
  kill $(jobs -p)
'
```

---

**Última atualização:** 02/09/2026  
**Autor:** CampeloSuporte
