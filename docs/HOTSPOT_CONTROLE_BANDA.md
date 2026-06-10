# Hotspot — Controle de Banda via DHCP Queue Simple

## Visão Geral

Funcionalidade que permite configurar controle de banda por IP no hotspot MikroTik usando Queue Simple ativado automaticamente pelo DHCP Lease Script. Cada dispositivo que recebe um IP do DHCP tem sua fila de banda criada automaticamente; ao liberar o IP, a fila é removida.

**Data de Implementação:** 10/06/2026  
**Módulo:** `clientes/` (HotspotConfig)  
**Migration:** `0071_hotspotconfig_dhcp_controle_banda`  
**Status:** ✅ Produção

---

## Como Funciona

```
Dispositivo conecta ao WiFi do hotspot
   └─ MikroTik DHCP Server concede IP (ex: 192.168.88.50)
   └─ DHCP Lease Script é executado automaticamente ($leaseBound = "1")
      └─ /queue simple remove [find name="DHCP-AA:BB:CC:DD:EE:FF"]  # remove se já existe
      └─ /queue simple add name="DHCP-AA:BB:CC:DD:EE:FF" target=192.168.88.50/32 max-limit=10M/10M

Dispositivo desconecta / lease expira
   └─ DHCP Lease Script executado ($leaseBound = "0")
      └─ /queue simple remove [find name="DHCP-AA:BB:CC:DD:EE:FF"]
```

---

## Script DHCP Gerado

O sistema configura automaticamente o seguinte script no campo `lease-script` do DHCP Server (`hs-dhcp-crm`):

```routeros
:local queueName ("DHCP-" . $leaseActMAC);
:if ($leaseBound = "1") do={
    /queue simple remove [find name=$queueName];
    /queue simple add name=$queueName target=($leaseActIP . "/32") \
        max-limit=10M/10M \
        comment=[/ip dhcp-server lease get [find where \
            active-mac-address=$leaseActMAC && active-address=$leaseActIP] host-name];
} else={
    /queue simple remove [find name=$queueName];
}
```

Onde `10M/10M` é o valor configurado em **dhcp_banda_limit** (download/upload).

---

## Modelo

```python
# clientes/models.py — HotspotConfig
dhcp_controle_banda = models.BooleanField(
    default=False,
    help_text='Ativar queue simple por IP via DHCP lease script'
)
dhcp_banda_limit = models.CharField(
    max_length=20,
    default='10M/10M',
    help_text='Ex: 10M/10M (download/upload)'
)
```

### Migration

```
clientes/migrations/0071_hotspotconfig_dhcp_controle_banda.py
├─ AddField: dhcp_controle_banda (BooleanField, default=False)
└─ AddField: dhcp_banda_limit (CharField, default='10M/10M')
```

---

## Interface

Na aba **Configuração** do hotspot (seção ao final do formulário):

```
🎛️ Controle de Banda (DHCP Queue Simple)
─────────────────────────────────────────
[✓] Ativar Queue Simple por IP via DHCP Lease Script

    Limite de Banda (download/upload)
    [10M/10M]

    Script que será aplicado no DHCP:
    ┌─────────────────────────────────────────────────┐
    │ :local queueName ("DHCP-" . $leaseActMAC);     │
    │ :if ($leaseBound = "1") do={                   │
    │     /queue simple remove [find name=$queue...] │
    │     /queue simple add name=$queueName ...      │
    │ } else={                                       │
    │     /queue simple remove [find name=$queue...] │
    │ }                                              │
    └─────────────────────────────────────────────────┘
```

- Preview do script atualiza em tempo real ao digitar o limite de banda
- Ao desmarcar o checkbox: campos somem, lease-script é limpo no MikroTik ao aplicar

---

## Aplicação no MikroTik

A função `_aplicar_mikrotik` em `clientes/hotspot_views.py` (passo 3b) aplica o lease-script logo após configurar o DHCP Server:

```python
# hotspot_views.py — _aplicar_mikrotik, step 3b
if hotspot.dhcp_controle_banda:
    limit = hotspot.dhcp_banda_limit or '10M/10M'
    lease_script = (
        ':local queueName (\\"DHCP-\\" . \\$leaseActMAC);\\n'
        ':if (\\$leaseBound = \\"1\\") do={\\n'
        '    /queue simple remove [find name=\\$queueName];\\n'
        f'    /queue simple add name=\\$queueName target=(\\$leaseActIP . \\"/32\\") max-limit={limit} '
        'comment=[/ip dhcp-server lease get [find where active-mac-address=\\$leaseActMAC && active-address=\\$leaseActIP] host-name];\\n'
        '} else={\\n'
        '    /queue simple remove [find name=\\$queueName];\\n'
        '}'
    )
    _mt_exec(client,
        f'/ip dhcp-server set [find name="{dhcp_name}"] lease-script="{lease_script}"'
    )
else:
    # Limpa o lease-script caso controle seja desativado
    _mt_exec(client, f'/ip dhcp-server set [find name="{dhcp_name}"] lease-script=""')
```

### Escaping RouterOS

Os `$` do script RouterOS precisam ser escapados como `\$` quando enviados via SSH (pois o RouterOS interpretaria como variáveis na string do comando). O Python usa `\\$` para produzir `\$` na string final enviada.

---

## Exemplos de Limite de Banda

| Valor | Significado |
|-------|-------------|
| `10M/10M` | 10 Mbps download / 10 Mbps upload |
| `5M/2M` | 5 Mbps download / 2 Mbps upload |
| `20M/10M` | 20 Mbps download / 10 Mbps upload |
| `1M/512k` | 1 Mbps download / 512 Kbps upload |
| `50M/50M` | 50 Mbps download / 50 Mbps upload |

---

## Verificar no MikroTik

Após aplicar, verificar no MikroTik:

```routeros
# Verificar lease-script configurado
/ip dhcp-server print detail where name="hs-dhcp-crm"

# Ver filas criadas (quando há dispositivos conectados)
/queue simple print

# Ver filas com filtro por nome do hotspot
/queue simple print where name~"DHCP-"
```

---

## Arquivos Relevantes

| Arquivo | Mudança |
|---------|---------|
| `clientes/models.py` | Campos `dhcp_controle_banda` e `dhcp_banda_limit` em `HotspotConfig` |
| `clientes/migrations/0071_hotspotconfig_dhcp_controle_banda.py` | Migration dos novos campos |
| `clientes/hotspot_views.py` | Step 3b em `_aplicar_mikrotik` + save/load dos campos |
| `clientes/templates/listar.html` | Seção "Controle de Banda" no formulário de configuração do hotspot |

---

## Considerações

- O controle de banda afeta **todos os dispositivos** que receberem IP do DHCP `hs-dhcp-crm`
- Se um dispositivo tiver uma fila existente com o mesmo nome (de sessão anterior), ela é removida antes de criar a nova (evita duplicata)
- O `comment` da fila recebe o hostname do dispositivo (útil para identificação no MikroTik)
- Ao desativar o controle de banda e reaplicar, o `lease-script` é limpo — filas existentes permanecem até expirarem naturalmente
