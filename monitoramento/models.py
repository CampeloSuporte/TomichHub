from django.db import models


class ZabbixConfig(models.Model):
    """
    Configuração da API Zabbix — uma por cliente.
    Suporta token de API (Zabbix 5.4+) ou login clássico.
    """
    # FK para o model Cliente do app clientes
    # Usamos string 'clientes.Cliente' para não criar dependência circular de import
    cliente = models.OneToOneField(
        'clientes.Cliente',
        on_delete=models.CASCADE,
        related_name='zabbix_config',
    )
    url = models.CharField(
        max_length=500,
        help_text="URL base do Zabbix (ex: https://zabbix.empresa.com) — sem /api_jsonrpc.php",
    )
    usuario    = models.CharField(max_length=100, blank=True, default='')
    senha      = models.CharField(max_length=255, blank=True, default='')
    api_token  = models.CharField(
        max_length=512, blank=True, null=True,
        help_text="Token de API (Zabbix 5.4+). Se preenchido, ignora usuário/senha.",
    )
    ativo      = models.BooleanField(default=True)
    data_criacao     = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Configuração Zabbix'
        verbose_name_plural = 'Configurações Zabbix'

    def __str__(self):
        return f"Zabbix — {self.cliente.nome_empresa}"


class MonitorTopology(models.Model):
    """Canvas de topologia de monitoramento vinculado a um cliente."""
    cliente  = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.CASCADE,
        related_name='monitor_topologies',
    )
    nome     = models.CharField(max_length=255)
    descricao = models.TextField(blank=True, default='')
    data_criacao     = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Topologia de Monitoramento'
        verbose_name_plural = 'Topologias de Monitoramento'
        ordering            = ['-data_atualizacao']

    def __str__(self):
        return f"{self.nome} — {self.cliente.nome_empresa}"


class MonitorNode(models.Model):
    """Nó (dispositivo) posicionado no canvas."""
    TIPO_CHOICES = [
        ('router',   'Roteador'),
        ('switch',   'Switch'),
        ('firewall', 'Firewall'),
        ('server',   'Servidor'),
        ('ap',       'Access Point'),
        ('cloud',    'Nuvem / Internet'),
        ('endpoint', 'Host / Endpoint'),
    ]

    topologia        = models.ForeignKey(
        MonitorTopology, on_delete=models.CASCADE, related_name='nodes',
    )
    tipo             = models.CharField(max_length=50, choices=TIPO_CHOICES, default='switch')
    label            = models.CharField(max_length=255)
    zabbix_hostid    = models.CharField(max_length=100, blank=True, null=True)
    zabbix_hostname  = models.CharField(max_length=255, blank=True, null=True)
    pos_x            = models.FloatField(default=200)
    pos_y            = models.FloatField(default=200)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.label} ({self.get_tipo_display()})"


class MonitorLink(models.Model):
    """
    Enlace entre dois MonitorNode.
    Armazena os IDs de itens Zabbix que representam
    tráfego de entrada, saída e status da interface.
    """
    topologia    = models.ForeignKey(
        MonitorTopology, on_delete=models.CASCADE, related_name='links',
    )
    node_origem  = models.ForeignKey(
        MonitorNode, on_delete=models.CASCADE, related_name='links_saida',
    )
    node_destino = models.ForeignKey(
        MonitorNode, on_delete=models.CASCADE, related_name='links_entrada',
    )
    label        = models.CharField(max_length=100, blank=True, default='')

    # IDs de itens Zabbix (podem ser nulos enquanto não configurados)
    zabbix_itemid_in     = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Item ID Zabbix — tráfego de entrada (bps)",
    )
    zabbix_itemid_out    = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Item ID Zabbix — tráfego de saída (bps)",
    )
    zabbix_itemid_status = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Item ID Zabbix — status operacional da interface (0=down / 1=up)",
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.node_origem.label} → {self.node_destino.label}"
