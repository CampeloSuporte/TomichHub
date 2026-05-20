"""
Agent NOC Tomich — Motor Principal
===================================
Integra Claude API (Anthropic) e OpenAI (ChatGPT) com ferramentas SSH/Telnet
para assistência NOC.

Canais suportados:
  - Terminal Web (modo supervisionado: operador aprova cada comando)
  - WhatsApp via Evolution API (modo autônomo: apenas comandos seguros)

Provedores de IA suportados:
  - Claude (Anthropic) — padrão
  - ChatGPT (OpenAI)   — alternativo
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import anthropic
from asgiref.sync import sync_to_async
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Listas de segurança
# ─────────────────────────────────────────────────────────────────

SAFE_COMMANDS: dict[str, list[str]] = {
    'zte': [
        r'^show\s+', r'^display\s+', r'^ping\s+',
        r'^show pon onu state', r'^show gpon onu detail-info',
        r'^show log\s*$', r'^show version\s*$',
    ],
    'huawei': [
        r'^display\s+', r'^ping\s+', r'^tracert\s+',
        r'^display ont info\s+', r'^display alarm\s*$',
    ],
    'cisco': [
        r'^show\s+', r'^ping\s+', r'^traceroute\s+',
    ],
    'mikrotik': [
        # Aceita tanto com "/" quanto sem (o modelo às vezes omite o prefixo)
        r'^/?interface\s+print', r'^/?interface\s+monitor',
        r'^/?ip\s+address\s+print', r'^/?ip\s+route\s+print',
        r'^/?ip\s+neighbor\s+print', r'^/?ip\s+firewall\s+',
        r'^/?ip\s+dns\s+print', r'^/?ip\s+service\s+print',
        r'^/?routing\s+', r'^/?mpls\s+',
        r'^/?log\s+print', r'^/?log\s+info',
        r'^/?system\s+resource\s+print', r'^/?system\s+identity\s+print',
        r'^/?system\s+clock\s+print', r'^/?system\s+health\s+print',
        r'^/?system\s+routerboard\s+print',
        r'^/?queue\s+print', r'^/?queue\s+simple\s+print',
        r'^/?tool\s+ping\s+', r'^/?tool\s+traceroute\s+',
        r'^/?ping\s+',
        r'^/?bridge\s+print', r'^/?switch\s+print',
        r'^/?ppp\s+active\s+print', r'^/?ppp\s+secret\s+print',
        r'^/?radius\s+print', r'^/?hotspot\s+active\s+print',
        r'^/?user\s+active\s+print',
    ],
    'generico': [
        r'^show\s+', r'^display\s+', r'^get\s+',
        r'^ping\s+', r'^traceroute\s+', r'^tracert\s+',
        # Linux read-only diagnostics
        r'^uptime$', r'^uname(\s|$)',
        r'^df(\s|$)', r'^free(\s|$)',
        r'^top\s+', r'^vmstat', r'^iostat', r'^mpstat',
        r'^cat\s+/proc/', r'^cat\s+/etc/(os-release|hostname|hosts|resolv\.conf)',
        r'^ip\s+(a|addr|address|r|route|l|link|n|neigh)\b',
        r'^ss(\s|$)', r'^netstat(\s|$)',
        r'^systemctl\s+status\s+', r'^journalctl\s+',
        r'^ps\s+', r'^lscpu$', r'^lsblk(\s|$)', r'^lsmem(\s|$)',
        r'^hostname$', r'^date$', r'^w$', r'^who$', r'^id$',
        r'^dmesg(\s|$)',
    ],
}

BLOCKED_COMMANDS: list[str] = [
    r'reboot', r'reload', r'reset', r'erase',
    r'delete\s', r'no\s+interface', r'shutdown\s*$',
    r'rm\s+-rf', r'format\s+', r'factory',
]

# Comandos permitidos em nível "operacional" (escrita não-destrutiva)
OPERATIONAL_COMMANDS: dict[str, list[str]] = {
    'mikrotik': [
        r'^/?ip\s+address\s+(add|remove|enable|disable|set)\b',
        r'^/?ip\s+route\s+(add|remove|enable|disable|set)\b',
        r'^/?interface\s+(enable|disable|set|comment)\b',
        r'^/?interface\s+vlan\s+(add|remove|set)\b',
        r'^/?ip\s+firewall\s+(nat|filter|mangle)\s+(add|remove|disable|enable|set|move)\b',
        r'^/?queue\s+simple\s+(add|remove|set|enable|disable)\b',
        r'^/?queue\s+tree\s+(add|remove|set|enable|disable)\b',
        r'^/?ip\s+pool\s+(add|remove|set)\b',
        r'^/?ip\s+dhcp-server\s+(add|remove|set|enable|disable)\b',
        r'^/?ip\s+dns\s+set\b',
        r'^/?system\s+identity\s+set\b',
        r'^/?ppp\s+secret\s+(add|remove|set|enable|disable)\b',
        r'^/?hotspot\s+(add|remove|set|enable|disable)\b',
        r'^/?bridge\s+(add|remove|set|enable|disable)\b',
        r'^/?routing\s+ospf\b', r'^/?routing\s+bgp\b',
    ],
    'huawei': [
        r'^interface\s+', r'^ip\s+address\s+', r'^undo\s+shutdown',
        r'^display\s+', r'^commit$', r'^quit$',
    ],
    'cisco': [
        r'^interface\s+', r'^ip\s+address\s+', r'^no\s+shutdown',
        r'^router\s+', r'^network\s+',
    ],
    'generico': [
        r'^systemctl\s+(start|stop|restart|enable|disable)\s+',
        r'^ip\s+(link|addr|route)\s+(add|del|set|change)\b',
        r'^iptables\s+', r'^ufw\s+',
    ],
}


def _is_safe_command(cmd: str, fabricante: str = 'generico') -> bool:
    """Retorna True se o comando é somente-leitura e pré-aprovado (nível leitura)."""
    cmd_lower = cmd.strip().lower()
    for pattern in BLOCKED_COMMANDS:
        if re.search(pattern, cmd_lower):
            return False
    safe_list = SAFE_COMMANDS.get(fabricante.lower(), SAFE_COMMANDS['generico'])
    for pattern in safe_list:
        if re.match(pattern, cmd_lower):
            return True
    return False


def _is_operational_command(cmd: str, fabricante: str = 'generico') -> bool:
    """Retorna True se o comando é permitido em nível operacional (escrita não-destrutiva)."""
    if _is_safe_command(cmd, fabricante):
        return True
    cmd_lower = cmd.strip().lower()
    for pattern in BLOCKED_COMMANDS:
        if re.search(pattern, cmd_lower):
            return False
    op_list = OPERATIONAL_COMMANDS.get(fabricante.lower(), OPERATIONAL_COMMANDS['generico'])
    for pattern in op_list:
        if re.match(pattern, cmd_lower):
            return True
    return False


# ─────────────────────────────────────────────────────────────────
# SSH helper (síncrono — rodado em thread pool via asyncio)
# ─────────────────────────────────────────────────────────────────

def _ssh_exec_sync(acesso, comando: str, timeout: int = 25) -> str:
    """
    Executa um comando SSH reutilizando a infraestrutura da plataforma
    (suporte a algoritmos legados, proxy, Huawei paging).
    """
    from clientes.consumers import platform_ssh_exec
    return platform_ssh_exec(acesso, comando, timeout=timeout)


# ─────────────────────────────────────────────────────────────────
# Definição das tools para a Claude API
# ─────────────────────────────────────────────────────────────────

TOOLS_DEFINITION: list[dict] = [
    {
        "name": "execute_command",
        "description": (
            "Executa um comando em um equipamento de rede (host/acesso) do cliente. "
            "Para o canal WhatsApp, apenas comandos da lista segura são permitidos sem aprovação. "
            "No terminal web, o operador aprova antes da execução."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "acesso_id": {"type": "integer", "description": "ID do Acesso (host) alvo"},
                "comando":   {"type": "string",  "description": "Comando a executar no equipamento"},
            },
            "required": ["acesso_id", "comando"],
        },
    },
    {
        "name": "get_client_info",
        "description": "Retorna informações do cliente: nome, CNPJ, endereço, contatos e resumo de acessos.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_hosts",
        "description": "Lista todos os hosts (acessos) disponíveis para o cliente desta sessão.",
        "input_schema": {
            "type": "object",
            "properties": {
                "protocolo": {
                    "type": "string",
                    "description": "Filtrar por protocolo: SSH, TELNET, HTTP, etc. Omitir para todos.",
                    "enum": ["SSH", "TELNET", "HTTP", "HTTPS", "WINBOX", "FTP"],
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "Busca na base de conhecimento técnico: comandos, procedures, troubleshooting, "
            "interpretação de alarmes e topologia. Use antes de executar comandos em equipamentos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string", "description": "Texto de busca"},
                "fabricante": {
                    "type": "string",
                    "description": "Filtrar por fabricante: zte, huawei, cisco, mikrotik, datacom, generico",
                },
                "categoria": {
                    "type": "string",
                    "description": "Filtrar por categoria: comando, procedure, troubleshooting, topologia, equipamento, alarme, geral",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "escalate_to_noc",
        "description": (
            "Escala o problema para o NOC humano via WhatsApp. "
            "Use quando não conseguir resolver após múltiplas tentativas ou quando o problema exigir intervenção humana."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resumo":    {"type": "string", "description": "Resumo do problema e tentativas realizadas"},
                "urgencia":  {
                    "type": "string",
                    "enum": ["baixa", "media", "alta", "critica"],
                    "description": "Nível de urgência",
                },
                "host_info": {"type": "string", "description": "Nome/IP do equipamento afetado (se houver)"},
            },
            "required": ["resumo", "urgencia"],
        },
    },
    {
        "name": "get_command_history",
        "description": "Retorna o histórico de comandos executados nesta sessão ou em sessões anteriores para o host especificado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "acesso_id": {"type": "integer", "description": "ID do host para filtrar histórico"},
                "horas":     {"type": "integer", "description": "Quantas horas para trás (padrão: 24)", "default": 24},
            },
            "required": ["acesso_id"],
        },
    },
    {
        "name": "get_terminal_output",
        "description": (
            "Captura o conteúdo visível do terminal SSH/Telnet que o operador tem aberto no browser. "
            "Use quando o usuário disser 'analisa esse log', 'o que aparece no terminal', 'veja o output', etc. "
            "Disponível apenas no canal terminal web — retorna as últimas linhas exibidas na tela."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "linhas": {
                    "type": "integer",
                    "description": "Número de linhas para capturar (padrão: 200, máximo: 500)",
                    "default": 200,
                },
            },
            "required": [],
        },
    },
]


# ─────────────────────────────────────────────────────────────────
# Tools no formato OpenAI (function calling)
# ─────────────────────────────────────────────────────────────────

TOOLS_OPENAI: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": (
                "Executa um comando em um equipamento de rede (host/acesso) do cliente. "
                "Para o canal WhatsApp, apenas comandos da lista segura são permitidos sem aprovação. "
                "No terminal web, o operador aprova antes da execução."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "acesso_id": {"type": "integer", "description": "ID do Acesso (host) alvo"},
                    "comando":   {"type": "string",  "description": "Comando a executar no equipamento"},
                },
                "required": ["acesso_id", "comando"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_client_info",
            "description": "Retorna informações do cliente: nome, CNPJ, endereço, contatos e resumo de acessos.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_hosts",
            "description": "Lista todos os hosts (acessos) disponíveis para o cliente desta sessão.",
            "parameters": {
                "type": "object",
                "properties": {
                    "protocolo": {
                        "type": "string",
                        "description": "Filtrar por protocolo: SSH, TELNET, HTTP, etc. Omitir para todos.",
                        "enum": ["SSH", "TELNET", "HTTP", "HTTPS", "WINBOX", "FTP"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Busca na base de conhecimento técnico: comandos, procedures, troubleshooting, "
                "interpretação de alarmes e topologia. Use antes de executar comandos em equipamentos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":      {"type": "string", "description": "Texto de busca"},
                    "fabricante": {"type": "string", "description": "Filtrar por fabricante: zte, huawei, cisco, mikrotik, datacom, generico"},
                    "categoria":  {"type": "string", "description": "Filtrar por categoria: comando, procedure, troubleshooting, topologia, equipamento, alarme, geral"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_noc",
            "description": (
                "Escala o problema para o NOC humano via WhatsApp. "
                "Use quando não conseguir resolver após múltiplas tentativas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resumo":    {"type": "string", "description": "Resumo do problema e tentativas realizadas"},
                    "urgencia":  {"type": "string", "enum": ["baixa", "media", "alta", "critica"], "description": "Nível de urgência"},
                    "host_info": {"type": "string", "description": "Nome/IP do equipamento afetado (se houver)"},
                },
                "required": ["resumo", "urgencia"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_command_history",
            "description": "Retorna o histórico de comandos executados nesta sessão ou em sessões anteriores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "acesso_id": {"type": "integer", "description": "ID do host para filtrar histórico"},
                    "horas":     {"type": "integer", "description": "Quantas horas para trás (padrão: 24)"},
                },
                "required": ["acesso_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_terminal_output",
            "description": (
                "Captura o conteúdo visível do terminal SSH/Telnet que o operador tem aberto no browser. "
                "Use quando o usuário disser 'analisa esse log', 'o que aparece no terminal', 'veja o output', etc. "
                "Disponível apenas no canal terminal web — retorna as últimas linhas exibidas na tela."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "linhas": {
                        "type": "integer",
                        "description": "Número de linhas para capturar (padrão: 200, máximo: 500)",
                    },
                },
                "required": [],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────
# Engine principal
# ─────────────────────────────────────────────────────────────────

class AgentNOCEngine:
    """
    Motor do Agent NOC Tomich.

    Parâmetros:
        sessao_id   : ID da AgentSessao já criada no banco
        canal       : 'terminal' | 'whatsapp'
        aprovacao_cb: coroutine(acesso_id, comando) → bool
                      Chamado quando um comando requer aprovação do operador.
                      No canal terminal, o consumer gerencia a espera.
                      No canal whatsapp, retorna False imediatamente para comandos não-seguros
                      (ou True para comandos seguros sem aprovação humana).
    """

    def __init__(
        self,
        sessao_id: int,
        canal: str = 'terminal',
        aprovacao_cb=None,
        notify_cb=None,
        terminal_output_cb=None,
    ):
        self.sessao_id = sessao_id
        self.canal = canal
        # aprovacao_cb(acesso_id, comando) → bool (async)
        self.aprovacao_cb = aprovacao_cb or self._aprovacao_default
        # notify_cb(msg_dict) → envia mensagem de volta ao cliente (async)
        self.notify_cb = notify_cb or self._noop
        # terminal_output_cb(linhas) → str  — captura buffer xterm do browser (apenas terminal web)
        self.terminal_output_cb = terminal_output_cb

        self._sessao = None   # carregado no primeiro uso
        self._config = None
        self._historico: list[dict] = []

    # ── Helpers internos ──────────────────────────────────────────

    async def _aprovacao_default(self, acesso_id: int, comando: str) -> bool:
        """Fallback: canal terminal exige callback real; WhatsApp usa checagem safe."""
        if self.canal == 'whatsapp':
            acesso = await self._get_acesso(acesso_id)
            fabricante = 'generico'
            if acesso and acesso.modelo:
                fabricante = getattr(acesso.modelo, 'fabricante', 'generico') or 'generico'
            return _is_safe_command(comando, fabricante)
        return False  # terminal sem callback real: rejeita por segurança

    async def _noop(self, msg: dict):
        pass

    async def _get_sessao(self):
        if self._sessao is None:
            from clientes.models import AgentSessao
            self._sessao = await sync_to_async(AgentSessao.objects.select_related(
                'cliente', 'acesso_ativo', 'wa_grupo', 'usuario'
            ).get)(id=self.sessao_id)
        return self._sessao

    async def _get_config(self):
        if self._config is None:
            from clientes.models import AgentConfig
            self._config = await sync_to_async(AgentConfig.get)()
        return self._config

    async def _get_acesso(self, acesso_id: int):
        from clientes.models import Acesso, AgentSessao
        sessao = await self._get_sessao()
        cliente_id = sessao.cliente_id
        try:
            return await sync_to_async(
                Acesso.objects.select_related('modelo', 'cliente').get
            )(id=acesso_id, cliente_id=cliente_id)
        except Exception:
            return None

    async def _registrar_log(self, tipo: str, conteudo: str, **kwargs):
        from clientes.models import AgentLog
        await sync_to_async(AgentLog.objects.create)(
            sessao_id=self.sessao_id,
            tipo=tipo,
            conteudo=conteudo,
            **kwargs,
        )

    # ── System prompt dinâmico ────────────────────────────────────

    async def _build_system_prompt(self) -> str:
        sessao = await self._get_sessao()
        config = await self._get_config()

        cliente = sessao.cliente
        cliente_nome = cliente.nome_empresa if cliente else '(desconhecido)'
        cliente_cidade = getattr(cliente, 'cidade', '') or ''
        cliente_estado = getattr(cliente, 'estado', '') or ''

        # Lista de hosts do cliente
        from clientes.models import Acesso
        acessos = await sync_to_async(list)(
            Acesso.objects.filter(cliente=cliente).select_related('modelo', 'funcao')
        )

        def _host_line(a) -> str:
            fabricante = ''
            modelo_nome = ''
            if a.modelo:
                modelo_nome = a.modelo.nome or ''
                fabricante  = getattr(a.modelo, 'fabricante', '') or ''
            funcao_nome = a.funcao.descricao if a.funcao else ''
            parts = [f'ID {a.id}', a.tipo, f'{a.host}:{a.porta}', a.protocolo]
            if funcao_nome:  parts.append(f'função={funcao_nome}')
            if modelo_nome:  parts.append(f'modelo={modelo_nome}')
            if fabricante:   parts.append(f'fabricante={fabricante}')
            return '  - ' + ' | '.join(parts)

        hosts_lines = '\n'.join(_host_line(a) for a in acessos) or '  (nenhum host cadastrado)'

        nivel_permissao = 'leitura'
        if self.canal == 'whatsapp' and sessao.wa_grupo:
            nivel_permissao = sessao.wa_grupo.nivel_permissao

        _NIVEL_DESC = {
            'leitura':     "read-only — apenas show/display/ping/print e diagnósticos passivos",
            'operacional': "operacional — leitura + configurações não-destrutivas (add/remove/enable/disable de IPs, rotas, interfaces, filas)",
            'admin':       "admin — todas as operações exceto comandos destrutivos (reboot/format/erase/factory reset)",
        }
        nivel_desc = _NIVEL_DESC.get(nivel_permissao, nivel_permissao)

        if self.canal == 'terminal':
            canal_instrucao = "Canal terminal: operador aprova cada comando antes da execução."
        elif nivel_permissao == 'leitura':
            canal_instrucao = "Canal WhatsApp nível LEITURA: execute apenas comandos read-only automaticamente."
        elif nivel_permissao == 'operacional':
            canal_instrucao = "Canal WhatsApp nível OPERACIONAL: execute automaticamente leitura E configurações não-destrutivas (add/set/enable/disable de IPs, rotas, interfaces, filas, etc.)."
        else:  # admin
            canal_instrucao = "Canal WhatsApp nível ADMIN: execute QUALQUER comando automaticamente, exceto reboot/format/erase/factory reset que são destrutivos."

        return f"""Você é o **Agent NOC Tomich**, assistente de inteligência artificial para operações de rede.

