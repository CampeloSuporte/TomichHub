"""
Racks físicos do cliente, o que está montado neles e os cabos entre portas.

A topologia (`clientes.TopologiaDiagrama`) é o desenho lógico e vive num JSON;
aqui é o inventário físico, em tabelas, porque tem regra que o banco precisa
garantir (dois equipamentos no mesmo U, uma porta com dois cabos). As duas
pontas se encontram por dois campos:

- `RackEquipamento.acesso` / `topologia_node_id` dizem qual node da topologia
  aquele equipamento é (host do CRM pelo acesso; node desenhado à mão pelo id);
- `ConexaoFisica.topologia_link_id` diz de qual enlace da topologia o cabo saiu.

Numeração de U segue a EIA-310: U1 é o de baixo.
"""
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q

from . import catalogo


class Rack(models.Model):
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='racks')
    nome = models.CharField(max_length=120)
    local = models.CharField(max_length=160, blank=True, default='',
                             help_text='Site, POP ou sala onde o rack está.')
    altura_u = models.PositiveSmallIntegerField(default=catalogo.ALTURA_RACK_PADRAO)
    observacoes = models.TextField(blank=True, default='')
    criado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['local', 'nome', 'id']
        verbose_name = 'Rack'
        verbose_name_plural = 'Racks'

    def __str__(self):
        return f'{self.nome} ({self.altura_u}U) — {self.cliente.nome_empresa}'


class RackEquipamento(models.Model):
    TIPOS = [(k, v['label']) for k, v in catalogo.TIPOS.items()]

    rack = models.ForeignKey(Rack, on_delete=models.CASCADE, related_name='equipamentos')
    tipo = models.CharField(max_length=20, choices=TIPOS, default='outro')
    nome = models.CharField(max_length=160)
    u_inicial = models.PositiveSmallIntegerField(help_text='U mais baixo ocupado (U1 = base do rack).')
    altura_u = models.PositiveSmallIntegerField(default=1)
    face = models.CharField(max_length=10, choices=catalogo.FACES, default='frente')
    profundidade_total = models.BooleanField(
        default=True, help_text='Ocupa frente e traseira do U. Passivos rasos (patch panel, PDU) não.')
    acesso = models.ForeignKey('clientes.Acesso', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='posicoes_rack')
    topologia_node_id = models.CharField(max_length=80, blank=True, default='',
                                         help_text='Id do node na topologia (para nodes que não são host do CRM).')
    fabricante = models.CharField(max_length=80, blank=True, default='')
    modelo = models.CharField(max_length=120, blank=True, default='')
    num_portas = models.PositiveSmallIntegerField(default=0)
    observacoes = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rack', '-u_inicial']
        verbose_name = 'Equipamento no rack'
        verbose_name_plural = 'Equipamentos no rack'
        constraints = [
            # Um host do CRM é um equipamento físico só: não fica em dois racks.
            models.UniqueConstraint(fields=['acesso'], condition=Q(acesso__isnull=False),
                                    name='rack_equip_acesso_unico'),
        ]

    @property
    def u_final(self):
        return self.u_inicial + self.altura_u - 1

    def __str__(self):
        return f'{self.nome} @ {self.rack.nome} U{self.u_inicial}'


class ConexaoFisica(models.Model):
    MEIOS = [(k, v['label']) for k, v in catalogo.MEIOS.items()]

    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='conexoes_fisicas')
    ponta_a = models.ForeignKey(RackEquipamento, on_delete=models.CASCADE, related_name='conexoes_a')
    porta_a = models.CharField(max_length=80, blank=True, default='')
    ponta_b = models.ForeignKey(RackEquipamento, on_delete=models.CASCADE, related_name='conexoes_b')
    porta_b = models.CharField(max_length=80, blank=True, default='')
    meio = models.CharField(max_length=12, choices=MEIOS, default='utp')
    conector = models.CharField(max_length=12, blank=True, default='')
    cor = models.CharField(max_length=9, blank=True, default='', help_text='Cor do cabo (hex).')
    comprimento_m = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    identificacao = models.CharField(max_length=120, blank=True, default='', help_text='Etiqueta do cabo.')
    topologia_link_id = models.CharField(max_length=80, blank=True, default='')
    sincronizado = models.BooleanField(
        default=False, help_text='Criado pela sincronização com a topologia e nunca editado à mão: '
                                 'acompanha o enlace (portas, tipo, etiqueta) e some junto com ele.')
    diagrama = models.ForeignKey('clientes.TopologiaDiagrama', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='+', help_text='Mapa da topologia onde está o enlace de origem.')
    observacoes = models.TextField(blank=True, default='')
    criado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Conexão física'
        verbose_name_plural = 'Conexões físicas'
        constraints = [
            # Um enlace da topologia vira no máximo um cabo.
            models.UniqueConstraint(fields=['cliente', 'topologia_link_id'], condition=~Q(topologia_link_id=''),
                                    name='conexao_fisica_link_unico'),
        ]

    def __str__(self):
        return f'{self.ponta_a.nome}:{self.porta_a} ↔ {self.ponta_b.nome}:{self.porta_b}'


class LinkSemCabo(models.Model):
    """Enlace da topologia cujo cabo a pessoa excluiu: a sincronização não
    o recria. Sai daqui pelo "Cabear de novo" da aba Conexões."""
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='+')
    topologia_link_id = models.CharField(max_length=80)
    criado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Enlace sem cabo'
        verbose_name_plural = 'Enlaces sem cabo'
        constraints = [models.UniqueConstraint(fields=['cliente', 'topologia_link_id'], name='link_sem_cabo_unico')]

    def __str__(self):
        return f'{self.topologia_link_id} — {self.cliente_id}'
