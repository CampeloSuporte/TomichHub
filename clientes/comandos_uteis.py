"""
Comandos úteis por família de equipamento — a 💡 do terminal web.

O terminal identifica o equipamento pelo cadastro (Modelo → fabricante +
nome; sem modelo, pelo `tipo` do acesso) e mostra os comandos de
verificação daquela família: sistema, interfaces, sinal óptico, L2,
roteamento, PON. Os grupos comuns são SOMENTE LEITURA (show/display/print).
Configuração só entra em grupo cujo título começa com ⚠ (`altera` no
catálogo): o painel destaca o grupo e o ▶ pede confirmação. O clique só
digita o comando no terminal do operador; executar é um segundo botão.

`conferido=True` só para o que foi rodado de verdade num equipamento da
família (Raisecom em 26/10/2026 num RAX711; o resto vem das funções já
validadas do Agent NOC e da automação BGP — ver docs/agent_noc.md e
docs/bgp_automacao.md; `show interface link` do Datacom confirmado pela equipe
em 26/10/2026). O resto é sintaxe documentada do fabricante, ainda
não rodada aqui: o painel marca a diferença. Quem conferir um comando ao vivo
troca a marca.

Campos variáveis vão entre chaves e viram um campo para preencher no painel:
`{porta}` (sugestões vêm das interfaces do último backup), `{ip}`, `{mac}`,
`{vlan}`, `{sn}`, `{slot}`, `{pon}`, `{onu}`, `{texto}`, `{usuario}`, `{senha}`,
`{tipo_onu}`, `{placa}`, `{arquivo}`.
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
    'usuario': 'Usuário',
    'senha': 'Senha',
    'tipo_onu': 'Tipo da ONU',
    'placa': 'Tipo da placa',
    'arquivo': 'Arquivo',
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
    'fiberhome_5k': {
        'nome': 'FiberHome OLT AN5516 (RP1000 / RP1200 / RP1800)',
        'linha': 'FiberHome',
        'versao': 'AN5516 · 5500',
        'aviso': 'A CLI é por diretórios: `cd onu`, `cd gpononu`, `cd service`… entram e `cd ..` volta. '
                 'Cada grupo começa com o `cd` do diretório certo. `lll` entra no modo DEBUG. '
                 'Grupos marcados ⚠ alteram o equipamento — o ▶ pede confirmação.',
        'exemplos': {'slot': '1', 'pon': '1', 'onu': '1', 'porta': '19:2', 'vlan': '100',
                     'sn': 'FHTT06ebe2e0', 'mac': '001122aabbcc', 'tipo_onu': '5506-01-a1',
                     'ip': '192.168.1.32', 'arquivo': 'OLT.txt', 'placa': 'gc8b'},
        'grupos': [
            ('Sistema', [
                _c('terminal length 0', 'Desliga a paginação (em Admin\\service#)'),
                _c('show cpuusage', 'Uso de CPU da OLT'),
                _c('show card', 'Placas e estado'),
                _c('sh auto_save', 'Auto save ligado e período'),
                _c('show system onusum authed_and_online', 'Total de ONUs autorizadas e online/offline'),
                _c('list {texto}', 'Procura comandos que contêm o texto'),
                _c('lll', 'Entra no modo DEBUG (Admin(DEBUG_H)>)'),
                _c('sh curalarm', 'Alarmes ativos (no modo DEBUG)'),
                _c('show debugv', 'Data de compilação do firmware da controladora (modo DEBUG)'),
            ]),
            ('ONU — diretório onu (RP1000)', [
                _c('cd onu', 'Entra em Admin\\onu#'),
                _c('show online slot {slot} pon {pon}', 'ONUs online na PON'),
                _c('show onu_state slot {slot} pon {pon} onulist {onu}', 'Ativa/desativada (aceita lista: 1,2,3)'),
                _c('show onu_time slot {slot} pon {pon} onu {onu}', 'Uptime da ONU'),
                _c('show onu_last_on_and_off_time slot {slot} pon {pon} onu {onu}', 'Última vez que desligou e ligou'),
                _c('show optic_module slot {slot} pon {pon} onu {onu}', 'Sinal óptico da ONU (RX/TX)'),
                _c('show optic_module_para slot {slot} pon {pon}', 'Sinal óptico de todas as ONUs da PON'),
                _c('show onu_vendor_and_equipment_info slot {slot} pon {pon}', 'Modelo, hardware e marca das ONUs da PON'),
                _c('show onu_ver slot {slot} pon {pon}', 'Versão de software/hardware das ONUs'),
                _c('show feport_status slot {slot} pon {pon} onu {onu}', 'Estado das portas LAN da ONU'),
                _c('show mac_list slot {slot} pon {pon} onu {onu} port 1', 'MACs aprendidos na porta LAN 1 da ONU'),
                _c('show onu_local_manage_config slot {slot} pon {pon} onu {onu}', 'Acesso web/telnet e porta HTTP da ONU'),
                _c('show energy_saving_cfg slot {slot} pon {pon} onu {onu}', 'Tipos de porta da ONU'),
            ]),
            ('ONU — diretório gpononu', [
                _c('cd gpononu', 'Entra em Admin\\gpononu#'),
                _c('sh discovery slot all link all', 'ONUs descobertas e ainda não autorizadas'),
                _c('sh authorization slot all link all', 'ONUs autorizadas'),
                _c('show mac_list slot {slot} link {pon} onu {onu} port 0', 'MACs aprendidos na ONU'),
            ]),
            ('PON — diretório gponline', [
                _c('cd gponline', 'Entra em Admin\\gponline#'),
                _c('sh optic_module_par slot {slot} link {pon}', 'TX/RX de todas as ONUs da PON'),
            ]),
            ('MAC / L2', [
                _c('show mac-address vlan {vlan}', 'MACs da VLAN (em Admin#)'),
                _c('show mac-address card 0/{slot}', 'MACs da placa PON (em Admin#)'),
                _c('cd device', 'Entra em Admin\\device#'),
                _c('show mac_find onu mac_address {mac}', 'Em que slot/PON/ONU está o MAC (Admin\\device#)'),
                _c('show port {porta}', 'Estado da porta uplink (Admin\\device#)'),
                _c('sh fdb slot {slot}', 'Tabela MAC da placa (Admin\\fdb#)'),
            ]),
            ('Gerência', [
                _c('cd service', 'Entra em Admin\\service#'),
                _c('show privateip', 'IP privado de acesso aos cards'),
                _c('show port_map', 'Mapa de portas (modo DEBUG)'),
            ]),
            ('⚠ Provisionar ONU', [
                _c('cd gpononu', 'Entra em Admin\\gpononu#'),
                _c('set authorization slot {slot} link {pon} type {tipo_onu} onuid {onu} phy_id {sn} password null logic_sn fiberhome password fiberhome',
                   'Autoriza a ONU pelo serial (phy_id)'),
                _c('set whitelist phy_addr address {sn} password null action add', 'Põe a ONU na whitelist'),
                _c('set whitelist phy_addr address {sn} password null action delete slot {slot} link {pon} onu {onu} type {tipo_onu}',
                   'Remove a ONU (deleta da whitelist)'),
                _c('set wifi_serv_cfg slot {slot} link {pon} onu {onu} wifi disable', 'Desliga o Wi-Fi da ONU'),
                _c('set onu_local_manage_config slot {slot} link {pon} onu {onu} config_enable_switch enable console_switch enable '
                   'telnet_switch enable web_switch enable web_port 80 web_ani_switch enable tel_ani_switch enable',
                   'Libera web/telnet da ONU pela WAN'),
                _c('set remote_manage_cfg slot {slot} link {pon} onu {onu} tr069 enable acs_url {texto} acl_user {usuario} '
                   'acl_pswd {senha} inform enable interval 0 port 30000 user user pswd user',
                   'TR-069: URL do ACS (no Texto) e usuário/senha do ACS'),
            ]),
            ('⚠ Serviço da ONU — diretório epononu\\qinq', [
                _c('cd epononu\ncd qinq', 'Entra em Admin\\epononu\\qinq#'),
                _c('set wancfg sl {slot} {pon} {onu} ind 1 mode inter ty r {vlan} 0 nat en qos dis qinq dis 33024 65535 1 '
                   'dsp pppoe pro dis {usuario} {senha} null auto active en', 'WAN PPPoE roteada na VLAN'),
                _c('set wanbind sl {slot} {pon} {onu} ind 1 entr 2 fe1 ssid1', 'Liga a WAN à LAN 1 e ao SSID 1'),
                _c('apply wancfg slot {slot} {pon} {onu}', 'Aplica a WAN'),
                _c('apply wanbind slot {slot} {pon} {onu}', 'Aplica o bind'),
                _c('set ep sl {slot} p {pon} o {onu} p 1 serv num 1', 'Bridge: 1 serviço na porta 1'),
                _c('set ep sl {slot} p {pon} o {onu} p 1 serv 1 vlan_m tag 4 33024 {vlan}', 'Bridge: VLAN tagueada no serviço 1'),
                _c('apply onu {slot} {pon} {onu} vlan', 'Aplica a VLAN da bridge'),
            ]),
            ('⚠ Uplink / LAG', [
                _c('cd device', 'Entra em Admin\\device#'),
                _c('set uplink port {porta} enable', 'Habilita a porta uplink'),
                _c('set uplink port {porta} auto_negotiation disable speed 1000m duplex full', 'Força 1G full'),
                _c('set uplink port {porta} interface_mode sgmii', 'Interface mode SGMII'),
                _c('set trunking groupno 1 mode lacp', 'LAG 1 em LACP'),
                _c('set trunking groupno 1 criteria sdmac', 'Balanceamento por MAC origem/destino'),
                _c('set lacp port {porta} key 1', 'Porta no LACP key 1 (em Admin\\device\\lacp#, depois de `set lacp enable`)'),
                _c('set mib performance switch enable', 'Habilita MIB de performance / todos os traps'),
                _c('save', 'Grava a configuração'),
            ]),
            ('⚠ Gerência / SNMP / Syslog', [
                _c('set debugip {ip} mask 255.255.255.0', 'IP da gerência outband (em Admin#)'),
                _c('cd service', 'Entra em Admin\\service#'),
                _c('set manage vlan name GERENCIA vid {vlan} inputport {porta} tagged', 'VLAN de gerência inband'),
                _c('set manage vlan name GERENCIA ip {ip}/24 {texto}', 'IP da gerência inband (gateway no Texto)'),
                _c('set snmp community readonly {texto}', 'Community SNMP somente leitura'),
                _c('set snmp community readwrite {texto}', 'Community SNMP leitura/escrita'),
                _c('set telnet acl 1 ip {ip} mask 255.255.255.0 enable', 'ACL de acesso telnet à OLT'),
                _c('set syslog_server func enable', 'Liga o envio de syslog'),
                _c('set syslog {ip} 100', 'Servidor de syslog'),
                _c('idle-timeout 0', 'Sessão telnet sem timeout'),
            ]),
            ('⚠ Usuários da CLI', [
                _c('cd service\nssh auth enable method password', 'Habilita SSH com senha'),
                _c('user add {usuario} login-password {senha}', 'Cria usuário (Admin\\service#; RP1200: em `cd aaa`)'),
                _c('user role {usuario} admin enable-password {senha}', 'Torna o usuário admin'),
                _c('user enable-password {usuario}', 'Troca a senha (pede a nova)'),
                _c('terminal user name {usuario} {senha} role admin', 'Cria usuário admin (RP1800)'),
            ]),
            ('⚠ Distância da PON', [
                _c('config\ninterface pon 1/{slot}/{pon}\ndistance max-distance 30 diff-distance 30',
                   'Distância máxima e diferencial da PON (30 km)'),
            ]),
            ('⚠ Sistema / backup', [
                _c('set auto_save enable period 1440', 'Auto save a cada 24 h'),
                _c('set save sync enable', 'Sincroniza a configuração entre as controladoras'),
                _c('set power_alarm enable 1', 'Alarme da fonte de energia'),
                _c('upload ftp config {ip} {usuario} {senha} {arquivo}', 'Backup da configuração para o FTP'),
                _c('download ftp config {ip} {usuario} {senha} {arquivo}', 'Restaura a configuração do FTP'),
                _c('download ftp system {ip} {usuario} {senha} {arquivo}', 'Atualiza a controladora'),
                _c('upgrade xdu {ip} {usuario} {senha} {arquivo} {slot}', 'Atualiza a placa de linha do slot'),
                _c('set card_auth slot {slot} type {placa}', 'Autoriza a placa no slot'),
                _c('set card_unauth slot {slot}', 'Desautoriza a placa do slot'),
                _c('set pon_interconnection_switch slot {slot} switch enable union_interconnect_switch enable',
                   'Aceita ONU de outros fabricantes (em Admin\\gponline#; depois `set sys_apply_flag 0 apply_sence 0`)'),
            ]),
        ],
    },
    'fiberhome_6k': {
        'nome': 'FiberHome OLT AN6000',
        'linha': 'FiberHome',
        'versao': 'AN6000 · 6k',
        'aviso': 'Frame é sempre 1 (`1/slot/porta`). Configuração em `config`. '
                 'Grupos marcados ⚠ alteram o equipamento — o ▶ pede confirmação.',
        'exemplos': {'slot': '7', 'pon': '1', 'porta': '1/9/4', 'ip': '192.168.1.252', 'vlan': '4002', 'placa': 'GPOA'},
        'grupos': [
            ('Sistema', [
                _c('show card info', 'Placas e estado'),
                _c('show manage-vlan all', 'VLANs de gerência'),
                _c('show ip address', 'IPs da OLT'),
                _c('show snmp-agent trap-receiver', 'Destinos de trap SNMP'),
            ]),
            ('ONU (telnet na placa)', [
                _c('telnet slot 1/{slot}', 'Entra na placa PON'),
                _c('cd onu\nshow discovery pono {pon}', 'ONUs descobertas na PON (dentro da placa)'),
                _c('cd ..\ncd omci\nshow stack_para', 'Parâmetros OMCI (dentro da placa)'),
            ]),
            ('⚠ Placas', [
                _c('config\ncard auth 1/{slot} {placa}', 'Autoriza a placa do slot'),
                _c('config\ncard auto-auth', 'Autoriza todas as placas'),
                _c('set stack_para msgtype 13 retry 3 rsp_time 3',
                   'Corrige "STE WORK MODE FAIL" de ONU Sumec (em omci, dentro da placa)'),
            ]),
            ('⚠ Uplink', [
                _c('config\ninterface eth {porta}\nshutdown\nauto-neg disable\nspeed 10000m\nduplex full\nport type XFI\nno shutdown',
                   'Força a uplink em 10G full (XFI)'),
            ]),
            ('⚠ Gerência / SNMP', [
                _c('config\ninterface meth 1\nip address {ip} mask 255.255.255.0', 'IP da porta de gerência (debug)'),
                _c('config\nmanage-vlan GR svlan {vlan} svlan-cos 0\nmanage-vlan ipv4 GR {ip}/30\nport vlan {vlan} tag {porta} 1',
                   'Gerência inband (porta como 1/9)'),
                _c('snmp-agent trap-receiver add ip {ip} security-name {texto} v2c', 'Destino de trap SNMP (community no Texto)'),
                _c('snmp-time interval 3600 servip ipv4 {ip}', 'Sincroniza a hora com o servidor (UNM)'),
                _c('save', 'Grava a configuração'),
            ]),
            ('⚠ Distância da PON', [
                _c('cd card', 'Entra em Admin\\card#'),
                _c('set pon_distance slot {slot} pon {pon} max_distance 20 diff_distance 40',
                   'Distância máxima (20 km) e diferencial (40 km) da PON'),
            ]),
            ('⚠ Ventilação', [
                _c('config\ninterface fan 1/23\nfan mode manual speed 1', 'Ventilador em velocidade manual'),
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
    if 'fiberhome' in texto or 'fiber home' in texto or re.search(r'\ban5516|\ban6000', texto):
        return 'fiberhome_6k' if re.search(r'an6000|\b6k\b', texto) else 'fiberhome_5k'
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
        grupos.append({'titulo': titulo, 'comandos': itens, 'altera': titulo.startswith('⚠')})
    return {
        'familia': familia,
        'nome': fam['nome'],
        'aviso': fam.get('aviso', ''),
        'exemplos': fam.get('exemplos', {}),
        'grupos': grupos,
        'versoes': versoes(familia),
    }


def versoes(familia):
    """Famílias da mesma linha (ex.: FiberHome 5500 e 6k) para o sub-menu de
    versão do painel. Vazio quando a família não tem irmãs."""
    linha = FAMILIAS[familia].get('linha')
    if not linha:
        return []
    return [{'familia': k, 'versao': v['versao']} for k, v in FAMILIAS.items() if v.get('linha') == linha]


def lista_familias():
    """Opções do seletor "Tipo". Uma linha com várias versões vira uma opção
    só (a primeira versão); a versão se troca no sub-menu do painel."""
    opcoes, linhas = [], {}
    for k, v in FAMILIAS.items():
        linha = v.get('linha')
        if not linha:
            opcoes.append({'familia': k, 'nome': v['nome'], 'membros': [k]})
        elif linha in linhas:
            linhas[linha]['membros'].append(k)
        else:
            linhas[linha] = {'familia': k, 'nome': f'{linha} OLT (escolha a versão)', 'membros': [k]}
            opcoes.append(linhas[linha])
    return opcoes
