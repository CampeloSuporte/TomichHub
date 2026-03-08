from django.contrib import admin
from .models import ZabbixConfig, MonitorTopology, MonitorNode, MonitorLink


@admin.register(ZabbixConfig)
class ZabbixConfigAdmin(admin.ModelAdmin):
    list_display  = ['cliente', 'url', 'ativo', 'data_atualizacao']
    list_filter   = ['ativo']
    search_fields = ['cliente__nome_empresa', 'url']


class MonitorNodeInline(admin.TabularInline):
    model  = MonitorNode
    extra  = 0
    fields = ['tipo', 'label', 'zabbix_hostid', 'zabbix_hostname', 'pos_x', 'pos_y']


class MonitorLinkInline(admin.TabularInline):
    model  = MonitorLink
    extra  = 0
    fields = ['node_origem', 'node_destino', 'label',
              'zabbix_itemid_in', 'zabbix_itemid_out', 'zabbix_itemid_status']


@admin.register(MonitorTopology)
class MonitorTopologyAdmin(admin.ModelAdmin):
    list_display  = ['nome', 'cliente', 'data_atualizacao']
    search_fields = ['nome', 'cliente__nome_empresa']
    inlines       = [MonitorNodeInline, MonitorLinkInline]
