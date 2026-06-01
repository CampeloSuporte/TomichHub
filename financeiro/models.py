from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from clientes.models import Cliente, BlocoIP
from datetime import timedelta
import uuid


class ConfiguracaoFinanceira(models.Model):
    """Configurações financeiras da empresa (boleto, dados bancários, etc)"""
    empresa_nome = models.CharField(max_length=255)
    empresa_cnpj = models.CharField(max_length=18)
    empresa_logo = models.ImageField(
        upload_to='financeiro/logos/',
        help_text="Logo para boleto (recomendado 300x100px)"
    )
    
    # Dados Bancários
    banco_nome = models.CharField(max_length=100, help_text="Ex: Banco do Brasil")
    banco_codigo = models.CharField(max_length=10, help_text="Código do banco")
    agencia = models.CharField(max_length=10)
    conta = models.CharField(max_length=20)
    digito_conta = models.CharField(max_length=1, blank=True)
    nosso_numero_sequencia = models.IntegerField(default=1, help_text="Próximo número sequencial")
    
    # Contato
    endereco = models.TextField()
    telefone = models.CharField(max_length=15)
    email = models.EmailField()
    
    # Configurações
    juros_atraso_percentual = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0.10,
        help_text="Juros diário por atraso (ex: 0.10 = 0.1%)"
    )
    multa_atraso = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Multa fixa por atraso"
    )
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Configuração Financeira'
        verbose_name_plural = 'Configurações Financeiras'
    
    def __str__(self):
        return self.empresa_nome
    
    def get_proximo_nosso_numero(self):
        """Gera e incrementa o próximo nosso número"""
        nosso = self.nosso_numero_sequencia
        self.nosso_numero_sequencia += 1
        self.save()
        return str(nosso).zfill(10)


class Consultoria(models.Model):
    """Serviços de consultoria faturáveis"""
    PERIODICIDADE_CHOICES = [
        ('MENSAL', 'Mensal'),
        ('TRIMESTRAL', 'Trimestral'),
        ('SEMESTRAL', 'Semestral'),
        ('ANUAL', 'Anual'),
        ('UNICA', 'Única'),
    ]
    
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('PAUSADO', 'Pausado'),
        ('CANCELADO', 'Cancelado'),
        ('ENCERRADO', 'Encerrado'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='consultorias')
    descricao = models.CharField(max_length=255, help_text="Ex: Consultoria de Redes")
    valor_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    quantidade_meses = models.IntegerField(default=1, help_text="Duração em meses")
    periodicidade = models.CharField(max_length=20, choices=PERIODICIDADE_CHOICES, default='MENSAL')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ATIVO')
    data_inicio = models.DateField()
    data_fim = models.DateField(blank=True, null=True)
    
    # Quitação
    quitado = models.BooleanField(default=False)
    data_quitacao = models.DateField(blank=True, null=True)
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Consultoria'
        verbose_name_plural = 'Consultorias'
        ordering = ['-data_criacao']
    
    def __str__(self):
        return f"{self.cliente.nome_empresa} - {self.descricao}"
    
    def get_valor_total(self):
        """Calcula valor total (valor_unitario * quantidade_meses)"""
        return self.valor_unitario * self.quantidade_meses
    
    def get_valor_mensal(self):
        """Calcula valor mensal"""
        if self.quantidade_meses > 0:
            return self.get_valor_total() / self.quantidade_meses
        return 0
    
    @property
    def dias_vencimento(self):
        """Retorna dias até vencimento"""
        if not self.data_fim:
            return 0
        from datetime import date
        delta = self.data_fim - date.today()
        return delta.days


class AluguelIPv4(models.Model):
    """Aluguel de blocos IPv4"""
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('PAUSADO', 'Pausado'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='alugueis_ipv4')
    bloco_ip = models.ForeignKey(BlocoIP, on_delete=models.SET_NULL, null=True, blank=True)
    
    bloco_descricao = models.CharField(max_length=100, help_text="Ex: 200.100.50.0/24")
    quantidade_ips = models.IntegerField(help_text="Quantidade de IPs no bloco")
    
    valor_mensal = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ATIVO')
    
    data_inicio = models.DateField()
    data_fim = models.DateField(blank=True, null=True)
    
    # Quitação
    quitado = models.BooleanField(default=False)
    data_quitacao = models.DateField(blank=True, null=True)
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Aluguel IPv4'
        verbose_name_plural = 'Alugueis IPv4'
        ordering = ['-data_criacao']
    
    def __str__(self):
        return f"{self.cliente.nome_empresa} - {self.bloco_descricao}"
    
    def get_valor_total_mes(self):
        """Valor total do mês"""
        return self.valor_mensal
    
    @property
    def dias_vencimento(self):
        """Retorna dias até vencimento"""
        if not self.data_fim:
            return 0
        from datetime import date
        delta = self.data_fim - date.today()
        return delta.days


