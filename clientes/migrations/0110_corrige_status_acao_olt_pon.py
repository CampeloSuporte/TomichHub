"""Reavalia o status das ações PON já gravadas.

Até `detectar_erro_cli` existir, `olt_pon.executar` marcava `sucesso` sempre que
a conexão SSH não estourava exceção — mas o VRP responde a recusa no texto e
segue no prompt. Resultado: duas tentativas de `laser-switch` recusadas com
`% Parameter error` ficaram registradas como sucesso, dizendo que uma porta foi
desativada quando nada aconteceu.

Auditoria que mente é pior que auditoria que falta, então a correção alcança o
que já está gravado: relê o `output` de cada registro com o detector novo e
corrige o `status`. Não mexe em nenhum outro campo — o texto do equipamento
continua exatamente como veio.
"""
from django.db import migrations


def _corrigir(apps, schema_editor):
    from clientes.olt_pon import detectar_erro_cli

    AcaoOltPon = apps.get_model('clientes', 'AcaoOltPon')
    corrigidos = 0
    for acao in AcaoOltPon.objects.all().iterator():
        esperado = 'erro' if detectar_erro_cli(acao.output) else 'sucesso'
        if acao.status != esperado:
            acao.status = esperado
            acao.save(update_fields=['status'])
            corrigidos += 1
    if corrigidos:
        print(f'  AcaoOltPon: {corrigidos} registro(s) com status corrigido')


def _reverter(apps, schema_editor):
    """Sem volta: o status antigo era o errado, não há o que restaurar."""


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0109_acao_olt_pon'),
    ]

    operations = [
        migrations.RunPython(_corrigir, _reverter),
    ]
