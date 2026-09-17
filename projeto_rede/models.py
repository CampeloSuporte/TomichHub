"""
Documentos de arquitetura de rede por cliente.

Três tipos de documento, todos no mesmo modelo e no mesmo editor:
  - `hld`: arquitetura/convenção (dados estruturados em `dados['convencao']`);
  - `asis`: estado atual reconstruído dos backups;
  - `change_plan`: Change Plan TO-BE (AS-IS em `documento_base`, HLD e
    cenário em `dados['hld_id']`/`dados['cenario_id']`, mapeamentos manuais
    em `dados['mapeamentos']`).

O conteúdo fica em `secoes` (lista de {id, chave, titulo, html, auto}) — é
sempre lido e gravado inteiro pelo editor, então não compensa normalizar.
"""
import json

from django.contrib.auth.models import User
from django.db import models


class JSONTextoField(models.TextField):
    """JSON guardado como texto UTF-8.

    O `crm_db` é SQL_ASCII: o `JSONField` do Django serializa acentos como
    `\\u00ed` e o jsonb recusa esses escapes nessa codificação ("unsupported
    Unicode escape sequence"). Estes documentos são texto em português do
    começo ao fim, então ficam num TextField — que já guarda UTF-8 cru —
    como o `TopologiaDiagrama.dados_json`. Nunca é filtrado por conteúdo.
    """

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None or isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value) if value else self.get_default()
        except ValueError:
            return self.get_default()

    def get_prep_value(self, value):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

    def value_to_string(self, obj):
        return self.get_prep_value(self.value_from_object(obj))


class DocumentoRede(models.Model):
    TIPO_HLD = 'hld'
    TIPO_ASIS = 'asis'
    TIPO_CHANGE_PLAN = 'change_plan'
    TIPOS = [
        (TIPO_HLD, 'HLD — Arquitetura de rede'),
        (TIPO_ASIS, 'AS-IS da infraestrutura'),
        (TIPO_CHANGE_PLAN, 'Change Plan TO-BE'),
    ]
    STATUS_RASCUNHO = 'rascunho'
    STATUS = [
        (STATUS_RASCUNHO, 'Rascunho'),
        ('revisao', 'Em revisão'),
        ('fechado', 'Fechado tecnicamente'),
        ('apresentado', 'Apresentado ao cliente'),
    ]

    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='documentos_rede')
    tipo = models.CharField(max_length=20, choices=TIPOS, default=TIPO_ASIS)
    documento_base = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='derivados',
        help_text='AS-IS de origem de um Change Plan TO-BE.')
    titulo = models.CharField(max_length=255)
    versao = models.CharField(max_length=20, default='1.0')
    status = models.CharField(max_length=20, choices=STATUS, default=STATUS_RASCUNHO)
    metadados = JSONTextoField(default=dict, blank=True)
    secoes = JSONTextoField(default=list, blank=True)
    dados = JSONTextoField(default=dict, blank=True,
                           help_text='Conteúdo estruturado (convenção do HLD, referências e mapeamentos do TO-BE).')
    coleta = JSONTextoField(default=dict, blank=True,
                              help_text='Resumo das fontes usadas na geração (backups, hashes, datas).')
    criado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    atualizado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-atualizado_em']
        verbose_name = 'Documento de rede'
        verbose_name_plural = 'Documentos de rede'

    def __str__(self):
        return f'{self.titulo} v{self.versao} — {self.cliente.nome_empresa}'


class DocumentoRedeRevisao(models.Model):
    """Fotografia do documento num momento (geração, marco de revisão ou
    antes de regerar uma seção) — permite comparar e restaurar."""
    documento = models.ForeignKey(DocumentoRede, on_delete=models.CASCADE, related_name='revisoes')
    versao = models.CharField(max_length=20)
    status = models.CharField(max_length=20, blank=True, default='')
    metadados = JSONTextoField(default=dict, blank=True)
    secoes = JSONTextoField(default=list, blank=True)
    motivo = models.CharField(max_length=255, blank=True, default='')
    autor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Revisão de documento de rede'
        verbose_name_plural = 'Revisões de documentos de rede'

    def __str__(self):
        return f'{self.documento_id} v{self.versao} — {self.motivo}'


class CenarioTopologia(models.Model):
    """Topologia alvo (TO-BE) de um cliente.

    Fica fora de `TopologiaDiagrama` de propósito: o editor de topologia
    resolve o mapa principal por `pai IS NULL` e o AS-IS lê todos os mapas do
    cliente — um clone ali quebraria os dois. O formato de `dados_json` é o
    mesmo do editor; os atributos de alvo ficam em `node.tobe` e `link.tobe`.
    """
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='cenarios_topologia')
    nome = models.CharField(max_length=255, default='Cenário TO-BE')
    origem = models.ForeignKey('clientes.TopologiaDiagrama', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='+', verbose_name='Mapa de origem')
    dados_json = models.TextField(default='{"nodes":[],"links":[]}')
    criado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-atualizado_em']
        verbose_name = 'Cenário de topologia TO-BE'
        verbose_name_plural = 'Cenários de topologia TO-BE'

    def __str__(self):
        return f'{self.nome} — {self.cliente.nome_empresa}'

    def dados(self):
        try:
            d = json.loads(self.dados_json or '{}')
        except ValueError:
            d = {}
        return {'nodes': d.get('nodes') or [], 'links': d.get('links') or []}

    def resumo(self):
        d = self.dados()
        nos = [n for n in d['nodes'] if n.get('type') not in ('area', 'text_box')]
        estados = [(n.get('tobe') or {}).get('estado') for n in nos]
        return {
            'nos': len(nos), 'enlaces': len(d['links']),
            'novos': estados.count('novo'), 'remover': estados.count('remover'),
            'com_papel': sum(1 for n in nos if (n.get('tobe') or {}).get('papeis') or (n.get('tobe') or {}).get('papel')),
        }