class Fatura(models.Model):
    """Agrupa consultorias e alugueis em uma fatura"""
    TIPO_CHOICES = [
        ('CONSULTORIA', 'Consultoria'),
        ('ALUGUEL_IPV4', 'Aluguel IPv4'),
        ('MISTA', 'Mista (Consultoria + IPv4)'),
    ]
    
    STATUS_CHOICES = [
        ('RASCUNHO', 'Rascunho'),
        ('ABERTA', 'Aberta'),
        ('PAGA', 'Paga'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    # UUID para referência única
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    numero_fatura = models.CharField(max_length=50, unique=True)
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='faturas')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    
    # Itens
    consultorias = models.ManyToManyField(Consultoria, blank=True, related_name='faturas')
    alugueis_ipv4 = models.ManyToManyField(AluguelIPv4, blank=True, related_name='faturas')
    
    # Valores
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    juros = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Datas
    data_emissao = models.DateField(auto_now_add=True)
    data_vencimento = models.DateField()
    data_pagamento = models.DateField(blank=True, null=True)
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='RASCUNHO')
    observacoes = models.TextField(blank=True, null=True)
    privada = models.BooleanField(
        default=False,
        help_text='Marcar como privada para mostrar apenas para staff'
    )

    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Fatura'
        verbose_name_plural = 'Faturas'
        ordering = ['-data_criacao']
    
    def __str__(self):
        return f"{self.numero_fatura} - {self.cliente.nome_empresa}"
    
    @property
    def dias_para_vencer(self):
        """Retorna dias até vencimento"""
        from datetime import date
        delta = self.data_vencimento - date.today()
        return delta.days
    
    @property
    def vencida(self):
        """Retorna True se está vencida"""
        from datetime import date
        return self.data_vencimento < date.today() and self.status != 'PAGA'
    
    def gerar_numero_fatura(self):
        """Gera número de fatura único"""
        from datetime import datetime
        ano_mes = datetime.now().strftime('%Y%m')
        contador = Fatura.objects.filter(numero_fatura__startswith=ano_mes).count() + 1
        return f"{ano_mes}{contador:05d}"
    
    def calcular_totais(self):
        """Calcula subtotal, juros e total"""
        subtotal = 0
        
        # Somar consultorias
        for consultoria in self.consultorias.all():
            subtotal += consultoria.get_valor_total()
        
        # Somar alugueis
        for aluguel in self.alugueis_ipv4.all():
            subtotal += aluguel.get_valor_total_mes()
        
        self.subtotal = subtotal
        
        # Calcular juros se vencida
        if self.vencida and self.status != 'PAGA':
            config = ConfiguracaoFinanceira.objects.first()
            if config:
                from datetime import date
                dias_atraso = (date.today() - self.data_vencimento).days
                juros_diario = (self.subtotal * config.juros_atraso_percentual) / 100
                self.juros = juros_diario * dias_atraso + config.multa_atraso
        else:
            self.juros = 0
        
        # Total
        self.valor_total = self.subtotal - self.desconto + self.juros
        self.save()


