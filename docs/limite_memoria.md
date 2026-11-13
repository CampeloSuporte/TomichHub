# Limite de memória do CRM — incidente de 13/11/2026 e proteções

## O incidente

| Hora (-03) | O que aconteceu |
|---|---|
| 12:20 | Uso normal: 3,8 GB de 25,5 GB (14,7 %). Nunca tinha passado de 50 % nos registros do `sar` (set–nov). |
| 12:22–12:24 | Proxy web do acesso 1152: `system/update` e timeouts do túnel (`group-stats` com 502). |
| 12:25:37 | **Última linha do Daphne.** O processo único que atende o proxy web e os terminais não registra mais nada até o fim. |
| ~12:27 | Gunicorn e Celery começam a atrasar. O journal para de gravar em 12:27:10 e só volta em lote às 12:34:27. |
| 12:32:24 | O nginx fecha com 499 um **`POST …/acessos/713/web/8006/…/storage/local/upload`**, um upload de arquivo para o Proxmox feito pelo proxy web, junto com as outras abas abertas desse operador. |
| 12:34:27 | `sar`: **96,3 % em uso**, só 520 MB de cache, 783 falhas de página maiores/s, load 123 (em 12:20 era 33). Workers do Gunicorn em `WORKER TIMEOUT`, Postgres recusando autenticação por timeout. |
| 12:34:31 | Fim do journal. O Postgres ainda gravou uma linha às 12:54, então a máquina estava travada em thrashing, não desligada. |
| 13:11 | Reinício manual. O Postgres fez recuperação automática e voltou sem perda. |

**Diagnóstico.** Algo dentro do container ocupou cerca de 21 GB em menos de 14 minutos. O
LXC tem só 512 MB de swap, e o OOM killer não agiu a tempo: o kernel ficou expulsando cache
(thrashing) e tudo parou por cerca de 37 minutos. Não havia registro de memória por processo,
então não dá para provar qual processo cresceu. Os indícios apontam para o **Daphne**:

- Foi o primeiro a ficar mudo (12:25:37), dois minutos antes dos outros.
- Ele roda o proxy web, que **lia a resposta do equipamento inteira para a RAM, sem teto**
  (`r.read()`, `r.content`, `gzip.decompress`). Um endpoint que não termina (stream, log ao
  vivo, download grande) ou um gzip-bomba crescia até acabar a memória.
- Também estava em andamento um upload pelo proxy web. O nginx aceitava até **2 GB** nessa rota.
  O Daphne guarda o corpo inteiro na RAM antes de entregar ao Django (medido: 1 GB enviado
  ocupa 1,1 GB no processo), e depois o Django recusa qualquer coisa acima de 100 MB. Um upload
  sozinho não chega a 21 GB, mas era RAM gasta à toa e travava o processo.

Achados paralelos, que não causaram o travamento:

- O `ovpn-routes-crm` falha a cada 5 s com `You must define TUN/TAP device (--dev)`.
- O Postgres recebia milhares de tentativas de login de fora (`85.11.167.7`, `137.74.164.63`,
  `69.5.169.8`…, `no pg_hba.conf entry`). A porta 5432 estava aberta para o mundo
  (`listen_addresses='*'`). **Fechada em 13/11/2026:** veja a seção 5.

## O que foi feito

### 1. Teto no proxy web (`clientes/proxy_engine.py`)

- Constante `MAX_RESPOSTA_PROXY = 256 MB`. Passou disso, a resposta é recusada (`None`, que
  vira 502) com `WARNING [TUNNEL] Resposta recusada por tamanho`. Isso não derruba o pool SSH
  e não tenta de novo.
- Túnel: `Content-Length` acima do teto é recusado sem ler o corpo. Sem `Content-Length`, a
  leitura para no teto (`_ler_http_client_limitado`).
- Direto: `stream=True` com leitura em blocos e o mesmo teto (`_ler_requests_limitado`).
- gzip/deflate descomprimidos com teto de saída (`_descomprimir_limitado`), o que cobre o
  gzip-bomba. Conteúdo inválido volta como veio, igual a antes.
- Teste: `clientes/tests_proxy_engine_teto_memoria.py`.

### 2. Upload no proxy web limitado a 100 MB (nginx)

