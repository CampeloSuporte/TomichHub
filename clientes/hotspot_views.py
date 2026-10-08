"""
hotspot_views.py — Captive portal público dos Hotspots MikroTik.

O módulo Hotspot foi removido da plataforma (aba, permissão e telas internas
saíram em 08/10/2026). Ficam só os endpoints públicos, porque MikroTiks de
clientes ainda apontam o login.html para cá: sem eles o WiFi desses clientes
para de autenticar. Os dados (HotspotConfig, HotspotLead, ...) continuam no
banco, sem tela para editar.
"""
import html as _html
import logging

from django.db.models import Q
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import HotspotConfig, HotspotLead

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HTML generation
# ─────────────────────────────────────────────────────────────────────────────

def _gerar_login_html(hotspot, portal_url):
    """
    Gera um login.html mínimo para o MikroTik que redireciona o usuário para
    o portal hospedado no CRM. Usa HTTP para que o walled-garden permita o
    acesso antes da autenticação.

    IMPORTANTE (sem <script>, sem $(link-login)/$(link-orig)): captive portal
    browsers embutidos (iOS Captive Network Assistant, Android) são muito mais
    restritos que um Safari/Chrome normal — historicamente falham silenciosamente
    (tela em branco) em páginas que dependem de JS para redirecionar, e o
    RouterOS precisa reescrever o arquivo em tempo real para substituir cada
    $(...) pelo valor real, o que pode inflar bastante o tamanho da resposta
    quando a variável é uma URL longa (ex: $(link-login), $(link-orig) — cada
    uma pode ter 100+ caracteres com querystring própria). Um <meta refresh>
    puro, sem JS e só com variáveis curtas ($(mac)/$(ip)), é a combinação mais
    compatível. O hotspot_portal_conectar já tem fallback para gateway quando
    "link"/"orig" não chegam (ver uso de h.gateway abaixo).

    IMPORTANTE 2 (IP em vez de hostname): usar o nome de domínio aqui exige que
    o cliente resolva DNS antes de conseguir navegar. Em Android isso costuma
    ir por "DNS Privado" (DoH do Google/Cloudflare, ignorando totalmente o DNS
    do roteador) — o domínio tem registro AAAA (IPv6), o aparelho tenta por
    IPv6 mesmo sem rota de verdade, e o navegador cativo restrito não faz o
    fallback pra IPv4 que um browser normal faria (net::ERR_CONNECTION_ABORTED).
    Resolvendo pra IP aqui (uma vez, no servidor) e usando o IP puro na URL,
    a navegação nunca depende de DNS do cliente — nginx tem crm.tomich.com.br
    e esse IP como server_name válidos, então a rota até o Django funciona
    igual em ambos os casos.
    """
    # Força HTTP: clientes não têm HTTPS no walled-garden antes de autenticar
    http_portal = portal_url.replace('https://', 'http://', 1).rstrip('/')
    try:
        from urllib.parse import urlsplit, urlunsplit
        import socket as _sock3
        _parts = urlsplit(http_portal)
        _ip_host = _sock3.gethostbyname(_parts.hostname)  # IPv4-only, evita AAAA
        http_portal = urlunsplit(_parts._replace(netloc=_ip_host))
    except Exception:
        pass  # se a resolução falhar, mantém o hostname (comportamento anterior)
    destino = http_portal + '/?mac=$(mac)&ip=$(ip)'

    return (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta http-equiv="refresh" content="1;url=' + destino + '">\n'
        '</head>\n'
        '<body>\n'
        '<p>Redirecionando para o portal...</p>\n'
        '<p><a href="' + destino + '">Clique aqui se não for redirecionado automaticamente</a></p>\n'
        '</body>\n'
        '</html>\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Termos de Uso / Política de Privacidade (LGPD) — conteúdo estático exibido
# no modal de aceite da tela de login do hotspot.
# ─────────────────────────────────────────────────────────────────────────────

_TERMOS_CONTEUDO_HTML = """
<div class="m-tabcontent active" id="mc-resumo">
  <p class="m-lead">Este documento define como coletamos, usamos e protegemos seus dados quando você usa nosso serviço de WiFi grátis, e as regras para uso do serviço.</p>
  <ul>
    <li>Coletamos seu <strong>nome</strong> e <strong>telefone</strong> para liberar seu acesso;</li>
    <li>Registramos dados técnicos de conexão por <strong>12 meses</strong> (exigência legal do Marco Civil da Internet);</li>
    <li>Você pode solicitar a exclusão dos seus dados, mas alguns registros serão mantidos pelo período legal;</li>
    <li>Você é responsável por usar o serviço de forma legal e adequada;</li>
    <li>Não compartilhamos seus dados com terceiros, exceto quando exigido por lei.</li>
  </ul>
  <p>Para detalhes completos, consulte as abas "Privacidade" e "Termos" acima.</p>
</div>

<div class="m-tabcontent" id="mc-privacidade">
  <p>Esta Política de Privacidade descreve como coletamos, usamos, armazenamos, compartilhamos e protegemos seus dados pessoais ao utilizar este serviço de WiFi gratuito ("Serviço"), em conformidade com a Lei Geral de Proteção de Dados (Lei nº 13.709/2018) e o Marco Civil da Internet (Lei nº 12.965/2014).</p>

  <h4>1. Dados que coletamos</h4>
  <ul>
    <li><strong>Dados cadastrais</strong>: nome, sobrenome e telefone para autenticação;</li>
    <li><strong>Dados de conexão</strong>: data e hora de início e término, duração, IP utilizado, endereço MAC do dispositivo e registros de acesso;</li>
    <li><strong>Dados de navegação</strong>: volume de dados trafegados (sem conteúdo das comunicações).</li>
  </ul>

  <h4>2. Finalidades do tratamento</h4>
  <ul>
    <li>Autenticar seu acesso ao Serviço;</li>
    <li>Cumprir obrigações legais e regulatórias;</li>
    <li>Proteger a segurança da rede e identificar usos inadequados ou ilícitos;</li>
    <li>Melhorar a qualidade do Serviço.</li>
  </ul>

  <h4>3. Bases legais</h4>
  <p>Execução de contrato, consentimento, cumprimento de obrigação legal (Marco Civil da Internet) e legítimo interesse na segurança da rede.</p>

  <h4>4. Compartilhamento de dados</h4>
  <ul>
    <li>Com autoridades públicas, mediante ordem judicial ou requisição legal;</li>
    <li>Com prestadores de serviço que nos auxiliam na operação do Serviço, sob contrato de confidencialidade;</li>
    <li>Em caso de reorganização societária, fusão ou aquisição.</li>
  </ul>

  <h4>5. Período de retenção</h4>
  <ul>
    <li><strong>Registros de conexão</strong>: 12 meses, conforme exigido pelo Marco Civil da Internet;</li>
    <li><strong>Dados cadastrais</strong>: enquanto você for usuário do Serviço e pelo período necessário ao cumprimento de obrigações legais.</li>
  </ul>

  <h4>6. Seus direitos (LGPD)</h4>
  <p>Você pode confirmar a existência de tratamento, acessar, corrigir, solicitar anonimização/bloqueio/eliminação de dados desnecessários, solicitar portabilidade e revogar o consentimento a qualquer momento.</p>
  <p class="m-important">Em caso de solicitação de exclusão, alguns dados poderão ser mantidos pelo período legal (registros de conexão) ou para cumprimento de outras obrigações legais.</p>

  <h4>7. Medidas de segurança</h4>
  <p>Adotamos criptografia, controle de acesso, firewalls e políticas internas de proteção de dados.</p>

  <h4>8. Alterações</h4>
  <p>Esta Política pode ser atualizada periodicamente; a versão mais recente estará sempre disponível nesta tela de login.</p>
</div>

<div class="m-tabcontent" id="mc-termos">
  <p>Estes Termos de Uso estabelecem as condições para utilização deste serviço de WiFi gratuito ("Serviço"). Ao utilizar o Serviço, você concorda com estes Termos.</p>

  <h4>1. Descrição do Serviço</h4>
  <p>O Serviço consiste no fornecimento de acesso gratuito à internet via WiFi. É oferecido "como está", sem garantias de velocidade ou disponibilidade contínua.</p>

  <h4>2. Condições de uso</h4>
  <ul>
    <li>Utilizar o Serviço apenas para finalidades lícitas;</li>
    <li>Não prejudicar o funcionamento do Serviço ou da rede;</li>
    <li>Não enviar spam, vírus ou outros códigos maliciosos;</li>
    <li>Não acessar ou distribuir conteúdos ilegais;</li>
    <li>Não violar direitos de propriedade intelectual;</li>
    <li>Não utilizar ferramentas para burlar limitações do Serviço ou ocultar sua identidade.</li>
  </ul>

  <h4>3. Limitações do Serviço</h4>
  <p>Limite de velocidade e de tempo de uso, bloqueio de determinados conteúdos, interrupções para manutenção e priorização de tráfego, conforme definido pelo provedor do Serviço.</p>

  <h4>4. Responsabilidades</h4>
  <p>Você é o único responsável pelas atividades realizadas durante sua sessão e pelo conteúdo que acessar. Não nos responsabilizamos por perda de dados, danos a dispositivos ou interrupções do Serviço.</p>

  <h4>5. Monitoramento e suspensão</h4>
  <p>Reservamo-nos o direito de monitorar o uso do Serviço para fins de segurança, suspender o acesso em caso de violação destes Termos e reportar atividades ilegais às autoridades competentes.</p>

  <h4>6. Legislação aplicável</h4>
  <p>Estes Termos são regidos pelas leis da República Federativa do Brasil.</p>
</div>
"""


def _alpha(hex_color, opacity):
    """Anexa um sufixo alpha de 2 dígitos hex a uma cor #RRGGBB (ex: #fff, .6 -> #fff99)."""
    return f'{hex_color}{round(opacity * 255):02x}'


# ─────────────────────────────────────────────────────────────────────────────
# Public portal — página de login hospedada no CRM, acessível via walled-garden
# ─────────────────────────────────────────────────────────────────────────────

def _portal_page_html(hotspot, link, mac, ip, orig, request):
    """Gera a página do portal de captação de lead hospedada no CRM."""
    scheme = 'https' if request.is_secure() else 'http'
    host   = request.get_host()
    submit_url = f'{scheme}://{host}/clientes/hotspot/portal/{hotspot.uuid}/conectar/'

    cor       = hotspot.cor_primaria or '#6366f1'
    titulo    = hotspot.portal_titulo or 'WiFi Grátis'
    subtitulo = hotspot.portal_subtitulo or 'Preencha seus dados para se conectar'

    # Logo — usa o mesmo scheme da requisição
    logo_url = f'{scheme}://{host}{hotspot.logo.url}' if hotspot.logo else None

    cor_dark = hotspot.cor_secundaria or cor
    cor_painel = hotspot.cor_painel or '#0f0f19'
    cor_texto = hotspot.cor_texto or '#ffffff'

    # Fundo da página de login — 3 estilos: gradiente (padrão, comportamento
    # histórico), solido, imagem. cor_fundo default reproduz o valor
    # hardcoded anterior (#0a0a0f), então configs existentes ficam idênticas.
    cor_fundo    = hotspot.cor_fundo or '#0a0a0f'
    estilo_fundo = hotspot.estilo_fundo or 'gradiente'
    imagem_fundo_url = f'{scheme}://{host}{hotspot.imagem_fundo.url}' if hotspot.imagem_fundo else None

    if estilo_fundo == 'imagem' and imagem_fundo_url:
        bg_css = (
            f"background:url('{imagem_fundo_url}') center/cover no-repeat, {cor_fundo};"
        )
        bg_overlay_css = (
            f"background:linear-gradient(180deg,{cor_fundo}99,{cor_fundo}cc);"
        )
    elif estilo_fundo == 'solido':
        bg_css = f"background:{cor_fundo};"
        bg_overlay_css = "background:none;"
    else:
        bg_css = f"background:{cor_fundo};"
        bg_overlay_css = (
            f"background:radial-gradient(ellipse at 20% 50%,{cor}22 0%,transparent 60%),"
            f"radial-gradient(ellipse at 80% 20%,{cor}18 0%,transparent 55%),"
            f"radial-gradient(ellipse at 50% 90%,{cor_dark}22 0%,transparent 60%);"
        )

    # Banners → slideshow do splash (mostrado ANTES da página de login)
    slides = []
    for i, b in enumerate(hotspot.banners.filter(ativo=True).order_by('ordem', 'criado_em')):
        active = ' active' if i == 0 else ''
        img_url = f'{scheme}://{host}{b.imagem.url}'
        slides.append(f'<div class="slide{active}" style="background-image:url(\'{img_url}\')"></div>')
    has_slides = bool(slides)
    slides_str = '\n'.join(slides) if slides else ''

    # Logo centralizada no topo do card (ou ícone de WiFi como fallback)
    logo_html = (f'<img class="empresa-logo" src="{logo_url}" alt="Logo">'
                 if logo_url else '<div class="wifi-ring">&#x1F4F6;</div>')

    # Splash de banners (só existe se houver banner ativo). Card de login começa
    # oculto enquanto o splash é exibido.
    if has_slides:
        splash_html = (
            '<div class="splash" id="splash">\n'
            '  <div class="splash-slides">\n'
            f'{slides_str}\n'
            '    <div class="splash-grad"></div>\n'
            '  </div>\n'
            '  <div class="splash-dots" id="dots"></div>\n'
            '  <button class="splash-btn" type="button" id="splashBtn">'
            'Continuar para o WiFi &#x2192;</button>\n'
            '</div>'
        )
        card_style = ' style="display:none"'
    else:
        splash_html = ''
        card_style = ''

    # Valores dos campos ocultos devem ser HTML-escaped: URLs contêm & que
    # seria interpretado como início de entidade HTML e quebraria o value=""
    link_h = _html.escape(link, quote=True)
    mac_h  = _html.escape(mac,  quote=True)
    ip_h   = _html.escape(ip,   quote=True)
    orig_h = _html.escape(orig, quote=True)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>{titulo}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
:root{{--c:{cor};--cr:color-mix(in srgb,{cor} 80%,#000)}}
html,body{{height:100%;overflow:hidden}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  {bg_css}
  display:flex;align-items:center;justify-content:center;
  position:relative;
}}

/* Fundo animado */
.bg{{
  position:fixed;inset:0;z-index:0;
  {bg_overlay_css}
  animation:bgpulse 8s ease-in-out infinite alternate;
}}
@keyframes bgpulse{{
  0%{{background-position:0% 50%}}
  100%{{background-position:100% 50%}}
}}

/* Card principal */
.card{{
  position:relative;z-index:1;
  width:min(420px,100vw);
  max-height:100dvh;
  overflow-y:auto;
  -webkit-overflow-scrolling:touch;
  border-radius:24px;
  background:{cor_painel}d9;
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border:1px solid rgba(255,255,255,0.08);
  box-shadow:0 32px 80px rgba(0,0,0,0.7),0 0 0 1px rgba(255,255,255,0.04) inset;
  animation:cardIn .5s cubic-bezier(.22,1,.36,1) both;
}}
@keyframes cardIn{{from{{opacity:0;transform:translateY(24px)}}to{{opacity:1;transform:none}}}}

/* ── Splash de banners (mostrado ANTES do login) ───────────────── */
.splash{{
  position:fixed;inset:0;z-index:10;
  display:flex;flex-direction:column;
  background:#0a0a0f;
  animation:splashIn .4s ease both;
}}
.splash.hide{{animation:splashOut .5s ease forwards}}
@keyframes splashIn{{from{{opacity:0}}to{{opacity:1}}}}
@keyframes splashOut{{to{{opacity:0;visibility:hidden}}}}
.splash-slides{{position:relative;flex:1;overflow:hidden}}
.slide{{
  position:absolute;inset:0;opacity:0;
  background-size:contain;background-repeat:no-repeat;background-position:center;
  transition:opacity .9s ease;
}}
.slide.active{{opacity:1}}
.splash-grad{{
  position:absolute;inset:0;z-index:1;pointer-events:none;
  background:linear-gradient(to bottom,transparent 60%,rgba(10,10,15,.9) 100%);
}}
.splash-dots{{
  position:absolute;bottom:96px;left:0;right:0;z-index:2;
  display:flex;justify-content:center;gap:6px;
}}
.dot{{
  width:7px;height:7px;border-radius:50%;
  background:rgba(255,255,255,.35);transition:all .3s;cursor:pointer;
}}
.dot.active{{width:20px;border-radius:4px;background:#fff}}
.splash-btn{{
  position:absolute;left:20px;right:20px;bottom:28px;z-index:3;
  height:54px;border:none;border-radius:14px;
  background:linear-gradient(135deg,{cor},{cor_dark});
  color:#fff;font-size:1rem;font-weight:700;letter-spacing:.02em;cursor:pointer;
  box-shadow:0 6px 24px {cor}55;
}}
.splash-btn:active{{transform:translateY(1px)}}

/* ── Cabeçalho do card de login (logo centralizada no topo) ─────── */
.slide{{background-color:#0a0a0f}}
.login-header{{text-align:center;padding:34px 24px 6px}}
.empresa-logo{{
  display:block;margin:0 auto 18px;
  max-height:100px;max-width:90%;
  object-fit:contain;
  filter:drop-shadow(0 4px 14px rgba(0,0,0,.55));
}}
.wifi-ring{{
  width:76px;height:76px;border-radius:22px;margin:0 auto 18px;
  background:rgba(255,255,255,0.08);
  border:1px solid rgba(255,255,255,0.16);
  display:flex;align-items:center;justify-content:center;
  font-size:36px;
}}
.login-header h2{{color:{cor_texto};font-size:1.5rem;font-weight:700;line-height:1.2}}
.login-header p{{color:{_alpha(cor_texto,.6)};font-size:.9rem;margin-top:6px}}

/* Form area */
.form-area{{padding:22px 22px 22px}}

/* Linha com dois campos lado a lado (Nome / Sobrenome) */
.field-row{{display:flex;gap:10px}}
.field-row .field{{flex:1 1 0;min-width:0}}

/* Inputs com ícone flutuante */
.field{{position:relative;margin-bottom:16px}}
.field-icon{{
  position:absolute;left:16px;top:50%;transform:translateY(-50%);
  font-size:17px;opacity:.5;pointer-events:none;
}}
.field input{{
  width:100%;height:54px;
  padding:0 14px 0 44px;
  background:rgba(255,255,255,0.06);
  border:1.5px solid rgba(255,255,255,0.14);
  border-radius:14px;
  color:{cor_texto};font-size:.95rem;
  outline:none;transition:border-color .2s,background .2s,box-shadow .2s;
  -webkit-appearance:none;appearance:none;
}}
.field.no-icon input{{padding-left:16px}}
.field input::placeholder{{color:{_alpha(cor_texto,.3)}}}
.field input:hover{{border-color:rgba(255,255,255,.22)}}
.field input:focus{{
  border-color:{cor};
  background:rgba(255,255,255,0.09);
  box-shadow:0 0 0 3px {cor}2e;
}}
.field.error input{{border-color:#f87171;background:rgba(248,113,113,.08)}}
.field-hint{{font-size:.72rem;color:{_alpha(cor_texto,.38)};margin:-10px 2px 14px;padding-left:4px;transition:color .2s}}
.field-hint.error{{color:#f87171}}
.field-label{{
  position:absolute;left:44px;top:50%;transform:translateY(-50%);
  font-size:.85rem;color:{_alpha(cor_texto,.38)};
  pointer-events:none;transition:all .2s;
}}
.field.no-icon .field-label{{left:16px}}
.field input:focus ~ .field-label,
.field input:not(:placeholder-shown) ~ .field-label{{
  top:9px;font-size:.68rem;color:{cor};letter-spacing:.03em;transform:none;
}}

/* Aceite de termos */
.terms-check{{
  display:flex;align-items:flex-start;gap:10px;
  margin:4px 2px 18px;cursor:pointer;user-select:none;
}}
.terms-check input{{
  margin-top:2px;width:18px;height:18px;flex:none;
  accent-color:{cor};cursor:pointer;
}}
.terms-check span{{font-size:.8rem;line-height:1.45;color:{_alpha(cor_texto,.55)}}}
.terms-check a{{color:{cor};text-decoration:underline;font-weight:600}}
.terms-check.error span{{color:#f87171}}

/* Botão */
.btn-wrap{{margin-top:6px}}
.btn{{
  width:100%;height:54px;
  background:linear-gradient(135deg,{cor},{cor_dark});
  border:none;border-radius:14px;
  color:#fff;font-size:1rem;font-weight:700;letter-spacing:.02em;
  cursor:pointer;position:relative;overflow:hidden;
  display:flex;align-items:center;justify-content:center;gap:10px;
  transition:transform .15s,box-shadow .2s;
  box-shadow:0 6px 24px {cor}55;
}}
.btn:hover{{transform:translateY(-1px);box-shadow:0 10px 32px {cor}66}}
.btn:active{{transform:translateY(0);box-shadow:0 4px 16px {cor}44}}
.btn:disabled{{opacity:.5;cursor:not-allowed;transform:none}}
.btn::after{{
  content:"";position:absolute;inset:0;
  background:linear-gradient(135deg,rgba(255,255,255,.15),transparent);
  border-radius:inherit;
}}
.spin{{
  width:20px;height:20px;border:2.5px solid rgba(255,255,255,.25);
  border-top-color:#fff;border-radius:50%;
  animation:spin .7s linear infinite;display:none;
}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}

/* Rodapé */
.footer{{
  text-align:center;margin-top:14px;
  font-size:.72rem;color:{_alpha(cor_texto,.2)};line-height:1.5;
}}
.footer a{{color:{_alpha(cor_texto,.35)};text-decoration:none}}

/* ── Modal de Termos de Uso / Política de Privacidade ────────────── */
.modal-overlay{{
  position:fixed;inset:0;z-index:20;display:none;
  background:rgba(5,5,10,.72);backdrop-filter:blur(4px);
  align-items:center;justify-content:center;padding:16px;
}}
.modal-overlay.open{{display:flex}}
.modal{{
  width:min(480px,100%);max-height:88vh;
  display:flex;flex-direction:column;
  background:#12121c;border-radius:20px;
  border:1px solid rgba(255,255,255,0.1);
  box-shadow:0 32px 80px rgba(0,0,0,.6);
  animation:cardIn .3s cubic-bezier(.22,1,.36,1) both;
}}
.modal-head{{
  display:flex;align-items:center;justify-content:space-between;
  padding:18px 20px;border-bottom:1px solid rgba(255,255,255,.08);
}}
.modal-head h3{{color:#fff;font-size:1.02rem;font-weight:700}}
.modal-close{{
  background:rgba(255,255,255,.08);border:none;color:#fff;
  width:30px;height:30px;border-radius:50%;font-size:18px;line-height:1;
  cursor:pointer;flex:none;
}}
.modal-tabs{{display:flex;gap:6px;padding:12px 20px 0}}
.m-tab{{
  padding:8px 14px;border-radius:10px;font-size:.78rem;font-weight:600;
  color:rgba(255,255,255,.5);background:rgba(255,255,255,.05);cursor:pointer;
}}
.m-tab.active{{color:#fff;background:{cor}}}
.modal-body{{
  padding:16px 20px 4px;overflow-y:auto;-webkit-overflow-scrolling:touch;
  color:rgba(255,255,255,.72);font-size:.82rem;line-height:1.6;
}}
.m-tabcontent{{display:none}}
.m-tabcontent.active{{display:block}}
.m-tabcontent h4{{color:#fff;font-size:.86rem;margin:16px 0 6px}}
.m-tabcontent h4:first-child{{margin-top:0}}
.m-tabcontent p{{margin-bottom:8px}}
.m-tabcontent ul{{margin:0 0 10px 18px}}
.m-tabcontent li{{margin-bottom:4px}}
.m-lead{{color:rgba(255,255,255,.85)}}
.m-important{{color:#fbbf24;font-weight:600}}
.modal-foot{{padding:16px 20px 20px}}
.modal-foot .btn{{height:48px;font-size:.9rem}}
</style>
</head>
<body>
<div class="bg"></div>

<!-- Banners exibidos ANTES da página de login (slideshow se >1) -->
{splash_html}

<div class="card" id="card"{card_style}>
  <!-- Cabeçalho: logo centralizada no topo -->
  <div class="login-header">
    {logo_html}
    <h2>{titulo}</h2>
    <p>{subtitulo}</p>
  </div>

  <!-- Formulário -->
  <div class="form-area">
    <form id="hf" method="post" action="{submit_url}" onsubmit="return onSubmit()">
      <input type="hidden" name="link"  value="{link_h}">
      <input type="hidden" name="mac"   value="{mac_h}">
      <input type="hidden" name="ip"    value="{ip_h}">
      <input type="hidden" name="orig"  value="{orig_h}">

      <div class="field-row">
        <div class="field">
          <span class="field-icon">&#x1F464;</span>
          <input type="text" name="nome" id="f_nome"
                 placeholder=" " required autocomplete="given-name">
          <label class="field-label" for="f_nome">Nome</label>
        </div>
        <div class="field no-icon">
          <input type="text" name="sobrenome" id="f_sobrenome"
                 placeholder=" " required autocomplete="family-name">
          <label class="field-label" for="f_sobrenome">Sobrenome</label>
        </div>
      </div>

      <div class="field">
        <span class="field-icon">&#x1F4F1;</span>
        <input type="tel" name="telefone" id="f_tel"
               placeholder=" " required inputmode="tel" autocomplete="tel">
        <label class="field-label" for="f_tel">WhatsApp / Telefone</label>
      </div>
      <div class="field-hint" id="f_tel_hint">Com o 9: (DD) 9XXXX-XXXX</div>

      <label class="terms-check" id="termsCheck">
        <input type="checkbox" name="termos" id="f_termos" required>
        <span>Li e aceito os <a href="#" id="termsLink">Termos de Uso e a Política de Privacidade</a></span>
      </label>

      <div class="btn-wrap">
        <button class="btn" type="submit" id="btn">
          <span id="btn-txt">Conectar ao WiFi</span>
          <span class="spin" id="spin"></span>
        </button>
      </div>
    </form>

    <div class="footer">
      Seus dados são protegidos e não serão compartilhados com terceiros.
    </div>
  </div>
</div>

<!-- Modal: Termos de Uso / Política de Privacidade -->
<div class="modal-overlay" id="termsOverlay">
  <div class="modal">
    <div class="modal-head">
      <h3>Termos e Privacidade</h3>
      <button type="button" class="modal-close" id="termsClose">&times;</button>
    </div>
    <div class="modal-tabs">
      <div class="m-tab active" data-tab="resumo">Resumo</div>
      <div class="m-tab" data-tab="privacidade">Privacidade</div>
      <div class="m-tab" data-tab="termos">Termos</div>
    </div>
    <div class="modal-body">
      {_TERMOS_CONTEUDO_HTML}
    </div>
    <div class="modal-foot">
      <button type="button" class="btn" id="termsAccept">Li e aceito</button>
    </div>
  </div>
</div>

<script>
(function(){{
  // Slideshow
  var slides=document.querySelectorAll('.slide');
  var dotsEl=document.getElementById('dots');
  var cur=0,timer;
  if(slides.length>1 && dotsEl){{
    slides.forEach(function(_,i){{
      var d=document.createElement('span');
      d.className='dot'+(i===0?' active':'');
      d.addEventListener('click',function(){{goTo(i);restart();}});
      dotsEl.appendChild(d);
    }});
    timer=setInterval(function(){{goTo((cur+1)%slides.length);}},4500);
  }}
  function goTo(n){{
    slides[cur].classList.remove('active');
    var ds=dotsEl?dotsEl.querySelectorAll('.dot'):[];
    if(ds[cur])ds[cur].classList.remove('active');
    cur=n;
    slides[cur].classList.add('active');
    if(ds[cur])ds[cur].classList.add('active');
  }}
  function restart(){{clearInterval(timer);timer=setInterval(function(){{goTo((cur+1)%slides.length);}},4500);}}

  // Splash → revela o card de login ao continuar
  var splash=document.getElementById('splash');
  var card=document.getElementById('card');
  var sb=document.getElementById('splashBtn');
  if(sb&&splash&&card){{
    sb.addEventListener('click',function(){{
      clearInterval(timer);
      splash.classList.add('hide');
      card.style.display='';
      setTimeout(function(){{splash.style.display='none';}},500);
    }});
  }}

  // Máscara telefone
  document.getElementById('f_tel').addEventListener('input',function(){{
    var v=this.value.replace(/[^0-9]/g,'');
    if(v.length>11)v=v.slice(0,11);
    if(v.length>7)v='('+v.slice(0,2)+') '+v.slice(2,7)+'-'+v.slice(7);
    else if(v.length>2)v='('+v.slice(0,2)+') '+v.slice(2);
    this.value=v;
  }});

  // Modal de Termos de Uso / Política de Privacidade
  var overlay=document.getElementById('termsOverlay');
  var link=document.getElementById('termsLink');
  var closeBtn=document.getElementById('termsClose');
  var acceptBtn=document.getElementById('termsAccept');
  var termsChk=document.getElementById('f_termos');
  var termsRow=document.getElementById('termsCheck');
  function openModal(e){{if(e)e.preventDefault();overlay.classList.add('open');}}
  function closeModal(){{overlay.classList.remove('open');}}
  if(link)link.addEventListener('click',openModal);
  if(closeBtn)closeBtn.addEventListener('click',closeModal);
  if(overlay)overlay.addEventListener('click',function(e){{if(e.target===overlay)closeModal();}});
  if(acceptBtn)acceptBtn.addEventListener('click',function(){{
    termsChk.checked=true;
    if(termsRow)termsRow.classList.remove('error');
    closeModal();
  }});
  var mtabs=document.querySelectorAll('.m-tab');
  mtabs.forEach(function(t){{
    t.addEventListener('click',function(){{
      mtabs.forEach(function(x){{x.classList.remove('active');}});
      document.querySelectorAll('.m-tabcontent').forEach(function(c){{c.classList.remove('active');}});
      t.classList.add('active');
      document.getElementById('mc-'+t.dataset.tab).classList.add('active');
    }});
  }});
}})();

function onSubmit(){{
  var nome=document.getElementById('f_nome');
  var sobrenome=document.getElementById('f_sobrenome');
  var tel=document.getElementById('f_tel');
  var termos=document.getElementById('f_termos');
  var ok=true;
  [nome,sobrenome].forEach(function(inp){{
    var field=inp.closest('.field');
    if(!inp.value.trim()){{field.classList.add('error');ok=false;}}
    else field.classList.remove('error');
  }});
  // Exige DDD + 9º dígito + 8 números (11 dígitos) — sem o 9, o número não
  // bate no formato do WhatsApp e a mensagem de disparo não é entregue.
  var telField=tel.closest('.field');
  var telHint=document.getElementById('f_tel_hint');
  var telDigits=tel.value.replace(/[^0-9]/g,'');
  if(telDigits.length!==11){{
    telField.classList.add('error');
    if(telHint)telHint.classList.add('error');
    ok=false;
  }}else{{
    telField.classList.remove('error');
    if(telHint)telHint.classList.remove('error');
  }}
  var termsRow=document.getElementById('termsCheck');
  if(!termos.checked){{termsRow.classList.add('error');ok=false;}}
  else termsRow.classList.remove('error');
  if(!ok)return false;
  var btn=document.getElementById('btn');
  btn.disabled=true;
  document.getElementById('btn-txt').textContent='Conectando...';
  document.getElementById('spin').style.display='block';
  return true;
}}
</script>
</body>
</html>"""


@csrf_exempt
def hotspot_login_html_publico(request, hotspot_uuid):
    """
    Endpoint público: retorna o login.html de redirect para o MikroTik baixar
    via /tool fetch. Não requer autenticação.
    """
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
    except HotspotConfig.DoesNotExist:
        return HttpResponse('', status=404)
    scheme = 'https' if request.is_secure() else 'http'
    portal = f'{scheme}://{request.get_host()}/clientes/hotspot/portal/{h.uuid}/'
    html = _gerar_login_html(h, portal)
    resp = HttpResponse(html, content_type='text/html; charset=utf-8')
    resp['Cache-Control'] = 'no-store'
    return resp


@csrf_exempt
def hotspot_portal(request, hotspot_uuid):
    """Portal de login do hotspot — acessível sem autenticação (walled-garden)."""
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
    except HotspotConfig.DoesNotExist:
        return HttpResponse('Portal não encontrado.', status=404)

    link = request.GET.get('link', '')
    mac  = request.GET.get('mac', '')
    ip   = request.GET.get('ip', '')
    orig = request.GET.get('orig', '')

    html = _portal_page_html(h, link, mac, ip, orig, request)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


@csrf_exempt
@require_http_methods(['POST'])
def hotspot_portal_conectar(request, hotspot_uuid):
    """
    Recebe o formulário do portal, salva o lead e retorna uma página
    que auto-submete as credenciais guest para o MikroTik autenticar o usuário.
    """
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
    except HotspotConfig.DoesNotExist:
        return HttpResponse('Hotspot não encontrado.', status=404)

    link      = request.POST.get('link', '').strip()
    orig      = request.POST.get('orig', '').strip()
    mac       = request.POST.get('mac', '').strip()
    ip_c      = request.POST.get('ip', '').strip()
    nome      = request.POST.get('nome', '').strip()
    sobrenome = request.POST.get('sobrenome', '').strip()
    tel       = request.POST.get('telefone', '').strip()
    termos    = request.POST.get('termos', '').strip().lower() in ('on', 'true', '1')

    nome_completo = f'{nome} {sobrenome}'.strip()

    if nome_completo or tel:
        # Evita salvar lead duplicado: mesmo hotspot + mesmo telefone ou mesmo
        # nome completo (case-insensitive) já cadastrado não gera novo registro.
        dup_filter = Q()
        if tel:
            dup_filter |= Q(telefone=tel)
        if nome_completo:
            dup_filter |= Q(nome__iexact=nome_completo)

        ja_existe = bool(dup_filter) and HotspotLead.objects.filter(hotspot=h).filter(dup_filter).exists()

        if not ja_existe:
            try:
                HotspotLead.objects.create(
                    hotspot=h,
                    nome=nome_completo[:100],
                    telefone=tel[:20],
                    mac=mac[:17],
                    ip_cliente=ip_c[:15],
                    termos_aceitos=termos,
                )
            except Exception as exc:
                logger.debug('hotspot_portal_conectar lead error: %s', exc)

    # Página que auto-submete ao MikroTik para autenticar
    # link = $(link-login) — inclui dst= para redirect pós-auth.
    # Se vazio (captive portal browser não executou JS no login.html e o
    # meta-refresh redirecionou sem parâmetros), cai para a URL padrão do
    # gateway, que é sempre acessível pelo cliente hotspot.
    raw_link = link if link else f'http://{h.gateway}/login'

    # Autentica no MikroTik por NAVEGAÇÃO (GET), não por form-submit.
    # Motivo: no desktop o portal do CRM é carregado por HTTPS (crm.tomich.com.br
    # sobe pra HTTPS). Enviar um FORMULÁRIO de uma página HTTPS para um destino
    # HTTP (o login do MikroTik, http://gateway/login, que não tem TLS) dispara o
    # aviso "As informações que você está prestes a enviar não estão protegidas".
    # Uma navegação top-level HTTPS→HTTP (window.location) é permitida sem aviso —
    # e o hotspot (login-by=http-pap) aceita username/password via query string.
    from urllib.parse import urlsplit as _urlsplit, urlencode as _urlencode
    _sp = _urlsplit(raw_link)
    if _sp.netloc:
        login_base = f'{_sp.scheme or "http"}://{_sp.netloc}{_sp.path or "/login"}'
    else:
        login_base = f'http://{h.gateway}/login'

    # dst vazio faz o MikroTik mostrar a tela de status ("Hi, guest!") em vez de
    # liberar a navegação — orig quase nunca chega (login.html não captura
    # $(link-orig), ver _gerar_login_html). Sem destino real, mandamos o
    # cliente para a própria URL de detecção de captive portal do SO: ela
    # responde com o "sucesso" esperado e o SO fecha o mini-browser sozinho,
    # liberando o usuário direto pra internet sem tela nenhuma.
    _ua = request.META.get('HTTP_USER_AGENT', '').lower()
    if 'android' in _ua:
        _default_dst = 'http://connectivitycheck.gstatic.com/generate_204'
    elif any(k in _ua for k in ('iphone', 'ipad', 'ipod', 'macintosh', 'cfnetwork')):
        _default_dst = 'http://captive.apple.com/hotspot-detect.html'
    elif 'windows' in _ua:
        _default_dst = 'http://www.msftconnecttest.com/redirect'
    else:
        _default_dst = 'http://connectivitycheck.gstatic.com/generate_204'

    login_full = login_base + '?' + _urlencode({
        'username': h.guest_usuario,
        'password': h.guest_senha,
        'dst': orig or _default_dst,
        'popup': 'true',
    })
    login_js   = login_full.replace('\\', '\\\\').replace("'", "\\'")
    login_href = _html.escape(login_full, quote=True)

    html = _sucesso_page_html(h, nome, login_js, login_href)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


def _sucesso_page_html(hotspot, nome, login_js, login_href):
    """Gera a tela de sucesso exibida entre o envio do formulário e o redirect que autentica no MikroTik."""
    cor        = hotspot.cor_primaria or '#6366f1'
    cor_dark   = hotspot.cor_secundaria or cor
    cor_painel = hotspot.cor_painel or '#0f0f19'
    cor_texto  = hotspot.cor_texto or '#ffffff'
    titulo     = _html.escape(hotspot.portal_titulo or 'WiFi Grátis', quote=True)
    logo_url   = hotspot.logo.url if hotspot.logo else None
    logo_html  = (f'<img class="logo" src="{logo_url}" alt="Logo">' if logo_url else '')

    primeiro_nome = _html.escape(nome, quote=True) if nome else ''
    saudacao = f'Tudo certo, <b>{primeiro_nome}</b>!' if primeiro_nome else 'Tudo certo!'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Conectado!</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
html,body{{height:100%;overflow:hidden}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  background:#0a0a0f;
  display:flex;align-items:center;justify-content:center;
  position:relative;color:{cor_texto};
}}
.bg{{
  position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse at 20% 25%,{cor}2e 0%,transparent 60%),
             radial-gradient(ellipse at 80% 75%,{cor_dark}2e 0%,transparent 60%);
  animation:bgpulse 7s ease-in-out infinite alternate;
}}
@keyframes bgpulse{{0%{{background-position:0% 50%}}100%{{background-position:100% 50%}}}}
.card{{
  position:relative;z-index:1;width:min(380px,90vw);
  border-radius:24px;background:{cor_painel}d9;
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border:1px solid rgba(255,255,255,0.08);
  box-shadow:0 32px 80px rgba(0,0,0,0.7),0 0 0 1px rgba(255,255,255,0.04) inset;
  padding:38px 30px 30px;text-align:center;
  animation:cardIn .5s cubic-bezier(.22,1,.36,1) both;
}}
@keyframes cardIn{{from{{opacity:0;transform:translateY(24px) scale(.96)}}to{{opacity:1;transform:none}}}}
.logo{{display:block;margin:0 auto 20px;max-height:64px;max-width:80%;object-fit:contain;filter:drop-shadow(0 4px 14px rgba(0,0,0,.5))}}
.check-wrap{{width:80px;height:80px;margin:0 auto 22px}}
.check-ring{{
  width:100%;height:100%;border-radius:50%;
  background:linear-gradient(135deg,{cor},{cor_dark});
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 10px 30px {cor}55;
  animation:popIn .5s cubic-bezier(.22,1.4,.36,1) .15s both;
}}
@keyframes popIn{{from{{transform:scale(0);opacity:0}}to{{transform:scale(1);opacity:1}}}}
.check-ring svg{{width:38px;height:38px}}
.check-ring path{{
  stroke:#fff;stroke-width:3;fill:none;stroke-linecap:round;stroke-linejoin:round;
  stroke-dasharray:40;stroke-dashoffset:40;
  animation:draw .45s ease .5s forwards;
}}
@keyframes draw{{to{{stroke-dashoffset:0}}}}
h1{{font-size:1.32rem;font-weight:700;margin-bottom:8px;letter-spacing:-.01em}}
.sub{{color:{_alpha(cor_texto,.6)};font-size:.9rem;line-height:1.55;margin-bottom:26px}}
.sub b{{color:{cor_texto}}}
.progress{{height:4px;border-radius:4px;background:rgba(255,255,255,.1);overflow:hidden;margin-bottom:14px}}
.progress-bar{{height:100%;border-radius:4px;background:linear-gradient(135deg,{cor},{cor_dark});
  width:0;animation:fill 2.1s ease forwards}}
@keyframes fill{{to{{width:100%}}}}
.status{{font-size:.76rem;color:{_alpha(cor_texto,.35)}}}
.status a{{color:{cor};text-decoration:none;font-weight:600}}
</style>
</head>
<body>
<div class="bg"></div>
<div class="card">
  {logo_html}
  <div class="check-wrap">
    <div class="check-ring">
      <svg viewBox="0 0 24 24"><path d="M4 12.5l5 5L20 6"></path></svg>
    </div>
  </div>
  <h1>{saudacao}</h1>
  <p class="sub">Sua conexão foi liberada.<br>Aproveite o <b>{titulo}</b>!</p>
  <div class="progress"><div class="progress-bar"></div></div>
  <p class="status" id="statusMsg">Finalizando conexão&hellip;</p>
</div>
<script>
setTimeout(function(){{ window.location.replace('{login_js}'); }}, 2200);
setTimeout(function(){{
  var s=document.getElementById('statusMsg');
  if(s) s.innerHTML='Demorando? <a href="{login_href}">Clique aqui</a>';
}}, 5000);
</script>
<noscript><p style="text-align:center;margin-top:16px;position:relative;z-index:1"><a href="{login_href}" style="color:{cor}">Clique aqui para navegar</a></p></noscript>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Public endpoint — lead capture pixel (called from MikroTik's login.html)
# No login required; returns 1×1 transparent GIF
# ─────────────────────────────────────────────────────────────────────────────

_TRANSPARENT_GIF = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00'
    b'\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)


@csrf_exempt
def hotspot_lead_pixel(request, hotspot_uuid):
    """
    Public pixel endpoint — accessed from within the hotspot network
    (unauthenticated users; added to MikroTik's walled-garden automatically).
    """
    try:
        h = HotspotConfig.objects.get(uuid=hotspot_uuid, ativo=True)
        nome      = request.GET.get('nome', '').strip()
        telefone  = request.GET.get('telefone', '').strip()
        cpf       = request.GET.get('cpf', '').strip()
        mac       = request.GET.get('mac', '').strip()
        ip_cli    = request.GET.get('ip', '').strip()

        if nome or telefone:
            # Mesma checagem de duplicidade do portal: mesmo hotspot + mesmo
            # telefone ou mesmo nome (case-insensitive) já cadastrado não gera
            # um novo lead.
            dup_filter = Q()
            if telefone:
                dup_filter |= Q(telefone=telefone)
            if nome:
                dup_filter |= Q(nome__iexact=nome)

            ja_existe = bool(dup_filter) and HotspotLead.objects.filter(hotspot=h).filter(dup_filter).exists()

            if not ja_existe:
                HotspotLead.objects.create(
                    hotspot=h,
                    nome=nome[:100],
                    telefone=telefone[:20],
                    cpf=cpf[:14],
                    mac=mac[:17],
                    ip_cliente=ip_cli[:15],
                )
    except Exception as exc:
        logger.debug('hotspot_lead_pixel error: %s', exc)

    resp = HttpResponse(_TRANSPARENT_GIF, content_type='image/gif')
    resp['Cache-Control'] = 'no-store'
    resp['Access-Control-Allow-Origin'] = '*'
    return resp
