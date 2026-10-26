"""
Comandos úteis por família de equipamento — a 💡 do terminal web.

O terminal identifica o equipamento pelo cadastro (Modelo → fabricante +
nome; sem modelo, pelo `tipo` do acesso) e mostra os comandos de
verificação daquela família: sistema, interfaces, sinal óptico, L2,
roteamento, PON. Tudo aqui é SOMENTE LEITURA (show/display/print) — nada de
configuração entra neste catálogo. O clique só digita o comando no terminal
do operador; executar é um segundo botão.

`conferido=True` só para o que foi rodado de verdade num equipamento da
família (Raisecom em 26/10/2026 num RAX711; o resto vem das funções já
validadas do Agent NOC e da automação BGP — ver docs/agent_noc.md e
docs/bgp_automacao.md; `show interface link` do Datacom confirmado pela equipe
em 26/10/2026). O resto é sintaxe documentada do fabricante, ainda
não rodada aqui: o painel marca a diferença. Quem conferir um comando ao vivo
troca a marca.

Campos variáveis vão entre chaves e viram um campo para preencher no painel:
`{porta}` (sugestões vêm das interfaces do último backup), `{ip}`, `{mac}`,
`{vlan}`, `{sn}`, `{slot}`, `{pon}`, `{onu}`, `{texto}`.
"""
import re

# Rótulo de cada campo variável no painel.
CAMPOS = {
    'porta': 'Porta',
    'ip': 'IP',
    'mac': 'MAC',
    'vlan': 'VLAN',
    'sn': 'Serial da ONU',
    'slot': 'Slot',
    'pon': 'PON',
    'onu': 'ONU',
    'texto': 'Texto',
}

_CAMPO_RE = re.compile(r'\{(' + '|'.join(CAMPOS) + r')\}')


def _c(cmd, desc, conferido=False):
    return {'cmd': cmd, 'desc': desc, 'conferido': conferido}


