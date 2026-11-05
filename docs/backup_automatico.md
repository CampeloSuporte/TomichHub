# Backup Automático — Documentação Técnica

**Arquivos principais:**
- `clientes/tasks.py` — tasks Celery
- `clientes/management/commands/rotina_backup.py` — management command
- `crm/celery.py` — agendamento Celery Beat

**Atualizado em:** 2026-09-02

---

## Visão Geral

O sistema possui um pipeline completo que:
1. **Detecta o modelo** do equipamento lendo o conteúdo do backup
2. **Habilita o backup automático** para todos os acessos SSH com modelo cadastrado,
   selecionando o template correto de acordo com o fabricante

O pipeline roda automaticamente todos os dias às **01h00** via Celery Beat.

---

## Templates de Backup Disponíveis

| ID | Nome | Fabricante | Usado por |
|---|---|---|---|
| 1 | Backup Completo | CISCO | Cisco, ZTE, Parks, Intelbras, VSOL, TP-Link, Raisecom, Dell |
| 3 | Backup Huawei | HUAWEI | Huawei roteadores e switches |
| 5 | Backup Mikrotik | MIKROTIK | Mikrotik (todos) |
| 7 | Backup Completo | JUNIPER | Juniper |
| 12 | Backup Datacom | DATACOM | Datacom |
| 14 | backup olt huawei | HUAWEI | Huawei OLTs (MA5600, MA5608, MA5800…) |
| 15 | Backup fiberhome | GENERICO | Fiberhome |
| 16 | Backup hillstone | GENERICO | Hillstone |
| 17 | Backup A10 | GENERICO | A10 Networks |
| 19 | Backup OLT VSOL | GENERICO | OLT VSOL (V1600…) — escolhido à mão no acesso |
| 11 | Backup Genérico | GENERICO | Fallback para fabricantes não mapeados |

### OLT VSOL (`_executar_comandos_vsol`)

Template "Backup OLT VSOL" = `enable` + `terminal length 0` + `show running-config`. O executor próprio entra quando o
**nome do template** contém "vsol" (não o modelo: há FiberHome/CDATA cadastradas com modelo
"OLT VSOL" que fazem backup pelo template FiberHome). Roteiro, igual ao console:

1. `Login:` / `Password:` dentro do shell → usuário e senha do acesso;
2. `enable` → `Password:` → **senha ADM** do acesso (sem ela, a senha do login);
3. `terminal length 0` (desliga a paginação) e `show running-config`; se ainda vier `--More--`, segue com espaço.

Senha de enable errada (`% Bad passwords`, prompt volta a `>`) vira erro legível no log do backup.
O backup é sempre SSH: acesso VSOL só com HTTP/HTTPS precisa do SSH como protocolo extra.

---

## Regras de Seleção de Template (`_selecionar_template`)

```
1. ZTE ou Parks (qualquer grafia)  →  template CISCO
2. Huawei + OLT (nome contém MA5 ou OLT)  →  template "backup olt huawei"
3. Huawei outros  →  template "Backup Huawei"
4. Mapeamento por fabricante:
     Mikrotik   → MIKROTIK
     Cisco      → CISCO
     Juniper    → JUNIPER
     Datacom    → DATACOM
     Fiberhome  → FIBERHOME
     Hillstone  → HILLSTONE
     A10 / A10 Networks → A10
     Intelbras  → CISCO  (CLI compatível com IOS)
     VSOL       → CISCO  (CLI compatível com IOS)
     TP-Link    → CISCO  (CLI compatível com IOS)
     Raisecom   → CISCO  (CLI compatível com IOS)
     Dell       → CISCO  (CLI compatível com IOS)
     Extreme    → EXTREME
     HP         → HP
5. Fallback  →  GENERICO
```

---

## Tasks Celery

### `habilitar_backups_automaticos`

Habilita backup nos acessos elegíveis:

**Critérios de elegibilidade:**
- `protocolo = 'SSH'`
- `modelo` preenchido (não nulo)
- `backup_habilitado = False`
- `funcao.descricao` **não** contém `vm`, `hipervisor` ou `hypervisor` (case-insensitive)

**Ações por acesso elegível:**
```python
Acesso.objects.filter(pk=acesso.pk).update(
    backup_habilitado=True,
    backup_automatico=True,
    backup_template=template,  # selecionado conforme fabricante
)
```

**Correção de templates nulos:** acessos que já tinham `backup_habilitado=True`
mas `backup_template=None` também são corrigidos (mesma exclusão de função VM/hipervisor).

**Limpeza de backups indevidos (adicionado 2026-05-27):** a task realiza uma varredura e
desabilita `backup_habilitado`, `backup_automatico` e `backup_template` de qualquer acesso
cuja função contenha `vm`, `hipervisor` ou `hypervisor`. Isso garante que equipamentos
virtuais e hipervisores cadastrados incorretamente com backup ativo sejam corrigidos
automaticamente a cada execução.

