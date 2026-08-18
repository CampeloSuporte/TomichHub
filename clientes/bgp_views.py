"""
clientes/bgp_views.py
Views da automação BGP: visualização do snapshot (sessões + anúncios
simulados) e execução de ações (ativar/desativar sessão, prepend, parar de
anunciar) num equipamento real. Restrito a staff/superuser — é engenharia
de rede em produção, não uma ferramenta de portal de cliente.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .bgp_actions import (
    AcaoBgpNaoSuportada,
    aplicar_efeito_localmente,
    buscar_prefix_lists_ao_vivo,
    comandos_aplicar_community,
    comandos_criar_sessao,
    comandos_novo_anuncio,
    comandos_parar_anuncio,
    comandos_prepend,
    comandos_toggle_sessao,
    executar_acao_bgp,
    validar_anuncios_ao_vivo,
    validar_trial_suportado,
)
from .bgp_community_auto import (
    comandos_definir_anuncio,
    comandos_novo_prefixo,
    comandos_provisionar_circuito,
    montar_mapa,
)
from .bgp_matcher import listar_prefix_lists
from .models import Acesso, AcaoBgp, BgpCommunity, BgpSnapshot
from usuario import perms as _perms

logger = logging.getLogger(__name__)


def _checar_staff(request):
    if not (_perms.is_backoffice(request.user) and _perms.ferramenta_habilitada(request.user, 'bgp')):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    return None


def _checar_acesso(request, acesso):
    """Checagem de posse do Acesso (mesma instância/consultor dono do cliente).
    Devolve um JsonResponse de erro (status 403) se não puder acessar, ou
    None se puder — mesmo estilo de `_checar_staff`."""
    if not _perms.pode_acessar_cliente(request.user, acesso.cliente):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    return None


@login_required(login_url='login')
def bgp_page(request, acesso_id):
    """GET /clientes/bgp/<acesso_id>/ — página da automação BGP do host."""
    if not (_perms.is_backoffice(request.user) and _perms.ferramenta_habilitada(request.user, 'bgp')):
        return render(request, 'terminal_link_invalido.html',
                       {'motivo': 'Sem permissão para acessar esta tela.'}, status=403)
    acesso = get_object_or_404(Acesso, id=acesso_id)
    if not _perms.pode_acessar_cliente(request.user, acesso.cliente):
        return render(request, 'terminal_link_invalido.html',
                       {'motivo': 'Sem permissão para acessar esta tela.'}, status=403)
    return render(request, 'bgp_automacao.html', {
        'acesso': acesso,
        'acesso_id': acesso.id,
    })


@login_required(login_url='login')
@require_http_methods(["GET"])
def bgp_dados(request, acesso_id):
    """GET /clientes/bgp/<acesso_id>/dados/ — snapshot atual em JSON."""
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    try:
        snap = BgpSnapshot.objects.select_related('acesso').get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host ainda.'}, status=404)
    return JsonResponse({
        'vendor': snap.vendor,
        'gerado_em': timezone.localtime(snap.gerado_em).strftime('%d/%m/%Y %H:%M'),
        'erro': snap.erro,
        'dados': snap.dados,
    })


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_atualizar_snapshot(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/atualizar/ — refaz a extração+simulação
    desse host agora, sem esperar a rotina noturna (02:45). Só lê o backup
    mais recente já salvo em disco e roda regex — não conecta em nada, não
    precisa de Celery, roda síncrono na própria request.
    """
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro

    from .tasks import _atualizar_snapshot_bgp_de_acesso
    resultado, detalhe = _atualizar_snapshot_bgp_de_acesso(acesso)

    if resultado == 'ok':
        return JsonResponse({'status': 'ok'})
    if resultado == 'sem_novidade':
        # Não é erro — só não havia backup novo pra extrair. O painel
        # continua com o estado atual (inclui qualquer atualização
        # otimista de uma ação real recente).
        return JsonResponse({'status': 'sem_novidade', 'mensagem': detalhe})
    return JsonResponse({'status': resultado, 'error': detalhe or resultado}, status=422)


