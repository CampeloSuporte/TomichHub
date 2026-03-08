"""
══════════════════════════════════════════════════════════════════
 INSTRUÇÕES DE INTEGRAÇÃO — App `monitoramento`
══════════════════════════════════════════════════════════════════

1. COPIE A PASTA DO APP
─────────────────────────────────────────────────────────────────
Coloque a pasta `monitoramento/` na raiz do seu projeto Django,
ao lado das outras apps (ex: `clientes/`, `core/`, etc.).

Estrutura resultante:
  meu_projeto/
  ├── clientes/
  ├── monitoramento/         ← nova pasta
  │   ├── __init__.py
  │   ├── apps.py
  │   ├── models.py
  │   ├── views.py
  │   ├── urls.py
  │   ├── services.py
  │   ├── admin.py
  │   ├── migrations/
  │   │   ├── __init__.py
  │   │   └── 0001_initial.py
  │   ├── templates/
  │   │   └── monitoramento/
  │   │       └── tab_monitoramento.html
  │   └── static/
  │       └── monitoramento/
  │           └── js/
  │               └── topology_monitor.js
  └── manage.py


2. REGISTRE O APP EM settings.py
─────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    ...
    'monitoramento.apps.MonitoramentoConfig',   # adicione esta linha
]


3. REGISTRE AS URLS EM urls.py (arquivo principal do projeto)
─────────────────────────────────────────────────────────────────
from django.urls import path, include

urlpatterns = [
    ...
    path('monitoramento/', include('monitoramento.urls', namespace='monitoramento')),
]


4. RODE AS MIGRATIONS
─────────────────────────────────────────────────────────────────
python manage.py migrate

ATENÇÃO: A migration 0001_initial.py referencia ('clientes', '0001_initial').
Se a sua migration inicial do app `clientes` tiver outro número ou nome,
ajuste a linha `dependencies` em monitoramento/migrations/0001_initial.py.


5. INSTALE A DEPENDÊNCIA (se ainda não tiver)
─────────────────────────────────────────────────────────────────
pip install requests


6. INCLUA A ABA NA PÁGINA DO CLIENTE
─────────────────────────────────────────────────────────────────
No template da página de detalhe do cliente (ex: clientes/listar.html),
adicione:

a) Link da aba (na lista de <li> das abas):
   <li class="nav-item">
     <a class="nav-link" onclick="trocarAba(event, 'monitoramento')">
       🖧 Monitoramento
     </a>
   </li>

b) Container do conteúdo da aba:
   {% include 'monitoramento/tab_monitoramento.html' %}

c) Variável global e script (dentro do <body>, antes do </body>):
   <script>window.CLIENTE_ID = '{{ cliente.id }}';</script>
   {% load static %}
   <script src="{% static 'monitoramento/js/topology_monitor.js' %}"></script>

d) Certifique-se de que a função trocarAba() mostra/esconde a div
   com id="tab-monitoramento":
   function trocarAba(e, nome) {
     document.querySelectorAll('.tab-content-panel').forEach(p => p.style.display = 'none');
     document.getElementById('tab-' + nome).style.display = 'block';
     // ... lógica de active tab, etc.
     if (nome === 'monitoramento') MON.init();
   }


7. (OPCIONAL) COLLECTSTATIC para produção
─────────────────────────────────────────────────────────────────
python manage.py collectstatic


══════════════════════════════════════════════════════════════════
 FLUXO DE USO
══════════════════════════════════════════════════════════════════

1. Acesse a aba Monitoramento no cadastro do cliente.
2. Clique em "Zabbix API" → configure a URL e credenciais → Salvar.
3. Clique em "Nova" → informe o nome da topologia.
4. Arraste dispositivos da paleta lateral para o canvas.
5. Dê duplo clique em um dispositivo → busque e vincule um host Zabbix.
6. Clique em "Conectar" → clique no nó de origem → clique no nó destino.
7. Dê duplo clique no enlace → selecione o host e interface → confirmar.
8. Clique em "Salvar" para persistir no banco.
9. Clique em "Monitorar" para iniciar o polling de 30 segundos.
   • Anel verde  = host online
   • Anel vermelho = host offline / indisponível
   • Anel amarelo = trigger ativo (alerta)
   • Anel cinza  = desconhecido / não configurado


══════════════════════════════════════════════════════════════════
 PERMISSÕES
══════════════════════════════════════════════════════════════════

Todas as views exigem login (`@login_required`).
Usuários com `is_staff=True` ou `is_superuser=True` podem gerenciar
qualquer cliente.
Usuários comuns só acessam o cliente associado ao próprio usuário
(via `Cliente.objects.get(usuario=request.user)`).

Ajuste a função `_pode_acessar_cliente()` em views.py se o seu
modelo de permissões for diferente.
"""