FAMILIAS = {
    'huawei_vrp': {
        'nome': 'Huawei VRP (switch / roteador)',
        'exemplos': {'porta': 'XGigabitEthernet0/0/1', 'mac': 'aabb-ccdd-eeff', 'vlan': '100'},
        'grupos': [
            ('Sistema', [
                _c('display version', 'Versão do VRP, modelo e uptime'),
                _c('display device', 'Placas, fontes e ventiladores'),
                _c('display cpu-usage', 'Uso de CPU'),
                _c('display memory-usage', 'Uso de memória'),
                _c('display clock', 'Data e hora do equipamento'),
                _c('display alarm active', 'Alarmes ativos'),
            ]),
            ('Interfaces', [
                _c('display interface brief', 'Estado, utilização e erros de todas as portas'),
                _c('display interface description', 'Portas com descrição', True),
                _c('display interface {porta}', 'Detalhe da porta: estado, velocidade, erros, tipo do SFP'),
                _c('display ip interface brief', 'Interfaces com IP'),
            ]),
            ('Sinal óptico', [
                _c('display transceiver diagnosis interface {porta}', 'TX/RX (dBm), temperatura e tensão', True),
                _c('display transceiver interface {porta} verbose', 'Tipo do módulo, λ, distância e DDM'),
            ]),
            ('L2', [
                _c('display vlan {vlan}', 'Portas da VLAN'),
                _c('display mac-address {mac}', 'Em que porta está o MAC'),
                _c('display lldp neighbor brief', 'Vizinhos LLDP'),
                _c('display eth-trunk', 'Agregações (Eth-Trunk) e membros'),
            ]),
            ('Roteamento', [
                _c('display ip routing-table {ip}', 'Rota usada para o IP'),
                _c('display bgp peer', 'Sessões BGP IPv4', True),
                _c('display bgp routing-table peer {ip} received-routes', 'Rotas recebidas do peer', True),
                _c('display bgp routing-table peer {ip} advertised-routes', 'Rotas anunciadas ao peer', True),
                _c('display ospf peer brief', 'Vizinhos OSPF'),
                _c('display arp | include {ip}', 'Entrada ARP do IP'),
            ]),
            ('Configuração', [
                _c('display current-configuration interface {porta}', 'Configuração só desta porta'),
                _c('display current-configuration | include {texto}', 'Linhas da configuração com o texto'),
            ]),
        ],
    },
    'huawei_olt': {
        'nome': 'Huawei OLT (MA5800 / MA5600)',
        'aviso': 'Rode `enable` antes. Alguns comandos pedem confirmação `{ <cr>||<K> }:` — tecle Enter.',
        'exemplos': {'slot': '1', 'pon': '0', 'onu': '1', 'sn': '48575443XXXXXXXX'},
        'grupos': [
            ('Sistema', [
                _c('enable', 'Entra no modo privilegiado'),
                _c('display version', 'Versão e uptime'),
                _c('display board 0', 'Placas do chassi e estado'),
                _c('display alarm active all', 'Alarmes ativos'),
            ]),
            ('ONU', [
                _c('display ont autofind all', 'ONUs descobertas e ainda não provisionadas'),
                _c('display ont info by-sn {sn}', 'Onde está a ONU pelo serial'),
                _c('display ont info 0 {slot} {pon} {onu}', 'Estado, distância e último motivo de queda da ONU'),
                _c('display ont info summary 0/{slot}/{pon}', 'Resumo de todas as ONUs da PON'),
                _c('display service-port port 0/{slot}/{pon} ont {onu}', 'Service-ports (VLANs) da ONU'),
            ]),
            ('Sinal óptico', [
                _c('config\ninterface gpon 0/{slot}\ndisplay ont optical-info {pon} {onu}\nquit\nquit',
                   'RX/TX da ONU e RX na OLT (entra e sai do modo da placa)'),
            ]),
        ],
    },
    'datacom': {
        'nome': 'Datacom DmOS (switch)',
        'exemplos': {'porta': 'ten-gigabit-ethernet-1/1/1', 'ip': '8.8.8.8'},
        'grupos': [
            ('Sistema', [
                _c('show platform', 'Placas, fontes e estado'),
                _c('show system cpu', 'Uso de CPU'),
                _c('show system memory', 'Uso de memória'),
                _c('show alarm', 'Alarmes ativos'),
            ]),
            ('Interfaces', [
                _c('show interface link', 'Estado, velocidade e duplex de cada porta', True),
                _c('show interface description', 'Portas com estado e descrição', True),
                _c('show interface {porta}', 'Detalhe da porta e contadores'),
            ]),
            ('Sinal óptico', [
                _c('show interface transceivers', 'TX/RX de TODOS os SFPs — procure a porta na tabela', True),
            ]),
            ('L2', [
                _c('show mac-address-table', 'Tabela MAC'),
                _c('show lldp neighbors', 'Vizinhos LLDP'),
            ]),
            ('Roteamento', [
                _c('show ip route {ip}', 'Rota usada para o IP'),
                _c('show ip route', 'Tabela de rotas'),
            ]),
            ('Configuração', [
                _c('show running-config interface {porta}', 'Configuração só desta porta'),
            ]),
        ],
    },
    'datacom_olt': {
        'nome': 'Datacom DmOS (OLT DM4610 / DM4615)',
        'exemplos': {'porta': 'ten-gigabit-ethernet-1/1/1', 'pon': '1/1/1', 'onu': '1'},
        'grupos': [
            ('Sistema', [
                _c('show platform', 'Placas, fontes e estado'),
                _c('show alarm', 'Alarmes ativos'),
            ]),
            ('ONU', [
                _c('show interface gpon discovered-onus', 'ONUs descobertas e ainda não provisionadas'),
                _c('show interface gpon {pon} onu', 'ONUs da PON com estado'),
                _c('show interface gpon {pon} onu {onu}', 'Detalhe da ONU, incluindo potência'),
            ]),
            ('Interfaces', [
                _c('show interface link', 'Estado, velocidade e duplex de cada porta', True),
                _c('show interface description', 'Portas com estado e descrição', True),
                _c('show interface transceivers', 'TX/RX de todos os SFPs de uplink', True),
            ]),
        ],
    },
    'cisco_ios': {
        'nome': 'Cisco IOS / IOS-XE',
        'aviso': '`terminal length 0` desliga a paginação nesta sessão.',
        'exemplos': {'porta': 'GigabitEthernet0/0/0', 'ip': '8.8.8.8'},
        'grupos': [
            ('Sistema', [
                _c('terminal length 0', 'Desliga a paginação (--More--)'),
                _c('show version', 'Versão, modelo e uptime'),
                _c('show inventory', 'Módulos e seriais'),
                _c('show processes cpu sorted 5min', 'Processos que mais usam CPU'),
                _c('show logging', 'Log do equipamento'),
            ]),
            ('Interfaces', [
                _c('show ip interface brief', 'Interfaces, IP e estado'),
                _c('show interfaces description', 'Portas com descrição', True),
                _c('show interfaces {porta}', 'Detalhe da porta, erros e taxa'),
            ]),
            ('Sinal óptico', [
                _c('show interfaces {porta} transceiver', 'TX/RX (dBm) da porta'),
            ]),
            ('Roteamento', [
                _c('show ip route {ip}', 'Rota usada para o IP'),
                _c('show ip bgp summary', 'Sessões BGP IPv4'),
                _c('show bgp ipv6 unicast summary', 'Sessões BGP IPv6'),
                _c('show ip bgp neighbors {ip} received-routes', 'Rotas recebidas do peer', True),
                _c('show ip bgp neighbors {ip} advertised-routes', 'Rotas anunciadas ao peer', True),
                _c('show ip ospf neighbor', 'Vizinhos OSPF'),
                _c('show arp', 'Tabela ARP'),
            ]),
            ('Configuração', [
                _c('show running-config interface {porta}', 'Configuração só desta porta'),
            ]),
        ],
    },
    'cisco_nxos': {
        'nome': 'Cisco NX-OS (Nexus)',
        'exemplos': {'porta': 'Ethernet1/1', 'ip': '8.8.8.8'},
        'grupos': [
            ('Sistema', [
                _c('terminal length 0', 'Desliga a paginação (--More--)'),
                _c('show version', 'Versão, modelo e uptime'),
                _c('show system resources', 'CPU e memória'),
                _c('show module', 'Módulos e estado'),
            ]),
            ('Interfaces', [
                _c('show interface brief', 'Estado de todas as portas'),
                _c('show interface description', 'Portas com descrição'),
                _c('show interface {porta}', 'Detalhe da porta, erros e taxa'),
            ]),
            ('Sinal óptico', [
                _c('show interface {porta} transceiver details', 'TX/RX (dBm) e alarmes do SFP'),
            ]),
            ('L2', [
                _c('show mac address-table', 'Tabela MAC'),
                _c('show vlan brief', 'VLANs e portas'),
                _c('show lldp neighbors', 'Vizinhos LLDP'),
            ]),
            ('Roteamento', [
                _c('show ip route {ip}', 'Rota usada para o IP'),
                _c('show bgp ipv4 unicast summary', 'Sessões BGP IPv4'),
            ]),
            ('Configuração', [
                _c('show running-config interface {porta}', 'Configuração só desta porta'),
            ]),
        ],
    },
    'juniper': {
        'nome': 'Juniper Junos (MX)',
        'exemplos': {'porta': 'xe-0/0/0', 'ip': '8.8.8.8'},
        'grupos': [
            ('Sistema', [
                _c('show version', 'Versão e modelo'),
                _c('show chassis hardware', 'Placas, módulos e seriais'),
                _c('show chassis alarms', 'Alarmes de hardware'),
                _c('show system alarms', 'Alarmes de sistema'),
                _c('show chassis routing-engine', 'CPU, memória e uptime da RE'),
                _c('show log messages | last 30', 'Últimas 30 linhas do log'),
            ]),
            ('Interfaces', [
                _c('show interfaces terse', 'Estado de todas as interfaces'),
                _c('show interfaces descriptions', 'Interfaces com descrição'),
                _c('show interfaces {porta} extensive | match error', 'Contadores de erro da porta'),
            ]),
            ('Sinal óptico', [
                _c('show interfaces diagnostics optics {porta}', 'TX/RX (dBm), temperatura e alarmes'),
            ]),
            ('Roteamento', [
                _c('show route {ip}', 'Rota usada para o IP'),
                _c('show bgp summary', 'Sessões BGP'),
                _c('show route receive-protocol bgp {ip}', 'Rotas recebidas do peer', True),
                _c('show route advertising-protocol bgp {ip}', 'Rotas anunciadas ao peer', True),
                _c('show ospf neighbor', 'Vizinhos OSPF'),
                _c('show arp no-resolve', 'Tabela ARP'),
                _c('show lldp neighbors', 'Vizinhos LLDP'),
            ]),
            ('Configuração', [
                _c('show configuration interfaces {porta} | display set', 'Configuração da porta em formato set'),
            ]),
        ],
    },
    'mikrotik': {
        'nome': 'MikroTik RouterOS',
        'exemplos': {'porta': 'sfp-sfpplus1', 'ip': '8.8.8.8'},
        'grupos': [
            ('Sistema', [
                _c('/system resource print', 'CPU, memória, uptime e versão'),
                _c('/system routerboard print', 'Modelo, serial e firmware'),
                _c('/system health print', 'Temperatura, tensão e fontes'),
                _c('/log print where topics~"error|critical"', 'Erros no log'),
            ]),
            ('Interfaces', [
                _c('/interface print', 'Interfaces e estado (R = running)'),
                _c('/interface ethernet print', 'Portas físicas'),
                _c('/interface monitor-traffic {porta} once', 'Tráfego atual da porta'),
                _c('/ip address print', 'Endereços IP'),
            ]),
            ('Sinal óptico', [
                _c('/interface ethernet monitor {porta} once', 'Link, velocidade e DDM do SFP (sfp-rx-power)'),
            ]),
            ('L2', [
                _c('/ip neighbor print', 'Vizinhos (MNDP/CDP/LLDP)'),
                _c('/ip arp print', 'Tabela ARP'),
                _c('/interface bridge host print', 'MACs aprendidos na bridge'),
            ]),
            ('Roteamento', [
                _c('/ip route print where dst-address="{ip}"', 'Rota com este destino'),
                _c('/routing bgp session print', 'Sessões BGP (RouterOS v7)', True),
                _c('/routing bgp peer print status', 'Sessões BGP (RouterOS v6)', True),
                _c('/routing ospf neighbor print', 'Vizinhos OSPF'),
                _c('/ppp active print count-only', 'Quantos PPPoE estão conectados'),
            ]),
        ],
    },
    'zte_c300': {
        'nome': 'ZTE OLT C300 / C320',
        'aviso': '`terminal length 0` desliga a paginação nesta sessão.',
        'exemplos': {'pon': '1/1/1', 'onu': '1'},
        'grupos': [
            ('Sistema', [
                _c('terminal length 0', 'Desliga a paginação'),
                _c('show card', 'Placas e estado'),
                _c('show version-running', 'Versão de software das placas'),
            ]),
            ('ONU', [
                _c('show gpon onu uncfg', 'ONUs descobertas e ainda não provisionadas'),
                _c('show gpon onu state gpon-olt_{pon}', 'Estado de todas as ONUs da PON'),
                _c('show gpon onu detail-info gpon-onu_{pon}:{onu}', 'Detalhe da ONU e histórico de quedas'),
                _c('show running-config interface gpon-onu_{pon}:{onu}', 'Configuração da ONU'),
            ]),
            ('Sinal óptico', [
                _c('show pon power attenuation gpon-onu_{pon}:{onu}', 'Potência OLT↔ONU nos dois sentidos'),
            ]),
        ],
    },
    'zte_titan': {
        'nome': 'ZTE OLT TITAN (C600 / C610 / C650)',
        'aviso': '`terminal length 0` desliga a paginação nesta sessão.',
        'exemplos': {'pon': '1/1/1', 'onu': '1'},
        'grupos': [
            ('Sistema', [
                _c('terminal length 0', 'Desliga a paginação'),
                _c('show card', 'Placas e estado'),
            ]),
            ('ONU', [
                _c('show pon onu uncfg', 'ONUs descobertas e ainda não provisionadas'),
                _c('show gpon onu state gpon_olt-{pon}', 'Estado de todas as ONUs da PON'),
                _c('show gpon onu detail-info gpon_onu-{pon}:{onu}', 'Detalhe da ONU e histórico de quedas'),
            ]),
            ('Sinal óptico', [
                _c('show pon power attenuation gpon_onu-{pon}:{onu}', 'Potência OLT↔ONU nos dois sentidos'),
            ]),
        ],
    },
    'raisecom': {
        'nome': 'Raisecom ROS (RAX700 / ISCOM)',
        'aviso': 'No DDM use o nome completo da porta (`tengigabitethernet 1/1/1`, `gigaethernet 1/2/1`).',
        'exemplos': {'porta': 'tengigabitethernet 1/1/1'},
        'grupos': [
            ('Sistema', [
                _c('show version', 'Modelo, versão do ROS e uptime', True),
                _c('terminal page-break disable', 'Desliga a paginação (--More--)', True),
            ]),
            ('Interfaces', [
                _c('show interface description', 'Estado e descrição de todas as portas', True),
                _c('show interface brief', 'Velocidade, utilização e erros', True),
            ]),
            ('Sinal óptico', [
                _c('show transceiver ddm {porta}', 'TX/RX (dBm), temperatura e tensão da porta', True),
                _c('show transceiver information {porta}', 'Tipo do módulo, λ, distância, se tem DDM', True),
            ]),
        ],
    },
    'linux': {
        'nome': 'Linux (Debian / Ubuntu)',
        'exemplos': {'ip': '8.8.8.8', 'texto': 'nginx'},
        'grupos': [
            ('Sistema', [
                _c('uptime', 'Tempo ligado e carga'),
                _c('free -m', 'Memória'),
                _c('df -h', 'Disco'),
                _c('top -bn1 | head -15', 'Processos que mais consomem'),
                _c('systemctl --failed --no-pager', 'Serviços com falha'),
                _c('journalctl -p err -n 30 --no-pager', 'Últimos erros do sistema'),
                _c('systemctl status {texto} --no-pager', 'Estado de um serviço'),
            ]),
            ('Rede', [
                _c('ip -br a', 'Interfaces e endereços (resumido)'),
                _c('ip r', 'Rotas'),
                _c('ip r get {ip}', 'Rota usada para o IP'),
                _c('ss -tulpn', 'Portas abertas e processos'),
                _c('ping -c 4 {ip}', '4 pings'),
            ]),
        ],
    },
    'proxmox': {
        'nome': 'Proxmox VE',
        'exemplos': {'texto': 'pveproxy'},
        'grupos': [
            ('Proxmox', [
                _c('pveversion', 'Versão do Proxmox'),
                _c('qm list', 'VMs e estado'),
                _c('pct list', 'Containers e estado'),
                _c('pvesm status', 'Storages e uso'),
            ]),
            ('Sistema', [
                _c('uptime', 'Tempo ligado e carga'),
                _c('free -m', 'Memória'),
                _c('df -h', 'Disco'),
                _c('systemctl --failed --no-pager', 'Serviços com falha'),
            ]),
        ],
    },
}


