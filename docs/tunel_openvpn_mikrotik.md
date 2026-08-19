# Túnel OpenVPN (aba Túneis) — MikroTik como cliente

## O que é

Único tipo de VPN da CRM desde 14/08/2026, ao lado do Proxy SSH na aba **Túneis** do cliente
(o WireGuard foi removido — ver [vpn_wireguard.md](vpn_wireguard.md)).
Aqui **a CRM é o servidor** OpenVPN e o **MikroTik do cliente é o client** — o oposto do módulo
"OpenVPN — Configuração automatizada em MikroTik" (`openvpn_manager.py`), que configura o roteador
do cliente como servidor para o NOC discar.

Serve para alcançar equipamentos em IP privado do cliente sem ProxyServer SSH: com o túnel de pé,
a rota existe no kernel e proxy web, Terminal SSH, WinBox, backup e monitoramento conectam direto.

Código: [`clientes/openvpn_tunnel_manager.py`](../clientes/openvpn_tunnel_manager.py) ·
views `vpn_ovpn_*` em [`clientes/views.py`](../clientes/views.py) · modelo `VPNOpenVPN`.

---

## Arquitetura

Cada túnel roda em **uma instância systemd dedicada**:

| Item | Valor |
|------|-------|
| Unit | `openvpn-server@server-crm-N` |
| Config | `/etc/openvpn/server/server-crm-N.conf` |
| Interface | `tun-crm-N` |
| Porta TCP | `1195 + N` (1194 = servidor legado, descomissionado) |
| Sub-rede | N-ésimo `/29` de `10.91.0.0/16` — servidor `.1`, cliente `.2` |
| CCD | `/etc/openvpn/ccd-instancias/server-crm-N/<common_name>` |
| PKI | `/etc/openvpn/pki-crm` (CA própria, gerada em Python via `cryptography`) |

Isolamento é por **processo**: cada instância aceita só o CN daquele cliente
(`client-config-dir` + `ccd-exclusive`, com um único arquivo no diretório).

O bootstrap do MikroTik é um one-liner (`/tool fetch` + `/import`) servido em endpoint público
protegido por token: baixa CA/cert/key, importa, cria a `ovpn-client` e o NAT `masquerade` do /29.

---

## As duas rotas que precisam existir (a pegadinha do modo `--server`)

Uma rede do cliente só é alcançável se **as duas** diretivas existirem:

| Diretiva | Onde | O que faz |
|----------|------|-----------|
| `route 10.20.0.0 255.255.0.0` | `.conf` da instância | põe a rota no **kernel** → o pacote chega na `tun-crm-N` |
| `iroute 10.20.0.0 255.255.0.0` | arquivo **CCD** do cliente | diz ao **OpenVPN** que a rede fica atrás daquele cliente |

Sem o `iroute`, o OpenVPN em modo `--server` não tem rota na tabela interna dele e **descarta o
pacote em silêncio**. O sintoma é traiçoeiro: o túnel sobe, o handshake fecha, `/ping` do IP do /29
responde — e **nada** da rede interna do cliente é alcançável.

Ambas são geradas a partir de `VPNOpenVPN.redes_privadas`, no `.conf` e no CCD, tanto ao criar
(`criar_instancia_servidor`) quanto ao editar as redes (`atualizar_redes_instancia`).

---

## Escolha das redes — por que faixa ampla é armadilha

A tabela de rotas do kernel é **uma só** e roteia por **destino**. Dois clientes declarando
`10.0.0.0/8` instalam duas rotas para o mesmo prefixo: só uma vale, e o tráfego destinado ao
equipamento do cliente A entra no roteador do cliente B — que ainda mascara a origem e pode
entregar num equipamento de mesmo IP na rede dele.

Por isso:

- O modal de criação sugere as **`/24` dos acessos privados já cadastrados** do cliente
  (`sugerir_redes`), em vez das faixas CGNAT+RFC1918.
- `redes_em_conflito()` recusa criar/editar um túnel com rede **idêntica** à de outro túnel
  OpenVPN ativo ou já presente na tabela de rotas do kernel, dizendo de quem ela é.
- Prefixos de tamanhos diferentes (um `/24` dentro do `/8` de outro cliente) são permitidos: o
  kernel casa o mais específico primeiro, o resultado é determinístico.
- Em tempo de conexão, `vpn_cobre_ip` (views) e `_rota_confere` (consumers) conferem o `dev` real
  da rota antes de usar o caminho direto — ver [proxy_web_acessos.md](proxy_web_acessos.md).

Sobreposição de espaço de endereço entre clientes continua sendo limitação de roteamento IP, não
de arquitetura de túnel: o kernel só pode mandar um pacote destinado a um IP para UM lugar.

