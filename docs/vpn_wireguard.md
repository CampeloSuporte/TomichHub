# VPN WireGuard — Documentação Técnica

## Arquitetura

O CRM gerencia túneis WireGuard com clientes (MikroTiks) em duas camadas:

| Camada | Interface | Quem usa | Como foi criada |
|--------|-----------|----------|------------------|
| **Legada compartilhada** | `wg0` (porta 51820) | Clientes criados antes de 16/06/2026 (ids 3, 7, 8, 9) | `vpn_manager.adicionar_peer()` / `remover_peer()` |
| **Isolada por cliente** | `wg5`, `wg6`, ... (porta 5182*N*) | Todo cliente novo a partir de 16/06/2026 | `vpn_manager.criar_interface_isolada()` / `adicionar_peer_isolado()` |

`wg1`-`wg4` existem no servidor (criadas manualmente antes desta correção) mas
**nunca foram realmente usadas** pelos MikroTiks dos clientes 3, 7, 8, 9 —
todos os 4 continuam conectando fisicamente em `wg0:51820`. O campo
`VPNWireGuard.interface_nome` desses 4 registros (`wg1`/`wg2`/`wg3`/`wg4`) é
**stale/aspiracional**, não reflete a realidade. Para distinguir
"legado em wg0" de "isolado de verdade", use `vpn_manager.vpn_e_isolada(vpn_ip)`
— que checa se o IP está fora da subnet `10.200.0.0/24` (legada) — em vez de
confiar em `interface_nome`.

---

## Incidente: Conecta ISP perdeu acesso interno (2026-06-14)

### Causa raiz

`vpn_manager.py` tinha `WG_INTERFACE = 'wg0'` hardcoded para **todas** as
operações de peer, e múltiplos clientes declaravam as mesmas faixas amplas
em `redes_privadas` (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`,
`198.18.0.0/15`, `100.64.0.0/10`).

`adicionar_peer()`/`remover_peer()` rodavam `ip route add|del <rede> dev wg0`
**sem verificar se outro cliente ainda dependia daquela rota**.

Sequência:
1. 12/06 — VPN do cliente 41 (Sartor Internet) criada em `wg0` com as faixas
   amplas padrão → rotas já existiam (no-op).
2. 14/06 21:05 — VPN do cliente 41 **removida** → `remover_peer()` executou
   `ip route del 10.0.0.0/8|172.16.0.0/12|192.168.0.0/16|198.18.0.0/15 dev wg0`,
   apagando rotas **compartilhadas** das quais Conecta ISP (e Infortecline
   id=3, DS Tech id=9) ainda dependiam.
3. O túnel UDP (handshake) continuou de pé — só o roteamento dos hosts
   internos parou de funcionar, o que mascarou o diagnóstico inicial
   ("a VPN está ativa mas não alcança nada").

### Correção (16/06/2026)

1. **`remover_peer()` agora é "ciente de colisão"**: antes de `ip route del`,
   verifica no banco se outro `VPNWireGuard` ativo (`ativo=True`,
   `peer_no_servidor=True`) ainda declara a mesma rede via
   `_outro_peer_usa_rede()`. Só remove se ninguém mais precisar.
2. **Isolamento real para clientes novos**: cada VPN criada a partir de agora
   ganha sua própria interface dedicada (`wg5`, `wg6`, ...), com `/30` e
   porta própria — nunca compartilha rotas de kernel com outro cliente.
   Deletar essa VPN derruba **só** a interface dela (`wg-quick down wgN` +
   remove `/etc/wireguard/wgN.conf`), nunca tocando em `wg0` ou em outra
   interface.

Os 4 clientes legados em `wg0` **não foram migrados** — migrar exigiria
reconfigurar o WireGuard real em cada MikroTik (mudar porta/endpoint), o que
é um corte arriscado e fica registrado como recomendação futura, não
executado nesta correção.

---

## Limitação conhecida: faixas amplas idênticas entre clientes

Mesmo com interfaces isoladas, **duas interfaces diferentes não podem ambas
ter uma rota de kernel para o EXATO mesmo CIDR simultaneamente** — é uma
limitação do roteamento IP, não do código. Se o cliente novo declarar
`10.0.0.0/8` e esse CIDR já tiver rota via `wg0` (ou outra interface), o
`ip route add` falha silenciosamente (a função loga um aviso) e o tráfego
para essa faixa continua indo para a interface **antiga**, não para a nova.

**Prática recomendada:** ao cadastrar `redes_privadas`, declare as sub-redes
**reais e específicas** do cliente (ex.: `192.168.50.0/24`, não
`192.168.0.0/16` genérico) — exatamente como já foi feito manualmente para
os 4 clientes legados em `wg0` (ver `AllowedIPs` em
`/etc/wireguard/wg0.conf`, que usam faixas estreitas e não sobrepostas).

---

## Funções principais (`clientes/vpn_manager.py`)

| Função | Uso |
|--------|-----|
| `adicionar_peer()` / `remover_peer()` | Legado, sempre `wg0`. `remover_peer` agora preserva rotas compartilhadas. |
| `alocar_proxima_interface()` | Acha o próximo `wgN` livre (banco + kernel). |
| `criar_interface_isolada(nome, porta, subnet_n, server_priv_key)` | Cria a interface via `wg-quick up`, idempotente. |
| `adicionar_peer_isolado(interface, ...)` | Adiciona o peer só na interface isolada do cliente. |
| `remover_interface_isolada(interface)` | `wg-quick down` + remove conf — nunca afeta outra interface. |
| `vpn_e_isolada(vpn_ip)` | `True` se o IP está fora de `10.200.0.0/24` (discrimina legado vs. isolado). |
| `get_peers_status()` | Agora varre **todas** as interfaces wg existentes, não só `wg0`. |
| `gerar_script_mikrotik(vpn, cfg)` | Usa porta/subnet da interface isolada quando aplicável; senão usa wg0/51820 como antes. |

## Limitação de sudoers

`www-data` tem `NOPASSWD` apenas para `wg`, `ip`, `wg-quick`, `tee`, `chmod`
(`/etc/sudoers.d/crm-wireguard`). Criar uma interface isolada tenta também
`sudo systemctl enable wg-quick@wgN` (para sobreviver a reboot) e
`disable` ao remover — isso **falha silenciosamente** hoje (só loga aviso),
pois `systemctl` não está liberado. A interface funciona normalmente em
runtime; só não volta sozinha após um reboot do servidor. Para isso
sobreviver a reboot, um admin precisa rodar manualmente, uma vez por
interface nova:
```bash
sudo systemctl enable wg-quick@wgN
```

---

## Diagnóstico rápido

```bash
# Ver todas as interfaces e peers
wg show all

# Ver rotas de uma interface
ip route show | grep wg0

# Testar se uma rede específica do cliente está alcançável
ping -c1 <ip_interno_do_cliente>

# Ver para onde uma rota específica está sendo decidida
ip route get <ip_interno_do_cliente>
```

Se o handshake está OK (`wg show` mostra `latest handshake` recente) mas a
rede interna não responde, **é roteamento, não o túnel** — confira
`ip route show | grep <interface>` antes de qualquer outra coisa.

---

**Última atualização:** 16/06/2026
**Autor:** CampeloSuporte
