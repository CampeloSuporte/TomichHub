import uuid
from datetime import date, datetime, time

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clientes.models import Cliente
from tarefas.models import Rotina, RotinaItem, Tarefa, TarefaChecklistItem
from tarefas.services import gerar_ocorrencias_rotinas
from usuario.models import Instancia, PerfilUsuario, TOTPDevice


def _dar_2fa(user):
    """`Forcar2FAMiddleware` redireciona quem não confirmou TOTP — sem isso
    todo request do teste vira 302 e a asserção passaria à toa."""
    TOTPDevice.objects.create(usuario=user, secret='A' * 32, confirmado=True)
    return user


def _cliente(nome, instancia):
    suf = uuid.uuid4().hex[:8]
    return Cliente.objects.create(
        nome_empresa=nome, cnpj=f'00.000.000/{suf[:4]}-00',
        endereco='Rua Teste, 123', email=f'{suf}@example.com',
        instancia=instancia,
    )


class ExcluirTarefaTest(TestCase):
    """Exclusão de tarefa pelo painel do dashboard (`tarefa_excluir`).

    Antes só dava pra excluir pelo kanban da página do cliente, que nem
    lista tarefa sem cliente — a de plataforma não tinha como sair.
    """

    def setUp(self):
        self.inst_a = Instancia.objects.create(nome='Instância A')
        self.inst_b = Instancia.objects.create(nome='Instância B')

        self.consultor = _dar_2fa(User.objects.create_user(
            username='consultor_a', email='ca@example.com', password='x',
            is_staff=True, is_active=True,
        ))
        PerfilUsuario.objects.create(
            usuario=self.consultor, role=PerfilUsuario.ROLE_CONSULTOR, instancia=self.inst_a,
        )
        self.admin = _dar_2fa(User.objects.create_user(
            username='admin_plataforma', email='ap@example.com', password='x',
            is_staff=True, is_superuser=True, is_active=True,
        ))
        self.portal = _dar_2fa(User.objects.create_user(
            username='login_portal', email='lp@example.com', password='x',
            is_staff=False, is_active=True,
        ))

        self.cliente_a = _cliente('CLIENTE A', self.inst_a)
        self.tarefa_a = Tarefa.objects.create(
            titulo='Tarefa da instância A', cliente=self.cliente_a,
            instancia=self.inst_a, criado_por=self.consultor,
        )
        self.tarefa_b = Tarefa.objects.create(
            titulo='Tarefa da instância B', cliente=_cliente('CLIENTE B', self.inst_b),
            instancia=self.inst_b, criado_por=self.admin,
        )
        # Tarefa de plataforma: sem cliente. O kanban da página do cliente
        # nem lista este caso, então era impossível excluí-la.
        self.tarefa_plataforma = Tarefa.objects.create(
            titulo='Tarefa sem cliente', instancia=self.inst_a, criado_por=self.consultor,
        )

    def _excluir(self, tarefa):
        return self.client.post(reverse('tarefa_excluir', args=[tarefa.id]), {'next': '/homeinstancia'})

    def test_consultor_exclui_tarefa_da_propria_instancia(self):
        self.client.force_login(self.consultor)
        r = self._excluir(self.tarefa_a)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Tarefa.objects.filter(id=self.tarefa_a.id).exists())

    def test_exclui_tarefa_sem_cliente(self):
        self.client.force_login(self.consultor)
        self._excluir(self.tarefa_plataforma)
        self.assertFalse(Tarefa.objects.filter(id=self.tarefa_plataforma.id).exists())

    def test_nao_exclui_tarefa_de_outra_instancia(self):
        self.client.force_login(self.consultor)
        r = self._excluir(self.tarefa_b)
        # 404 e não 403: não revela que a tarefa existe em outra instância.
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Tarefa.objects.filter(id=self.tarefa_b.id).exists())

    def test_administrador_exclui_de_qualquer_instancia(self):
        self.client.force_login(self.admin)
        self._excluir(self.tarefa_b)
        self.assertFalse(Tarefa.objects.filter(id=self.tarefa_b.id).exists())

    def test_login_de_portal_nao_exclui(self):
        self.client.force_login(self.portal)
        r = self._excluir(self.tarefa_a)
        self.assertNotEqual(r.status_code, 200)
        self.assertTrue(Tarefa.objects.filter(id=self.tarefa_a.id).exists())

    def test_get_nao_exclui(self):
        """Só POST — link/prefetch do navegador não pode apagar tarefa."""
        self.client.force_login(self.consultor)
        r = self.client.get(reverse('tarefa_excluir', args=[self.tarefa_a.id]))
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Tarefa.objects.filter(id=self.tarefa_a.id).exists())

    def test_botao_aparece_no_painel(self):
        self.client.force_login(self.consultor)
        html = self.client.get('/homeinstancia').content.decode()
        self.assertIn(reverse('tarefa_excluir', args=[self.tarefa_a.id]), html)


