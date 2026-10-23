from django import template
from django.urls import reverse

from usuario import perms

from ..models import Tarefa

register = template.Library()

_ORDEM_PRIORIDADE = {Tarefa.PRIORIDADE_ALTA: 0, Tarefa.PRIORIDADE_MEDIA: 1, Tarefa.PRIORIDADE_BAIXA: 2}


def tarefas_abertas_do_responsavel(user):
    """Tarefas em aberto em que `user` é responsável, atrasadas primeiro,
    depois por prazo (sem prazo por último) e prioridade. Sem
    `visiveis_para`: a elegibilidade já foi checada ao marcar o responsável."""
    tarefas = list(
        Tarefa.objects.filter(responsaveis=user)
        .exclude(status__in=[Tarefa.STATUS_CONCLUIDA, Tarefa.STATUS_CANCELADA])
        .select_related('cliente').distinct()
    )
    tarefas.sort(key=lambda t: (
        not t.atrasada,
        t.prazo is None,
        t.prazo.timestamp() if t.prazo else 0,
        _ORDEM_PRIORIDADE.get(t.prioridade, 1),
    ))
    return tarefas


def _url_da_tarefa(tarefa, user):
    """Com cliente: aba Tarefas da página do cliente. Sem cliente (tarefa de
    plataforma): painel do dashboard."""
    if tarefa.cliente_id:
        return f"{reverse('listar_clientes')}?id={tarefa.cliente_id}&aba=tarefas"
    return reverse('quadro_geral' if perms.is_admin(user) else 'quadro_instancia')


@register.inclusion_tag('tarefas/_aviso_login.html', takes_context=True)
def tarefas_aviso_login(context):
    """Modal "Suas tarefas a concluir" — abre em toda página com base.html
    enquanto o usuário for responsável por tarefa em aberto. Não depende do
    login: a sessão dura 7 dias renovando a cada acesso, então quase ninguém
    passa pela tela de login (a 1ª versão, presa ao login, nunca aparecia).
    Fechar vale só para a página atual; a próxima navegação mostra de novo.
    Só equipe (Administrador/Consultor/Operador): o login do portal do
    cliente pode ser responsável por tarefa, mas não recebe o aviso."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not perms.is_backoffice(user):
        return {'tarefas': []}
    tarefas = tarefas_abertas_do_responsavel(user)
    for t in tarefas:
        t.url_aviso = _url_da_tarefa(t, user)
    return {
        'tarefas': tarefas,
        'atrasadas': sum(1 for t in tarefas if t.atrasada),
        # Botão ✓ concluir faz POST via fetch; inclusion_tag não herda o contexto.
        'csrf_token': context.get('csrf_token'),
    }
