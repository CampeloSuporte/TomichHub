"""
financeiro/whatsapp.py
Envio de cobranças via WhatsApp usando a Evolution API.
"""
import re
import logging
import requests

logger = logging.getLogger(__name__)

_TIPO_LABEL = {
    'CONSULTORIA':      'Consultoria',
    'ALUGUEL_IPV4':     'Aluguel de Bloco IP',
    'VENDA_EQUIPAMENTO':'Venda de Equipamento',
    'MISTA':            'Serviços',
}


def _normalizar_telefone(telefone: str) -> str | None:
    """
    Converte qualquer formato de telefone brasileiro para o JID do WhatsApp.
    Ex: "(11) 99999-9999" → "5511999999999@s.whatsapp.net"
    Retorna None se não conseguir extrair um número válido.
    """
    if not telefone:
        return None
    digitos = re.sub(r'\D', '', telefone)
    if digitos.startswith('55') and len(digitos) > 11:
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        return None
    local = digitos[2:]
    # Plano de numeração ANATEL: fixo tem 8 dígitos e começa com 2-5; a faixa
    # 6-9 é exclusiva de celular (que tem 9 dígitos, sempre com o 9 na
    # frente). Um local de 8 dígitos começando com 6-9 tem a forma de fixo
    # mas o prefixo de celular — quase sempre é um celular com o 9 faltando
    # no cadastro (typo/truncamento), não um fixo válido. Rejeitar aqui
    # evita mandar um número que não existe pra Evolution API e receber um
    # 400 sem explicação nenhuma.
    if len(local) == 8 and local[0] in '6789':
        return None
    return f'55{digitos}@s.whatsapp.net'


def _fmt_valor(valor):
    return f'{float(valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _cfg():
    from .models import ConfiguracaoFinanceira
    try:
        return ConfiguracaoFinanceira.objects.get(pk=1)
    except ConfiguracaoFinanceira.DoesNotExist:
        return None


def evolution_client():
    cfg = _cfg()
    if not cfg or not cfg.wa_evolution_url or not cfg.wa_api_key or not cfg.wa_instance:
        return None, None, None
    session = requests.Session()
    session.headers.update({'Content-Type': 'application/json', 'apikey': cfg.wa_api_key})
    return session, cfg.wa_evolution_url.rstrip('/'), cfg.wa_instance


def testar_conexao() -> tuple[bool, str]:
    session, base_url, instance = evolution_client()
    if not session:
        return False, 'Evolution API não configurada (URL, API Key ou Instância ausentes).'
    try:
        r = session.get(f'{base_url}/instance/connectionState/{instance}', timeout=10)
        if r.status_code == 404:
            return False, f"Instância '{instance}' não encontrada (404)."
        r.raise_for_status()
        state = r.json().get('instance', {}).get('state') or r.json().get('state', 'unknown')
        if state == 'open':
            return True, f"Conectado — instância '{instance}' online."
        return False, f"Instância encontrada mas estado: {state}."
    except Exception as e:
        return False, str(e)


def _numero_nao_existe(resp) -> bool:
    """Detecta o 400 específico da Evolution API que indica número inexistente
    no WhatsApp: {"response": {"message": [{"exists": false, ...}]}}."""
    if resp is None or resp.status_code != 400:
        return False
    try:
        itens = resp.json().get('response', {}).get('message', [])
        return any(item.get('exists') is False for item in itens)
    except Exception:
        return False


def _alternar_nono_digito(jid: str) -> str | None:
    """Alterna a presença do 9º dígito num JID celular BR (55+DDD+[9]+8 dígitos).
    Números BR podem existir no WhatsApp com ou sem esse dígito (contas
    antigas/portadas) e quem cadastrou o telefone do cliente não tem como
    saber qual variante está registrada. Retorna None se o JID não tiver
    essa forma (nada a alternar)."""
    if '@' not in jid:
        return None
    numero, dominio = jid.split('@', 1)
    if not numero.startswith('55') or not numero.isdigit():
        return None
    ddd = numero[2:4]
    local = numero[4:]
    if len(local) == 9 and local[0] == '9':
        return f'55{ddd}{local[1:]}@{dominio}'
    if len(local) == 8:
        return f'55{ddd}9{local}@{dominio}'
    return None


def enviar_mensagem(jid: str, texto: str) -> tuple[bool, str]:
    session, base_url, instance = evolution_client()
    if not session:
        return False, 'Evolution API não configurada.'

    def _post(numero):
        return session.post(
            f'{base_url}/message/sendText/{instance}',
            json={'number': numero, 'text': texto},
            timeout=20,
        )

    try:
        r = _post(jid)
        r.raise_for_status()
        return True, r.json().get('key', {}).get('id', 'enviado')
    except requests.HTTPError as e:
        jid_alt = _alternar_nono_digito(jid) if _numero_nao_existe(r) else None
        if jid_alt:
            logger.warning('WhatsApp financeiro: %s não existe — tentando variante do 9º dígito: %s', jid, jid_alt)
            try:
                r2 = _post(jid_alt)
                r2.raise_for_status()
                return True, r2.json().get('key', {}).get('id', 'enviado')
            except Exception as e2:
                logger.error('WhatsApp financeiro: variante %s também falhou: %s', jid_alt, e2)
        # A Evolution API normalmente devolve um corpo explicando a rejeição
        # (ex: número não existe no WhatsApp) — sem capturar r.text, só
        # sobra "400 Bad Request" genérico e a causa real fica invisível.
        detalhe = str(e)
        try:
            corpo = r.text.strip()
            if corpo:
                detalhe = f'{detalhe} — resposta da API: {corpo[:300]}'
        except Exception:
            pass
        logger.error('WhatsApp financeiro: erro ao enviar para %s: %s', jid, detalhe)
        return False, detalhe
    except Exception as e:
        logger.error('WhatsApp financeiro: erro ao enviar para %s: %s', jid, e)
        return False, str(e)