class _BaseRotinas(TestCase):
    def setUp(self):
        self.inst_a = Instancia.objects.create(nome='Instância A')
        self.inst_b = Instancia.objects.create(nome='Instância B')
        self.consultor = _dar_2fa(User.objects.create_user(
            username='consultor_a', email='ca@example.com', password='x', is_staff=True,
        ))
        PerfilUsuario.objects.create(usuario=self.consultor, role=PerfilUsuario.ROLE_CONSULTOR, instancia=self.inst_a)
        self.consultor_b = _dar_2fa(User.objects.create_user(
            username='consultor_b', email='cb@example.com', password='x', is_staff=True,
        ))
        PerfilUsuario.objects.create(usuario=self.consultor_b, role=PerfilUsuario.ROLE_CONSULTOR, instancia=self.inst_b)
        self.admin = _dar_2fa(User.objects.create_user(
            username='admin_plataforma', email='ap@example.com', password='x', is_staff=True, is_superuser=True,
        ))
        self.cliente_a = _cliente('CLIENTE A', self.inst_a)

    def _rotina(self, dia=10, inicio=date(2026, 1, 1), itens=('Conferir backup', 'Conferir OLT'), **kwargs):
        rotina = Rotina.objects.create(
            titulo='Revisão mensal', dia_do_mes=dia, inicio=inicio,
            instancia=kwargs.pop('instancia', self.inst_a), criado_por=self.consultor, **kwargs,
        )
        for i, texto in enumerate(itens):
            RotinaItem.objects.create(rotina=rotina, texto=texto, ordem=i)
        return rotina