```python
# Resultado da primeira varredura (2026-05-27)
# 112 equipamentos tiveram backup removido (511 → 399 com backup ativo)
```

---

### `rotina_backup_completa`

Pipeline encadeado executado diariamente às 01h:

```python
@shared_task
def rotina_backup_completa(self):
    r_modelo = detectar_modelos_via_backup()   # passo 1: detectar modelo
    r_backup = habilitar_backups_automaticos()  # passo 2: habilitar backup
```

**Agendamento no `crm/celery.py`:**
```python
'rotina-backup-completa': {
    'task': 'clientes.tasks.rotina_backup_completa',
    'schedule': crontab(hour=1, minute=0),
},
```

---

## Management Command `rotina_backup`

Executa o pipeline manualmente com saída formatada no terminal.

### Uso

```bash
# Pipeline completo (detectar modelo + habilitar backup)
python manage.py rotina_backup

# Força re-detecção de modelos para hosts sem modelo (ignora cache de 3 dias)
python manage.py rotina_backup --forcar-modelo

# Só corrige templates, sem rodar detecção de modelo
python manage.py rotina_backup --apenas-templates
```

### Saída esperada

```
══════════════════════════════════════════
  ROTINA DE BACKUP — VERIFICAÇÃO E HABILITAÇÃO
══════════════════════════════════════════

▶ Passo 1 — Detecção de modelo via backup
  ✓ Verificados: 183 | Modelos novos: 91 | Sem arquivo: 84 | Sem match: 92
  Total com modelo: 726 → 817

▶ Passo 2 — Habilitação e seleção de template
  ✓ Habilitados: 244 | Templates corrigidos: 7 | Sem template: 0
  Total com backup ativo: 267 → 511

▶ Distribuição por template:
  Backup Mikrotik                 (MIKROTIK  )  137 equipamentos
  Backup Completo                 (CISCO     )  119 equipamentos
  ...

  SSH sem modelo (pendente próx. ciclo): 1
  Não-SSH (Telnet/HTTP/etc, excluídos): 312
══════════════════════════════════════════
  Concluído em 0.5s
══════════════════════════════════════════
```

---

## Resultado após Implantação (2026-05-26)

| Métrica | Valor |
|---|---|
| Total de acessos | 824 |
| Com backup habilitado | **511** (62%) |
| Por template Mikrotik | 137 |
| Por template Cisco | 119 |
| Por template Huawei (roteadores/switches) | 58 |
| Por template Datacom | 55 |
| Por template OLT Huawei | 14 |
| Por template Juniper | 3 |
| Outros templates | 6 |
| SSH sem modelo (pendente) | 1 |
| Não-SSH excluídos (Telnet/HTTP/etc.) | 312 |

## Resultado após limpeza VM/Hipervisor (2026-05-27)

| Métrica | Valor |
|---|---|
| Com backup habilitado antes | 511 |
| Removidos (função VM/Hipervisor) | **112** |
| Com backup habilitado após | **399** |
| Por template Mikrotik | 137 |
| Por template Cisco | 118 |
| Por template Huawei | 58 |
| Por template Datacom | 55 |
| Por template OLT Huawei | 14 |
| Por template A10 | 10 |
| Por template Juniper | 3 |
| Outros | 4 |

---

## Detecção de Fabricante e KEX SSH em `realizar_backup` — Melhorado em 2026-07-20

**Arquivo:** `clientes/views.py` (`realizar_backup`, usada tanto pelo botão manual quanto pelas
tasks Celery do pipeline automático).

### Detecção de fabricante mais robusta

Antes, a detecção de fabricante (`is_huawei`, `is_a10`, etc.) usava só `acesso.modelo.nome`. Se o
`Modelo_equipamento` vinculado ao acesso estivesse cadastrado errado (ex: uma OLT ZTE com modelo
"debian 12" por engano de cadastro), a detecção falhava silenciosamente. Agora a string de
detecção combina `modelo.fabricante` + `modelo.nome` + `acesso.tipo`:

```python
_partes_deteccao = []
if acesso.modelo:
    if acesso.modelo.fabricante:
        _partes_deteccao.append(str(acesso.modelo.fabricante))
    if acesso.modelo.nome:
        _partes_deteccao.append(str(acesso.modelo.nome))
if acesso.tipo:
    _partes_deteccao.append(acesso.tipo)
modelo_nome = ' '.join(_partes_deteccao).lower()
```

`acesso.tipo` costuma estar correto mesmo quando o modelo cadastrado não está — mesmo fallback já
usado em `consumers.py` para a detecção de fabricante do terminal interativo.

**Atualização (2026-10-10):** o `fabricante` do **template de backup** também entra na string
(exceto `GENERICO`) — são os comandos dele que vão rodar. Switches Huawei da Sartor cadastrados
como "SW-HU-TUTILANDIA", "Switch Lucas"… com modelo vazio não eram reconhecidos como Huawei e
caíam no `exec_command` por comando (`_executar_comandos_sem_pty`); o VRP encerra a sessão depois
do primeiro exec e o `display current-configuration` voltava `ERRO: SSH session not active` toda
madrugada.

