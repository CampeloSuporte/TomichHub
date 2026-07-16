"""
Agent NOC — WebSocket Consumer
================================
Conecta o terminal web ao AgentNOCEngine via WebSocket.

Protocolo:
  Cliente → Servidor:
    {"type": "message",  "content": "..."}          ← nova mensagem ao agent
    {"type": "approve",  "log_id": 42}               ← aprova execução de comando
    {"type": "reject",   "log_id": 42, "motivo":"..."} ← rejeita execução
    {"type": "context",  "terminal_output": "..."}   ← cola output do terminal
    {"type": "close"}

  Servidor → Cliente:
    {"type": "agent_message",  "content": "...", "sessao_id": 1}
    {"type": "tool_call",      "tool": "...", "command": "...", "requires_approval": true, "log_id": 42}
    {"type": "tool_result",    "tool": "...", "output": "...", "approved": true}
    {"type": "thinking",       "content": "..."}
    {"type": "tokens",         "used": N, "input": N, "output": N}
    {"type": "error",          "message": "..."}
    {"type": "sessao_info",    "sessao_id": N, "cliente": "...", "canal": "terminal"}
"""
from __future__ import annotations

import asyncio
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser

logger = logging.getLogger(__name__)


class AgentNOCConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer para o Agent NOC no terminal web.

    URL: /ws/agent/<acesso_id>/
         acesso_id é o host que o operador tem aberto no terminal.
         A sessão é criada/recuperada automaticamente.
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.acesso_id = self.scope['url_route']['kwargs'].get('acesso_id')
        self.sessao_id = None
        self.engine = None
        self._pending_aprovacoes: dict[str, asyncio.Future] = {}  # tool_use_id → Future
        self._pending_terminal_output: asyncio.Future | None = None  # get_terminal_output request

        await self.accept()
        await self._inicializar_sessao(user)

    async def _inicializar_sessao(self, user):
        """Cria ou recupera a sessão ativa do operador para este host."""
        from clientes.models import AgentSessao, Acesso, AgentConfig
        from django.utils import timezone
        from datetime import timedelta

        try:
            config = await sync_to_async(AgentConfig.get)()
            if not config.ativo:
                await self._send_json({"type": "error",
                                       "message": "Agent NOC desativado. Configure em Configurações → Agent NOC."})
                await self.close()
                return

            # Tentar recuperar sessão ativa (nos últimos 30 min) para este usuário/acesso
            acesso = None
            cliente = None
            if self.acesso_id:
                try:
                    acesso = await sync_to_async(
                        Acesso.objects.select_related('cliente').get
                    )(id=self.acesso_id)
                    cliente = acesso.cliente
                except Acesso.DoesNotExist:
                    pass

            if not cliente:
                # Tentar pelo query param ?cliente=ID passado pelo terminal
                from urllib.parse import parse_qs
                qs_raw = self.scope.get('query_string', b'')
                qs = parse_qs(qs_raw.decode('utf-8', errors='replace'))
                cliente_id_qs = qs.get('cliente', [None])[0]
                if cliente_id_qs:
                    try:
                        from clientes.models import Cliente
                        cliente = await sync_to_async(Cliente.objects.get)(id=cliente_id_qs)
                    except Exception:
                        pass

            if not cliente:
                # Tentar pegar pelo perfil do usuário
                try:
                    from clientes.models import Cliente
                    cliente = await sync_to_async(Cliente.objects.get_by_usuario_vinculado)(user)
                except Exception:
                    pass

            if not cliente:
                await self._send_json({
                    "type": "error",
                    "message": "Não foi possível identificar o cliente para esta sessão."
                })
                await self.close()
                return

            # Sessão recente?
            sessao_existente = await sync_to_async(
                AgentSessao.objects.filter(
                    canal='terminal',
                    usuario=user,
                    cliente=cliente,
                    status='ativa',
                    ultima_atividade__gte=timezone.now() - timedelta(minutes=30),
                ).order_by('-ultima_atividade').first
            )()

            if sessao_existente:
                sessao = sessao_existente
                # Atualizar host ativo se necessário
                if acesso and sessao.acesso_ativo_id != acesso.id:
                    await sync_to_async(
                        AgentSessao.objects.filter(id=sessao.id).update
                    )(acesso_ativo=acesso)
            else:
                sessao = await sync_to_async(AgentSessao.objects.create)(
                    canal='terminal',
                    canal_id=f'terminal-{user.id}-{self.acesso_id or ""}',
                    cliente=cliente,
                    acesso_ativo=acesso,
                    usuario=user,
                    status='ativa',
                )

            self.sessao_id = sessao.id

            # Criar engine com callbacks para este consumer
            from home.agent_engine import AgentNOCEngine
            self.engine = AgentNOCEngine(
                sessao_id=self.sessao_id,
                canal='terminal',
                aprovacao_cb=self._esperar_aprovacao,
                notify_cb=self._send_json,
                terminal_output_cb=self._solicitar_terminal_output,
            )

            # Pré-carregar histórico da sessão
            from clientes.models import AgentLog
            logs = await sync_to_async(list)(
                AgentLog.objects.filter(
                    sessao_id=self.sessao_id,
                    tipo__in=['user_msg', 'agent_msg'],
                ).order_by('criado_em')[:40]
            )
            for log in logs:
                role = 'user' if log.tipo == 'user_msg' else 'assistant'
                self.engine._historico.append({"role": role, "content": log.conteudo})

            await self._send_json({
                "type": "sessao_info",
                "sessao_id": self.sessao_id,
                "cliente": cliente.nome_empresa,
                "canal": "terminal",
                "host": str(acesso) if acesso else None,
                "historico_msgs": len(logs),
                "retomada": bool(sessao_existente),
            })

        except Exception as exc:
            logger.exception(f"Erro ao inicializar sessão Agent NOC: {exc}")
            await self._send_json({"type": "error", "message": f"Erro ao inicializar sessão: {exc}"})
            await self.close()

    async def disconnect(self, code):
        if self.sessao_id:
            try:
                from clientes.models import AgentSessao
                await sync_to_async(
                    AgentSessao.objects.filter(id=self.sessao_id).update
                )(status='encerrada')
            except Exception:
                pass
        # Cancelar futures pendentes
        for fut in self._pending_aprovacoes.values():
            if not fut.done():
                fut.cancel()
        self._pending_aprovacoes.clear()

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get('type', '')

        if msg_type == 'message':
            content = (data.get('content') or '').strip()
            if content and self.engine:
                # Processar em background para não bloquear o receive
                asyncio.ensure_future(self._processar_mensagem(content))

        elif msg_type == 'context':
            # Usuário colou output do terminal no painel do agent
            terminal_output = data.get('terminal_output', '')
            if terminal_output and self.engine:
                prefixo = "_[Output colado do terminal:]_\n```\n"
                sufixo = "\n```\nAnalise o output acima."
                asyncio.ensure_future(
                    self._processar_mensagem(prefixo + terminal_output[:3000] + sufixo)
                )

        elif msg_type == 'approve':
            key = data.get('tool_call_key', '') or data.get('log_id', '')
            key = str(key)
            if key in self._pending_aprovacoes:
                fut = self._pending_aprovacoes.pop(key)
                if not fut.done():
                    fut.set_result(True)

        elif msg_type == 'reject':
            key = data.get('tool_call_key', '') or data.get('log_id', '')
            key = str(key)
            if key in self._pending_aprovacoes:
                fut = self._pending_aprovacoes.pop(key)
                if not fut.done():
                    fut.set_result(False)

        elif msg_type == 'terminal_output_response':
            # Resposta do browser ao get_terminal_output
            if self._pending_terminal_output and not self._pending_terminal_output.done():
                self._pending_terminal_output.set_result(data.get('content', ''))

        elif msg_type == 'close':
            await self.close()

    async def _processar_mensagem(self, content: str):
        if not self.engine:
            return
        try:
            await self.engine.processar_mensagem(content)
        except Exception as exc:
            logger.exception(f"Erro no processamento da mensagem Agent NOC: {exc}")
            await self._send_json({"type": "error", "message": f"Erro interno: {exc}"})

    async def _esperar_aprovacao(self, acesso_id: int, comando: str) -> bool:
        """
        Aguarda aprovação do operador via WebSocket.
        Envia tool_call para o cliente e espera mensagem approve/reject (timeout: 120s).
        """
        import time
        key = f"{acesso_id}-{comando[:30]}-{int(time.time())}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_aprovacoes[key] = fut

        # Enviar notificação com a chave para que o cliente possa aprovar/rejeitar
        await self._send_json({
            "type": "awaiting_approval",
            "tool_call_key": key,
            "acesso_id": acesso_id,
            "comando": comando,
        })

        try:
            resultado = await asyncio.wait_for(fut, timeout=120)
            return resultado
        except asyncio.TimeoutError:
            self._pending_aprovacoes.pop(key, None)
            await self._send_json({
                "type": "tool_result",
                "tool": "execute_command",
                "command": comando,
                "output": "Tempo esgotado aguardando aprovação.",
                "approved": False,
            })
            return False

    async def _solicitar_terminal_output(self, linhas: int = 200) -> str:
        """
        Solicita ao browser o conteúdo do xterm ativo.
        Envia {type: "request_terminal_output"} e aguarda {type: "terminal_output_response"}.
        """
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_terminal_output = fut
        await self._send_json({"type": "request_terminal_output", "linhas": linhas})
        try:
            return await asyncio.wait_for(fut, timeout=8.0)
        except asyncio.TimeoutError:
            return ''
        finally:
            self._pending_terminal_output = None

    async def _send_json(self, data: dict):
        try:
            await self.send(text_data=json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
