"""
Coleta do inventário AS-IS de um cliente.

Fontes, nesta ordem de confiança:
  1. último backup de configuração bem-sucedido de cada Acesso;
  2. topologia desenhada no CRM (enlaces com interface, IP e VLAN);
  3. cadastro (Acesso, função, modelo, blocos IP).

Só lê. Arquivo de backup ausente no disco é registrado como lacuna — nunca
apaga nem altera BackupLog (ver incidente de 16/09/2026 com MEDIA_ROOT vazio).
"""
import json
import logging
import os
import re

from django.conf import settings
from django.utils import timezone

from clientes.backup_parser import _detectar_vendor_backup, parse_backup
from clientes.models import Acesso, BackupLog, BlocoIP, TopologiaDiagrama

from . import extratores

logger = logging.getLogger(__name__)

# Tokens de nomenclatura que não identificam o POP no hostname
# (ex.: RTR-PE-JNA-CEN-01 → JNA; SW3-PE-RIO-APIACAS-CEN-01 → RIO-APIACAS).
_TOKENS_PAPEL = {
    'RTR', 'SW', 'SW1', 'SW2', 'SW3', 'PE', 'P', 'CE', 'CEN', 'CEM', 'BNG', 'BRAS', 'CGN',
    'CGNAT', 'RR', 'NE8K', 'NE40', 'NE8000', 'CCR', 'RB', 'REG', 'OLT', 'CORE', 'BORDA',
    'MAIN', 'SRV', 'SERVER', 'SERVVER', 'SVR', 'VM', 'POP', 'RT', 'ROUTER', 'SWITCH',
}

_VENDOR_POR_FABRICANTE = {
    'huawei': 'huawei', 'mikrotik': 'mikrotik', 'cisco': 'cisco', 'juniper': 'juniper',
    'datacom': 'datacom', 'zte': 'zte',
}


def pop_do_nome(nome):
    tokens = [t for t in re.split(r'[-_\s.]+', (nome or '').upper()) if t]
    uteis = [t for t in tokens if t not in _TOKENS_PAPEL and not t.isdigit()
             and not re.fullmatch(r'\d+[A-Z]?', t)]
    return '-'.join(uteis) or (nome or '')


def _ultimo_backup(acesso):
    return (BackupLog.objects
            .filter(acesso=acesso, status='SUCESSO')
            .exclude(arquivo_path='')
            .order_by('-data_backup')
            .first())


