import calendar
from datetime import date

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class EscopoInstanciaManager(models.Manager):
    """Escopo por instância, mesmo padrão de Cliente.objects.visiveis_para:
    Administrador vê tudo, Consultor/Operador só a própria instância."""

    def visiveis_para(self, user):
        from usuario.perms import is_admin, get_instancia, is_backoffice
        if is_admin(user):
            return self.all()
        if is_backoffice(user):
            instancia = get_instancia(user)
            return self.filter(instancia=instancia) if instancia else self.none()
        return self.none()


class TarefaManager(EscopoInstanciaManager):

    def do_cliente(self, cliente):
        """Tarefas de um cliente específico — base do Kanban na aba do
        cliente (usado tanto por back-office quanto pelo próprio portal do
        cliente; a permissão de acesso ao `cliente` é checada na view)."""
        return self.filter(cliente=cliente).select_related('criado_por').prefetch_related(
            'responsaveis', 'checklist__verificado_por',
        )


class Tarefa(models.Model):
    """To-do do back-office, opcionalmente vinculado a um Cliente. Sem
    designação = qualquer atendente da mesma instância pode assumir; o
    campo `instancia` (derivado do cliente, ou de quem criou quando não há
    cliente) é o que garante o isolamento multi-tenant, já que tarefa não
    tem dono fixo até ser assumida."""

    STATUS_PENDENTE = 'pendente'
    STATUS_ANDAMENTO = 'andamento'
    STATUS_CONCLUIDA = 'concluida'
    STATUS_CANCELADA = 'cancelada'
    STATUS_CHOICES = [
        (STATUS_PENDENTE, 'Pendente'),
        (STATUS_ANDAMENTO, 'Em Andamento'),
        (STATUS_CONCLUIDA, 'Concluída'),
        (STATUS_CANCELADA, 'Cancelada'),
    ]

    PRIORIDADE_BAIXA = 'baixa'
    PRIORIDADE_MEDIA = 'media'
    PRIORIDADE_ALTA = 'alta'
    PRIORIDADE_CHOICES = [
        (PRIORIDADE_BAIXA, 'Baixa'),
        (PRIORIDADE_MEDIA, 'Média'),
        (PRIORIDADE_ALTA, 'Alta'),
    ]

    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True, default='')
    cliente = models.ForeignKey(
        'clientes.Cliente', on_delete=models.SET_NULL, null=True, blank=True, related_name='tarefas'
    )
    instancia = models.ForeignKey(
        'usuario.Instancia', on_delete=models.SET_NULL, null=True, blank=True, related_name='tarefas',
        help_text='Derivada do cliente, ou da instância de quem criou quando não há cliente. Vazia = tarefa de plataforma (só Administrador vê).'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDENTE)
    prioridade = models.CharField(max_length=20, choices=PRIORIDADE_CHOICES, default=PRIORIDADE_MEDIA)
    prazo = models.DateTimeField(null=True, blank=True)
    responsaveis = models.ManyToManyField(
        User, blank=True, related_name='tarefas_responsavel',
        help_text='Quem está responsável pela tarefa — pode ser mais de uma pessoa.'
    )
    criado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tarefas_criadas'
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    concluida_em = models.DateTimeField(null=True, blank=True)

    # Preenchidos só na tarefa gerada por uma Rotina (ver services.gerar_ocorrencias_rotinas).
    rotina = models.ForeignKey(
        'Rotina', on_delete=models.SET_NULL, null=True, blank=True, related_name='ocorrencias',
        help_text='Rotina mensal que gerou esta tarefa. Excluir a rotina mantém o histórico.'
    )
    competencia = models.DateField(
        null=True, blank=True,
        help_text='Mês de referência da ocorrência (sempre o dia 1º).'
    )

    objects = TarefaManager()

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Tarefa'
        verbose_name_plural = 'Tarefas'
        indexes = [
            models.Index(fields=['instancia', 'status']),
            models.Index(fields=['status', 'prazo']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['rotina', 'competencia'], condition=models.Q(rotina__isnull=False),
                name='tarefa_uma_ocorrencia_por_mes',
            ),
        ]

    def __str__(self):
        return self.titulo

    @property
    def atrasada(self):
        return bool(
            self.prazo and self.status not in (self.STATUS_CONCLUIDA, self.STATUS_CANCELADA)
            and timezone.now() > self.prazo
        )

    @property
    def checklist_progresso(self):
        """(verificados, total) — lê `checklist.all()` para aproveitar o
        prefetch das listagens em vez de fazer duas contagens por linha."""
        itens = list(self.checklist.all())
        return sum(1 for i in itens if i.verificado), len(itens)