## Contexto desta sessão
- **Canal:** {'terminal web' if self.canal == 'terminal' else 'WhatsApp'}
- **Cliente:** {cliente_nome} ({cliente_cidade}/{cliente_estado})
- **Nível de permissão:** {nivel_permissao} — {nivel_desc}
- **Sessão ID:** {self.sessao_id}

## Hosts disponíveis para este cliente (NUNCA acesse hosts de outros clientes)
Cada host contém: ID | nome/tipo | IP:porta | protocolo | função | modelo | fabricante
{hosts_lines}

## Como usar as informações do host
Antes de executar qualquer comando em um host, identifique:
1. **Fabricante** — define a sintaxe dos comandos:
   - `mikrotik` → comandos RouterOS: `/ip address print`, `/interface print`, `/system resource print`
   - `huawei` → comandos VRP: `display ip interface brief`, `display version`, `display alarm`
   - `zte` → comandos ZTE OLT: `show pon onu state`, `show gpon onu detail-info`
   - `cisco` → comandos IOS: `show ip interface brief`, `show version`, `show running-config`
   - `datacom` → comandos Datacom/Linux: `show`, `display`
   - `generico` / Linux → comandos bash: `ip addr`, `df -h`, `free -m`, `systemctl status`
2. **Função** — define o que o equipamento faz (OLT, roteador, switch, servidor, firewall, etc.) e quais comandos fazem sentido
3. **Modelo** — detalhes adicionais de hardware que influenciam os comandos

