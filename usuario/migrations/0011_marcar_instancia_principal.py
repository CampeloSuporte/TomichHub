"""Marca a operação própria do Administrador como `Instancia.principal`.

A instância "Principal" (id 25) foi criada em 19/08/2026 para receber os 47
clientes que estavam com `instancia = NULL` — ver `docs/PERMISSOES_CONSULTOR.md`.
Ela não é uma revenda: é o Administrador operando. O módulo de Atendimento é
exclusivo dela, então a flag precisa existir no banco antes de
`perms.pode_acessar_atendimento` valer alguma coisa.

Casa por nome (não por id) e só quando houver exatamente uma candidata — em
banco novo/de teste não há nenhuma e a migração não faz nada, que é o correto:
sem instância principal, o Atendimento fica só com o Administrador.
"""
from django.db import migrations


def marcar(apps, schema_editor):
    Instancia = apps.get_model('usuario', 'Instancia')
    candidatas = Instancia.objects.filter(nome__iexact='Principal')
    if candidatas.count() == 1:
        candidatas.update(principal=True)


def desmarcar(apps, schema_editor):
    Instancia = apps.get_model('usuario', 'Instancia')
    Instancia.objects.filter(principal=True).update(principal=False)


class Migration(migrations.Migration):

    dependencies = [
        ('usuario', '0010_instancia_principal'),
    ]

    operations = [
        migrations.RunPython(marcar, desmarcar),
    ]