class Boleto(models.Model):
    """Registro de boletos gerados"""
    TIPO_CHOICES = [
        ('FATURA', 'Fatura'),
        ('CONSULTORIA', 'Consultoria'),
        ('ALUGUEL_IPV4', 'Aluguel IPv4'),
    ]
    
    STATUS_CHOICES = [
        ('GERADO', 'Gerado'),
        ('ENVIADO', 'Enviado'),
        ('PAGO', 'Pago'),
        ('VENCIDO', 'Vencido'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    # UUID e código
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    numero_boleto = models.CharField(max_length=50, unique=True)
    nosso_numero = models.CharField(max_length=20)  # Código do banco
    
    # Relacionamentos
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='boletos')
    fatura = models.OneToOneField(Fatura, on_delete=models.CASCADE, null=True, blank=True, related_name='boleto')
    
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    
    # Valores
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Datas
    data_geracao = models.DateField(auto_now_add=True)
    data_vencimento = models.DateField()
    data_pagamento = models.DateField(blank=True, null=True)
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='GERADO')
    
    # Arquivo
    arquivo_pdf = models.FileField(upload_to='financeiro/boletos/%Y/%m/', blank=True, null=True)
    
    # Rastreamento
    codigo_barras = models.CharField(max_length=50, blank=True)
    url_banco = models.URLField(blank=True, null=True, help_text="Link para pagar no banco")
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Boleto'
        verbose_name_plural = 'Boletos'
        ordering = ['-data_geracao']
    
    def __str__(self):
        return f"{self.numero_boleto} - {self.cliente.nome_empresa}"
    
    @property
    def dias_para_vencer(self):
        """Retorna dias até vencimento"""
        from datetime import date
        delta = self.data_vencimento - date.today()
        return delta.days
    
    @property
    def vencido(self):
        """Retorna True se está vencido"""
        from datetime import date
        return self.data_vencimento < date.today() and self.status != 'PAGO'


class Pagamento(models.Model):
    """Registra pagamentos recebidos"""
    TIPO_CHOICES = [
        ('BOLETO', 'Boleto'),
        ('PIX', 'PIX'),
        ('TRANSFERENCIA', 'Transferência'),
        ('CARTAO', 'Cartão'),
        ('CHEQUE', 'Cheque'),
        ('OUTRO', 'Outro'),
    ]
    
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    numero_recibo = models.CharField(max_length=50, unique=True)
    
    # Relacionamento
    fatura = models.ForeignKey(Fatura, on_delete=models.CASCADE, related_name='pagamentos')
    boleto = models.ForeignKey(Boleto, on_delete=models.SET_NULL, null=True, blank=True, related_name='pagamentos')
    
    # Dados do pagamento
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Datas
    data_pagamento = models.DateField()
    data_confirmacao = models.DateField(blank=True, null=True)
    
    # Informações adicionais
    referencia = models.CharField(max_length=100, blank=True, help_text="Ex: nº do comprovante")
    observacoes = models.TextField(blank=True, null=True)
    
    # ✅ NOVO: Campos separados para os dois tipos de comprovante
    # Comprovante enviado pelo usuário (opcional)
    comprovante = models.FileField(
        upload_to='financeiro/comprovantes/%Y/%m/',
        blank=True,
        null=True,
        help_text='Comprovante enviado pelo usuário (opcional: PDF, JPG, PNG)'
    )
    
    # ✅ NOVO: PDF gerado automaticamente pelo sistema
    comprovante_pdf_gerado = models.FileField(
        upload_to='financeiro/comprovantes-pdf/%Y/%m/',
        blank=True,
        null=True,
        help_text='PDF gerado automaticamente pelo sistema'
    )
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        verbose_name = 'Pagamento'
        verbose_name_plural = 'Pagamentos'
        ordering = ['-data_pagamento']
    
    def __str__(self):
        return f"{self.numero_recibo} - {self.fatura.numero_fatura}"
    
    def gerar_numero_recibo(self):
        """✅ CORRIGIDO: Método único (removia a cópia anterior)"""
        from datetime import datetime
        ano_mes = datetime.now().strftime('%Y%m')
        contador = Pagamento.objects.filter(numero_recibo__startswith='REC').count() + 1
        return f"REC{ano_mes}{contador:05d}"
    
    def download_comprovante(self):
        """Retorna URL para download do comprovante do usuário"""
        if self.comprovante:
            return self.comprovante.url
        return None
    
    # ✅ NOVO: Método para acessar PDF gerado
    def download_comprovante_pdf_gerado(self):
        """Retorna URL para download do PDF gerado pelo sistema"""
        if self.comprovante_pdf_gerado:
            return self.comprovante_pdf_gerado.url
        return None
    
    # ✅ NOVO: Método para verificar se tem comprovante
    @property
    def tem_comprovante_usuario(self):
        """Verifica se há comprovante do usuário"""
        return bool(self.comprovante)
    
    # ✅ NOVO: Método para verificar se tem PDF
    @property
    def tem_pdf_gerado(self):
        """Verifica se há PDF gerado"""
        return bool(self.comprovante_pdf_gerado)
    
    # ✅ NOVO: Propriedade para saber se tem algum comprovante
    @property
    def tem_comprovante(self):
        """Verifica se há algum tipo de comprovante"""
        return self.tem_comprovante_usuario or self.tem_pdf_gerado