Se o host não tiver modelo/fabricante cadastrado, tente inferir pelo nome do tipo ou pela resposta do equipamento.

## Regras de execução
{canal_instrucao}
- Comandos SEMPRE proibidos: reboot, reload, reset, format, erase, factory reset, rm -rf
- Você NUNCA acessa hosts de outros clientes além de: **{cliente_nome}**
- Nunca revele senhas — você pode USAR os acessos mas não informar credenciais

## Comportamento — REGRA PRINCIPAL
**Quando pedido para acessar host, executar comando ou verificar algo: use `execute_command` IMEDIATAMENTE.**
NÃO responda com texto dizendo que não pode ou pedindo confirmação — EXECUTE e apresente o resultado.

- Use `list_hosts` para localizar o ID correto pelo nome
- Use `execute_command` com acesso_id e comando adequado ao equipamento
- **SEMPRE inclua o output bruto do terminal na resposta**, dentro de um bloco de código (``` ```). Nunca substitua o output por um resumo formatado — mostre o output real e depois adicione análise se necessário.
- Responda em **português brasileiro**
"""

    # ── Execução de tools ─────────────────────────────────────────

    async def _tool_execute_command(self, acesso_id: int, comando: str) -> str:
        acesso = await self._get_acesso(acesso_id)
        if not acesso:
            return f"❌ Erro: host ID {acesso_id} não encontrado ou não pertence ao cliente desta sessão."

        # Verificar se precisa de aprovação
        fabricante = 'generico'
        if acesso.modelo:
            fabricante = getattr(acesso.modelo, 'fabricante', 'generico') or 'generico'

        eh_seguro = _is_safe_command(comando, fabricante)
        requer_aprovacao = not eh_seguro or self.canal == 'terminal'

        if requer_aprovacao:
            await self.notify_cb({
                "type": "tool_call",
                "tool": "execute_command",
                "command": comando,
                "acesso_id": acesso_id,
                "acesso_desc": f"{acesso.tipo} - {acesso.host}",
                "requires_approval": True,
            })
            aprovado = await self.aprovacao_cb(acesso_id, comando)
            if not aprovado:
                await self._registrar_log('tool_call', f"REJEITADO: {comando}",
                                          tool_name='execute_command',
                                          tool_input={"acesso_id": acesso_id, "comando": comando},
                                          tool_output="Rejeitado pelo operador")
                return f"❌ Comando rejeitado pelo operador: `{comando}`"
        else:
            await self.notify_cb({
                "type": "tool_call",
                "tool": "execute_command",
                "command": comando,
                "acesso_id": acesso_id,
                "acesso_desc": f"{acesso.tipo} - {acesso.host}",
                "requires_approval": False,
            })

        # Execução
        t0 = time.monotonic()
        try:
            if acesso.protocolo == 'SSH':
                output = await asyncio.get_event_loop().run_in_executor(
                    None, _ssh_exec_sync, acesso, comando,
                )
            elif acesso.protocolo == 'TELNET':
                output = await self._telnet_exec(acesso, comando)
            else:
                output = f"Protocolo {acesso.protocolo} não suportado para execução de comandos pelo agent."
        except Exception as exc:
            output = f"❌ Erro de conexão: {exc}"

        duracao_ms = int((time.monotonic() - t0) * 1000)

        await self._registrar_log('tool_call', f"EXECUTADO: {comando}",
                                  tool_name='execute_command',
                                  tool_input={"acesso_id": acesso_id, "comando": comando},
                                  tool_output=output[:2000],
                                  duracao_ms=duracao_ms)

        await self.notify_cb({
            "type": "tool_result",
            "tool": "execute_command",
            "command": comando,
            "output": output[:3000],
            "approved": True,
        })

        return f"Output do comando `{comando}` em {acesso.host}:\n```\n{output[:3000]}\n```"

    async def _telnet_exec(self, acesso, comando: str) -> str:
        """Execução Telnet simplificada via asyncio."""
        import asyncio
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(acesso.host, acesso.porta or 23),
                timeout=10,
            )
            # Esperar prompt
            await asyncio.wait_for(reader.read(1024), timeout=5)
            # Login
            writer.write((acesso.usuario + '\n').encode())
            await asyncio.wait_for(reader.read(512), timeout=3)
            writer.write((acesso.senha + '\n').encode())
            await asyncio.wait_for(reader.read(1024), timeout=5)
            # Enviar comando
            writer.write((comando + '\n').encode())
            output_bytes = await asyncio.wait_for(reader.read(8192), timeout=15)
            writer.close()
            return output_bytes.decode('utf-8', errors='replace').strip()
        except Exception as exc:
            return f"Erro Telnet: {exc}"

    async def _tool_get_client_info(self) -> str:
        sessao = await self._get_sessao()
        c = sessao.cliente
        if not c:
            return "Cliente não identificado nesta sessão."
        return (
            f"**Cliente:** {c.nome_empresa}\n"
            f"**CNPJ:** {c.cnpj}\n"
            f"**Endereço:** {c.endereco}\n"
            f"**Cidade/Estado:** {getattr(c,'cidade','?')}/{getattr(c,'estado','?')}\n"
            f"**Telefone:** {getattr(c,'telefone','') or '—'}\n"
            f"**E-mail:** {c.email}\n"
        )

    async def _tool_list_hosts(self, protocolo: str | None = None) -> str:
        from clientes.models import Acesso
        sessao = await self._get_sessao()
        qs = Acesso.objects.filter(cliente=sessao.cliente).select_related('modelo', 'funcao')
        if protocolo:
            qs = qs.filter(protocolo__iexact=protocolo)
        acessos = await sync_to_async(list)(qs)
        if not acessos:
            return "Nenhum host encontrado."
        linhas = [
            f"ID {a.id}: **{a.tipo}** — {a.host}:{a.porta} ({a.protocolo})"
            + (f" | {a.modelo.nome}" if a.modelo else "")
            + (f" | {a.funcao.descricao}" if a.funcao else "")
            for a in acessos
        ]
        return "Hosts disponíveis:\n" + "\n".join(f"- {l}" for l in linhas)

    async def _tool_search_knowledge(self, query: str, fabricante: str = '',
                                     categoria: str = '') -> str:
        from clientes.models import AgentKnowledge, AgentKnowledgeDoc
        from django.db.models import Q
        sessao = await self._get_sessao()
        palavras = query.split()

        # ── Artigos manuais ──────────────────────────────────────────────────
        qs = AgentKnowledge.objects.filter(ativo=True).filter(
            Q(cliente__isnull=True) | Q(cliente=sessao.cliente)
        )
        if fabricante:
            qs = qs.filter(fabricante__iexact=fabricante)
        if categoria:
            qs = qs.filter(categoria__iexact=categoria)
        for palavra in palavras[:4]:
            qs = qs.filter(Q(titulo__icontains=palavra) | Q(conteudo__icontains=palavra))

        artigos = await sync_to_async(list)(qs[:5])
        if artigos:
            ids = [a.id for a in artigos]
            await sync_to_async(AgentKnowledge.objects.filter(id__in=ids).update)(
                uso_count=models_uso_increment()
            )

        # ── Documentos PDF ───────────────────────────────────────────────────
        dqs = AgentKnowledgeDoc.objects.filter(ativo=True).filter(
            Q(cliente__isnull=True) | Q(cliente=sessao.cliente)
        )
        if fabricante:
            dqs = dqs.filter(fabricante__iexact=fabricante)
        if categoria:
            dqs = dqs.filter(categoria__iexact=categoria)
        for palavra in palavras[:4]:
            dqs = dqs.filter(Q(titulo__icontains=palavra) | Q(conteudo_extraido__icontains=palavra))

        docs = await sync_to_async(list)(dqs[:3])
        if docs:
            dids = [d.id for d in docs]
            await sync_to_async(AgentKnowledgeDoc.objects.filter(id__in=dids).update)(
                uso_count=models_uso_increment()
            )

        if not artigos and not docs:
            return f"Nenhum resultado encontrado para: '{query}'"

        resultado = []
        for art in artigos:
            resultado.append(
                f"### [{art.get_fabricante_display()}] {art.titulo}\n"
                f"*Categoria: {art.get_categoria_display()}*\n\n"
                f"{art.conteudo[:1500]}"
            )
        for doc in docs:
            # Extrair trecho relevante do documento (janela ao redor da primeira ocorrência)
            texto = doc.conteudo_extraido
            trecho = texto[:2000]
            for palavra in palavras[:2]:
                idx = texto.lower().find(palavra.lower())
                if idx != -1:
                    inicio = max(0, idx - 200)
                    trecho = texto[inicio:inicio + 2000]
                    break
            resultado.append(
                f"### [PDF][{doc.get_fabricante_display()}] {doc.titulo}\n"
                f"*Categoria: {doc.get_categoria_display()} — {doc.paginas} páginas*\n\n"
                f"{trecho}"
            )
        return "\n\n---\n\n".join(resultado)

    async def _tool_escalate_to_noc(self, resumo: str, urgencia: str,
                                     host_info: str = '') -> str:
        sessao = await self._get_sessao()
        config = await self._get_config()

        cliente_nome = sessao.cliente.nome_empresa if sessao.cliente else '?'
        urgencia_emoji = {'baixa': '🟡', 'media': '🟠', 'alta': '🔴', 'critica': '🚨'}.get(urgencia, '⚠️')

        msg = (
            f"{urgencia_emoji} *ESCALONAMENTO NOC — {urgencia.upper()}*\n\n"
            f"*Cliente:* {cliente_nome}\n"
            f"*Host:* {host_info or '—'}\n"
            f"*Canal:* {self.canal}\n\n"
            f"*Resumo:*\n{resumo}\n\n"
            f"_Sessão ID: {self.sessao_id}_"
        )

        # Tenta enviar via Evolution API
        wa_enviado = False
        if config.wa_grupo_noc:
            try:
                from clientes.models import EvolutionAPIConfig
                evo = await sync_to_async(EvolutionAPIConfig.get)()
                if evo.url and evo.api_key and evo.instance_name:
                    await _evolution_send(evo, config.wa_grupo_noc, msg)
                    wa_enviado = True
            except Exception as exc:
                logger.warning(f"Falha ao enviar escalonamento via WA: {exc}")

        await self._registrar_log('system', f"ESCALONAMENTO: {resumo[:500]}")

        # Atualizar status da sessão
        from clientes.models import AgentSessao
        await sync_to_async(
            AgentSessao.objects.filter(id=self.sessao_id).update
        )(status='encerrada')

        resp = f"✅ Escalonamento enviado para o NOC (urgência: {urgencia})."
        if not wa_enviado:
            resp += " (WhatsApp NOC não configurado — registrado em log)"
        return resp

    async def _tool_get_command_history(self, acesso_id: int, horas: int = 24) -> str:
        from clientes.models import AgentLog, AgentSessao
        from django.utils import timezone
        from datetime import timedelta
        sessao = await self._get_sessao()

        desde = timezone.now() - timedelta(hours=horas)
        # Verificar que o acesso pertence ao cliente
        acesso = await self._get_acesso(acesso_id)
        if not acesso:
            return f"❌ Host ID {acesso_id} não encontrado para este cliente."

        logs = await sync_to_async(list)(
            AgentLog.objects.filter(
                sessao__cliente=sessao.cliente,
                tipo='tool_call',
                tool_name='execute_command',
                criado_em__gte=desde,
            ).filter(
                tool_input__acesso_id=acesso_id
            ).order_by('-criado_em')[:20]
        )

        if not logs:
            return f"Nenhum comando encontrado para o host ID {acesso_id} nas últimas {horas}h."

        linhas = [
            f"- `{l.tool_input.get('comando','?')}` — {l.criado_em.strftime('%d/%m %H:%M')}"
            for l in logs
        ]
        return f"Histórico ({len(logs)} comandos, últimas {horas}h):\n" + "\n".join(linhas)

    async def _tool_get_terminal_output(self, linhas: int = 200) -> str:
        """Solicita ao browser o conteúdo visível do terminal xterm.js ativo."""
        if self.canal != 'terminal':
            return "❌ Ferramenta disponível apenas no canal terminal web."
        if not self.terminal_output_cb:
            return "❌ Captura de terminal não disponível nesta sessão."
        linhas = min(max(linhas, 10), 500)
        try:
            output = await asyncio.wait_for(
                self.terminal_output_cb(linhas), timeout=10.0
            )
            if not output or not output.strip():
                return "⚠️ Terminal sem conteúdo visível no momento."
            return f"Conteúdo do terminal ({linhas} linhas solicitadas):\n```\n{output}\n```"
        except asyncio.TimeoutError:
            return "❌ Timeout ao aguardar o terminal. Tente usar o botão '📋 Colar output' manualmente."
        except Exception as exc:
            return f"❌ Erro ao capturar terminal: {exc}"

    # ── Dispatcher de tools ───────────────────────────────────────

    async def _dispatch_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == 'execute_command':
            return await self._tool_execute_command(
                int(tool_input['acesso_id']), tool_input['comando']
            )
        elif tool_name == 'get_client_info':
            return await self._tool_get_client_info()
        elif tool_name == 'list_hosts':
            return await self._tool_list_hosts(tool_input.get('protocolo'))
        elif tool_name == 'search_knowledge':
            return await self._tool_search_knowledge(
                tool_input['query'],
                tool_input.get('fabricante', ''),
                tool_input.get('categoria', ''),
            )
        elif tool_name == 'escalate_to_noc':
            return await self._tool_escalate_to_noc(
                tool_input['resumo'],
                tool_input.get('urgencia', 'media'),
                tool_input.get('host_info', ''),
            )
        elif tool_name == 'get_command_history':
            return await self._tool_get_command_history(
                int(tool_input['acesso_id']),
                int(tool_input.get('horas', 24)),
            )
        elif tool_name == 'get_terminal_output':
            return await self._tool_get_terminal_output(
                int(tool_input.get('linhas', 200))
            )
        else:
            return f"❌ Ferramenta desconhecida: {tool_name}"

    # ── Loop principal de processamento ──────────────────────────

    async def processar_mensagem(self, mensagem: str) -> str:
        """
        Recebe uma mensagem do usuário, processa com o provedor configurado
        (Claude ou OpenAI) e retorna a resposta final.
        """
        config = await self._get_config()

        if not config.ativo:
            return "❌ Agent NOC desativado. Configure em Sistema → Configurações → Agent NOC."

        provedor = config.provedor_ia or 'claude'

        if provedor == 'openai':
            if not config.openai_api_key:
                return "❌ Chave de API do OpenAI não configurada."
        else:
            if not config.claude_api_key:
                return "❌ Chave de API do Claude não configurada."

        await self._registrar_log('user_msg', mensagem)
        self._historico.append({"role": "user", "content": mensagem})
        if len(self._historico) > 40:
            self._historico = self._historico[-40:]

        await self.notify_cb({"type": "thinking", "content": "Processando..."})

        if provedor == 'openai':
            return await self._processar_com_openai(config)
        else:
            return await self._processar_com_claude(config)

    # ── Processamento via Claude ─────────────────────────────────

    async def _processar_com_claude(self, config) -> str:
        system_prompt = await self._build_system_prompt()
        client   = anthropic.AsyncAnthropic(api_key=config.claude_api_key)
        messages = list(self._historico)

        t0 = time.monotonic()
        total_tokens_in = total_tokens_out = 0
        resposta_final  = ""

        while True:
            try:
                response = await client.messages.create(
                    model=config.claude_model or 'claude-sonnet-4-6',
                    max_tokens=config.claude_max_tokens or 4096,
                    system=system_prompt,
                    messages=messages,
                    tools=TOOLS_DEFINITION,
                )
            except anthropic.APIError as exc:
                err = f"❌ Erro na API Claude: {exc}"
                await self._registrar_log('error', err)
                return err

            total_tokens_in  += response.usage.input_tokens
            total_tokens_out += response.usage.output_tokens
            await self._atualizar_tokens(total_tokens_in, total_tokens_out)

            tool_calls_feitos = []
            texto_resposta = ""
            for bloco in response.content:
                if bloco.type == 'text':
                    texto_resposta += bloco.text
                elif bloco.type == 'tool_use':
                    tool_calls_feitos.append(bloco)

            if response.stop_reason == 'end_turn' or not tool_calls_feitos:
                resposta_final = texto_resposta or "(sem resposta)"
                messages.append({"role": "assistant", "content": response.content})
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tc in tool_calls_feitos:
                await self.notify_cb({"type": "thinking", "content": f"Executando: {tc.name}..."})
                resultado = await self._dispatch_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": resultado,
                })
            messages.append({"role": "user", "content": tool_results})

        return await self._finalizar_resposta(resposta_final, total_tokens_in, total_tokens_out, t0)

    # ── Processamento via OpenAI ─────────────────────────────────

    async def _processar_com_openai(self, config) -> str:
        import openai as openai_lib
        system_prompt = await self._build_system_prompt()

        # OpenAI usa system como primeira mensagem
        messages = [{"role": "system", "content": system_prompt}] + list(self._historico)

        client = openai_lib.AsyncOpenAI(api_key=config.openai_api_key)

        t0 = time.monotonic()
        total_tokens_in = total_tokens_out = 0
        resposta_final  = ""

        while True:
            try:
                response = await client.chat.completions.create(
                    model=config.openai_model or 'gpt-4o',
                    max_tokens=config.openai_max_tokens or 4096,
                    temperature=config.openai_temperature or 0.2,
                    messages=messages,
                    tools=TOOLS_OPENAI,
                    tool_choice='auto',
                )
            except openai_lib.APIError as exc:
                err = f"❌ Erro na API OpenAI: {exc}"
                await self._registrar_log('error', err)
                return err

            uso = response.usage
            if uso:
                total_tokens_in  += uso.prompt_tokens
                total_tokens_out += uso.completion_tokens
                await self._atualizar_tokens(total_tokens_in, total_tokens_out)

            choice  = response.choices[0]
            msg_out = choice.message
            texto_resposta = msg_out.content or ""
            tool_calls     = msg_out.tool_calls or []

            if choice.finish_reason == 'stop' or not tool_calls:
                resposta_final = texto_resposta or "(sem resposta)"
                messages.append({"role": "assistant", "content": resposta_final})
                break

            # Adicionar a mensagem do assistente com os tool_calls
            messages.append(msg_out)

            # Processar cada tool call
            for tc in tool_calls:
                fn_name  = tc.function.name
                fn_input = json.loads(tc.function.arguments)
                await self.notify_cb({"type": "thinking", "content": f"Executando: {fn_name}..."})
                resultado = await self._dispatch_tool(fn_name, fn_input)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      resultado,
                })

        return await self._finalizar_resposta(resposta_final, total_tokens_in, total_tokens_out, t0)

    # ── Helpers compartilhados ────────────────────────────────────

    async def _atualizar_tokens(self, tokens_in: int, tokens_out: int):
        total = tokens_in + tokens_out
        await self.notify_cb({
            "type":   "tokens",
            "used":   total,
            "input":  tokens_in,
            "output": tokens_out,
        })

    async def _finalizar_resposta(self, resposta_final: str,
                                   tokens_in: int, tokens_out: int, t0: float) -> str:
        duracao_ms = int((time.monotonic() - t0) * 1000)
        await self._registrar_log('agent_msg', resposta_final,
                                  tokens_input=tokens_in,
                                  tokens_output=tokens_out,
                                  duracao_ms=duracao_ms)
        self._historico.append({"role": "assistant", "content": resposta_final})
        await self.notify_cb({
            "type":       "agent_message",
            "content":    resposta_final,
            "sessao_id":  self.sessao_id,
            "duracao_ms": duracao_ms,
        })
        return resposta_final


# ─────────────────────────────────────────────────────────────────
# Evolution API helpers
# ─────────────────────────────────────────────────────────────────

async def _evolution_send(evo_config, jid: str, texto: str) -> dict:
    """Envia mensagem de texto via Evolution API."""
    import httpx
    url = f"{evo_config.url.rstrip('/')}/message/sendText/{evo_config.instance_name}"
    headers = {"apikey": evo_config.api_key, "Content-Type": "application/json"}
    payload = {"number": jid, "text": texto}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def models_uso_increment():
    """Helper para usar em bulk update de uso_count — retorna expressão F."""
    from django.db.models import F
    return F('uso_count') + 1