class TarefaChecklistItem(models.Model):
    """Item de checklist de uma tarefa. Na tarefa gerada por rotina, é uma
    cópia do RotinaItem feita no dia da geração: editar a rotina depois não
    mexe no que já foi (ou está sendo) verificado naquele mês."""

    tarefa = models.ForeignKey(Tarefa, on_delete=models.CASCADE, related_name='checklist')
    texto = models.CharField(max_length=255)
    ordem = models.PositiveSmallIntegerField(default=0)
    verificado = models.BooleanField(default=False)
    verificado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    verificado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['ordem', 'id']
        verbose_name = 'Item de checklist'
        verbose_name_plural = 'Itens de checklist'

    def __str__(self):
        return self.texto


class Rotina(models.Model):
    """Modelo de tarefa que se repete todo mês no `dia_do_mes`. A cada mês
    vira uma Tarefa comum (com prazo no dia e o checklist copiado), então
    entra em Atrasadas/Minhas/Kanban como qualquer outra."""

    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True, default='')
    cliente = models.ForeignKey(
        'clientes.Cliente', on_delete=models.SET_NULL, null=True, blank=True, related_name='rotinas'
    )
    instancia = models.ForeignKey(
        'usuario.Instancia', on_delete=models.SET_NULL, null=True, blank=True, related_name='rotinas',
        help_text='Mesma regra da Tarefa: do cliente, ou de quem criou. Vazia = só Administrador vê.'
    )
    dia_do_mes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text='Em mês mais curto (ex.: dia 31 em fevereiro), cai no último dia.'
    )
    prioridade = models.CharField(max_length=20, choices=Tarefa.PRIORIDADE_CHOICES, default=Tarefa.PRIORIDADE_MEDIA)
    responsaveis = models.ManyToManyField(
        User, blank=True, related_name='rotinas_responsavel',
        help_text='Copiados para a tarefa de cada mês.'
    )
    ativa = models.BooleanField(default=True)
    inicio = models.DateField(
        default=timezone.localdate,
        help_text='Não gera ocorrência com data anterior a este dia (evita criar a do mês já vencida).'
    )
    ultima_competencia = models.DateField(
        null=True, blank=True,
        help_text='Último mês já gerado. Impede que a tarefa excluída do mês volte a ser criada.'
    )
    criado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rotinas_criadas'
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    objects = EscopoInstanciaManager()

    class Meta:
        ordering = ['dia_do_mes', 'titulo']
        verbose_name = 'Rotina mensal'
        verbose_name_plural = 'Rotinas mensais'

    def __str__(self):
        return self.titulo

    def data_no_mes(self, competencia):
        ultimo_dia = calendar.monthrange(competencia.year, competencia.month)[1]
        return date(competencia.year, competencia.month, min(self.dia_do_mes, ultimo_dia))

    def proxima_data(self, hoje=None):
        """Próximo dia em que a rotina vai gerar tarefa, depois de `hoje`."""
        hoje = hoje or timezone.localdate()
        competencia = hoje.replace(day=1)
        if self.ultima_competencia is None or self.ultima_competencia < competencia:
            data = self.data_no_mes(competencia)
            if data >= hoje and data >= self.inicio:
                return data
        proximo = date(competencia.year + (competencia.month == 12), competencia.month % 12 + 1, 1)
        return self.data_no_mes(proximo)


class RotinaItem(models.Model):
    rotina = models.ForeignKey(Rotina, on_delete=models.CASCADE, related_name='itens')
    texto = models.CharField(max_length=255)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordem', 'id']
        verbose_name = 'Item da rotina'
        verbose_name_plural = 'Itens da rotina'

    def __str__(self):
        return self.texto