Na mesma correção, `_backup_tem_conteudo` passou a ignorar a saída dos comandos de paginação
(`screen-length`, `terminal length/pager/page-break/datadump`, `no page`, `disable clipaging`,
`set cli screen-length` — `_RE_CMD_PAGINACAO`). No Huawei essa saída traz o banner de login
("Info: The max number of VTY users…") e sozinha bastava para gravar como **SUCESSO** um backup de
~1 KB sem configuração nenhuma. Agora esse caso vira erro ("Backup vazio") e aparece no histórico.

Ainda no Huawei, `_executar_comandos_huawei` segue o `---- More ----` com espaço (até 2000
páginas) e remove o marcador do arquivo. Em VRP antigo (SW-HU-NOVA-MONTE-VERDE, V200R010) o
`screen-length 0 temporary` às vezes é digitado enquanto o banner de login ainda chega e se perde;
a paginação fica ligada e o backup parava na primeira página (1,7 KB).

### KEX para equipamentos de CPU limitada (ZTE etc.)

A conexão `paramiko.SSHClient().connect()` usada pelo backup passa
`disabled_algorithms={'kex': [...]}` desabilitando os KEX pesados (`group-exchange-sha256/sha1`,
`group16-sha512`, `group18-sha512`), forçando o paramiko a negociar `curve25519`/`ecdh`/`group14`
(rápidos, suportados por qualquer servidor SSH2 moderno). Mesmo problema e mesma causa raiz do fix
de KEX do terminal interativo — ver [terminal_ssh.md](terminal_ssh.md).

**Correção (2026-07-27):** esse disable era aplicado **para todos os fabricantes**, não só ZTE.
Um Huawei NE8000 M8 (roteador de borda/BGP) só oferece `diffie-hellman-group-exchange-sha256` como
KEX — desabilitar esse algoritmo pra todo mundo zerava o KEX em comum com esse equipamento e o
backup falhava com `Incompatible ssh peer (no acceptable kex algorithm)` antes mesmo da
autenticação. Agora o disable só é aplicado quando `is_zte` (a flag já calculada logo acima, pela
mesma detecção combinada de fabricante). Huawei, Cisco, A10, MikroTik etc. usam a lista completa de
KEX do paramiko.

---

## Retry em falha transitória de handshake SSH — 2026-08-05

**Sintoma:** backup da OLT-HU-LEAL (Huawei MA5800, IP privado via proxy) falhou 3 madrugadas
seguidas — `No existing session` (03/08 e 04/08) e `Authentication failed: transport shut down or
saw EOF` (05/08) — enquanto o terminal interativo acessava o mesmo host normalmente no mesmo
período, com as mesmas credenciais.

**Causa:** o backup abre o `paramiko.SSHClient().connect()` sobre um túnel próprio
(`criar_ssh_tunnel`, relay TCP por threads separado do canal usado pelo terminal — ver
[terminal_ssh.md](terminal_ssh.md)), mais sensível a variação de latência do que o canal direto do
terminal. Nessa OLT o handshake ocasionalmente não completa a tempo; a falha é do timing daquele
instante, não das credenciais nem de KEX incompatível (que já tem proteção própria, ver seção
acima).

**Correção** (`clientes/views.py::realizar_backup`): o `client.connect()` agora tenta novamente
uma vez, com 3s de espera, mas **só** quando a exceção é uma dessas duas mensagens conhecidas como
transitórias (`No existing session` / `transport shut down or saw EOF`) — qualquer outro erro
(senha errada, host inacessível, etc.) continua propagando na primeira tentativa, sem retry
mascarando um problema real.

```python
_erros_transitorios = ('transport shut down or saw EOF', 'No existing session')
for _tentativa in (1, 2):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(**_connect_kwargs)
        break
    except paramiko.SSHException as _e:
        if _tentativa == 2 or not any(t in str(_e) for t in _erros_transitorios):
            raise
        time.sleep(3)
```

---

## Bug corrigido — `FileNotFoundError` ao salvar backup de acesso com "/" no tipo (2026-07-20)

**Sintoma:**
```
Erro: [Errno 2] No such file or directory: '/opt/crm/media/backups/cliente_34/acesso_441/BRAS/CGNAT/BORDA_-_JUNIPER_20260720_110811.txt'
```

**Causa:** o nome do arquivo de backup era montado com `acesso.tipo.replace(' ', '_')` — só
espaço virava `_`. O acesso 441 (cliente 34) tem `tipo = "BRAS/CGNAT/BORDA - JUNIPER"` (`/`
usado como separador hierárquico no nome cadastrado). O `/` sobrevivia à sanitização e virava
separador de diretório dentro de `os.path.join(backup_dir, nome_arquivo)` — os subdiretórios
`BRAS/CGNAT/` resultantes nunca são criados (só `backup_dir` em si é, via
`preparar_diretorio_backup`), e o `open(arquivo_path, 'w')` falhava.

