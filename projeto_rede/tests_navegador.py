"""
Teste de ponta a ponta do editor AS-IS num Chrome headless real.

O Chrome deste servidor não abre socket (ERR_ACCESS_DENIED até em
127.0.0.1) e bloqueia a porta de depuração: o controle é feito por
`--remote-debugging-pipe` e cada requisição da página é respondida pelo
Python (`Fetch.fulfillRequest`), que a repassa ao live server do Django.
Pulado quando não há Chrome instalado.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from .models import DocumentoRede
from .tests import _BaseViews

CHROME = '/usr/bin/google-chrome'


class CDP:
    """CDP via --remote-debugging-pipe (a porta TCP é bloqueada neste host)."""
    def __init__(self):
        self.tmp = tempfile.mkdtemp()
        r_chrome, w_nos = os.pipe()     # nós -> chrome (fd 3)
        r_nos, w_chrome = os.pipe()     # chrome -> nós (fd 4)
        def _fds():
            os.dup2(r_chrome, 3)
            os.dup2(w_chrome, 4)
        self.proc = subprocess.Popen([
            CHROME, '--headless=new', '--no-sandbox', '--disable-gpu',
            '--remote-debugging-pipe', f'--user-data-dir={self.tmp}', '--window-size=1400,1000', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_fds, pass_fds=(3, 4))
        os.close(r_chrome); os.close(w_chrome)
        self.w = os.fdopen(w_nos, 'wb', buffering=0)
        self.r = os.fdopen(r_nos, 'rb', buffering=0)
        self.buf = b''
        self.n = 0
        self.erros = []
        self.sessao = None
        alvos = self.cmd('Target.getTargets')['targetInfos']
        pagina = next(t for t in alvos if t['type'] == 'page')
        self.sessao = self.cmd('Target.attachToTarget', targetId=pagina['targetId'], flatten=True)['sessionId']
        self.cmd('Runtime.enable')
        self.cmd('Page.enable')
        self.cmd('Emulation.setFocusEmulationEnabled', enabled=True)
        self.base = None
        self.cookie_fixo = ''
        self.cookies_extra = {}

        class SemRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        self.opener = urllib.request.build_opener(SemRedirect)

    def servir(self, base):
        # Chrome headless aqui não abre socket (ERR_ACCESS_DENIED até em 127.0.0.1):
        # toda requisição é respondida pelo Python via Fetch.fulfillRequest.
        self.base = base
        self.cmd('Fetch.enable', patterns=[{'urlPattern': '*'}])

    def _atender(self, p):
        req = p['request']
        rid = p['requestId']
        if not req['url'].startswith(self.base):
            self._enviar('Fetch.failRequest', requestId=rid, errorReason='Failed')
            return
        # /static/js/ é servido pelo nginx a partir de STATIC_ROOT, que o live
        # server de teste não enxerga (só STATICFILES_DIRS)
        caminho = req['url'][len(self.base):].split('?')[0]
        if caminho.startswith('/static/js/'):
            arq = os.path.join(str(settings.BASE_DIR), caminho.lstrip('/'))
            if os.path.isfile(arq):
                with open(arq, 'rb') as fh:
                    self._enviar('Fetch.fulfillRequest', requestId=rid, responseCode=200,
                                 responseHeaders=[{'name': 'Content-Type', 'value': 'application/javascript'}],
                                 body=base64.b64encode(fh.read()).decode())
                return
        corpo = req.get('postData')
        r = urllib.request.Request(req['url'], data=corpo.encode() if corpo is not None else None,
                                   method=req['method'])
        for k, v in req['headers'].items():
            if k.lower() not in ('cookie', 'host', 'content-length'):
                r.add_header(k, v)
        extras = '; '.join(f'{k}={v}' for k, v in self.cookies_extra.items())
        r.add_header('Cookie', '; '.join(filter(None, [self.cookie_fixo, extras])))
        try:
            resp = self.opener.open(r, timeout=60)
            status, headers, dados = resp.status, resp.getheaders(), resp.read()
        except urllib.error.HTTPError as e:
            status, headers, dados = e.code, e.headers.items(), e.read()
        for k, v in headers:
            if k.lower() == 'set-cookie':
                nome, _, resto = v.partition('=')
                valor = resto.split(';', 1)[0]
                if nome != 'sessionid':
                    self.cookies_extra[nome] = valor
        self._enviar('Fetch.fulfillRequest', requestId=rid, responseCode=status,
                     responseHeaders=[{'name': k, 'value': v} for k, v in headers],
                     body=base64.b64encode(dados).decode())

    def _enviar(self, metodo, **params):
        self.n += 1
        self.w.write(json.dumps({'id': self.n, 'method': metodo, 'params': params,
                                 'sessionId': self.sessao}).encode() + b'\0')

    def _recv(self):
        while b'\0' not in self.buf:
            parte = self.r.read(65536)
            if not parte:
                raise RuntimeError('chrome fechou o pipe')
            self.buf += parte
        msg, self.buf = self.buf.split(b'\0', 1)
        return json.loads(msg)

    def cmd(self, metodo, **params):
        self.n += 1
        meu_id = self.n
        pedido = {'id': meu_id, 'method': metodo, 'params': params}
        if self.sessao and not metodo.startswith('Target.'):
            pedido['sessionId'] = self.sessao
        self.w.write(json.dumps(pedido).encode() + b'\0')
        while True:
            msg = self._recv()
            if msg.get('method') == 'Fetch.requestPaused':
                self._atender(msg['params'])
                continue
            if msg.get('method') == 'Runtime.exceptionThrown':
                self.erros.append(msg['params']['exceptionDetails'].get('exception', {}).get('description', msg))
            if msg.get('method') == 'Runtime.consoleAPICalled' and msg['params']['type'] == 'error':
                self.erros.append(str(msg['params']['args'])[:300])
            if msg.get('id') == meu_id:
                if 'error' in msg:
                    raise RuntimeError(msg['error'])
                return msg.get('result', {})

    def js(self, expr):
        r = self.cmd('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'])
        return r['result'].get('value')

    def esperar(self, expr, timeout=20):
        fim = time.time() + timeout
        while time.time() < fim:
            try:
                if self.js(expr):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.2)
        raise AssertionError(f'timeout esperando: {expr}')

    def ir(self, url):
        self.cmd('Page.navigate', url=url)
        self.esperar('document.readyState === "complete"')

    def fechar(self):
        self.proc.kill()
        for f in (self.w, self.r):
            try:
                f.close()
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
class EditorNavegadorTest(StaticLiveServerTestCase):
    def setUp(self):
        # Só o setUp de _BaseViews: herdar dela traria a transação do TestCase,
        # invisível para a thread do live server.
        _BaseViews.setUp(self)
        self.over = override_settings(MEDIA_ROOT=self.media)
        self.over.enable()
        self.addCleanup(self.over.disable)
        c = Client()
        c.force_login(self.admin)
        self.sessao = c.cookies[settings.SESSION_COOKIE_NAME].value
        self.b = CDP()
        self.addCleanup(self.b.fechar)
        self.b.servir(self.live_server_url)
        self.b.cookie_fixo = f'{settings.SESSION_COOKIE_NAME}={self.sessao}'

    def confirmar(self):
        self.b.esperar("getComputedStyle(document.getElementById('uiModalConfirm')).display === 'flex'")
        self.b.js("document.getElementById('ui-confirm-ok').click()")

    def test_fluxo_completo(self):
        b = self.b
        b.ir(f'{self.live_server_url}/projetos-rede/cliente/{self.cliente.id}/')
        self.assertTrue(b.js("!!document.getElementById('btnGerar')"), b.js('location.href'))
        b.js("document.getElementById('btnGerar').click()")
        self.confirmar()
        b.esperar("location.pathname.includes('/documento/') && document.readyState === 'complete' && document.querySelectorAll('.ed-secao').length > 5")
        doc = DocumentoRede.objects.get()
        n_secoes = b.js("document.querySelectorAll('.ed-secao').length")
        self.assertEqual(n_secoes, len(doc.secoes))
        self.assertEqual(b.js("document.querySelector('.ed-secao .num').textContent"), '1.')
        self.assertIn('Controle', b.js("document.querySelector('#edSumario li').textContent"))

        # editar texto da 1ª seção e inserir tabela pela toolbar
        b.js("""(() => { const c = document.querySelector('.ed-secao .ed-corpo');
            c.focus(); c.innerHTML = '<p>Texto revisado pelo NOC</p>';
            const r = document.createRange(); r.selectNodeContents(c.querySelector('p')); r.collapse(false);
            const s = getSelection(); s.removeAllRanges(); s.addRange(r);
            c.dispatchEvent(new Event('input', {bubbles:true})); })()""")
        b.js("document.querySelector('[data-inserir=tabela]').click()")
        self.assertEqual(b.js("document.querySelector('.ed-secao .ed-corpo').querySelectorAll('table').length"), 1)
        b.js("""(() => { const td = document.querySelector('.ed-secao .ed-corpo tbody td');
            const r = document.createRange(); r.selectNodeContents(td); r.collapse(true);
            const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()""")
        b.esperar("!document.querySelector('[data-tab=linha-abaixo]').disabled")
        b.js("document.querySelector('[data-tab=linha-abaixo]').click()")
        b.js("document.querySelector('[data-tab=coluna]').click()")
        self.assertEqual(b.js("document.querySelector('.ed-secao .ed-corpo tbody').rows.length"), 3)
        self.assertEqual(b.js("document.querySelector('.ed-secao .ed-corpo thead tr').cells.length"), 4)

        # colar HTML sujo: sanitizado no cliente
        b.js("""(() => { const c = document.querySelector('.ed-secao .ed-corpo'); c.focus();
            const dt = new DataTransfer(); dt.setData('text/html', '<p style="color:red" onclick="x()">Colado <b>forte</b></p><script>window.hack=1<\\/script>');
            c.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); })()""")

        # mover a 1ª seção para baixo, criar seção nova, editar capa
        b.js("document.querySelector('.ed-secao .ed-secao-ctl button[title=\"Mover para baixo\"]').click()")
        self.assertEqual(b.js("document.querySelectorAll('.ed-secao .ed-titulo')[1].value"), 'Controle e finalidade')
        b.js("document.getElementById('btnNovaSecao').click()")
        b.js("""(() => { const secs = document.querySelectorAll('.ed-secao'); const t = secs[secs.length-1].querySelector('.ed-titulo');
            t.value = 'Premissas do cliente'; t.dispatchEvent(new Event('input', {bubbles:true}));
            const i = document.querySelector('[data-meta=responsavel]'); i.value = 'Marcio Marinho';
            i.dispatchEvent(new Event('input', {bubbles:true})); })()""")
        self.assertIn('Alterações', b.js("document.querySelector('#edEstado .txt').textContent"))

        # salvar
        b.js("document.getElementById('btnSalvar').click()")
        b.esperar("document.querySelector('#edEstado .txt').textContent.startsWith('Salvo às')")
        doc.refresh_from_db()
        self.assertEqual(doc.metadados['responsavel'], 'Marcio Marinho')
        self.assertEqual(doc.secoes[1]['titulo'], 'Controle e finalidade')
        self.assertFalse(doc.secoes[1]['auto'])
        self.assertIn('Texto revisado pelo NOC', doc.secoes[1]['html'])
        self.assertEqual(doc.secoes[1]['html'].count('<tr>'), 4)
        self.assertIn('<strong>forte</strong>', doc.secoes[1]['html'])
        self.assertNotIn('onclick', doc.secoes[1]['html'])
        self.assertNotIn('script', doc.secoes[1]['html'])
        self.assertIsNone(b.js("window.hack === undefined ? null : window.hack"))
        self.assertEqual(doc.secoes[-1]['titulo'], 'Premissas do cliente')

        # registrar revisão com nova versão
        b.js("document.getElementById('btnRevisao').click()")
        self.assertEqual(b.js("document.getElementById('revVersao').value"), '1.1')
        b.js("document.getElementById('revMotivo').value = 'Revisão NOC'; document.getElementById('revConfirmar').click()")
        b.esperar("!document.getElementById('modalRevisao').classList.contains('aberto')")
        doc.refresh_from_db()
        self.assertEqual(doc.versao, '1.1')
        self.assertEqual(doc.revisoes.count(), 2)

        # histórico lista as revisões
        b.js("document.getElementById('btnHistorico').click()")
        b.esperar("document.querySelectorAll('#histLista .ed-rev').length === 2")

        # recalcular uma seção automática (volta a ser 'auto')
        b.js("document.querySelector('#modalHistorico [data-fechar]').click()")
        b.js("document.querySelectorAll('.ed-secao')[1].querySelector('button[title^=\"Recalcular\"]').click()")
        self.confirmar()
        b.esperar("!document.querySelectorAll('.ed-secao')[1].querySelector('.ed-corpo').textContent.includes('revisado')")
        b.js("document.getElementById('btnSalvar').click()")
        b.esperar("document.querySelector('#edEstado .txt').textContent.startsWith('Salvo às')")
        doc.refresh_from_db()
        self.assertTrue(doc.secoes[1]['auto'])

        self.assertEqual(b.erros, [])

        # excluir
        b.js("document.getElementById('btnExcluir').click()")
        self.confirmar()
        b.esperar("location.pathname.endsWith('/cliente/%d/')" % self.cliente.id)
        self.assertFalse(DocumentoRede.objects.exists())


@unittest.skipUnless(os.path.exists(CHROME), 'Google Chrome não instalado')
class CenarioNavegadorTest(EditorNavegadorTest):
    """Editor de topologia em modo cenário TO-BE: painel de alvo, gravação no
    cenário (não no mapa real) e o editor normal intacto."""

    def test_fluxo_completo(self):   # substitui o fluxo do editor de documento
        import json as _json
        from clientes.models import Acesso, TopologiaDiagrama
        from .models import CenarioTopologia
        acesso = Acesso.objects.get()
        mapa = TopologiaDiagrama.objects.create(cliente=self.cliente, nome='BACKBONE', dados_json=_json.dumps({
            'nodes': [{'id': 'n1', 'type': 'router', 'x': 0, 'y': 0, 'label': 'PE-AFT', 'ip': '10.0.0.1',
                       'color': '#58a6ff', 'w': 64, 'h': 64, 'acesso_id': acesso.id},
                      {'id': 'n2', 'type': 'router', 'x': 200, 'y': 0, 'label': 'PE-JNA', 'ip': '10.0.0.2',
                       'color': '#58a6ff', 'w': 64, 'h': 64}],
            'links': [{'id': 'l1', 'src': 'n1', 'tgt': 'n2', 'iface': '100g', 'label': '', 'ip_local': '',
                       'ip_remote': '', 'vlan': '', 'style': 'solid'}]}))
        b = self.b

        # editor normal continua funcionando (sem TOPO_CENARIO)
        b.ir(f'{self.live_server_url}/clientes/{self.cliente.id}/topologia/editor/')
        b.esperar('window.topo && topo.nodes.length === 2')
        self.assertFalse(b.js('!!window.TOPO_CENARIO'))
        b.js("topo._select('node', 'n1')")
        self.assertFalse(b.js("!!document.getElementById('pn-tobe-estado')"))

        # cria o cenário pela tela de arquitetura
        b.ir(f'{self.live_server_url}/projetos-rede/cliente/{self.cliente.id}/')
        b.js(f"document.getElementById('cenOrigem').value = '{mapa.id}'; window.open = () => null;"
             "document.getElementById('btnNovoCenario').click()")
        b.esperar("document.readyState === 'complete' && document.body.innerText.includes('TO-BE — BACKBONE')")
        cen = CenarioTopologia.objects.get()

        b.ir(f'{self.live_server_url}/projetos-rede/cenario/{cen.id}/')
        b.esperar('window.topo && topo.nodes.length === 2')
        self.assertTrue(b.js('!!window.TOPO_CENARIO'))
        self.assertTrue(b.js("document.getElementById('btn-agrupar').hidden"))
        b.js("topo._select('node', 'n1')")
        b.esperar("!!document.getElementById('pn-tobe-estado')")
        b.js("""document.querySelectorAll('.pn-tobe-papel').forEach(i => { i.checked = ['RR01', 'BNG01'].includes(i.value); });
                document.getElementById('pn-tobe-loopback').value = '198.18.248.1/32';
                topo._applyNodeProps('n1');""")
        self.assertTrue(b.js("!!document.querySelector('[data-id=\"n1\"] .tobe-badge')"))
        b.js("topo._select('link', 'l1')")
        b.esperar("!!document.getElementById('pl-tobe-mtu')")
        b.js("""document.getElementById('pl-tobe-mtu').value = '9100';
                document.getElementById('pl-tobe-estado').value = 'remover';
                topo._applyLinkProps('l1');""")
        self.assertTrue(b.js("document.querySelector('g[data-link=\"l1\"]').classList.contains('tobe-remover')"))
        b.js("topo.addNode('router', 400, 0, {label: 'PE-NOVO'})")
        b.js("document.getElementById('nome-diagrama').value = 'Alvo 2027'; topo.save()")
        b.esperar("document.getElementById('st-save').textContent.includes('Salvo')")

        cen.refresh_from_db()
        dados = cen.dados()
        n1 = next(n for n in dados['nodes'] if n['id'] == 'n1')
        self.assertEqual(n1['tobe']['papeis'], ['RR01', 'BNG01'])
        self.assertEqual(n1['tobe']['loopback'], '198.18.248.1/32')
        self.assertEqual(dados['links'][0]['tobe']['mtu_alvo'], '9100')
        self.assertEqual(dados['links'][0]['tobe']['estado'], 'remover')
        novo = next(n for n in dados['nodes'] if n['label'] == 'PE-NOVO')
        self.assertEqual(novo['tobe']['estado'], 'novo')
        self.assertEqual(cen.nome, 'Alvo 2027')
        mapa.refresh_from_db()
        self.assertNotIn('tobe', mapa.dados_json)            # mapa real intacto
        self.assertEqual(b.erros, [])
