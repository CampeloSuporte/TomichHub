from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class TarefaManager(models.Manager):
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

    def do_cliente(self, cliente):
        """Tarefas de um cliente específico — base do Kanban na aba do
        cliente (usado tanto por back-office quanto pelo próprio portal do
        cliente; a permissão de acesso ao `cliente` é checada na view)."""
        return self.filter(cliente=cliente).select_related('criado_por').prefetch_related('responsaveis')


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

    objects = TarefaManager()

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Tarefa'
        verbose_name_plural = 'Tarefas'
        indexes = [
            models.Index(fields=['instancia', 'status']),
            models.Index(fields=['status', 'prazo']),
        ]

    def __str__(self):
        return self.titulo

    @property
    def atrasada(self):
        return bool(
            self.prazo and self.status not in (self.STATUS_CONCLUIDA, self.STATUS_CANCELADA)
            and timezone.now() > self.prazo
        )