def _ler_arquivo(backup):
    caminho = os.path.join(str(settings.MEDIA_ROOT), backup.arquivo_path)
    if not os.path.isfile(caminho):
        return None
    try:
        with open(caminho, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError as e:
        logger.warning('AS-IS: leitura do backup %s falhou: %s', backup.id, e)
        return None


def _vendor(acesso, conteudo):
    fab = ((acesso.modelo.fabricante if acesso.modelo else '') or '').strip().lower()
    for chave, vendor in _VENDOR_POR_FABRICANTE.items():
        if chave in fab:
            return vendor
    if acesso.backup_template_id and acesso.backup_template:
        tpl = (acesso.backup_template.fabricante or '').lower()
        if tpl in _VENDOR_POR_FABRICANTE.values():
            return tpl
    return _detectar_vendor_backup(conteudo or '')


def _coletar_equipamento(acesso):
    eq = {
        'acesso_id': acesso.id,
        'nome': acesso.tipo or acesso.host,
        'host': acesso.host or '',
        'protocolo': acesso.protocolo or '',
        'funcao': str(acesso.funcao) if acesso.funcao else '',
        'modelo': acesso.modelo.nome if acesso.modelo else '',
        'fabricante': acesso.modelo.fabricante if acesso.modelo else '',
        'backup_habilitado': acesso.backup_habilitado,
        'backup': None,
        'vendor': '',
        'hostname': '',
        'pop': '',
        'extr': None,
        'generico': None,
        'l2vpn': [],
    }
    backup = _ultimo_backup(acesso) if acesso.backup_habilitado or acesso.protocolo == 'SSH' else None
    conteudo = _ler_arquivo(backup) if backup else None
    if backup:
        coletado = backup.ultima_verificacao or backup.data_backup
        eq['backup'] = {
            'id': backup.id,
            'data': timezone.localtime(backup.data_backup).isoformat(),
            'confirmado_em': timezone.localtime(coletado).isoformat(),
            'hash': backup.hash_conteudo or '',
            'arquivo': backup.arquivo_path,
            'arquivo_disponivel': conteudo is not None,
        }
    if conteudo:
        vendor = _vendor(acesso, conteudo)
        eq['vendor'] = vendor
        try:
            eq['extr'] = extratores.extrair(conteudo, vendor)
        except Exception:
            logger.exception('AS-IS: extrator %s falhou no acesso %s', vendor, acesso.id)
        try:
            gen = parse_backup(conteudo, eq['nome'], vendor_hint=vendor if vendor != 'desconhecido' else '')
            eq['l2vpn'] = [{k: v for k, v in s.items() if k != 'trecho'} for s in gen.get('l2vpn', [])]
            if not eq['extr']:
                eq['generico'] = {
                    'as_local': gen.get('as_local', ''),
                    'bgp': [{k: b.get(k, '') for k in ('peer_ip', 'peer_as', 'descricao', 'policy_in', 'policy_out')}
                            for b in gen.get('bgp', [])],
                    'ospf': gen.get('ospf', []),
                    'ips': gen.get('ips', [])[:200],
                    'modelo': gen.get('modelo', ''),
                }
        except Exception:
            logger.exception('AS-IS: parse_backup falhou no acesso %s', acesso.id)
        eq['hostname'] = (eq['extr'] or {}).get('hostname', '') or ''
    eq['pop'] = pop_do_nome(eq['hostname'] or eq['nome'])
    return eq


def _coletar_topologia(cliente):
    mapas, enlaces = [], []
    for diag in TopologiaDiagrama.objects.filter(cliente=cliente).order_by('pai_id', 'id'):
        try:
            dados = json.loads(diag.dados_json or '{}')
        except ValueError:
            continue
        nos = {n.get('id'): n for n in dados.get('nodes', []) if isinstance(n, dict)}
        mapas.append({'id': diag.id, 'nome': diag.nome, 'submapa': bool(diag.pai_id),
                      'nos': len(nos), 'enlaces': len(dados.get('links', []))})
        for lk in dados.get('links', []):
            a, b = nos.get(lk.get('src'), {}), nos.get(lk.get('tgt'), {})
            if not a or not b:
                continue
            enlaces.append({
                'mapa': diag.nome,
                'a': a.get('label', ''), 'a_acesso': a.get('acesso_id'),
                'b': b.get('label', ''), 'b_acesso': b.get('acesso_id'),
                'iface_a': lk.get('iface_a', ''), 'iface_b': lk.get('iface_b', ''),
                'ip_a': lk.get('ip_local', ''), 'ip_b': lk.get('ip_remote', ''),
                'vlan': lk.get('vlan', ''), 'capacidade': lk.get('iface', ''),
                'rotulo': lk.get('label', ''),
            })
    # O mesmo enlace aparece no mapa principal e no sub-mapa do POP
    vistos, unicos = set(), []
    for e in enlaces:
        chave = tuple(sorted([(e['a'], e['iface_a']), (e['b'], e['iface_b'])]))
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(e)
    return {'mapas': mapas, 'enlaces': unicos}


def coletar(cliente):
    acessos = (Acesso.objects.filter(cliente=cliente)
               .select_related('funcao', 'modelo', 'backup_template')
               .order_by('tipo'))
    equipamentos = [_coletar_equipamento(a) for a in acessos]
    blocos = [{'tipo': b.tipo, 'bloco': b.bloco, 'asn': b.asn or ''}
              for b in BlocoIP.objects.filter(cliente=cliente).order_by('tipo', 'bloco')]
    return {
        'cliente': {'id': cliente.id, 'nome': cliente.nome_empresa,
                    'cidade': cliente.cidade or '', 'estado': cliente.estado or ''},
        'gerado_em': timezone.localtime().isoformat(),
        'equipamentos': equipamentos,
        'topologia': _coletar_topologia(cliente),
        'blocos_ip': blocos,
    }