---

## Diagnóstico rápido

```bash
systemctl status openvpn-server@server-crm-1
journalctl -u openvpn-server@server-crm-1 -n 50 --no-pager
cat /etc/openvpn/ccd-instancias/server-crm-1/*        # tem linha iroute?
ip route get 198.18.10.2                              # dev bate com tun-crm-1?
ping -c2 10.91.0.10                                   # IP do cliente no /29
```

| Sintoma | Causa provável |
|---------|----------------|
| `/import` morre com `Script Error: syntax error` na linha da `ovpn-client` | nome de parâmetro que não existe naquela versão do RouterOS — conferir `?v=` no log do nginx |
| Túnel `running`, ping do `/29` OK, rede interna morta | falta `iroute` no CCD |
| `ip route get` sai por outra `tun-crm-*` | rede ampla colidindo com outro cliente |
| Proxy cai em "Nenhum proxy SSH ativo" | `vpn_cobre_ip` recusou: rota não é daquele túnel |
| Tela barra a ação citando proxy SSH, mas o túnel está de pé | pré-checagem de front-end perguntando só por `ProxyServer` — usar `/clientes/proxies/ativo/?acesso_id=N`, que responde `alcancavel` |
| Unit em loop de restart | `.conf` inexistente — `systemctl disable --now` + `reset-failed` |
| `IP packet with unknown IP version=0 seen` no log | ruído do keepalive do RouterOS, inofensivo |

---

## O script de bootstrap depende da versão do RouterOS

O one-liner manda `?v=$version` e `gerar_setup_rsc()` monta o script para aquela versão. O que muda:

| Parâmetro | ROS 6 | ROS 7.0–7.5 | ROS 7.6+ |
|-----------|-------|-------------|----------|
| `protocol=tcp` | não existe (TCP é implícito) | sim | sim |
| `cipher=` | `aes256` | `aes256` | `aes256-cbc` (a MikroTik renomeou ao acrescentar GCM) |
| `auth=` | `sha1` (não aceita sha256) | `sha1` | `sha1` |

Errar o nome do parâmetro **não** vira falha de conexão: o `/import` morre antes, com
`Script Error: syntax error (line N column C)` — a coluna aponta o valor recusado. Para saber a
versão que o roteador mandou:

```bash
grep get_setup.rsc /var/log/nginx/*access*.log | tail -3   # "...get_setup.rsc?v=7.21.4"
```

---

## Corrigido em 14/08/2026

`cipher=aes256` era emitido para qualquer RouterOS 7. Em 7.6 a MikroTik renomeou os valores do
ovpn-client (`aes256-cbc`/`aes256-gcm`) e `aes256` puro deixou de existir — o `/import` do túnel da
Conecta ISP (RouterOS 7.21.4) morria com `syntax error (line 20 column 109)`, exatamente na coluna
do cipher. `gerar_setup_rsc()` passou a ler major **e** minor (`_parse_versao_ros`) e emitir o nome
que aquela versão entende. Sem versão informada, assume 7.6+.

A validação de conflito de redes passou a olhar também a **tabela de rotas do kernel**, não só o
banco — rota posta na mão ou resto de configuração antiga não tem registro no banco e escapava da
checagem.

---

## Corrigido em 13/08/2026

Os dois túneis em produção (TOPNET e INFORTECLINE) estavam **100% sem tráfego interno**:

1. **Faltava `iroute`** — o CCD era criado só com um comentário. Nenhuma rede interna passava, em
   nenhum dos dois túneis; `atualizar_redes_instancia` também nem reescrevia o CCD ao editar redes.
2. **Redes amplas idênticas** — os dois declaravam as mesmas 5 faixas CGNAT+RFC1918; o kernel usava
   só as rotas da `tun-crm-2`, então o `198.18.10.2` da TOPNET saía pelo túnel da INFORTECLINE.
   Ajustados para as redes específicas de cada um (`198.18.10.0/24`; `10.192.195.0/24` +
   `172.31.120.0/24`), e novos túneis passam pela validação de conflito.
3. **`vpn_cobre_ip` mentia** — respondia "coberto" com base só na declaração, mandando o proxy para
   dentro do túnel do cliente errado. Agora confere o `dev` real da rota.
4. **Unit zumbi** `openvpn-server@server-crm-999` acumulou **558 mil** reinícios apontando para um
   `.conf` inexistente: falha ao subir a instância não desfazia o `enable`. `criar_instancia_servidor`
   agora limpa (`disable --now` + `reset-failed`) e `alocar_proxima_instancia` não reaproveita N com
   sobra em disco.