**Correção** (`clientes/views.py::realizar_backup`): qualquer caractere fora de
letras/números/`-`/`_` no `tipo` agora vira `_` antes de compor o nome do arquivo:

```python
tipo_seguro = re.sub(r'[^A-Za-z0-9_-]+', '_', acesso.tipo).strip('_') or 'backup'
nome_arquivo = f"{tipo_seguro}_{timestamp}.txt"
```

Resultado para o caso acima: `BRAS_CGNAT_BORDA_-_JUNIPER_20260720_110811.txt` (nome plano, sem
criar subpastas). Afeta tanto o botão manual de backup quanto o pipeline automático (mesma
função `realizar_backup`, chamada por `clientes/tasks.py`).

---

## Raisecom ROS: shell interativo e `terminal page-break disable` — 2026-09-02

**Sintoma:** todos os switches Raisecom (RAX700/RAX711, ~10 acessos) gravavam backup marcado como
**SUCESSO** com ~790 bytes — só o cabeçalho, o separador de cada comando e uma linha `ERRO: ` sem
mensagem nenhuma. Duração típica de 130s para dois comandos:

```
============================================================
Comando: terminal length 0
============================================================
ERRO:

============================================================
Comando: show running-config
============================================================
```

**Causas (três, encadeadas):**

1. **Caminho de execução errado.** `realizar_backup` só tinha flags para Huawei, A10, Cisco, ZTE,
   Parks e MikroTik. `RAISECOM` não casa com nenhuma, então caía no `else` final
   (`_executar_comandos_sem_pty`, que usa `client.exec_command()`). O SSH do Raisecom
   (`SSH-1.99-Raisecom_ssh_1.01`) aceita o pedido de `exec` mas nunca responde: o `stdout.read()`
   fica pendurado até o timeout de 120s e estoura `socket.timeout` — cuja `str()` é **vazia**, daí
   o `ERRO: ` sem texto. Depois disso a sessão fica inutilizável e o comando seguinte volta vazio
   (confirmado ao vivo no BA-CAR-A01-SW-01). Por `invoke_shell` o mesmo equipamento responde
   normalmente e já entra no prompt privilegiado `#`, sem pedir login de novo.
2. **Comando de paginação inexistente no ROS.** `terminal length 0` responde
   `Error input in the position marked by '^'`. No Raisecom o comando é
   `terminal page-break disable` (`Set successfully`) — confirmado no `terminal ?` do equipamento.
3. **Backup vazio contava como sucesso.** A única trava era `len(output.strip()) < 100`, e só os
   cabeçalhos já passam de 100 bytes. O backup entrava no histórico como SUCESSO.

**Correções** (`clientes/views.py`):

- `is_raisecom = 'raisecom' in modelo_nome` (mesma detecção combinada
  fabricante + nome + `acesso.tipo`) e roteamento para `_executar_comandos_cisco`, junto com
  Cisco e ZTE.
- `_executar_comandos_cisco` recebe `cmd_paginacao` (default `terminal length 0`); o Raisecom passa
  `terminal page-break disable`. O laço ignora **os dois** comandos de paginação quando vierem no
  template, já que o correto foi enviado antes.
- `_backup_tem_conteudo(output)`: descarta separadores, linhas `Comando:` e linhas `ERRO:` e exige
  pelo menos 40 caracteres de saída real. Sem isso, backup vazio vira SUCESSO.
- `limpar_ansi`: o ROS ecoa `\r\r\n` no fim da linha; o `.replace('\r\n','\n').replace('\r','\n')`
  antigo transformava isso em **duas** quebras e dobrava o arquivo. Agora `re.sub(r'\r+\n', '\n', …)`
  colapsa os CR repetidos antes.

**Resultado** no BA-CAR-A01-SW-01 (DS TECH, via proxy SSH): 774 bytes em 134s → **6.349 bytes de
`show running-config` completo em 27s**.

> A trava de backup vazio vale para todos os fabricantes: acessos que vinham gravando "sucesso" só
> com cabeçalho (algumas OLTs Intelbras, Fiberhome, VSOL e Hillstone, com ~800–1.900 bytes) passam
> a registrar **ERRO** — o backup não existia, apenas não aparecia como falha.

---

## Observações Importantes

- **Telnet não é suportado**: o sistema habilita backup apenas para `protocolo='SSH'`.
  Equipamentos Telnet devem ser migrados para SSH ou configurados manualmente.

- **Sem modelo = sem backup automático**: o pipeline requer que o modelo do equipamento
  esteja cadastrado. A detecção automática (`detectar_modelos_via_backup`) tenta preencher
  o modelo a cada 3 dias para hosts que ainda não têm.

- **Idempotente**: rodar o pipeline múltiplas vezes não causa duplicatas nem
  sobrescreve configurações manuais de hosts que já tinham `backup_habilitado=True`.

- **Novos equipamentos**: ao cadastrar um novo acesso SSH, o modelo é detectado
  automaticamente no próximo ciclo de 3 dias da detecção, e o backup é habilitado
  no próximo ciclo diário.