def _coletar_itens(fatura):
    """Retorna lista de dicts {descricao, tipo_label, valor} dos itens da fatura."""
    itens = []
    for c in fatura.consultorias.all():
        itens.append({
            'descricao':   c.descricao,
            'tipo_label':  'Consultoria',
            'valor':       float(c.valor_unitario),
        })
    for a in fatura.alugueis_ipv4.all():
        bloco = a.bloco_descricao
        if a.bloco_v6:
            bloco += f' / {a.bloco_v6}'
        itens.append({
            'descricao':  f'Aluguel de Bloco IP — {bloco}',
            'tipo_label': 'Aluguel IPv4',
            'valor':      float(a.valor_mensal),
        })
    if hasattr(fatura, 'vendas_equipamentos'):
        for v in fatura.vendas_equipamentos.all():
            inicio_fmt = v.data_inicio.strftime('%d/%m/%Y') if v.data_inicio else ''
            itens.append({
                'descricao':  f'{v.descricao} ({v.quantidade_parcelas}x — início {inicio_fmt})',
                'tipo_label': 'Venda de Equipamento',
                'valor':      float(v.get_valor_parcela()) if hasattr(v, 'get_valor_parcela') else float(v.valor_total),
            })
    return itens


def _msg_aviso_padrao():
    return (
        "Olá *{nome}*! 👋\n\n"
        "Informamos que sua fatura *#{numero}* vence em *{vencimento}* ({dias} dias).\n\n"
        "{itens}"
        "💰 *Total: R$ {valor}*\n\n"
        "{pix}"
        "Por favor, entre em contato para efetuar o pagamento.\n\n"
        "Atenciosamente,\n*{empresa}*\n\n"
        "_Mensagem gerada automaticamente pelo sistema._"
    )


def _msg_vencido_padrao():
    return (
        "Olá *{nome}*! ⚠️\n\n"
        "Sua fatura *#{numero}* está vencida desde *{vencimento}* ({atraso} dias de atraso).\n\n"
        "{itens}"
        "💰 *Total: R$ {valor}*\n\n"
        "{pix}"
        "Por favor, regularize sua situação.\n\n"
        "Atenciosamente,\n*{empresa}*\n\n"
        "_Mensagem gerada automaticamente pelo sistema._"
    )


def montar_texto_cobranca(fatura, cfg=None) -> str:
    """
    Monta o texto completo da mensagem de cobrança com os itens detalhados.
    Pode ser usado para preview (sem envio) ou antes do envio real.
    """
    from datetime import date
    if cfg is None:
        cfg = _cfg()

    hoje      = date.today()
    venc      = fatura.data_vencimento
    atraso    = (hoje - venc).days
    dias_ate  = max((venc - hoje).days, 0)
    venc_fmt  = venc.strftime('%d/%m/%Y')
    empresa   = (cfg.empresa_nome if cfg else None) or 'Nossa empresa'
    nome      = fatura.cliente.nome_empresa
    numero    = fatura.numero_fatura or str(fatura.pk)
    valor_fmt = _fmt_valor(fatura.valor_total)

    # Monta bloco de itens
    itens = _coletar_itens(fatura)
    if itens:
        linhas = ['📋 *Detalhamento:*']
        for it in itens:
            linhas.append(f'  • {it["descricao"]} — R$ {_fmt_valor(it["valor"])}')
        itens_bloco = '\n'.join(linhas) + '\n\n'
    else:
        itens_bloco = ''

    # Monta bloco de PIX
    pix_chave = (cfg.wa_pix_chave if cfg else None) or ''
    pix_tipo  = (cfg.wa_pix_tipo  if cfg else None) or ''
    if pix_chave:
        label = f' ({pix_tipo})' if pix_tipo else ''
        pix_bloco = f'💳 *PIX{label}:* `{pix_chave}`\n\n'
    else:
        pix_bloco = ''

    if atraso > 0:
        template = (cfg.wa_msg_vencido if cfg else None) or _msg_vencido_padrao()
        return template.format(
            nome=nome, numero=numero, valor=valor_fmt,
            vencimento=venc_fmt, atraso=atraso, empresa=empresa,
            itens=itens_bloco, pix=pix_bloco,
        )
    else:
        template = (cfg.wa_msg_aviso if cfg else None) or _msg_aviso_padrao()
        return template.format(
            nome=nome, numero=numero, valor=valor_fmt,
            vencimento=venc_fmt, dias=dias_ate, empresa=empresa,
            itens=itens_bloco, pix=pix_bloco,
        )


def enviar_cobranca_fatura(fatura) -> tuple[bool, str]:
    """Envia alerta de cobrança para o cliente da fatura."""
    cfg = _cfg()
    if not cfg or not cfg.wa_ativo:
        return False, 'WhatsApp não habilitado.'

    jid = _normalizar_telefone(getattr(fatura.cliente, 'telefone', None))
    if not jid:
        return False, f'Telefone inválido ou ausente: {fatura.cliente.telefone!r}'

    texto = montar_texto_cobranca(fatura, cfg)
    return enviar_mensagem(jid, texto)