def _montar_comandos(tipo, vendor, dados, alvo, params):
    comandos = _gerar_comandos_por_tipo(tipo, vendor, dados, alvo, params)
    if vendor in ('cisco', 'datacom'):
        # Cisco/Datacom aplica direto no running-config (sem candidate-
        # config/commit como Huawei/Juniper) — sem salvar, a mudança se
        # perde no próximo reload do equipamento. `end` garante que
        # `write` rode em modo EXEC privilegiado mesmo quando os comandos
        # gerados terminam aninhados (ex: dentro de `address-family`).
        # Aparece no preview igual a qualquer outro comando — o operador
        # pode remover as duas linhas no textarea antes de confirmar se
        # não quiser salvar (mesma flexibilidade de "editar antes de
        # confirmar" já usada pelo resto da automação).
        comandos = comandos + ['end', 'write']
    return comandos


def _gerar_comandos_por_tipo(tipo, vendor, dados, alvo, params):
    if tipo == 'ativar_sessao':
        return comandos_toggle_sessao(vendor, dados, alvo, ativar=True)
    if tipo == 'desativar_sessao':
        return comandos_toggle_sessao(vendor, dados, alvo, ativar=False)
    if tipo == 'prepend':
        nome_sessao = params.get('sessao', '')
        delta = int(params.get('delta', 1))
        return comandos_prepend(vendor, dados, nome_sessao, alvo, delta=delta)
    if tipo == 'parar_anuncio':
        nome_sessao = params.get('sessao', '')
        return comandos_parar_anuncio(vendor, dados, nome_sessao, alvo)
    if tipo == 'community':
        nome_sessao = params.get('sessao', '')
        valor = params.get('valor', '')
        label = params.get('label', '')
        return comandos_aplicar_community(vendor, dados, nome_sessao, alvo, valor, label=label)
    if tipo == 'novo_anuncio':
        nome_sessao = params.get('sessao', '')
        lista = params.get('lista') or None
        prefixo_novo = params.get('prefixo') or None
        return comandos_novo_anuncio(vendor, dados, nome_sessao, lista_escolhida=lista, prefixo_novo=prefixo_novo)
    if tipo == 'criar_sessao':
        return comandos_criar_sessao(vendor, dados, params)
    if tipo in ('anuncio_community', 'novo_prefixo_community', 'provisionar_circuito'):
        # Automação de anúncios por community (Huawei) — o mapa de circuitos
        # é redescoberto a cada chamada a partir do MESMO snapshot, então
        # preview e execução enxergam exatamente o mesmo estado.
        mapa = montar_mapa(dados, vendor)
        # `destino` é o identificador do circuito (`c-01`, `ix-05`, `cdn-02`)
        # ou do grupo global (`glob-all-upstream`). `circuito` é o nome antigo
        # do mesmo campo — aceito pra não quebrar nada que já esteja no ar.
        destino = str(params.get('destino') or params.get('circuito') or '')
        if tipo == 'anuncio_community':
            return comandos_definir_anuncio(
                dados, mapa, alvo, destino,
                params.get('acao', ''), route_policy=params.get('route_policy', ''),
            )
        if tipo == 'novo_prefixo_community':
            destinos = {str(k): v for k, v in (params.get('destinos') or {}).items() if v}
            return comandos_novo_prefixo(
                dados, mapa, alvo, destinos, nome_policy=params.get('route_policy', ''),
            )
        return comandos_provisionar_circuito(
            dados, mapa, destino, params.get('opcoes') or {},
        )
    raise AcaoBgpNaoSuportada(f'Tipo de ação "{tipo}" desconhecido.')


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_escanear_prefixo(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/escanear-prefixo/ — body {sessao, ao_vivo}.
    Lista TODAS as prefix-lists nomeadas conhecidas no equipamento (não só
    as já usadas por essa sessão), marcando quais a sessão informada já
    anuncia — pra UI oferecer escolher uma pra anexar via um node/termo
    novo (`comandos_novo_anuncio`) ou pra escolher/criar prefix-list no
    formulário de "Configurar nova sessão" (`comandos_criar_sessao`).

    `sessao` pode ser o nome de uma sessão que AINDA NÃO EXISTE no
    snapshot (ex: "__nova_sessao__") — nesse caso não marca nenhuma
    candidata como `ja_anunciando` em vez de devolver 404.

    `ao_vivo=true` (Cisco/Datacom apenas) troca a fonte de `prefix_lists`/
    `policies`: em vez do snapshot (backup em disco, pode estar
    desatualizado), conecta AGORA no equipamento (`buscar_prefix_lists_
    ao_vivo`) e usa o resultado fresco só pra esta consulta — não grava
    nada no snapshot.
    """
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    sessao_nome = (body.get('sessao') or '').strip()
    ao_vivo = bool(body.get('ao_vivo', False))
    if not sessao_nome:
        return JsonResponse({'error': 'Informe a sessão.'}, status=400)

    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host.'}, status=404)

    if ao_vivo:
        if snap.vendor not in ('cisco', 'datacom'):
            return JsonResponse({'error': f'Leitura ao vivo ainda não suportada para "{snap.vendor}".'}, status=422)
        try:
            fonte = buscar_prefix_lists_ao_vivo(acesso)
        except Exception as e:
            return JsonResponse({'error': f'Falha ao conectar no equipamento: {e}'}, status=422)
    else:
        fonte = {'prefix_lists': snap.dados.get('prefix_lists', {}), 'policies': snap.dados.get('policies', {})}

    # `sessao_nome` pode ser de uma sessão que AINDA NÃO EXISTE no
    # snapshot (ex: "__nova_sessao__", usado pelo modal de "Configurar
    # nova sessão") — nesse caso não há `policy_out` pra comparar, então
    # nenhuma candidata é marcada como `ja_anunciando` (em vez de 404).
    sessao = next((s for s in snap.dados.get('sessoes', []) if s.get('nome') == sessao_nome), None)
    if sessao and not sessao.get('policy_out'):
        return JsonResponse({'error': 'Esta sessão não tem export policy identificada.'}, status=422)
    policy_out = sessao.get('policy_out') if sessao else ''

    resultado = listar_prefix_lists(fonte['prefix_lists'], fonte['policies'], policy_out)
    resultado['vendor'] = snap.vendor
    return JsonResponse(resultado)


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_validar_anuncios(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/validar-anuncios/ — body {sessao}.
    Conecta AO VIVO no equipamento (nunca escreve nada, só comandos de
    leitura) e devolve o que essa sessão está anunciando/recebendo de
    verdade agora — complementar à simulação baseada em config já exibida
    no painel (dados['anuncios'], de bgp_matcher.simular_anuncios), que
    mostra o que a policy deveria deixar passar, não o estado real do RIB.
    """
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    sessao_nome = (body.get('sessao') or '').strip()
    if not sessao_nome:
        return JsonResponse({'error': 'Informe a sessão.'}, status=400)

    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host.'}, status=404)

    sessao = next((s for s in snap.dados.get('sessoes', []) if s.get('nome') == sessao_nome), None)
    if not sessao:
        return JsonResponse({'error': f'Sessão "{sessao_nome}" não encontrada no snapshot.'}, status=404)

    resultado = validar_anuncios_ao_vivo(acesso, snap.vendor, snap.dados, sessao)
    if resultado['status'] == 'erro':
        return JsonResponse({'error': resultado['mensagem']}, status=422)
    return JsonResponse(resultado)


@login_required(login_url='login')
@require_http_methods(["GET"])
def bgp_community_mapa(request, acesso_id):
    """
    GET /clientes/bgp/<acesso_id>/community-mapa/ — descobre, a partir do
    snapshot, os circuitos que a caixa já tem configurados no padrão de
    community (`c-NN`/`ix-NN`/`cdn-NN` + ação → `65100:<grupo><sufixo>`), os
    grupos globais "anunciar para todos" e o alcance real de cada um, a matriz
    prefixo × destino → ação em vigor (com o efeito real por circuito) e as
    inconsistências encontradas.

    Leitura pura sobre `BgpSnapshot.dados` — não conecta em nada, não grava
    nada. Devolve 422 pra fabricante sem suporte (a convenção é Huawei/VRP).
    """
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host.'}, status=404)

    try:
        mapa = montar_mapa(snap.dados, snap.vendor)
    except AcaoBgpNaoSuportada as e:
        return JsonResponse({'error': str(e), 'suportado': False}, status=422)

    if not mapa['circuitos']:
        return JsonResponse({
            'suportado': True, 'circuitos': {}, 'globais': {}, 'anuncios': [],
            'acoes': mapa['acoes'], 'tipos': mapa['tipos'], 'avisos': [], 'as_local': '',
            'mensagem': 'Este equipamento não tem community-filters no padrão '
                        '`c-NN-<ação>` / `ix-NN-<ação>` / `cdn-NN-<ação>` — nada a mapear.',
        })

    sessoes = snap.dados.get('sessoes') or []
    mapa['suportado'] = True
    mapa['as_local'] = (sessoes[0].get('as_local', '') if sessoes else '') or snap.dados.get('as_local', '')
    return JsonResponse(mapa)


@login_required(login_url='login')
@require_http_methods(["GET"])
def bgp_communities_listar(request, acesso_id):
    """GET /clientes/bgp/<acesso_id>/communities/[?sessao=NOME] — communities
    cadastradas pro host. Sem `?sessao=`, devolve TODAS agrupadas por sessão
    de uma vez (`{"communities": {"<sessao_nome>": [...], ...}}`) — a UI
    carrega isso uma vez só no load da página em vez de um fetch por sessão."""
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    sessao_nome = request.GET.get('sessao')
    qs = BgpCommunity.objects.filter(acesso_id=acesso_id)
    if sessao_nome:
        qs = qs.filter(sessao_nome=sessao_nome)
    agrupado = {}
    for c in qs.order_by('sessao_nome', 'label'):
        agrupado.setdefault(c.sessao_nome, []).append({'id': c.id, 'label': c.label, 'valor': c.valor})
    return JsonResponse({'communities': agrupado})


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_communities_criar(request, acesso_id):
    """POST /clientes/bgp/<acesso_id>/communities/ — body {sessao, label, valor}."""
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    sessao_nome = (body.get('sessao') or '').strip()
    label       = (body.get('label') or '').strip()
    valor       = (body.get('valor') or '').strip()
    if not sessao_nome or not label or not valor:
        return JsonResponse({'error': 'Sessão, rótulo e valor são obrigatórios.'}, status=400)
    if len(label) > 100 or len(valor) > 255:
        return JsonResponse({'error': 'Rótulo ou valor longo demais.'}, status=400)

    try:
        community = BgpCommunity.objects.create(
            acesso=acesso, sessao_nome=sessao_nome, label=label, valor=valor,
            criado_por=request.user,
        )
    except Exception as e:
        # provável violação do unique_together (label repetido nessa sessão)
        return JsonResponse({'error': f'Não foi possível salvar: {e}'}, status=400)
    return JsonResponse({'id': community.id, 'label': community.label, 'valor': community.valor})


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_communities_deletar(request, acesso_id, community_id):
    """POST /clientes/bgp/<acesso_id>/communities/<community_id>/deletar/"""
    erro = _checar_staff(request)
    if erro:
        return erro
    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    community = get_object_or_404(BgpCommunity, id=community_id, acesso_id=acesso_id)
    community.delete()
    return JsonResponse({'status': 'ok'})


@login_required(login_url='login')
@require_http_methods(["POST"])
def bgp_executar_acao(request, acesso_id):
    """
    POST /clientes/bgp/<acesso_id>/acao/
    body: {"tipo", "alvo", "params": {...}, "preview": bool, "comandos": [...],
           "trial": bool, "trial_segundos": int}

    `preview=true` só monta e devolve os comandos gerados automaticamente,
    sem tocar no equipamento — é o que a UI usa pra preencher o textarea
    editável do modal de confirmação. `preview=false` executa de verdade e
    grava AcaoBgp; se o body trouxer `comandos` (o texto do modal, possivelmente
    editado à mão — ex: trocar o ASN usado no prepend), usa exatamente esses
    comandos em vez de gerar de novo — dá pra revisar/ajustar antes de confirmar.

    `trial=true` (só Huawei/Juniper — `validar_trial_suportado`) troca o
    commit final por um commit TEMPORÁRIO (`commit trial N`/`commit
    confirmed N`, `trial_segundos` controla N) — a mudança reverte sozinha
    se ninguém confirmar depois. Nesse caso o snapshot local NÃO é
    atualizado otimisticamente (ver comentário abaixo).
    """
    erro = _checar_staff(request)
    if erro:
        return erro

    acesso = get_object_or_404(Acesso, id=acesso_id)
    erro = _checar_acesso(request, acesso)
    if erro:
        return erro
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    tipo = body.get('tipo', '')
    alvo = body.get('alvo', '')
    params = body.get('params') or {}
    preview = bool(body.get('preview', True))
    comandos_editados = body.get('comandos')
    trial = bool(body.get('trial', False))
    try:
        trial_segundos = int(body.get('trial_segundos') or 60)
    except (TypeError, ValueError):
        trial_segundos = 60

    try:
        snap = BgpSnapshot.objects.get(acesso_id=acesso_id)
    except BgpSnapshot.DoesNotExist:
        return JsonResponse({'error': 'Sem snapshot BGP para este host — aguarde a próxima atualização noturna.'}, status=404)

    if trial:
        try:
            validar_trial_suportado(snap.vendor)
        except AcaoBgpNaoSuportada as e:
            return JsonResponse({'error': str(e)}, status=422)

    if preview:
        # Preview sempre gera do zero — é o texto inicial que preenche o
        # textarea editável do modal, nunca deve refletir uma edição anterior.
        try:
            comandos = _montar_comandos(tipo, snap.vendor, snap.dados, alvo, params)
        except AcaoBgpNaoSuportada as e:
            return JsonResponse({'error': str(e)}, status=422)
        return JsonResponse({'comandos': comandos})

    if isinstance(comandos_editados, list) and comandos_editados and all(isinstance(c, str) for c in comandos_editados):
        # Limite de sanidade do texto editado no modal. Gerar o bloco padrão
        # de um circuito (10 community-filters + ~10 nodes por família) passa
        # facilmente de 30 linhas, então esse tipo tem um teto próprio — as
        # demais ações continuam curtas por natureza.
        limite_linhas = 300 if tipo == 'provisionar_circuito' else 30
        if len(comandos_editados) > limite_linhas or any(len(c) > 500 for c in comandos_editados):
            return JsonResponse({'error': 'Comando editado longo demais — revise antes de enviar.'}, status=400)
        comandos = [c.strip() for c in comandos_editados if c.strip()]
    else:
        try:
            comandos = _montar_comandos(tipo, snap.vendor, snap.dados, alvo, params)
        except AcaoBgpNaoSuportada as e:
            return JsonResponse({'error': str(e)}, status=422)

    output, status = executar_acao_bgp(acesso, snap.vendor, comandos, trial=trial, trial_segundos=trial_segundos)
    if status == 'sucesso' and not trial:
        # Atualiza o snapshot local pro painel já refletir o efeito da
        # ação (ex: prefixo some da lista de anunciados depois de "Parar
        # de anunciar") sem esperar o próximo backup/rotina noturna — ver
        # docstring de aplicar_efeito_localmente. Nunca deve derrubar a
        # resposta de sucesso já obtida do equipamento. Pulado em modo
        # trial: a mudança reverte sozinha se não for confirmada, então
        # marcar o painel como se fosse permanente seria enganoso — nem
        # este código nem o resto da automação sabem quando o rollback
        # automático do equipamento efetivamente acontece.
        try:
            aplicar_efeito_localmente(snap.vendor, snap.dados, tipo, params.get('sessao', ''), alvo, params)
            # Marca que este snapshot tem uma mutação otimista não
            # confirmada por um backup novo — protege esse patch de ser
            # sobrescrito se "Atualizar agora"/rotina noturna rodar antes
            # do equipamento ser rebackupeado (mesmo backup de sempre; ver
            # tasks.py::_atualizar_snapshot_bgp_de_acesso).
            snap.patch_local_pendente = True
            snap.save(update_fields=['dados', 'patch_local_pendente'])
        except Exception as e:
            logger.warning(f'aplicar_efeito_localmente falhou pra acesso {acesso_id} (não crítico): {e}')
    AcaoBgp.objects.create(
        acesso=acesso, usuario=request.user, tipo=tipo, alvo=alvo,
        comandos='\n'.join(comandos), output=output, status=status,
    )
    return JsonResponse({'status': status, 'output': output, 'comandos': comandos})
