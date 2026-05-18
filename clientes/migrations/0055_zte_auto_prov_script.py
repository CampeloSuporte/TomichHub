"""
Migration 0055 — Insere script ZTE Auto-Provisionamento em Massa (do Oltzte.py original)
"""
from django.db import migrations
import json


SCRIPT = {
    'nome': 'ZTE — Autorizar ONUs em Massa (Auto)',
    'descricao': (
        'Detecta automaticamente todas as ONUs não autorizadas na PON informada e as '
        'provisiona em sequência. Baseado no script original Oltzte.py.\n\n'
        'Algoritmo:\n'
        '1. Consulta ONUs já autorizadas (show gpon onu state)\n'
        '2. Consulta ONUs aguardando autorização (show pon onu un sn)\n'
        '3. Para cada ONU nova: executa bloco de configuração completo\n'
        '   (gpon_olt → gpon_onu → vport → pon-onu-mng)'
    ),
    'fabricante': 'zte',
    'modo_execucao': 'zte_auto_prov',
    'comandos': (
        '# Modo: ZTE Auto-Provisionamento em Massa\n'
        '# A lógica é executada automaticamente pelo motor da plataforma.\n'
        '# Parâmetros: PON, VLAN, TIPO_ONU, TCONT_PROFILE, NOME_PREFIX'
    ),
    'parametros': [
        {'nome': 'PON',           'label': 'Interface PON',    'tipo': 'text',   'default': '1/3/1',   'obrigatorio': True,  'ajuda': 'Ex: 1/3/1 ou 1/1/1',                            'opcoes': ''},
        {'nome': 'VLAN',          'label': 'VLAN do Cliente',  'tipo': 'number', 'default': '100',     'obrigatorio': True,  'ajuda': 'VLAN de internet do cliente',                   'opcoes': ''},
        {'nome': 'TIPO_ONU',      'label': 'Tipo ONU',         'tipo': 'select', 'default': 'F601',    'obrigatorio': True,  'ajuda': 'Modelo da ONU',                                 'opcoes': 'F601,F660,F670L,F6600P,ZXHN-F601'},
        {'nome': 'TCONT_PROFILE', 'label': 'Perfil TCONT',     'tipo': 'text',   'default': '1g',      'obrigatorio': True,  'ajuda': 'Ex: 1g, 100m, 50m',                            'opcoes': ''},
        {'nome': 'NOME_PREFIX',   'label': 'Prefixo Nome ONU', 'tipo': 'text',   'default': 'CLIENTE', 'obrigatorio': False, 'ajuda': 'Prefixo do nome da ONU. Ex: CLIENTE -> CLIENTE-5', 'opcoes': ''},
    ],
    'ativo': True,
}


def criar_script(apps, schema_editor):
    ScriptCRM = apps.get_model('clientes', 'ScriptCRM')
    if not ScriptCRM.objects.filter(nome=SCRIPT['nome']).exists():
        ScriptCRM.objects.create(
            nome=SCRIPT['nome'],
            descricao=SCRIPT['descricao'],
            fabricante=SCRIPT['fabricante'],
            modo_execucao=SCRIPT['modo_execucao'],
            comandos=SCRIPT['comandos'],
            parametros=SCRIPT['parametros'],
            ativo=SCRIPT['ativo'],
        )


def remover_script(apps, schema_editor):
    ScriptCRM = apps.get_model('clientes', 'ScriptCRM')
    ScriptCRM.objects.filter(nome=SCRIPT['nome']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0054_alter_scriptcrm_comandos_alter_scriptcrm_parametros'),
    ]

    operations = [
        migrations.RunPython(criar_script, remover_script),
    ]
