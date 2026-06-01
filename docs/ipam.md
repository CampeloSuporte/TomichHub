# IPAM — Documentação Técnica

**Arquivo principal:** `clientes/ipam_views.py`  
**Models:** `IPAMVlan`, `IPAMPrefixo`, `IPAMSubRede`, `IPAMEndereco`, `IPAMVpnDoc`  
**Atualizado em:** 2026-05-26

---

## Visão Geral

O IPAM (IP Address Management) nativo do CRM organiza endereços IP em quatro níveis hierárquicos:

```
IPAMPrefixo (container /24 ou bloco maior)
  └── IPAMSubRede  (ex: /30, /29 — sub-rede real)
        └── IPAMEndereco  (host individual, ex: 192.168.1.1/30)

IPAMVlan  →  associada a IPAMSubRede
```

---

## Estrutura de URLs

Todas as rotas são prefixadas com `/clientes/<cliente_id>/ipam/`.

| Método | Rota                        | View                      | Descrição                         |
|--------|-----------------------------|---------------------------|-----------------------------------|
| GET    | `vlans/`                    | `ipam_vlans_listar`       | Lista VLANs do cliente            |
| POST   | `vlans/salvar/`             | `ipam_vlan_salvar`        | Cria ou atualiza VLAN             |
| POST   | `vlans/<id>/deletar/`       | `ipam_vlan_deletar`       | Remove VLAN                       |
| GET    | `prefixos/`                 | `ipam_prefixos_listar`    | Lista prefixos                    |
| POST   | `prefixos/salvar/`          | `ipam_prefixo_salvar`     | Cria ou atualiza prefixo          |
| POST   | `prefixos/<id>/deletar/`    | `ipam_prefixo_deletar`    | Remove prefixo                    |
| GET    | `subredes/`                 | `ipam_subredes_listar`    | Lista sub-redes (filtrável)       |
| POST   | `subredes/salvar/`          | `ipam_subrede_salvar`     | Cria ou atualiza sub-rede         |
| POST   | `subredes/<id>/deletar/`    | `ipam_subrede_deletar`    | Remove sub-rede                   |
| GET    | `ips/`                      | `ipam_ips_listar`         | Lista endereços (busca e filtro)  |
| POST   | `ips/salvar/`               | `ipam_ip_salvar`          | Cria ou atualiza endereço         |
| POST   | `ips/<id>/deletar/`         | `ipam_ip_deletar`         | Remove endereço                   |
| POST   | `importar/`                 | `ipam_importar`           | Importa IPs dos Acessos           |

---

## Importação Automática de Acessos

A view `ipam_importar` percorre todos os `Acesso` do cliente que possuam `host` ou `host_ipv6`
no formato CIDR (ex: `192.168.1.1/30`) e cria automaticamente a hierarquia:

```
Acesso.host (CIDR)
  → IPAMPrefixo container (/24)
  → IPAMSubRede para o bloco /24 pai   ← novo em 2026-05-26
  → IPAMSubRede com CIDR real (/30, etc.)
  → IPAMEndereco (host individual)
```

### Função `_get_or_create_prefixo_pai` — Alteração de 2026-05-26

**Arquivo:** `clientes/ipam_views.py`, linha ~1831

**Comportamento anterior:**  
Criava apenas um `IPAMPrefixo` do tipo `container` para o bloco /24. O bloco /24 aparecia
somente na aba de Prefixos; a aba de Sub-redes só exibia os CIDRs reais (/30, /29 etc.).

**Comportamento atual:**  
Além do `IPAMPrefixo`, cria também um `IPAMSubRede` para o bloco /24 pai com:
- `status = 'reservado'`
- `descricao = 'Bloco <cidr> (auto)'`
- `prefixo` vinculado ao `IPAMPrefixo` correspondente

**Resultado visível:**  
Os blocos /24 passam a aparecer nas duas abas — Prefixos (como container) e Sub-redes
(como sub-rede reservada) — facilitando a navegação e visualização da ocupação do bloco.

**Código simplificado:**

```python
def _get_or_create_prefixo_pai(net):
    # Calcula /24 pai (ou /48 para IPv6)
    preflen = min(net.prefixlen, 24)  # 48 para IPv6
    pai_net = net.supernet(new_prefix=preflen) if net.prefixlen > preflen else net
    cidr_pai = str(pai_net)

    # Cria IPAMPrefixo container
    obj, created = IPAMPrefixo.objects.get_or_create(
        cliente=c, prefixo=cidr_pai,
        defaults={'tipo': 'container', 'descricao': f'Bloco {cidr_pai} (auto)'}
    )

    # NOVO: cria também IPAMSubRede para o /24 aparecer na aba Sub-redes
    sub_pai, sub_criada = IPAMSubRede.objects.get_or_create(
        cliente=c, rede=cidr_pai,
        defaults={
            'prefixo': obj,
            'descricao': f'Bloco {cidr_pai} (auto)',
            'status': 'reservado',
        }
    )
    return obj, created
```

---

## Agrupamento por Prefixo

A listagem de Sub-redes aceita o parâmetro `?prefixo_id=<id>` para filtrar por bloco pai.  
O frontend utiliza o select `#ipam-sr-filtro-prefixo` para acionar este filtro.

## Busca de Endereços

A listagem de IPs aceita `?q=<texto>` para busca por IP ou hostname e
`?subrede_id=<id>` para filtrar por sub-rede específica. A busca tem debounce de 350 ms
no frontend (`ipamBuscarIPs`).

---

## Models Relacionados

| Model          | Campos principais                                              |
|----------------|----------------------------------------------------------------|
| `IPAMVlan`     | `cliente`, `numero` (1-4094), `nome`, `status`               |
| `IPAMPrefixo`  | `cliente`, `prefixo` (CIDR), `tipo`, `status`, `pool_cheia`  |
| `IPAMSubRede`  | `cliente`, `rede` (CIDR), `prefixo` (FK), `vlan` (FK), `status` |
| `IPAMEndereco` | `cliente`, `ip`, `subrede` (FK), `hostname`, `descricao`     |
| `IPAMVpnDoc`   | documentação de VPNs vinculada ao cliente                     |
