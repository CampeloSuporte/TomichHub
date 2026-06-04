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
x11vnc  →  Xvfb (display :N, WxHx24)
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

O browser envia `?w=` e `?h=` para o WebSocket com as dimensões do viewport:

```javascript
// winbox.html
const _initW = parseInt(_up.get('w')) || document.documentElement.clientWidth;
const _initH = parseInt(_up.get('h')) || document.documentElement.clientHeight;
const wsUrl = `.../ws/vnc/${acessoId}/?mode=winbox&w=${_initW}&h=${_initH}`;
```

O consumer usa esses valores para criar o Xvfb na resolução exata do painel do usuário.

---

## Configuração noVNC (winbox.html)

```javascript
rfb.scaleViewport  = true;   // escala client-side
rfb.resizeSession  = false;  // NÃO pede resize ao servidor (evita desmaximizar WinBox)
rfb.qualityLevel   = 6;
rfb.compressionLevel = 2;
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

## Modos Suportados

| Modo | URL | Descrição |
|------|-----|-----------|
| `winbox` | `/clientes/winbox/<id>/` | WinBox 4 nativo via VNC |
| `browser` | `/clientes/webfig/<id>/` | Navegador web apontando para WebFig/HTTP |

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
  DISPLAY=:199 x11vnc -display :199 -nopw -listen 127.0.0.1 -rfbport 5999 -shared -forever -quiet &
  sleep 0.5
  DISPLAY=:199 /opt/crm/static/winbox4/WinBox &
  sleep 5
  DISPLAY=:199 wmctrl -lG  # deve mostrar WinBox em 0 0 1366 768
  kill $(jobs -p)
'
```

---

**Última atualização:** 03/06/2026  
**Autor:** CampeloSuporte
