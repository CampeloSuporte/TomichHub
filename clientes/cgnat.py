"""
Gerador de CGNAT determinístico para MikroTik (RouterOS v6 e v7).

Mesmo modelo do gerador da Redes Brasil (cgnat.redesbrasil.com.br), conferido
contra a saída real dele para 131.100.240.192/29 + 100.64.0.0/23
(clientes/tests_cgnat.py):

- o bloco privado é fatiado em sub-blocos do tamanho do bloco público; cada
  fatia é "um cliente por IP público" e recebe uma faixa de portas própria;
- `srcnat` pula para a chain `cgnat` (address-list com o bloco privado), que
  pula para uma chain por fatia; nela, TCP e UDP fazem `netmap` para o bloco
  público com `to-ports` da faixa da fatia — o netmap mantém o deslocamento,
  então 100.64.0.3 sai sempre por .195 (offset 3) nas portas da fatia 0;
- ICMP e qualquer outro protocolo (GRE, ESP…) caem nas regras sem porta no
  fim: `netmap` do bloco privado inteiro para o público, sem `to-ports`.

A tabela de mapeamento (IP privado → IP público + faixa de portas) é o que o
NOC consulta quando chega notificação de abuso com IP público:porta.

v6 x v7: as regras são as mesmas. O v7 sai com a sintaxe de caminho do v7
(`/ip/firewall/nat`), que o v6 não aceita, e nomes de chain sem "/"
(`cgnat-100.64.0.0-29`); o v6 mantém exatamente o formato da Redes Brasil
(chain = o próprio CIDR da fatia), para não divergir de quem já usa aquele
script.
"""
import csv
import io
import ipaddress
from dataclasses import dataclass, field

VERSOES = ('v6', 'v7')
ADDRESS_LIST = 'rede-privada-para-cgnat'
CHAIN = 'cgnat'
PORTA_MIN = 1024
PORTA_MAX = 65535
# Abaixo disso cada assinante fica com tão poucas portas que navegador/jogos
# começam a falhar; o gerador avisa, mas deixa gerar.
PORTAS_RECOMENDADAS = 1000
# Teto de fatias (chains) para o script não virar milhares de regras.
MAX_FATIAS = 4096
# Maior bloco privado por geração (a tabela de mapeamento tem 1 linha por IP).
PRIVADO_MIN_PREFIXO = 16

_CGNAT_NET = ipaddress.ip_network('100.64.0.0/10')


class CGNATErro(ValueError):
    """Entrada inválida — a mensagem vai direto para a tela."""


@dataclass
class Fatia:
    indice: int
    privado: ipaddress.IPv4Network
    porta_ini: int
    porta_fim: int

    @property
    def portas(self):
        return f'{self.porta_ini}-{self.porta_fim}'


@dataclass
class PlanoCGNAT:
    publico: ipaddress.IPv4Network
    privado: ipaddress.IPv4Network
    porta_inicial: int
    porta_final: int
    portas_por_cliente: int
    fatias: list = field(default_factory=list)
    avisos: list = field(default_factory=list)

    @property
    def ips_publicos(self):
        return self.publico.num_addresses

    @property
    def ips_privados(self):
        return self.privado.num_addresses

    @property
    def clientes_por_ip(self):
        return len(self.fatias)

    def resumo(self):
        return {
            'publico': str(self.publico),
            'privado': str(self.privado),
            'ips_publicos': self.ips_publicos,
            'ips_privados': self.ips_privados,
            'clientes_por_ip': self.clientes_por_ip,
            'portas_por_cliente': self.portas_por_cliente,
            'porta_inicial': self.porta_inicial,
            'porta_final': self.porta_final,
            'portas_sobrando': (self.porta_final - self.porta_inicial + 1)
                               - self.portas_por_cliente * self.clientes_por_ip,
        }

    def mapeamento(self):
        """[(ip_privado, ip_publico, porta_ini, porta_fim)] na ordem do bloco privado."""
        pubs = list(self.publico)
        linhas = []
        for f in self.fatias:
            for off, ip in enumerate(f.privado):
                linhas.append((str(ip), str(pubs[off]), f.porta_ini, f.porta_fim))
        return linhas

    def localizar(self, ip_publico, porta):
        """IP privado que saiu por `ip_publico`:`porta`, ou None."""
        try:
            ip = ipaddress.ip_address(str(ip_publico).strip())
            porta = int(porta)
        except ValueError:
            return None
        if ip not in self.publico:
            return None
        off = int(ip) - int(self.publico.network_address)
        for f in self.fatias:
            if f.porta_ini <= porta <= f.porta_fim:
                return str(f.privado[off])
        return None


