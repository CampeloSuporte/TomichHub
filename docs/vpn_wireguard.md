# VPN WireGuard — REMOVIDA em 14/08/2026

> **Este módulo não existe mais.** O único tipo de VPN da CRM é o
> [Túnel OpenVPN](tunel_openvpn_mikrotik.md). Este arquivo fica como registro
> histórico — o conteúdo técnico anterior (interfaces isoladas `wg1`–`wg4`,
> incidente de rotas compartilhadas do Conecta ISP, `_outro_peer_usa_rede`) está
> no histórico do git e no [CHANGELOG](../CHANGELOG.md).

## O que foi removido

| Camada | O que saiu |
|--------|-----------|
| Modelos | `VPNWireGuard`, `VPNServidorConfig` (migração `0111_remover_wireguard`) |
| Código | `clientes/vpn_manager.py`, views `vpn_wg_*`, rotas `/clientes/**/vpn-wg/**` |
| Frontend | seção "VPN WireGuard — MikroTik" da aba Túneis e todo o JS `wg*` |
| Servidor | interfaces `wg0`–`wg4` (`wg-quick@` parado e desabilitado), `/etc/wireguard/`, `/etc/sudoers.d/crm-wireguard` |

Backup das configs em `/root/backup-wireguard-removido-20260814/`.

## Por que

Dois tipos de VPN para o mesmo fim significavam duas implementações do mesmo
roteamento, dois caminhos de fallback em cada consumer (proxy web, Terminal SSH,
WinBox, backup, monitoramento) e duas fontes de rota concorrendo pela mesma
tabela do kernel — foi assim que rotas amplas de um túnel OpenVPN e de uma VPN
WireGuard passaram a disputar os mesmos prefixos. O OpenVPN cobre o mesmo caso de
uso com instância dedicada por cliente e bootstrap de um comando no MikroTik.

## Impacto na época da remoção

Dois clientes ainda usavam WireGuard e ficaram **sem acesso** até migrarem:

- **DS TECH** — peer ativo no `wg0` com 17 redes `/16` e tráfego real.
- **DIONES FERREIRA SILVA** — peer ativo, sem nenhuma rede de cliente declarada
  (só o `/32` do túnel), ou seja, sem acesso interno mesmo antes.

Migração: criar o túnel na aba **Túneis → Túnel OpenVPN**, declarar as redes
específicas do cliente e rodar o one-liner de bootstrap no MikroTik dele. Ver
[tunel_openvpn_mikrotik.md](tunel_openvpn_mikrotik.md).