Em `/etc/nginx/sites-enabled/crm`, a location `~ ^/clientes/acessos/[0-9]+/web` ganhou
`client_max_body_size 100m`. Esse é o mesmo limite do Django (`DATA_UPLOAD_MAX_MEMORY_SIZE`),
mas agora o nginx devolve 413 antes do arquivo chegar ao Daphne. Upload de ISO para o Proxmox
pelo CRM não funcionava antes e continua não funcionando; deve ser feito direto no Proxmox ou
por SFTP/`scp` para o storage. A rota de firmware (`/clientes/firmware/upload/`, Gunicorn,
sem buffer) continua com 2 GB.

### 3. Limite por serviço (systemd, `tools/memoria/dropins/`)

| Serviço | Normal | MemoryHigh | MemoryMax | Ao estourar |
|---|---|---|---|---|
| daphne | ~200 MB (+1,3 GB por UNM2000 Web aberto) | 6G | 8G | cgroup mata, `Restart=always` religa em 2 s |
| gunicorn | ~550 MB | 3G | 4G | `OOMPolicy=continue`: o master repõe o worker |
| celery | ~250 MB | 3G | 4G | `OOMPolicy=continue` |
| celery-atendimento-ia | ~250 MB | 1,5G | 2G | `OOMPolicy=continue` |

A soma dos tetos é 18 GB, o que deixa cerca de 7 GB para Postgres, Redis, OpenVPN e shell. Em
`MemoryHigh` o kernel passa a recuperar memória só daquele serviço, que fica lento. Em
`MemoryMax` o processo maior do serviço é morto. Os dois só afetam o próprio serviço, nunca o
container inteiro. O OOM dentro do cgroup foi testado neste LXC com
`systemd-run --scope -p MemoryMax=200M`: o processo foi morto com exit 137.

### 4. Vigia de memória do container (`crm-memoria-guard.service`)

`tools/memoria/crm_memoria_guard.py` usa só a biblioteca padrão, roda como root, fica travado
na RAM (`mlockall`), tem `OOMScoreAdjust=-1000` e lê a memória a cada 2 s:

- **Disponível < 25 %:** a cada 30 s grava em `/var/log/crm-memoria.log` (e no journal) os 15
  maiores processos (RSS, serviço/cgroup, comando) e o consumo por serviço. É o registro que
  faltou para descobrir o culpado em 13/11.
- **Disponível < 6 % em 2 leituras seguidas:** grava o retrato e mata com `SIGKILL` o maior
  processo fora da lista de protegidos (Postgres, Redis, nginx, sshd, OpenVPN, systemd,
  journald, rsyslog, cron, fail2ban). O systemd religa o serviço dono.

Ele cobre o que fica fora dos limites por serviço: shell, sessões, scripts avulsos e o Claude
Code.

Ajuste por ambiente: `CRM_MEM_ALERTA_PCT`, `CRM_MEM_CRITICO_PCT` e `CRM_MEM_LOG`.

### 5. Postgres fechado para a internet (`deploy/firewall/crm-postgres.nft`)

A tabela nft própria `inet crm_postgres` deixa chegar ao 5432 só o loopback e
`179.48.70.165`, que são exatamente os acessos que o `pg_hba.conf` libera (`crm_db` e
`n8n_memory`). O resto é descartado (`counter drop`). Como tudo o que foi bloqueado já era
recusado pelo `pg_hba`, nada legítimo deixou de funcionar.

- Instalar ou alterar: copiar para `/etc/nftables.d/` e rodar
  `nft -f /etc/nftables.d/crm-postgres.nft`, nunca `systemctl reload nftables`, porque o
  `flush ruleset` apaga as outras tabelas. O include já está em `/etc/nftables.conf`, então a
  regra vale após o boot.
- Ao liberar um IP novo no `pg_hba.conf`, acrescente-o no set `permitidos_v4`.
- Para conferir quantos pacotes foram bloqueados: `nft list chain inet crm_postgres input`.

## Instalar ou reinstalar

```bash
sh /opt/crm/tools/memoria/instalar.sh
```

O script copia os drop-ins para `/etc/systemd/system/<svc>.service.d/memoria.conf`, instala e
liga o vigia. `MemoryHigh`/`MemoryMax` passam a valer na hora, sem reiniciar os serviços.

## Conferir

```bash
systemctl show daphne gunicorn celery celery-atendimento-ia -p MemoryHigh -p MemoryMax -p MemoryCurrent
```

```bash
journalctl -u crm-memoria-guard -n 20
```

```bash
tail -50 /var/log/crm-memoria.log
```