class GeracaoRotinaTest(_BaseRotinas):
    """`gerar_ocorrencias_rotinas`: uma tarefa por mês, no dia configurado."""

    def test_gera_no_dia_com_checklist_copiado_e_prazo_no_fim_do_dia(self):
        rotina = self._rotina(dia=10, cliente=self.cliente_a)
        rotina.responsaveis.add(self.consultor)

        criadas = gerar_ocorrencias_rotinas(hoje=date(2026, 9, 10))

        self.assertEqual(len(criadas), 1)
        tarefa = criadas[0]
        self.assertEqual(tarefa.rotina, rotina)
        self.assertEqual(tarefa.competencia, date(2026, 9, 1))
        self.assertEqual(tarefa.instancia, self.inst_a)
        self.assertEqual(tarefa.cliente, self.cliente_a)
        prazo = timezone.localtime(tarefa.prazo)
        self.assertEqual((prazo.date(), prazo.hour, prazo.minute), (date(2026, 9, 10), 23, 59))
        self.assertEqual(list(tarefa.checklist.values_list('texto', flat=True)), ['Conferir backup', 'Conferir OLT'])
        self.assertIn(self.consultor, tarefa.responsaveis.all())

    def test_nao_gera_antes_do_dia(self):
        self._rotina(dia=10)
        self.assertEqual(gerar_ocorrencias_rotinas(hoje=date(2026, 9, 9)), [])

    def test_uma_por_mes_mesmo_rodando_varias_vezes(self):
        self._rotina(dia=10)
        gerar_ocorrencias_rotinas(hoje=date(2026, 9, 10))
        gerar_ocorrencias_rotinas(hoje=date(2026, 9, 10))
        gerar_ocorrencias_rotinas(hoje=date(2026, 9, 25))
        self.assertEqual(Tarefa.objects.count(), 1)
        gerar_ocorrencias_rotinas(hoje=date(2026, 10, 10))
        self.assertEqual(Tarefa.objects.count(), 2)

    def test_tarefa_excluida_nao_volta_no_mesmo_mes(self):
        self._rotina(dia=10)
        gerar_ocorrencias_rotinas(hoje=date(2026, 9, 10))[0].delete()
        self.assertEqual(gerar_ocorrencias_rotinas(hoje=date(2026, 9, 11)), [])

    def test_dia_31_em_fevereiro_cai_no_ultimo_dia(self):
        self._rotina(dia=31)
        self.assertEqual(gerar_ocorrencias_rotinas(hoje=date(2026, 2, 27)), [])
        tarefa = gerar_ocorrencias_rotinas(hoje=date(2026, 2, 28))[0]
        self.assertEqual(timezone.localtime(tarefa.prazo).date(), date(2026, 2, 28))

    def test_rotina_criada_depois_do_dia_comeca_no_mes_seguinte(self):
        """Criada dia 15 com dia 10: não aparece uma tarefa já vencida."""
        rotina = self._rotina(dia=10, inicio=date(2026, 9, 15))
        self.assertEqual(gerar_ocorrencias_rotinas(hoje=date(2026, 9, 20)), [])
        self.assertEqual(rotina.proxima_data(hoje=date(2026, 9, 20)), date(2026, 10, 10))
        self.assertEqual(len(gerar_ocorrencias_rotinas(hoje=date(2026, 10, 10))), 1)

    def test_proxima_data_vira_o_ano(self):
        rotina = self._rotina(dia=5)
        self.assertEqual(rotina.proxima_data(hoje=date(2026, 12, 20)), date(2027, 1, 5))

    def test_pausada_nao_gera(self):
        self._rotina(dia=10, ativa=False)
        self.assertEqual(gerar_ocorrencias_rotinas(hoje=date(2026, 9, 10)), [])

    def test_excluir_rotina_mantem_as_tarefas(self):
        rotina = self._rotina(dia=10)
        tarefa = gerar_ocorrencias_rotinas(hoje=date(2026, 9, 10))[0]
        rotina.delete()
        tarefa.refresh_from_db()
        self.assertIsNone(tarefa.rotina_id)
        self.assertEqual(tarefa.checklist.count(), 2)