def identificar_familia(acesso):
    """Família do catálogo pelo cadastro: Modelo (fabricante + nome) e, sem
    modelo, o `tipo` do acesso. Devolve None quando não reconhece — o painel
    deixa o operador escolher."""
    modelo = getattr(acesso, 'modelo', None)
    fab = (getattr(modelo, 'fabricante', '') or '').lower()
    nome = (getattr(modelo, 'nome', '') or '').lower()
    tipo = (getattr(acesso, 'tipo', '') or '').lower()
    texto = f'{fab} {nome}' if (fab or nome) else tipo

    if 'huawei' in texto:
        return 'huawei_olt' if re.search(r'\bma5[68]\d\d|\bolt\b', texto) else 'huawei_vrp'
    if 'datacom' in texto:
        return 'datacom_olt' if re.search(r'dm46\d\d|\bolt\b', texto) else 'datacom'
    if 'cisco' in texto:
        return 'cisco_nxos' if re.search(r'nexus|nx-?os', texto) else 'cisco_ios'
    if 'juniper' in texto or re.search(r'\bmx\d', texto):
        return 'juniper'
    if 'mikrotik' in texto or 'routeros' in texto:
        return 'mikrotik'
    if 'zte' in texto or 'zxa10' in texto:
        return 'zte_titan' if re.search(r'titan|c6\d\d', texto) else 'zte_c300'
    if 'raisecom' in texto:
        return 'raisecom'
    if 'proxmox' in texto:
        return 'proxmox'
    if re.search(r'debian|ubuntu|linux', texto):
        return 'linux'
    return None


def catalogo(familia):
    """Grupos da família no formato do painel, com os campos variáveis de
    cada comando já listados."""
    fam = FAMILIAS[familia]
    grupos = []
    for titulo, comandos in fam['grupos']:
        itens = []
        for item in comandos:
            campos = list(dict.fromkeys(_CAMPO_RE.findall(item['cmd'])))
            itens.append({**item, 'campos': campos})
        grupos.append({'titulo': titulo, 'comandos': itens})
    return {
        'familia': familia,
        'nome': fam['nome'],
        'aviso': fam.get('aviso', ''),
        'exemplos': fam.get('exemplos', {}),
        'grupos': grupos,
    }


def lista_familias():
    return [{'familia': k, 'nome': v['nome']} for k, v in FAMILIAS.items()]
