"""
Função/tipo do host no CRM -> tipo de device no editor de topologia.

Saiu de `views.topologia_hosts` para ser reaproveitado pelos racks
(`racks/services.py`), que classificam hosts ainda não desenhados no mapa
com o mesmo critério do "Importar Hosts". Ver docs/topologia.md.
"""

# A ordem é significativa: a primeira regra que casar vence.
MAPA_FUNCAO_TIPO = [
    (['cgnat', 'cg-nat', 'carrier grade nat'], 'cgnat'),
    # IX/PTT e trânsito vêm antes de router/switch: um host chamado
    # "Router IX.br" é, no desenho, o ponto de troca — não mais um
    # roteador igual aos outros. Palavras curtas ('ix', 'ptt', 'wan')
    # só entram com separador, senão casariam com "matrix", "unix" etc.
    (['ix.br', 'ixbr', 'ix br', 'ix-', 'ptt ', 'ptt-', 'ptt.', 'peering'], 'ix'),
    (['transito', 'trânsito', 'upstream', 'internet', 'wan-', 'wan '], 'internet'),
    (['bras', 'bng', 'broadband network'], 'router'),
    (['router', 'roteador', 'core', 'border', 'borda'], 'router'),
    (['switch l3', 'sw-l3', 'camada 3'], 'switch_l3'),
    (['switch', 'sw-', 'catalyst', 'nexus'], 'switch_l2'),
    (['access point', 'acess point', 'ponto de acesso', 'unifi', 'ap-', 'ap_'], 'ap'),
    (['radio', 'rádio', 'wireless', 'ubiquiti', 'mikrotik', 'ap ', 'airmax', 'ltu'], 'radio'),
    (['dwdm', 'oadm', 'ots', 'mstp', 'transponder'], 'dwdm'),
    (['splitter', 'divisor optico', 'divisor óptico'], 'splitter'),
    (['olt', 'gpon', 'xgs', 'epon'], 'olt'),
    (['onu', 'ont'], 'onu'),
    (['server', 'servidor', 'zabbix', 'grafana', 'proxmox'], 'server'),
    (['firewall', 'utm', 'fortigate', 'pfsense', 'sophos'], 'firewall'),
    (['vm', 'virtual machine', 'virtualizado', 'kvm', 'qemu', 'vmware', 'vps'], 'vm'),
    (['cpe', 'modem'], 'cpe'),
]


def tipo_topologia_do_acesso(acesso):
    """Tipo do device (chave de TOPO_DEVICES) para um `clientes.Acesso`."""
    funcao_nome = ((acesso.funcao.descricao or '') if acesso.funcao else '').lower()
    tipo_lower = (acesso.tipo or '').lower()
    for keywords, dev_tipo in MAPA_FUNCAO_TIPO:
        if any(k in funcao_nome or k in tipo_lower for k in keywords):
            return dev_tipo
    return 'host'