class ChecklistTest(_BaseRotinas):
    """Marcar item como verificado (`checklist_item_marcar`)."""

    def setUp(self):
        super().setUp()
        self.tarefa = Tarefa.objects.create(titulo='Revisão', instancia=self.inst_a, cliente=self.cliente_a)
        self.item1 = TarefaChecklistItem.objects.create(tarefa=self.tarefa, texto='Um', ordem=0)
        self.item2 = TarefaChecklistItem.objects.create(tarefa=self.tarefa, texto='Dois', ordem=1)

    def _marcar(self, item, verificado=True):
        return self.client.post(reverse('tarefa_checklist_marcar', args=[item.id]), {'verificado': '1' if verificado else '0'})

    def test_primeiro_item_poe_em_andamento_e_quem_marcou_assume(self):
        self.client.force_login(self.consultor)
        r = self._marcar(self.item1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()['feitos'], r.json()['total']), (1, 2))
        self.item1.refresh_from_db()
        self.tarefa.refresh_from_db()
        self.assertTrue(self.item1.verificado)
        self.assertEqual(self.item1.verificado_por, self.consultor)
        self.assertIsNotNone(self.item1.verificado_em)
        self.assertEqual(self.tarefa.status, Tarefa.STATUS_ANDAMENTO)
        self.assertIn(self.consultor, self.tarefa.responsaveis.all())

    def test_todos_marcados_conclui_e_desmarcar_reabre(self):
        self.client.force_login(self.consultor)
        self._marcar(self.item1)
        r = self._marcar(self.item2)
        self.assertEqual(r.json()['status'], Tarefa.STATUS_CONCLUIDA)
        self.tarefa.refresh_from_db()
        self.assertIsNotNone(self.tarefa.concluida_em)

        r = self._marcar(self.item2, verificado=False)
        self.assertEqual(r.json()['status'], Tarefa.STATUS_ANDAMENTO)
        self.item2.refresh_from_db()
        self.tarefa.refresh_from_db()
        self.assertIsNone(self.item2.verificado_por)
        self.assertIsNone(self.tarefa.concluida_em)

    def test_cancelada_nao_muda_de_status(self):
        self.tarefa.status = Tarefa.STATUS_CANCELADA
        self.tarefa.save()
        self.client.force_login(self.consultor)
        self._marcar(self.item1)
        self._marcar(self.item2)
        self.tarefa.refresh_from_db()
        self.assertEqual(self.tarefa.status, Tarefa.STATUS_CANCELADA)

    def test_outra_instancia_da_404(self):
        self.client.force_login(self.consultor_b)
        self.assertEqual(self._marcar(self.item1).status_code, 404)
        self.item1.refresh_from_db()
        self.assertFalse(self.item1.verificado)

    def test_get_nao_marca(self):
        self.client.force_login(self.consultor)
        r = self.client.get(reverse('tarefa_checklist_marcar', args=[self.item1.id]))
        self.assertEqual(r.status_code, 405)

    def test_kanban_traz_checklist_e_prazo_no_fuso_local(self):
        self.tarefa.prazo = timezone.make_aware(datetime.combine(date(2026, 9, 10), time(23, 59)))
        self.tarefa.save()
        self.client.force_login(self.admin)
        dados = self.client.get(reverse('tarefas_kanban_json', args=[self.cliente_a.id])).json()
        t = next(x for x in dados['tarefas'] if x['id'] == self.tarefa.id)
        # Antes saía em UTC: "11/09 02:59".
        self.assertEqual(t['prazo_fmt'], '10/09 23:59')
        self.assertEqual((t['checklist_feitos'], t['checklist_total']), (0, 2))
        self.assertEqual([i['texto'] for i in t['checklist']], ['Um', 'Dois'])