## Porta SSH do backup (`porta_ssh`) — Adicionado em 2026-09-11

O backup é sempre por SSH. `realizar_backup` usa `Acesso.porta_ssh()`: a porta padrão quando o
protocolo padrão é SSH; senão, a do primeiro SSH cadastrado como protocolo extra do host
(`AcessoProtocolo`); sem SSH em lugar nenhum, o comportamento antigo (porta padrão, ou 22). Um host
com HTTP como padrão e SSH extra passa a fazer backup pelo SSH extra, e o log mostra
`🔑 SSH do protocolo extra: porta N`. Ver [acessos_protocolos_extras.md](acessos_protocolos_extras.md).

---

## Host desativado (`Acesso.ativo`) — Adicionado em 2026-10-13

Botão **Desativar host / Reativar host** no rodapé do modal **Editar Acesso**
(`POST /clientes/acessos/ativo/<id>/` → `alternar_ativo_acesso`, mesma permissão
de editar: `pode_alterar_acesso`). Serve para equipamento fora de operação que
não deve mais gerar tentativa de backup, sem excluir o cadastro.

Por que um campo próprio e não só desmarcar "Habilitar Backup": a rotina diária
`habilitar_backups_automaticos` religa o backup de todo host SSH com modelo e
`backup_habilitado=False`. O host desativado fica fora de:

| Ponto | Onde |
|---|---|
| Agendador (com e sem Celery) | `agendar_backups_pendentes*` filtram `ativo=True` |
| Task individual | `executar_backup_agendado` devolve `ignorado` |
| Habilitação automática / correção de template | `habilitar_backups_automaticos` |
| Detecção de modelo via backup | `detectar_modelos_via_backup` |
| Backup manual (botão do card, Backup em Massa) | `executar_backup_acesso` responde 400; botão some do card |
| Listas do Backup em Massa e "hosts sem backup" | filtram `ativo=True` |
| Alerta de erro de backup na tela do cliente | ignora desativado |

A configuração de backup (habilitado, template, automático) é preservada; ao
reativar, o host volta às rotinas como estava. No card, o host desativado
aparece apagado com o selo **Desativado**. Acesso SSH/Web ao host continua
funcionando normalmente.

---

## `[Errno 13] Permission denied` ao gravar o backup — 2026-10-13

Sintoma: backup de um host específico falha com
`Erro: [Errno 13] Permission denied: '/opt/crm/media/backups/cliente_41/acesso_1586/...rsc'`,
enquanto os demais hosts gravam normalmente.

Causa: a pasta `media/backups/cliente_X/acesso_N/` foi criada por `root` — backup
disparado à mão via `manage.py shell` logado como root. O gunicorn e o celery rodam
como `www-data` e não conseguem mais gravar ali. Em 13/10/2026 eram 8 pastas nessa
situação (criadas em 03/08, 02/09 e 10/10), todas corrigidas.

Diagnóstico e correção:

```bash
find /opt/crm/media/backups ! -user www-data
chown -R www-data:www-data /opt/crm/media/backups
```

Para testar backup real pelo shell sem criar o problema, rode como o usuário do app:

```bash
sudo -u www-data /opt/crm/venv/bin/python /opt/crm/manage.py shell
```

Não aplicar o `chown` em `media/firmware`: o dono root ali é do TFTP/FTP.


---

## MikroTik: `/export` que para de responder no meio — 2026-10-26

Sintoma: backup de MikroTik que levava ~2 s passa a levar **~182 s** e grava
`ERRO — Backup vazio: nenhum comando do template retornou configuração`. Visto em
24–26/10/2026 no "BGP - CENTRAL" (INFORTECLINE, 191.243.96.1:22002) e no "BORDA - CGNAT"
(179.191.35.128:22002), os dois CCR1036-8G-2S+ com RouterOS 6.49.18.

Causa (conferida ao vivo, só com comandos de leitura): o subsistema `/tool graphing` travou
no equipamento. Até `/tool graphing print` fica mudo para sempre. O `/export terse` manda
26 KB em 1 s (tudo até `/tool e-mail`) e emudece ali. Todas as outras seções exportam em
menos de 1 s. É bug do RouterOS, não do CRM: a correção definitiva é no equipamento
(reboot, ou desligar/reconfigurar o graphing na janela do cliente).

O CRM piorava o quadro: `_executar_comandos_mikrotik` usava `stdout.read()` com timeout de
120 s por `recv` e, no estouro, **descartava tudo que já tinha chegado**. Somava o
`show-sensitive` recusado pelo 6.x (só existe no 7.x), mais a repetição sem ele, mais o
timeout. Resultado: cerca de 3 min por equipamento e nenhum arquivo.

Correção (vale para todo MikroTik, 6.x e 7.x):

- `_exec_routeros_ate_silencio` lê o canal com **timeout de silêncio** (20 s sem nenhum byte
  = travado) e devolve o que chegou, informando se houve EOF.
