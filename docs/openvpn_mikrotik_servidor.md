# OpenVPN Server no MikroTik do cliente (aba VPN)

## O que é

Na aba **VPN** do cliente, **"OpenVPN — Configuração automatizada em MikroTik"** entra por SSH na
RB do cliente, monta nela um **servidor** OpenVPN (CA + certificados + usuário PPP) e devolve um
`.ovpn` para o técnico conectar o notebook direto na RB — o mesmo papel de um L2TP de acesso remoto.

Não confundir com o **Túnel OpenVPN (aba Túneis)** — `openvpn_tunnel_manager.py`,
[tunel_openvpn_mikrotik.md](tunel_openvpn_mikrotik.md) — em que a RB é **cliente** do servidor da CRM.

Código: `clientes/openvpn_manager.py` (`executar_config_openvpn`, `adicionar_usuario_openvpn`).
Modelos: `OpenVPNConfig`, `OpenVPNUsuario`. Testes: `clientes/tests_openvpn_manager.py`.

## Fluxo

1. Conecta por SSH (direto, pelo túnel OpenVPN da CRM ou pelo `ProxyServer` do cliente).
2. **Checagens prévias — só leitura.** Se alguma barrar, a execução para **antes do primeiro
   comando** e a RB fica exatamente como estava (status *Erro* com o motivo):
   - servidor OpenVPN do próprio cliente já em uso (ver abaixo);
   - escolha do profile PPP (ver abaixo);
   - no pool próprio, colisão do pool com rotas da RB.
3. Certificados (CA, `Servidor-OPEN`, cliente), secret PPP `service=ovpn`, regra de input da porta,
   `ovpn-server` (`set` no v6 e na maioria dos v7; `add` nos v7 com lista de instâncias).
4. Baixa CA/cert/chave (SFTP, com fallback por comando), gera o `.ovpn` (`redirect-gateway def1`).

O log da configuração (botão **Logs**) começa com linhas `#` dizendo qual profile foi usado e por quê.

## Profile PPP: o usuário cai na faixa que a rede já conhece

**Padrão (recomendado):** o usuário OpenVPN entra no **profile de VPN que a RB já usa** — o dos
secrets L2TP/PPTP/SSTP/OVPN do próprio cliente. `_detectar_profile_vpn` pega o mais usado entre os
que têm `local-address` e `remote-address` apontando para um pool. Ficam de fora os secrets
`pppoe`/`any` e o próprio `OPEN_VPN`. **Plano de assinante nunca serve**, mesmo que algum secret de
VPN aponte para ele: são descartados o `default-profile` dos pppoe-servers, profiles usados por
secrets pppoe e profiles com `rate-limit`. Achado real: na ALTA RADIO, um secret pptp usava o profile
`pppoe` (IP público local, pool `CGNAT_01`), que é o default dos 12 pppoe-servers. Na dúvida, a
plataforma cai no pool próprio.

Nesse modo a plataforma **não cria** pool, profile nem NAT: o usuário herda o roteamento e o NAT que
já funcionam para o L2TP. O rate-limit do cadastro não se aplica.

**Pool próprio** (`OPEN_VPN` + `POOL_OpenVPN` + regra `NAT_OpenVPN`): só quando a RB não tem nenhum
profile de VPN reaproveitável, ou quando o operador escolhe em *Configurações avançadas → Profile
PPP dos usuários*. Antes de criar, `_rotas_no_pool` confere se o pool (ou o IP local) se sobrepõe a
alguma rota da RB — se sim, aborta pedindo outro pool.

`OpenVPNConfig.ppp_profile` guarda o profile usado, que aparece no card da configuração. Usuários
adicionais (`adicionar_usuario_openvpn`) entram no mesmo profile.

### Por que (achado real, CONECTONLINE, 10/09/2026)

Na mesma RB, o L2TP acessava tudo e o OpenVPN gerado não alcançava algumas redes. O túnel subia
normalmente (log `paulo logged in, 192.168.250.254`); a diferença era **onde o usuário caía**:

