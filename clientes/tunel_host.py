"""
Por qual túnel do cliente chegar num host.

Um cliente pode ter mais de um túnel — proxies SSH (VM Debian) e/ou túneis
OpenVPN (MikroTik) — cada um dando acesso a uma rede diferente, inclusive
com faixas de IP repetidas entre elas. Quando tem mais de um, o host
(`Acesso.tunel_proxy` / `Acesso.tunel_vpn`) pode fixar o túnel dele.

Toda conexão a equipamento passa por dois pontos, e os dois consultam a
escolha do host aqui:

  - `vpn_rota.tunel_para(cliente, host)` → túnel OpenVPN, ou None;
  - `proxy_para(cliente, host)`          → proxy SSH, ou None.

Host fixado num proxy: `tunel_para` devolve None e `proxy_para` o proxy.
Host fixado numa VPN: `proxy_para` devolve None e `tunel_para` a VPN —
mesmo que a VPN esteja fora, para nunca cair num equipamento de mesmo IP
na OUTRA rede. Sem escolha (ou túnel escolhido desativado/excluído), vale
o comportamento automático de antes.

Quem tem o objeto Acesso em mãos passa `acesso=`; sem ele (Ferramentas,
IPAM, proxy web), a escolha é procurada pelo IP entre os hosts do cliente.
"""

PROXY = 'proxy'
VPN = 'vpn'


def tuneis_do_cliente(cliente):
    """[(valor, rótulo)] dos túneis ativos do cliente, na ordem do select do
    host. valor = 'proxy:<id>' ou 'vpn:<id>'."""
    from .models import ProxyServer, VPNOpenVPN
    opcoes = []
    for p in ProxyServer.objects.filter(cliente=cliente, ativo=True).order_by('nome', 'id'):
        opcoes.append((f'{PROXY}:{p.id}', f'Proxy SSH · {p.nome} ({p.host})'))
    for v in VPNOpenVPN.objects.filter(cliente=cliente, ativo=True).order_by('nome', 'id'):
        opcoes.append((f'{VPN}:{v.id}', f'OpenVPN · {v.nome}'))
    return opcoes


def valor_do_acesso(acesso):
    """'proxy:<id>', 'vpn:<id>' ou '' (automático)."""
    if acesso.tunel_proxy_id:
        return f'{PROXY}:{acesso.tunel_proxy_id}'
    if acesso.tunel_vpn_id:
        return f'{VPN}:{acesso.tunel_vpn_id}'
    return ''


def aplicar_valor(acesso, valor):
    """Grava em memória a escolha vinda do form ('proxy:3', 'vpn:5' ou '').
    Só aceita túnel do próprio cliente do host. Devolve a mensagem de erro
    ou ''."""
    from .models import ProxyServer, VPNOpenVPN
    valor = (valor or '').strip()
    acesso.tunel_proxy = None
    acesso.tunel_vpn = None
    if not valor:
        return ''
    tipo, _, ident = valor.partition(':')
    if not ident.isdigit() or tipo not in (PROXY, VPN):
        return 'Túnel inválido.'
    modelo = ProxyServer if tipo == PROXY else VPNOpenVPN
    obj = modelo.objects.filter(id=int(ident), cliente_id=acesso.cliente_id).first()
    if obj is None:
        return 'O túnel escolhido não pertence a este cliente.'
    if tipo == PROXY:
        acesso.tunel_proxy = obj
    else:
        acesso.tunel_vpn = obj
    return ''


def _norm_host(host):
    h = str(host or '').strip()
    if '://' in h:
        h = h.split('://', 1)[1]
    return h.split('/', 1)[0]


def escolha(cliente, host=None, acesso=None):
    """(PROXY, ProxyServer) | (VPN, VPNOpenVPN) | (None, None).

    Só vale túnel ativo do mesmo cliente; escolha apontando para túnel
    desativado é ignorada (automático)."""
    if acesso is None and cliente is not None and host:
        from .models import Acesso
        alvo = _norm_host(host)
        candidatos = (Acesso.objects
                      .filter(cliente=cliente, host__in={alvo, str(host).strip()})
                      .exclude(tunel_proxy__isnull=True, tunel_vpn__isnull=True)
                      .select_related('tunel_proxy', 'tunel_vpn')
                      .order_by('id'))
        acesso = candidatos.first()
    if acesso is None:
        return None, None
    cliente_id = getattr(cliente, 'id', cliente) if cliente is not None else getattr(acesso, 'cliente_id', None)
    p = acesso.tunel_proxy if getattr(acesso, 'tunel_proxy_id', None) else None
    if p is not None and p.ativo and p.cliente_id == cliente_id:
        return PROXY, p
    v = acesso.tunel_vpn if getattr(acesso, 'tunel_vpn_id', None) else None
    if v is not None and v.ativo and v.cliente_id == cliente_id:
        return VPN, v
    return None, None


def proxy_para(cliente, host=None, acesso=None):
    """Proxy SSH a usar para o host: o escolhido nele; nenhum se ele escolheu
    uma VPN; senão o primeiro proxy ativo do cliente (como antes)."""
    from .models import ProxyServer
    if cliente is None and acesso is not None:
        cliente = acesso.cliente
    tipo, obj = escolha(cliente, host, acesso)
    if tipo == PROXY:
        return obj
    if tipo == VPN:
        return None
    filtro = {'cliente_id': cliente} if isinstance(cliente, int) else {'cliente': cliente}
    return ProxyServer.objects.filter(ativo=True, **filtro).first()