- Quando um `/export` não termina, `_completar_export_travado` exporta cada seção de novo,
  sozinha (`/ip export terse`, `/user export terse`…, com 15 s de silêncio e teto de 120 s
  no total), e acrescenta só os blocos de **menus que ainda não vieram**. A comparação é
  pelo caminho completo (`/ip firewall filter`, `/user aaa`), porque o export intercala
  seções: `/user group` sai no começo e `/user aaa` só no fim. A seção onde o export parou,
  se tiver subseções conhecidas (`_ROUTEROS_SUBSECOES`, hoje `/tool`), é aberta direto
  pelas subseções. Assim `/tool romon` não se perde junto com o `/tool graphing`.
- `/user add` (que só o `/user export` traz) fica de fora, para o `.rsc` continuar
  importável.
- O que não respondeu vai para uma linha `# AVISO CRM: sem resposta do RouterOS em /tool
  graphing — essa parte ficou fora do backup` no arquivo. O `BackupLog` fica **SUCESSO**
  com a mensagem `Backup realizado com aviso: …`, porque cerca de 30 consumidores (BGP,
  IPAM, projeto de rede, Zabbix) leem só `status='SUCESSO'` e o resto da configuração
  está íntegro.

Tempos medidos (mesmo equipamento, mesmo dia):

| Equipamento | Antes | Depois | Fora do backup |
|---|---|---|---|
| BGP - CENTRAL | 182 s, ERRO (0 bytes) | 48 s, 26.9 KB | `/tool graphing interface add` |
| BORDA - CGNAT | 182 s, ERRO (0 bytes) | 44 s, 33.4 KB | `/tool graphing interface add` ×2 |

MikroTik com export saudável não muda nada: o EOF chega e nenhuma seção extra é pedida.
Testes: `clientes/tests_backup_mikrotik_travado.py`.

---

## Backup cortado no meio gravado como SUCESSO — 2026-11-04

**Sintoma:** "Switch Huawei Servidores Core CPD Sinop" (Sartor, S6730) com o último backup de 16/08
com 7 KB, contra 13 KB dos anteriores. O arquivo parava em `vlan 1001 / description
1001-br-storages`, sem interfaces, BGP nem `return`, e estava marcado SUCESSO.

**Causa 1: fim do comando por silêncio.** `_executar_comandos_huawei` e
`_executar_comandos_shell_generico` davam o comando por encerrado após 3 s sem dados. O S6730 pausa
logo depois das VLANs para montar a seção de interfaces (medido ao vivo: 1,4 s num dia, mais de 3 s
em outros). Quando a pausa passava de 3 s, metade da config era gravada.

**Causa 2: deduplicação contra qualquer backup antigo.** O hash era comparado com *qualquer* SUCESSO
do acesso. A config completa voltava, casava com o backup de 30/07 e só atualizava a
`ultima_verificacao` dele. O cortado de 16/08 seguia como "último backup" na tela e para
L2VPN/BGP/IPAM.

**Correção:**

- `_ler_ate_prompt(channel, …)` lê até o **prompt** voltar (`<SW>`, `[~SW]`, `OLT>`,
  `MA5800(config)#`, `OLT-NBR#` — `_RE_PROMPT_CLI`). O silêncio só encerra junto com o prompt. Sem
  prompt, continua até 90 s seguidos sem nenhum byte (teto de 900 s) e devolve `completo=False`. Ele
  também segue o `---- More ----` (1,5 s de espera após cada espaço). Hoje é usado pelo Huawei e pelo
  shell genérico (FiberHome). Cisco/ZTE/VSOL/MikroTik não mudaram: na varredura não havia corte
  neles.
- Comando sem prompt grava `MARCA_SAIDA_INCOMPLETA` na saída. `_motivo_backup_incompleto` também
  exige `return` no fim do `display current-configuration` do Huawei (VRP sempre termina assim; 155
  de 155 backups Huawei bons tinham). Qualquer um dos dois → `realizar_backup` registra **ERRO
  "Backup incompleto: …"**. Nunca grava SUCESSO e não substitui o último backup bom.
- A deduplicação compara só com o **último** SUCESSO. A → B → A grava um arquivo novo.

**Hosts afetados encontrados na varredura (último backup cortado):** 1058 Switch Huawei Servidores
Core CPD Sinop, 266 SW-BRAVIN (GREEN), 722 SW-HU-CORE (Friends), 231 e 890 SW Huawei Villaggio
Business, 753 OLT CORE SORRISO 01 (Alta Rádio), e as FiberHome 1729 OLT FB COLNIZA e 1132 OLT-NBR
(`show startup-config` vazio). Testes: `clientes/tests_backup_incompleto.py`.

