"""Grafo de conhecimento do Agent NOC (home/agent_grafo.py) pela linha de comando.

  manage.py agent_grafo "Sartor"                       → resumo + economia do prompt
  manage.py agent_grafo "Sartor" -q "cgnat vrouter"    → mesma consulta que o agent faz
  manage.py agent_grafo "Sartor" --saida /tmp/graph.json
        → exporta no formato node-link do Graphify (abre em `graphify query --graph`)

Só lê o banco; não acessa equipamento nem grava nada no CRM.
"""
import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Mostra, consulta ou exporta o grafo de conhecimento do Agent NOC de um cliente.'

    def add_arguments(self, parser):
        parser.add_argument('cliente', help='ID ou parte do nome do cliente')
        parser.add_argument('-q', '--consulta', default='', help='Pergunta (como em consultar_grafo)')
        parser.add_argument('--saida', default='', help='Arquivo graph.json a gravar')

    def handle(self, *args, **opts):
        from clientes.models import Acesso, Cliente
        from home.agent_grafo import construir_grafo, linha_host_compacta

        ref = opts['cliente']
        qs = Cliente.objects.filter(id=int(ref)) if ref.isdigit() else \
            Cliente.objects.filter(nome_empresa__icontains=ref)
        clientes = list(qs[:5])
        if not clientes:
            raise CommandError(f'Cliente "{ref}" não encontrado.')
        if len(clientes) > 1:
            raise CommandError('Mais de um cliente: ' + ', '.join(f'{c.id}={c.nome_empresa}' for c in clientes))
        cliente = clientes[0]

        acessos = list(Acesso.objects.filter(cliente=cliente).select_related('modelo', 'funcao'))
        g = construir_grafo(cliente, acessos)
        self.stdout.write(f'{cliente.nome_empresa}: {g.resumo()}')
        backup_total = sum(len(a.contexto_backup or '') for a in acessos)
        compacto = sum(len(linha_host_compacta(a)) for a in acessos)
        self.stdout.write(f'Backups dos hosts: {backup_total} chars | lista compacta: {compacto} chars')

        if opts['consulta']:
            self.stdout.write('')
            self.stdout.write(g.consultar(opts['consulta']))
        if opts['saida']:
            with open(opts['saida'], 'w', encoding='utf-8') as f:
                json.dump(g.exportar_graphify_json(), f, ensure_ascii=False, indent=1)
            self.stdout.write(f'graph.json gravado em {opts["saida"]}')