class RotinaViewsTest(_BaseRotinas):

    def test_consultor_cria_rotina_e_a_de_hoje_ja_sai(self):
        self.client.force_login(self.consultor)
        hoje = timezone.localdate()
        r = self.client.post(reverse('rotina_criar'), {
            'titulo': 'Revisão dos backups', 'dia_do_mes': hoje.day, 'prioridade': 'alta',
            'itens': ['Concentrador', '  ', 'OLT'], 'atribuir_a_mim': '1', 'next': '/homeinstancia',
        })
        self.assertEqual(r.status_code, 302)
        rotina = Rotina.objects.get()
        self.assertEqual(rotina.instancia, self.inst_a)
        self.assertEqual(list(rotina.itens.values_list('texto', flat=True)), ['Concentrador', 'OLT'])
        tarefa = rotina.ocorrencias.get()
        self.assertEqual(tarefa.checklist.count(), 2)
        self.assertIn(self.consultor, tarefa.responsaveis.all())

    def test_rotina_sem_itens_nao_e_criada(self):
        self.client.force_login(self.consultor)
        self.client.post(reverse('rotina_criar'), {'titulo': 'Sem itens', 'dia_do_mes': 5, 'itens': ['']})
        self.assertFalse(Rotina.objects.exists())

    def test_dia_invalido_nao_cria(self):
        self.client.force_login(self.consultor)
        self.client.post(reverse('rotina_criar'), {'titulo': 'X', 'dia_do_mes': 32, 'itens': ['a']})
        self.assertFalse(Rotina.objects.exists())

    def test_editar_troca_itens_sem_mexer_na_tarefa_do_mes(self):
        hoje = timezone.localdate()
        rotina = self._rotina(dia=hoje.day)
        tarefa = gerar_ocorrencias_rotinas()[0]
        self.client.force_login(self.consultor)
        self.client.post(reverse('rotina_editar', args=[rotina.id]), {
            'titulo': 'Revisão nova', 'dia_do_mes': hoje.day, 'itens': ['Só este'],
        })
        self.assertEqual(list(rotina.itens.values_list('texto', flat=True)), ['Só este'])
        self.assertEqual(tarefa.checklist.count(), 2)
        self.assertEqual(Tarefa.objects.count(), 1)

    def test_nao_mexe_em_rotina_de_outra_instancia(self):
        rotina = self._rotina()
        self.client.force_login(self.consultor_b)
        for nome in ('rotina_editar', 'rotina_ativar', 'rotina_excluir'):
            r = self.client.post(reverse(nome, args=[rotina.id]), {'titulo': 'x', 'dia_do_mes': 1, 'itens': ['a']})
            self.assertEqual(r.status_code, 404, nome)
        self.assertTrue(Rotina.objects.filter(pk=rotina.pk, ativa=True).exists())

    def test_retomar_comeca_a_contar_de_hoje(self):
        rotina = self._rotina(ativa=False, inicio=date(2020, 1, 1))
        self.client.force_login(self.consultor)
        self.client.post(reverse('rotina_ativar', args=[rotina.id]))
        rotina.refresh_from_db()
        self.assertTrue(rotina.ativa)
        self.assertEqual(rotina.inicio, timezone.localdate())

    def test_criar_tarefa_ja_atribuida_a_mim(self):
        self.client.force_login(self.consultor)
        self.client.post(reverse('tarefa_criar'), {'titulo': 'Minha', 'atribuir_a_mim': '1'})
        self.assertIn(self.consultor, Tarefa.objects.get(titulo='Minha').responsaveis.all())

    def test_painel_tem_rotinas_e_adicionar_dentro_das_listas(self):
        rotina = self._rotina(dia=timezone.localdate().day)
        gerar_ocorrencias_rotinas()
        outra = self._rotina(instancia=self.inst_b)
        outra.titulo = 'Rotina da instância B'
        outra.save()

        self.client.force_login(self.consultor)
        html = self.client.get('/homeinstancia').content.decode()
        self.assertIn('Rotinas mensais', html)
        self.assertIn('data-nova-tarefa', html)
        self.assertNotIn('data-bs-target="#modalNovaTarefa"', html)
        self.assertIn(reverse('rotina_editar', args=[rotina.id]), html)
        self.assertIn('Conferir backup', html)
        self.assertNotIn('Rotina da instância B', html)
        item = TarefaChecklistItem.objects.filter(tarefa__rotina=rotina).first()
        self.assertIn(reverse('tarefa_checklist_marcar', args=[item.id]), html)

    def test_tarefa_unica_do_painel_com_checklist(self):
        self.client.force_login(self.consultor)
        self.client.post(reverse('tarefa_criar'), {'titulo': 'Com itens', 'itens': ['A', '', 'B']})
        tarefa = Tarefa.objects.get(titulo='Com itens')
        self.assertEqual(list(tarefa.checklist.values_list('texto', flat=True)), ['A', 'B'])
        self.assertIsNone(tarefa.rotina_id)