**Complemento (mesmo dia): FiberHome "system busy".** Ao regerar, a OLT FB COLNIZA respondeu
`system busy,please try later. / Command executes failed.` ao `show startup-config`, e esse 1 KB era
gravado como SUCESSO. Isso valia para 15 FiberHome, várias assim desde 14/10. Agora o shell genérico
espera 20 s e repete o comando até 3 vezes. Se continuar ocupada, `_motivo_backup_incompleto`
(`_RE_CLI_OCUPADA`) recusa o backup como ERRO.

---

## Backup só com erro da CLI gravado como SUCESSO — 2026-11-04

A varredura de 741 backups achou **54 hosts** cujo último "SUCESSO" não tinha configuração, só a
recusa do equipamento. Exemplos: `%% Invalid command`, `bad command name show`, `Bad UserName or Bad
Password`, `% Unknown command.`, `Only tacacs authenticated users…`. Somando erro, prompt e banner, a
saída passava dos 40 caracteres de `_backup_tem_conteudo`.

**Correção na checagem:**

- `_backup_tem_conteudo` descarta as linhas que casam com `_RE_LINHA_ERRO_CLI` e o prompt sozinho
  (`_RE_PROMPT_CLI`) antes de contar. Na varredura, 40 backups passaram a ser recusados e nenhum com
  config real (a menor config legítima, um MikroTik, tem 690 caracteres úteis).
- `_motivo_backup_incompleto` dá o motivo legível de usuário sem privilégio (`_RE_SEM_PRIVILEGIO`).
  Exemplos: FiberHome com TACACS, `enable` → "Only tacacs authenticated users can use this command"
  ou "you do not have improve the privilege". Isso se resolve no equipamento/TACACS, não no CRM.
- O retry de 20 s do shell genérico vale só para `system busy`. `Command executes failed` sozinho
  não é ocupação.

**Fluxos novos, pelo nome do template:**

| Template | Equipamento | Roteiro |
|---|---|---|
| `Backup OLT Intelbras G16` | Intelbras G16 | executor VSOL (`g16` no nome): senha de novo no shell, `enable`, `show running-config` |
| `Backup Nokia ISAM` | Nokia ISAM FX-4 | shell genérico: `environment inhibit-alarms mode batch`, `info configure flat` (2 MB, minutos, pausas) |
| `Backup OLT TP-Link` | OLT TP-Link | shell genérico: `enable`, `show current-config` (não existe `show running-config`) |

O shell genérico também segue `--More--` (`_RE_MORE_VSOL`). O prompt do ISAM (`typ:isadmin>#`,
`typ:isadmin>environment#`) entrou no `_RE_PROMPT_CLI`.

**Ficaram sem solução no CRM (backup agora aparece como ERRO, com o motivo):**

- Intelbras 8820i: não tem `show running-config`/`enable`. Só `backup file`/`backup network`, que
  exportam por arquivo e exigem outro fluxo.
- FiberHome com usuário sem privilégio (Sartor: JUARA, TIBAJI, ITANHANGA, NOVA HORIZONTE, PORTO DOS
  GAUCHOS, TAPURAH, FONTANILHAS, JAPURANA, COTRIGUACU, AMERICANA; Infoline: TRVD, STED, SHPG…).
- FiberHome JUINA: `cli not have enough memory` (memória da OLT).
- Rádios Mimosa com template A10: sem CLI de configuração.
- Senha/SSH: OLT VSOL NOVO HORIZONTE (senha), Intelbras 4840E (`No existing session`).

**Complemento: keepalive e paginação TP-Link.**

- A OLT CORE SORRISO 01 (MA5800) derrubava a sessão quando recebia o keepalive SSH de 10 s do
  paramiko no meio do `display current-configuration`. Com keepalive, cortava em 137 KB; sem ele,
  vinham os 494 KB (testado ao vivo). Agora o `realizar_backup` não liga o keepalive quando o
  fabricante é Huawei e o template tem "olt" no nome.
- O shell genérico segue a paginação por `_RE_MORE_GENERICO`. Ela cobre `--More--`, `---- More ----`
  e o `--More ( Press 'Q' to quit )--` da OLT TP-Link, que vem seguido de um byte `\x00`.

---

## Alerta "Erro de Backup" preso num erro antigo — 2026-11-04

O alerta do painel do cliente (`clientes/views.py`, `acessos_com_erro_backup`) olhava só o último
`BackupLog` do host. Quando o backup seguinte dá "sem mudanças", não nasce registro novo: só a
`ultima_verificacao` do backup bom é atualizada. Assim, uma queda de um dia (o proxy
`tunnel_zabbix-startnet`, 24.152.31.3:666, ficou fora em 02/11) deixava o erro "Unable to connect to
port 666" no alerta para sempre. Foram 20 hosts da Startnet, todos com backup verificado em 04/11.

Agora o erro só aparece se nenhum SUCESSO do host tiver `ultima_verificacao` mais nova que ele.

Na mesma lista havia 7 hosts novos, sem backup nenhum:

- SW-ALENITA, SW-AGROVILA e CRS-OURO-VERDE (MikroTik CRS309): o SSH está na porta 65111, o padrão
  da Startnet, e eles estavam cadastrados na 22. Cadastro corrigido.