| | L2TP (feito pelo cliente) | OpenVPN (como era gerado) |
|---|---|---|
| Profile | `VPN-Profile` | `OPEN_VPN` |
| IP do usuário | `10.190.180.x` (Pool-VPN) | `192.168.250.x` — pool fixo |
| NAT | com exceções (`dst-address-list=!LOOPBACKVPNS`) | `NAT_OpenVPN`: tudo para o IP público |

- **192.140.66.160:** as loopbacks dos sites Starlink ligados por L2TP (`LOOPBACKVPNS`,
  172.25.255.0/24) são isentas de NAT para o pool do L2TP. O usuário OpenVPN chegava lá com origem
  pública, e o site respondia pela própria internet — a resposta nunca voltava.
- **45.180.36.1:** o tráfego que sai pela `Procyon-VPN` (10.201.116.0/24) não passa por NAT, e a
  outra ponta não sabe voltar para 192.168.250.x.

A varredura das demais RBs mostrou o mesmo padrão: o NAT do próprio cliente sempre tem exceção para
destino interno (`!SEM-NAT-IPS-PUBLICOS`, `!PRIVADO`…), e o `NAT_OpenVPN` nunca teve. O 192.168.250.x
também já aparecia na rede de alguns clientes (rotas OSPF, uma "VPN LOJA" com o mesmo /25).

## Servidor OpenVPN que já existe na RB

No RouterOS v6 (e na maioria dos v7) só existe **um** `ovpn-server`, e o `set` da plataforma trocava
porta, certificado e profile do servidor que o cliente já usava. Achado real: na 45.180.36.1 o
servidor próprio do cliente (porta 51194, certificados `ovpn-server`/`ovpn-ca`) virou o da
plataforma na 61194.

Agora `_checar_servidor_existente` aborta se o servidor estiver ativo com um certificado que não é
o `Servidor-OPEN` da plataforma. Nos v7 com lista de instâncias, a plataforma remove **só as
instâncias dela** (antes era `remove [find]`, que apagava todas) e só barra se uma instância do
cliente já usa a mesma porta.

O dono é decidido pelo **certificado**, não pelo nome. No PROMOFI, a instância tem o nome da
configuração (`VPN-PROMOFI`), mas alguém trocou o certificado para `SRV-PROMOFI`, e ela atende 60
usuários. Reexecutar ali é barrado de propósito: recriar com `Servidor-OPEN` derrubaria todos eles.

Na checagem de colisão do pool, rotas desabilitadas e as dinâmicas de sessões OpenVPN não contam.

## NAT do pool próprio

`_pool_cidr` passou a devolver o menor prefixo que cobre o pool inteiro. Antes era sempre "IP
inicial + /25", que só acertava o pool padrão `.128-.254`: num pool `.2-.254` o NAT cobria só
`.0/25`, e quem recebia IP de `.128` para cima ficava sem internet.

## Configurações antigas

As configurações criadas antes desta mudança ficam como estão na RB (`ppp_profile = OPEN_VPN`). A
plataforma não reconfigura nada sozinha: o comportamento novo só vale ao criar uma configuração ou
ao reexecutar uma (**Tentar Novamente**, que aparece em configurações com erro). Para corrigir uma
RB já gerada sem reexecutar, mova os secrets para o profile de VPN do cliente:

```
/ppp secret set [find profile=OPEN_VPN] profile=VPN-Profile
```

(troque `VPN-Profile` pelo profile que os secrets L2TP da RB usam) e reconecte. A regra
`NAT_OpenVPN` fica sem uso e pode ser removida.

## Diagnóstico rápido

| Sintoma | Onde olhar |
|---|---|
| Conecta, mas algumas redes não respondem | `/ppp active print where service=ovpn`: o endereço está na mesma faixa do L2TP? Se estiver em `192.168.250.x`, é o caso acima |
| Erro "servidor OpenVPN próprio ativo" | a RB já tem OpenVPN do cliente; combine com ele antes (o v6 só tem um servidor) |
| Erro "pool se sobrepõe a rotas" | a faixa já existe na rede do cliente — escolha outro pool nas Configurações avançadas |
| Se ainda sobrar destino inacessível | MTU: `ovpn-server` com `max-mtu` 1500 × L2TP com 1450 |
