from django.contrib import admin
from .models import Cliente, Acesso
from .models import ProxyServer
from .models import (
    IPAMVlan, IPAMPrefixo, IPAMSubRede, IPAMEndereco, IPAMVpnDoc,
    IPAMScanResultado, IPAMAuditLog,
)


from .models import BackupTemplate, BackupLog
from .models import AcessoSessao, AcessoComando, TerminalLinkExterno
from .models import BgpSnapshot, AcaoBgp, BgpCommunity

@admin.register(BackupTemplate)
class BackupTemplateAdmin(admin.ModelAdmin):
    list_display = ['nome', 'fabricante', 'ativo', 'data_criacao']
    list_filter = ['fabricante', 'ativo']
    search_fields = ['nome', 'descricao']

@admin.register(BackupLog)
class BackupLogAdmin(admin.ModelAdmin):
    list_display = ['acesso', 'cliente', 'status', 'tamanho_bytes', 'data_backup', 'duracao_segundos']
    list_filter = ['status', 'data_backup', 'cliente']
    search_fields = ['acesso__tipo', 'cliente__nome_empresa']
    readonly_fields = ['data_backup']

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome_empresa', 'usuario', 'cnpj', 'telefone')
    search_fields = ('nome_empresa', 'cnpj')
    filter_horizontal = ('usuarios_adicionais',)

@admin.register(Acesso)
class AcessoAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'host', 'porta', 'protocolo', 'cliente')
    list_filter = ('tipo', 'protocolo')
    search_fields = ('host', 'cliente__nome_empresa')



class AcessoComandoInline(admin.TabularInline):
    model = AcessoComando
    extra = 0
    readonly_fields = ['comando', 'executado_em']
    can_delete = False

@admin.register(AcessoSessao)
class AcessoSessaoAdmin(admin.ModelAdmin):
    list_display = ['tipo', 'acesso', 'usuario', 'ip_origem', 'status', 'iniciada_em', 'encerrada_em']
    list_filter = ['tipo', 'status']
    search_fields = ['acesso__tipo', 'acesso__host', 'usuario__username']
    readonly_fields = ['iniciada_em']
    inlines = [AcessoComandoInline]


@admin.register(TerminalLinkExterno)
class TerminalLinkExternoAdmin(admin.ModelAdmin):
    list_display = ['id', 'acesso', 'criado_por', 'criado_em', 'expira_em', 'revogado']
    list_filter = ['revogado', 'criado_em']
    search_fields = ['acesso__host', 'criado_por__username']
    readonly_fields = ['id', 'criado_em']


@admin.register(BgpSnapshot)
class BgpSnapshotAdmin(admin.ModelAdmin):
    list_display = ['acesso', 'vendor', 'gerado_em', 'tem_erro']
    list_filter = ['vendor']
    search_fields = ['acesso__host', 'acesso__tipo']
    readonly_fields = ['gerado_em']

    def tem_erro(self, obj):
        return bool(obj.erro)
    tem_erro.boolean = True
    tem_erro.short_description = 'Erro?'


@admin.register(AcaoBgp)
class AcaoBgpAdmin(admin.ModelAdmin):
    list_display = ['tipo', 'acesso', 'usuario', 'alvo', 'status', 'executado_em']
    list_filter = ['tipo', 'status']
    search_fields = ['acesso__host', 'usuario__username', 'alvo']
    readonly_fields = ['executado_em']


@admin.register(BgpCommunity)
class BgpCommunityAdmin(admin.ModelAdmin):
    list_display = ['acesso', 'sessao_nome', 'label', 'valor', 'criado_por', 'criado_em']
    list_filter = ['criado_em']
    search_fields = ['acesso__host', 'sessao_nome', 'label', 'valor']
    readonly_fields = ['criado_em']


@admin.register(ProxyServer)
class ProxyServerAdmin(admin.ModelAdmin):
    list_display = ['nome', 'host', 'porta', 'ativo', 'data_criacao']
    list_filter = ['ativo', 'data_criacao']
    search_fields = ['nome', 'host']


@admin.register(IPAMVlan)
class IPAMVlanAdmin(admin.ModelAdmin):
    list_display = ['numero', 'nome', 'cliente', 'status']
    list_filter = ['status']
    search_fields = ['nome', 'cliente__nome_empresa']

@admin.register(IPAMPrefixo)
class IPAMPrefixoAdmin(admin.ModelAdmin):
    list_display = ['prefixo', 'tipo', 'status', 'pai', 'cliente', 'pool_cheia']
    list_filter = ['tipo', 'status', 'pool_cheia']
    search_fields = ['prefixo', 'cliente__nome_empresa']

@admin.register(IPAMSubRede)
class IPAMSubRedeAdmin(admin.ModelAdmin):
    list_display = ['rede', 'gateway', 'vlan', 'status', 'cliente', 'scan_automatico', 'ultimo_scan']
    list_filter = ['status', 'scan_automatico']
    search_fields = ['rede', 'cliente__nome_empresa']

@admin.register(IPAMEndereco)
class IPAMEnderecoAdmin(admin.ModelAdmin):
    list_display = ['ip', 'hostname', 'tipo', 'status', 'subrede', 'cliente']
    list_filter = ['tipo', 'status']
    search_fields = ['ip', 'hostname', 'mac_address', 'cliente__nome_empresa']

@admin.register(IPAMVpnDoc)
class IPAMVpnDocAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'status', 'cliente']
    list_filter = ['tipo', 'status']
    search_fields = ['nome', 'cliente__nome_empresa']

@admin.register(IPAMScanResultado)
class IPAMScanResultadoAdmin(admin.ModelAdmin):
    list_display = ['ip', 'online', 'checado_em', 'cliente']
    list_filter = ['online']
    search_fields = ['ip', 'cliente__nome_empresa']

@admin.register(IPAMAuditLog)
class IPAMAuditLogAdmin(admin.ModelAdmin):
    list_display = ['criado_em', 'modelo', 'acao', 'objeto_repr', 'usuario', 'cliente']
    list_filter = ['modelo', 'acao']
    search_fields = ['objeto_repr', 'cliente__nome_empresa']
    readonly_fields = ['criado_em']