- SW-ONF-BRASIL: é um CSS610 (SwOS), sem SSH. Não dá para fazer backup por CLI.
- OLT-VSOL-AGROVILA: só Telnet (porta 23). O SSH aceita a conexão e fecha sem banner, e o backup do
  CRM é só por SSH.
- GROVE-CLEANDRO e BRAS_PREFEITURA BRASNORTE: não respondem em nenhuma porta, nem pelo proxy nem pelo
  servidor.


---

## FiberHome com a CLI sem memória (OLT FB JUINA) — 2026-11-05

A 1724 OLT FB JUINA (Sartor, AN5516-06, V104R002, uma só controladora HSWA no slot 9) nunca teve
backup bom: dava "Backup vazio" (e uma vez "Command executes failed"). Testado ao vivo pelo proxy: o
login e o `cd service` / `terminal length 0` funcionam, mas o `show startup-config` responde

```
vty out fail, cli not have enough memory!
vty out fail! cli not have enough memory
Command executes failed.
```

Acontece em toda sessão, não é ocupação momentânea. O mesmo `show startup-config` funcionava nas
outras FiberHome da Sartor.

**Solução: modo debug.** O `show running-config` do modo debug (`lll`, prompt `HOST(DEBUG_H)>`)
devolve a config completa, terminando em `!end  of config`: 2,3 MB na JUINA, em cerca de 5 min.
Fora do debug, `show running-config` só aceita `slot` e responde "Command incomplete". O
`terminal length 0` precisa vir **antes** do `lll`. Dentro do debug ele não existe, e sem ele a OLT
pagina com `--Press any key to continue Ctrl+c to stop--`, que o shell genérico não reconhece.

O template **"Backup fiberhome"** (id 15, todos os 43 acessos) passou a ser:

```
cd service
terminal length 0
cd ..
lll
show running-config
```

Validado ao vivo antes da troca em JDCA, COTRI, NORDESTINA (VSOL), VILA-VELHA e AEXPNET, com o
mesmo tamanho do backup anterior. JAPURANA, SHPG e JUARA continuam em ERRO exatamente como antes
(`Command executes failed`, sem privilégio). O modo debug tem `reboot`, `recover`, `reset` e
`delete`. O template só usa `show`.

Independente disso, `_RE_CLI_SEM_MEMORIA` faz `_motivo_backup_incompleto` dar esse motivo, e o "Backup vazio"
agora diz por que o equipamento recusou (sem memória, ocupado, sem privilégio) em vez da frase
genérica.

### Série 6k: template "Backup fiberhome 6k" (mesmo dia)

As OLTs FiberHome da série 6k (versões RP1300 a RP1600) não têm `cd service` nem `lll`. Com o
template AN5516, davam `Command executes failed` ou "prompt não voltou depois de 'cd service'". O
roteiro delas, indicado pelo usuário e validado ao vivo, é:

```
config
terminal length 0
show startup-config
```

O prompt fica `HOST(config)#` e a config termina em `!end  of config`, seguida de `!version:RPxxxx`.
O template "Backup fiberhome 6k" foi vinculado a JAPURANA, JUARA, ITANHANGA, COTRIGUACU, TAPURAH,
NOVA HORIZONTE, PORTO DOS GAUCHOS, FONTANILHAS, AMERICANA e TIBAJI (Sartor). Os 10 backups reais
deram SUCESSO, de 190 a 820 KB, cerca de 20 s cada.

Para saber a série, use o `show version`. AN5516 mostra `system device version is:V104R002` e
controladora HSWA. Nela, `config` não muda o prompt e `terminal length 0` dá "Unknown command" fora
do `cd service`. A ARIPUANA estava na lista de erro, mas é AN5516 e ficou no "Backup fiberhome".

**Paginação "Press any key".** Sem `terminal length 0`, a FiberHome pagina com
`--Press any key to continue Ctrl+c to stop--`. `_RE_MORE_GENERICO` agora reconhece essa paginação
e responde com espaço, e `_RE_MORE_GENERICO_LINHA` limpa a linha do arquivo. Seguir página por
página é lento: uma config de 1,6 MB passa dos 900 s de `max_wait`. Por isso o template certo
continua sendo o que desliga a paginação.

**Código:** template com `fiberhome` no nome sempre usa `_executar_comandos_shell_generico`.
Antes, o template 6k não tinha `cd`, então cada comando ia num `exec_command` separado e o
`terminal length 0` se perdia. Além disso, 5 dessas OLTs estão cadastradas com modelo "OLT HUAWEI
MA5800X7" e iam para o executor Huawei. Testes: `clientes/tests_backup_fiberhome.py`.

Ficaram de fora por outros motivos:
- AGUA DA PRATA: "Authentication failed", senha do cadastro.
- JUARA e ITANHANGA: estão com o mesmo IP (198.18.103.218) e o backup das duas sai igual
  (`OLT-JUARA`). O IP da ITANHANGA no cadastro deve estar errado.
