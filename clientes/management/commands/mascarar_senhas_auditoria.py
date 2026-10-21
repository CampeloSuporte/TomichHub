"""Mascara as senhas já gravadas na auditoria de terminal (AcessoComando e
transcript das sessões SSH/Telnet). As sessões novas já são gravadas
mascaradas pelo consumer; isto é para o que veio antes.

    python manage.py mascarar_senhas_auditoria --dry-run
    python manage.py mascarar_senhas_auditoria [--cliente 41]

Por cliente, junta as senhas cadastradas nos acessos e as que aparecem em
qualquer sessão (palavra-chave no eco, resposta sem eco a prompt de senha) e
troca todas, em todas as sessões do cliente — a senha digitada num roteador
costuma reaparecer no stelnet para o vizinho.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from clientes import auditoria_mascara as m
from clientes.models import Acesso, AcessoComando, AcessoSessao


class Command(BaseCommand):
    help = 'Mascara senhas nos comandos e transcripts de auditoria já gravados.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Só conta, não grava.')
        parser.add_argument('--cliente', type=int, help='Só este cliente (id).')
        parser.add_argument('--mostrar', action='store_true',
                            help='Lista cada comando alterado, já mascarado.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        sessoes = AcessoSessao.objects.filter(tipo__in=('ssh', 'telnet'))
        if opts['cliente']:
            sessoes = sessoes.filter(acesso__cliente_id=opts['cliente'])
        clientes = sessoes.values_list('acesso__cliente_id', flat=True).distinct()

        tot_cmd = tot_tr = 0
        for cliente_id in sorted(set(clientes)):
            n_cmd, n_tr = self._cliente(cliente_id, sessoes.filter(acesso__cliente_id=cliente_id),
                                        dry, opts['mostrar'])
            if n_cmd or n_tr:
                self.stdout.write(f'cliente {cliente_id}: {n_cmd} comandos, {n_tr} transcripts')
            tot_cmd += n_cmd
            tot_tr += n_tr
        acao = 'seriam alterados' if dry else 'alterados'
        self.stdout.write(self.style.SUCCESS(f'Total {acao}: {tot_cmd} comandos, {tot_tr} transcripts'))

    def _cliente(self, cliente_id, sessoes, dry, mostrar):
        segredos = m.segredos_de_acessos(
            Acesso.objects.filter(cliente_id=cliente_id).values_list('senha', 'senha_adm'))

        # 1ª passada: aprende os segredos de todas as sessões do cliente.
        por_sessao = {}
        for s in sessoes.only('id', 'transcript').iterator():
            cmds = list(AcessoComando.objects.filter(sessao_id=s.id).values_list('id', 'comando'))
            if not cmds and not s.transcript:
                continue
            sem_eco = m.segredos_sem_eco([c for _i, c in cmds], s.transcript or '')
            segredos |= sem_eco
            segredos |= m.extrair_segredos(s.transcript or '')
            for _i, c in cmds:
                segredos |= m.extrair_segredos(c)
            por_sessao[s.id] = (cmds, sem_eco)

        # 2ª passada: aplica.
        n_cmd = n_tr = 0
        for s in sessoes.filter(id__in=list(por_sessao)).only('id', 'transcript').iterator():
            cmds, sem_eco = por_sessao[s.id]
            novos = []
            for cid, c in cmds:
                novo = m.MASCARA if c.strip() in sem_eco else m.mascarar(c, segredos)
                if novo != c:
                    novos.append((cid, novo))
                    if mostrar:
                        self.stdout.write(f'  sessão {s.id} cmd {cid}: {novo!r}')
            tr = s.transcript or ''
            tr_novo = m.mascarar(tr, segredos)
            n_cmd += len(novos)
            n_tr += int(tr_novo != tr)
            if dry:
                continue
            with transaction.atomic():
                for cid, novo in novos:
                    AcessoComando.objects.filter(id=cid).update(comando=novo)
                if tr_novo != tr:
                    AcessoSessao.objects.filter(id=s.id).update(transcript=tr_novo)
        return n_cmd, n_tr
