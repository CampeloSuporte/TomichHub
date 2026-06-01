# Terminal SSH — Documentação Técnica

**Arquivo:** `clientes/consumers.py`  
**Classe principal:** `SSHConsumer` (Django Channels WebSocket Consumer)  
**Atualizado em:** 2026-05-26

---

## Visão Geral

O Terminal SSH é implementado como um WebSocket Consumer do Django Channels.  
Cada sessão de terminal no browser abre uma conexão WebSocket que o `SSHConsumer` mantém,
gerenciando o processo SSH (via `pexpect`) ou a sessão Paramiko diretamente com o equipamento
de rede.

---

## Arquitetura

```
Browser (xterm.js)
    │  WebSocket (JSON + binary frames)
    ▼
SSHConsumer (channels)
    ├── conexão direta SSH/Telnet  →  equipamento
    └── via ProxyServer             →  proxy SSH  →  equipamento
```

### Pool de Conexões com Proxies (`_ProxyPool`)

Classe auxiliar que mantém conexões Paramiko ativas com servidores proxy em cache.  
Evita o custo de re-handshake SSH a cada novo terminal aberto para o mesmo proxy.

- **`get(proxy)`** — retorna conexão ativa existente ou `None`
- **`put(proxy, client)`** — armazena cliente SSHClient no pool
- **`remove(proxy)`** — remove do pool (chamado em caso de falha)

A instância global `_proxy_pool` é compartilhada entre todos os consumers do processo.

---

## Ciclo de Vida da Sessão

1. Browser abre WebSocket → `SSHConsumer.connect()` aceita e limpa estado anterior
2. Browser envia `{"action": "connect", "acesso_id": N}` → consumer lê o `Acesso` do banco
3. `SSHConsumer` decide protocolo (SSH / Telnet) e inicia conexão
4. Thread de leitura (`read_thread`) fica em loop enviando output para o browser
5. Browser envia frames binários (teclas) → `receive()` repassa ao processo/canal SSH
6. Ao fechar: `disconnect()` → `limpar_recursos()` encerra threads e fecha canais

### Limpeza de Recursos (`limpar_recursos`)

- Fecha `ssh_process`, `telnet_client`, `tunnel_process`, `_paramiko_shell`,
  `_paramiko_dest_transport`, `_tunnel_server`
- **Não fecha** `_paramiko_client` (é o cliente do pool compartilhado — fechá-lo derrubaria
  outros terminais ativos no mesmo proxy)

---

## Configuração SSH — KexAlgorithms

### Situação anterior (problema)

A lista de algoritmos de troca de chaves tinha `diffie-hellman-group16-sha512` (DH 4096-bit)
em posição alta. Equipamentos ZTE com CPU lenta levavam vários segundos para completar o
handshake, causando timeout da sessão.

### Correção aplicada em 2026-05-26

A ordem foi reestruturada para priorizar algoritmos leves:

```
KexAlgorithms=
  diffie-hellman-group14-sha256,   ← prioridade 1 (2048-bit, rápido)
  diffie-hellman-group14-sha1,     ← prioridade 2 (compatibilidade legada)
  curve25519-sha256,               ← curva elíptica (modernos)
  curve25519-sha256@libssh.org,
  ecdh-sha2-nistp256,
  ecdh-sha2-nistp384,
  ecdh-sha2-nistp521,
  diffie-hellman-group-exchange-sha256,
  diffie-hellman-group-exchange-sha1,
  diffie-hellman-group16-sha512,   ← 4096-bit movido para o final
  diffie-hellman-group18-sha512,
  diffie-hellman-group1-sha1
```

**Motivo:** O cliente SSH negocia o primeiro algoritmo que o servidor também suporte.
Ao colocar `group14-sha256` (DH 2048-bit) antes de `group16-sha512` (DH 4096-bit), o
handshake com ZTEs e outros equipamentos de CPU limitada passa a ser concluído em ~1 s
em vez de provocar timeout.

---

## Outras Opções SSH Relevantes

| Opção                         | Valor / Justificativa                              |
|-------------------------------|----------------------------------------------------|
| `StrictHostKeyChecking`       | `no` — ambiente interno controlado                 |
| `ConnectTimeout`              | `10` segundos                                      |
| `ServerAliveInterval`         | `60` s — mantém sessão viva em links instáveis     |
| `ServerAliveCountMax`         | `3` tentativas antes de desconectar                |
| `HostKeyAlgorithms`           | `+ssh-rsa,ssh-dss` — suporte a equipamentos legados|
| `Ciphers`                     | inclui `aes128-cbc`, `aes256-cbc`, `3des-cbc`      |
| `PreferredAuthentications`    | `password,keyboard-interactive`                    |

---

## Suporte a Huawei (modo especial)

Equipamentos Huawei requerem tratamento de prompt diferenciado.  
O flag `self.is_huawei` é ativado na detecção do tipo de equipamento e altera o comportamento
de parsing de output e envio de comandos.

---

## Protocolos Suportados

| Protocolo | Implementação              |
|-----------|---------------------------|
| SSH       | `pexpect` + processo `ssh` |
| Telnet    | `telnetlib`                |
| SSH via Proxy | Paramiko + tunnel      |