def _rede(valor, nome):
    valor = (valor or '').strip()
    if not valor:
        raise CGNATErro(f'Informe o bloco {nome}.')
    if '/' not in valor:
        raise CGNATErro(f'Bloco {nome} "{valor}" sem máscara — use o formato 100.64.0.0/22.')
    try:
        rede = ipaddress.ip_network(valor)
    except ValueError:
        try:
            certo = ipaddress.ip_network(valor, strict=False)
        except ValueError:
            raise CGNATErro(f'Bloco {nome} "{valor}" não é um CIDR válido.')
        raise CGNATErro(
            f'Bloco {nome} "{valor}" não começa no endereço de rede — o certo é {certo}.')
    if rede.version != 4:
        raise CGNATErro(f'Bloco {nome} precisa ser IPv4 (CGNAT é IPv4).')
    return rede


def _int(valor, nome, padrao=None):
    if valor in (None, ''):
        return padrao
    try:
        return int(str(valor).strip())
    except ValueError:
        raise CGNATErro(f'{nome} precisa ser um número inteiro.')


def planejar(publico, privado, porta_inicial=PORTA_MIN, porta_final=PORTA_MAX,
             portas_por_cliente=None):
    """Valida as entradas e devolve o PlanoCGNAT. Levanta CGNATErro."""
    pub = _rede(publico, 'público')
    priv = _rede(privado, 'privado')

    if pub.is_private or _CGNAT_NET.overlaps(pub):
        raise CGNATErro(f'O bloco público {pub} é privado/CGNAT — informe o bloco roteável.')
    if not (priv.is_private or priv.subnet_of(_CGNAT_NET)):
        raise CGNATErro(
            f'O bloco privado {priv} não é RFC 1918 nem 100.64.0.0/10 (CGNAT).')
    if priv.prefixlen < PRIVADO_MIN_PREFIXO:
        raise CGNATErro(
            f'Bloco privado {priv} grande demais — gere em partes de até /{PRIVADO_MIN_PREFIXO}.')
    if pub.overlaps(priv):
        raise CGNATErro('Os blocos público e privado se sobrepõem.')
    if priv.prefixlen > pub.prefixlen:
        raise CGNATErro(
            f'O bloco privado ({priv}, {priv.num_addresses} IPs) é menor que o público '
            f'({pub}, {pub.num_addresses} IPs).')
    # Os dois são potências de 2 com privado ≥ público, então sempre divide;
    # mantido explícito porque é a regra que o netmap exige.
    if priv.num_addresses % pub.num_addresses:
        raise CGNATErro('O bloco privado precisa ser divisível pelo público.')

    ini = _int(porta_inicial, 'Porta inicial', PORTA_MIN)
    fim = _int(porta_final, 'Porta final', PORTA_MAX)
    if not (1 <= ini <= PORTA_MAX and 1 <= fim <= PORTA_MAX):
        raise CGNATErro('As portas vão de 1 a 65535.')
    if ini >= fim:
        raise CGNATErro('A porta inicial precisa ser menor que a final.')

    n_fatias = priv.num_addresses // pub.num_addresses
    if n_fatias > MAX_FATIAS:
        raise CGNATErro(
            f'{n_fatias} clientes por IP público é demais (máximo {MAX_FATIAS}) — '
            'use um bloco público maior ou um privado menor.')
    faixa = fim - ini + 1
    disponivel = faixa // n_fatias
    ppc = _int(portas_por_cliente, 'Portas por cliente', None) or disponivel
    if disponivel < 1:
        raise CGNATErro(
            f'Portas insuficientes: {faixa} portas para {n_fatias} clientes por IP público.')
    if ppc < 1:
        raise CGNATErro('Portas por cliente precisa ser maior que zero.')
    if ppc > disponivel:
        raise CGNATErro(
            f'Portas insuficientes: {n_fatias} clientes × {ppc} portas = {n_fatias * ppc}, '
            f'mas a faixa {ini}-{fim} tem {faixa} portas (máximo {disponivel} por cliente).')

    plano = PlanoCGNAT(publico=pub, privado=priv, porta_inicial=ini, porta_final=fim,
                       portas_por_cliente=ppc)
    for i, sub in enumerate(priv.subnets(new_prefix=pub.prefixlen)):
        p0 = ini + i * ppc
        plano.fatias.append(Fatia(i, sub, p0, p0 + ppc - 1))

    if ppc < PORTAS_RECOMENDADAS:
        plano.avisos.append(
            f'Só {ppc} portas por cliente — abaixo de {PORTAS_RECOMENDADAS} já há relato de '
            'falha em jogos e páginas pesadas.')
    if ini < PORTA_MIN:
        plano.avisos.append(
            f'A faixa começa em {ini}, dentro das portas reservadas (< {PORTA_MIN}).')
    return plano