class KanbanChecklistTest(_BaseRotinas):
    """Modal do Kanban do cliente: checklist na criação, rotina e itens em tarefa existente."""

    def test_kanban_cria_tarefa_com_checklist(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('tarefa_kanban_criar', args=[self.cliente_a.id]), {
            'titulo': 'Troca de equipamento', 'itens': ['Configurar', 'Testar'],
        })
        self.assertTrue(r.json()['success'])
        self.assertEqual(r.json()['tarefa']['checklist_total'], 2)

    def test_kanban_cria_rotina_do_cliente(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('rotina_kanban_criar', args=[self.cliente_a.id]), {
            'titulo': 'Revisão do cliente', 'dia_do_mes': 28, 'itens': ['Backup'],
        })
        self.assertTrue(r.json()['success'], r.content)
        rotina = Rotina.objects.get()
        self.assertEqual((rotina.cliente, rotina.instancia), (self.cliente_a, self.inst_a))
        rotinas = self.client.get(reverse('tarefas_kanban_json', args=[self.cliente_a.id])).json()['rotinas']
        self.assertEqual([x['titulo'] for x in rotinas], ['Revisão do cliente'])

    def test_kanban_rotina_sem_itens_da_erro(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('rotina_kanban_criar', args=[self.cliente_a.id]), {'titulo': 'X', 'dia_do_mes': 5})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Rotina.objects.exists())

    def test_consultor_de_outra_instancia_nao_cria_rotina_no_cliente(self):
        self.client.force_login(self.consultor_b)
        self.client.post(reverse('rotina_kanban_criar', args=[self.cliente_a.id]), {
            'titulo': 'Intrusa', 'dia_do_mes': 5, 'itens': ['a'],
        })
        self.assertFalse(Rotina.objects.exists())

    def test_adicionar_item_reabre_tarefa_concluida(self):
        tarefa = Tarefa.objects.create(titulo='T', instancia=self.inst_a, status=Tarefa.STATUS_CONCLUIDA)
        TarefaChecklistItem.objects.create(tarefa=tarefa, texto='Feito', verificado=True)
        self.client.force_login(self.consultor)
        r = self.client.post(reverse('tarefa_checklist_adicionar', args=[tarefa.id]), {'texto': 'Faltou isto'})
        self.assertEqual((r.json()['feitos'], r.json()['total'], r.json()['status']), (1, 2, Tarefa.STATUS_ANDAMENTO))
        self.assertEqual(r.json()['item']['texto'], 'Faltou isto')

    def test_remover_o_item_que_faltava_conclui(self):
        tarefa = Tarefa.objects.create(titulo='T', instancia=self.inst_a, status=Tarefa.STATUS_ANDAMENTO)
        TarefaChecklistItem.objects.create(tarefa=tarefa, texto='Feito', verificado=True, ordem=0)
        pendente = TarefaChecklistItem.objects.create(tarefa=tarefa, texto='Não precisa', ordem=1)
        self.client.force_login(self.consultor)
        r = self.client.post(reverse('tarefa_checklist_remover', args=[pendente.id]))
        self.assertEqual(r.json()['status'], Tarefa.STATUS_CONCLUIDA)
        self.assertFalse(TarefaChecklistItem.objects.filter(pk=pendente.pk).exists())

    def test_remover_ultimo_item_nao_reabre_concluida(self):
        tarefa = Tarefa.objects.create(titulo='T', instancia=self.inst_a, status=Tarefa.STATUS_CONCLUIDA)
        item = TarefaChecklistItem.objects.create(tarefa=tarefa, texto='Único', verificado=True)
        self.client.force_login(self.consultor)
        r = self.client.post(reverse('tarefa_checklist_remover', args=[item.id]))
        self.assertEqual((r.json()['total'], r.json()['status']), (0, Tarefa.STATUS_CONCLUIDA))

    def test_adicionar_e_remover_em_outra_instancia_da_404(self):
        tarefa = Tarefa.objects.create(titulo='T', instancia=self.inst_a)
        item = TarefaChecklistItem.objects.create(tarefa=tarefa, texto='x')
        self.client.force_login(self.consultor_b)
        self.assertEqual(self.client.post(reverse('tarefa_checklist_adicionar', args=[tarefa.id]), {'texto': 'y'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('tarefa_checklist_remover', args=[item.id])).status_code, 404)
        self.assertEqual(tarefa.checklist.count(), 1)

    def test_pagina_do_cliente_tem_modal_com_checklist_e_rotina(self):
        self.client.force_login(self.admin)
        html = self.client.get(f'/clientes/listar/?id={self.cliente_a.id}').content.decode()
        self.assertIn('id="ktChecklist"', html)
        self.assertIn('data-modo="rotina"', html)
        self.assertIn('#tab-tarefas .modal-overlay > .modal-acesso { align-self:flex-start; }', html)
