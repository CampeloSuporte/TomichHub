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
| 11 | Backup Genérico | GENERICO | Fallback para fabricantes não mapeados |

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