def _chain(fatia, versao):
    if versao == 'v6':
        return str(fatia.privado)  # igual à Redes Brasil
    return f'{CHAIN}-{fatia.privado.network_address}-{fatia.privado.prefixlen}'


def gerar_script(plano, versao='v6', icmp=True, fallback=True):
    """Script .rsc pronto para colar no terminal do MikroTik."""
    if versao not in VERSOES:
        raise CGNATErro('Versão do RouterOS precisa ser v6 ou v7.')
    pub, priv = plano.publico, plano.privado
    r = plano.resumo()
    if versao == 'v6':
        nat, alist = '/ip firewall nat add ', '/ip firewall address-list add '
    else:
        nat, alist = '/ip/firewall/nat/add ', '/ip/firewall/address-list/add '

    out = [
        f'# ====== CGNAT {pub} <= {priv} — RouterOS {versao} (gerado pelo CRM) ======',
        f'# {r["ips_privados"]} IPs privados | {r["ips_publicos"]} IPs públicos | '
        f'{r["clientes_por_ip"]} clientes por IP público | {r["portas_por_cliente"]} portas por cliente',
        '',
        f'{alist}list={ADDRESS_LIST} address={priv}',
        f'{nat}action=jump chain=srcnat comment="Jump ==> CGNAT" jump-target={CHAIN} '
        f'src-address-list={ADDRESS_LIST}',
    ]
    for f in plano.fatias:
        ch = _chain(f, versao)
        out.append(
            f'{nat}action=jump chain={CHAIN} comment="Jump para rede ==> {f.privado} '
            f'portas={f.portas}" jump-target={ch} src-address={f.privado}')
        for proto in ('tcp', 'udp'):
            out.append(
                f'{nat}chain={ch} comment="{f.privado} ==> portas={f.portas}" protocol={proto} '
                f'action=netmap to-addresses={pub} to-ports={f.portas}')
    if icmp:
        out.append(
            f'{nat}chain=srcnat comment="ICMP {priv} ==> {pub}" protocol=icmp action=netmap '
            f'src-address={priv} to-addresses={pub}')
    if fallback:
        out.append(
            f'{nat}chain=srcnat comment="{priv} ==> {pub}" action=netmap '
            f'src-address={priv} to-addresses={pub}')
    out.append('# Fim do script CGNAT')
    return '\n'.join(out) + '\n'


def gerar_csv(plano):
    """CSV (;) com BOM para abrir direto no Excel."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow(['IP Privado', 'IP Público', 'Porta inicial', 'Porta final', 'Portas'])
    for priv, pub, p0, p1 in plano.mapeamento():
        w.writerow([priv, pub, p0, p1, f'{p0}-{p1}'])
    return '﻿' + buf.getvalue()


def nome_base(plano, versao):
    pub = str(plano.publico).replace('/', '_')
    priv = str(plano.privado).replace('/', '_')
    return f'CGNAT_{pub}__{priv}_{versao}'
