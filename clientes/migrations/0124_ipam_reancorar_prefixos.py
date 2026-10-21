import ipaddress

from django.db import migrations


def reancorar(apps, schema_editor):
    """
    Recalcula IPAMPrefixo.pai de todos os clientes. A análise de backup criava
    os containers /24 sem pai, e eles apareciam soltos na raiz em vez de dentro
    do 198.18.0.0/15 (ou de qualquer bloco que os contenha).

    Também desce o FK das sub-redes que apontam pra um bloco de cima quando há
    um prefixo mais específico que as contém (ex: 198.18.248.0/24 ligada ao
    /15 enquanto existe o container 198.18.248.0/24) — senão a pasta do /15
    continuaria mostrando a sub-rede fora do container dela.
    Lógica duplicada de _computar_pai_id de propósito (migration não importa
    código do app).
    """
    IPAMPrefixo = apps.get_model('clientes', 'IPAMPrefixo')
    IPAMSubRede = apps.get_model('clientes', 'IPAMSubRede')

    for cliente_id in IPAMPrefixo.objects.values_list('cliente_id', flat=True).distinct():
        nets = {}
        for pid, cidr in IPAMPrefixo.objects.filter(cliente_id=cliente_id).values_list('id', 'prefixo'):
            try:
                nets[pid] = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue

        def _mais_especifico(alvo, excluir_id=None, aceita_igual=False):
            melhor_id, melhor_pl = None, -1
            for oid, onet in nets.items():
                if oid == excluir_id or onet.version != alvo.version:
                    continue
                if onet.prefixlen > alvo.prefixlen or (onet.prefixlen == alvo.prefixlen and not aceita_igual):
                    continue
                if alvo.subnet_of(onet) and onet.prefixlen > melhor_pl:
                    melhor_id, melhor_pl = oid, onet.prefixlen
            return melhor_id

        for pid, alvo in nets.items():
            novo = _mais_especifico(alvo, excluir_id=pid)
            IPAMPrefixo.objects.filter(id=pid).exclude(pai_id=novo).update(pai_id=novo)

        for sid, rede, fk in (IPAMSubRede.objects.filter(cliente_id=cliente_id, prefixo_id__in=list(nets))
                              .values_list('id', 'rede', 'prefixo_id')):
            try:
                snet = ipaddress.ip_network(rede, strict=False)
            except ValueError:
                continue
            atual = nets[fk]
            if snet.version != atual.version or not snet.subnet_of(atual):
                continue   # FK escolhido à mão fora do bloco: não mexe
            melhor = _mais_especifico(snet, aceita_igual=True)
            if melhor and melhor != fk and nets[melhor].prefixlen > atual.prefixlen:
                IPAMSubRede.objects.filter(id=sid).update(prefixo_id=melhor)


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0123_transporte_enlace'),
    ]

    operations = [
        migrations.RunPython(reancorar, migrations.RunPython.noop),
    ]