class RelatorioFinanceiro(models.Model):
    """Cache de relatórios financeiros"""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='relatorio_financeiro')
    
    # Totalizações
    total_consultorias = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_alugueis = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_faturas = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_pago = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    saldo_aberto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Estatísticas
    faturas_abertas = models.IntegerField(default=0)
    faturas_vencidas = models.IntegerField(default=0)
    faturas_pagas = models.IntegerField(default=0)
    
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Relatório Financeiro'
        verbose_name_plural = 'Relatórios Financeiros'
    
    def __str__(self):
        return f"Relatório - {self.cliente.nome_empresa}"
    
# ============================================
# ADICIONAR ESTE MODELO NO financeiro/models.py
# ============================================

class VendaEquipamento(models.Model):
    """Vendas de equipamentos com parcelamento automático"""
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('CANCELADO', 'Cancelado'),
        ('ENCERRADO', 'Encerrado'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='vendas_equipamentos')
    descricao = models.CharField(max_length=255, help_text="Nome do equipamento")
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, help_text="Valor total da venda")
    quantidade_parcelas = models.IntegerField(default=1, help_text="Número de parcelas")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ATIVO')
    data_inicio = models.DateField()
    data_fim = models.DateField(blank=True, null=True)
    
    # Quitação
    quitado = models.BooleanField(default=False)
    data_quitacao = models.DateField(blank=True, null=True)
    
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_atualizacao = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Venda de Equipamento'
        verbose_name_plural = 'Vendas de Equipamentos'
        ordering = ['-data_criacao']
    
    def __str__(self):
        return f"{self.cliente.nome_empresa} - {self.descricao}"
    
    def get_valor_parcela(self):
        """Calcula valor de cada parcela"""
        if self.quantidade_parcelas > 0:
            return self.valor_total / self.quantidade_parcelas
        return 0
    
    def get_valor_total(self):
        """Retorna valor total"""
        return self.valor_total


class Despesa(models.Model):
    """Despesas operacionais da empresa com controle de vencimento."""

    CATEGORIA_CHOICES = [
        ('INFRAESTRUTURA', 'Infraestrutura'),
        ('PESSOAL',        'Pessoal'),
        ('SERVICOS',       'Serviços'),
        ('ADMINISTRATIVO', 'Administrativo'),
        ('FISCAL',         'Fiscal / Tributário'),
        ('OUTROS',         'Outros'),
    ]
    RECORRENCIA_CHOICES = [
        ('UNICA',       'Única'),
        ('MENSAL',      'Mensal'),
        ('BIMESTRAL',   'Bimestral'),
        ('TRIMESTRAL',  'Trimestral'),
        ('SEMESTRAL',   'Semestral'),
        ('ANUAL',       'Anual'),
    ]
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('PAGO',     'Pago'),
    ]

    nome            = models.CharField(max_length=255)
    descricao       = models.TextField(blank=True)
    valor           = models.DecimalField(max_digits=12, decimal_places=2)
    categoria       = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, default='OUTROS')
    recorrencia          = models.CharField(max_length=20, choices=RECORRENCIA_CHOICES, default='UNICA')
    meses_recorrencia    = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Total de meses/ocorrências da recorrência. Vazio = indefinido.'
    )
    ocorrencia_atual     = models.PositiveIntegerField(default=1, help_text='Número da ocorrência atual')
    data_vencimento      = models.DateField()
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')
    data_pagamento  = models.DateField(null=True, blank=True)
    observacoes     = models.TextField(blank=True)
    privada         = models.BooleanField(
        default=False,
        help_text='Marcar como privada para mostrar apenas ao criador'
    )
    criado_por      = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='despesas_criadas'
    )
    criado_em       = models.DateTimeField(auto_now_add=True)
    atualizado_em   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Despesa'
        verbose_name_plural = 'Despesas'
        ordering            = ['data_vencimento']

    def __str__(self):
        return f'{self.nome} — {self.data_vencimento}'

    @property
    def status_efetivo(self):
        if self.status == 'PAGO':
            return 'PAGO'
        today = timezone.localdate()
        if self.data_vencimento < today:
            return 'VENCIDO'
        if self.data_vencimento == today:
            return 'VENCE_HOJE'
        return 'PENDENTE'

    @property
    def dias_para_vencer(self):
        return (self.data_vencimento - timezone.localdate()).days