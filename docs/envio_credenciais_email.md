# Envio Periódico de Credenciais por E-mail

**Arquivos:**
- `clientes/tasks.py` — funções `_gerar_pdf_todos_acessos`, `_gerar_excel_todos_acessos` e `enviar_pdf_credenciais`
- `clientes/views.py` — `importar_acessos_excel` (restaura a partir da planilha)
- `crm/celery.py` — agendamento Celery Beat

**Adicionado em:** 2026-05-27

---

## Visão Geral

A cada **2 dias**, o sistema gera automaticamente um PDF e uma planilha Excel com todas as
credenciais de acesso de todos os clientes e envia os dois anexos para uma lista de
destinatários configurados. O PDF é para leitura; o Excel (desde 26/10/2026) é para
**restaurar** os acessos pelo "Importar Excel" caso algo se perca.

---

## Destinatários

```python
DESTINATARIOS_SENHAS = [
    'campelosuporte.ti@gmail.com',
    'noc@tomich.com.br',
    'danilo@tomich.com.br',
]
```

Para alterar a lista, editar a constante `DESTINATARIOS_SENHAS` em `clientes/tasks.py`.

---

## Conteúdo do PDF

- **Orientação:** A4 paisagem
- **Organização:** Um bloco por cliente (nome + CNPJ), ordenado alfabeticamente
- **Colunas por cliente:** Descrição, Host, Protocolo, Porta, Usuário, Senha, Senha Root, Função
- **Clientes sem acessos** são omitidos automaticamente

---

## Conteúdo do Excel (`credenciais_AAAAMMDD.xlsx`)

| Aba | Conteúdo |
|---|---|
| `Todos` | Tudo numa tabela só: Cliente, CNPJ + as colunas abaixo (consulta/filtro) |
| Uma aba por cliente | Layout do template de importação: linha 1 legenda, 2 cabeçalho, 3 vazia, dados a partir da 4 |
| `_Clientes` (oculta) | Índice aba → Cliente ID, CNPJ, nome |

Colunas das abas de cliente (A–K iguais ao template de importação, L–M extras):
Nome do Host (tipo), IP / Hostname, Protocolo, Porta, Usuário, Senha, Senha ADM, VLAN, Função,
Modelo (`Fabricante — Nome`), Notas Agent NOC, IPv6, Porta WinBox.

Todas as células de texto são gravadas como texto: senha que começa com `=` não vira fórmula
(sumiria na importação) e `0123` não vira número.

Se a geração do Excel falhar, o e-mail sai só com o PDF (erro no log).

### Restaurando

Na tela do cliente → Acessos → **Importar Excel**, enviar o arquivo do e-mail inteiro. O
importador vê a aba oculta `_Clientes` e escolhe sozinho a aba do cliente: por ID + nome, depois
CNPJ, depois nome (ID/CNPJ podem mudar se o banco for recriado). Sem aba do cliente → erro
"A planilha não tem uma aba para …".

Regras do importador que valem para a restauração:
- Protocolo mantém a grafia do banco (`Telnet`, `TELNET`, `RDP`…); digitado à mão vira a grafia do
  formulário. RDP passou a ser aceito (antes virava SSH).
- Só pula o acesso que já existe igual (nome, host, porta, protocolo, usuário e senha, ignorando
  espaços nas pontas). Antes pulava qualquer acesso com o mesmo nome — o banco tem nomes repetidos
  com host/credencial diferentes. Reimportar a mesma planilha não duplica nada.
- Senha e Senha ADM vão exatamente como estão (espaço no começo/fim é mantido); nome, host e
  usuário são aparados.

Validado em 26/10/2026 contra o banco: apagando e reimportando cada um dos 42 clientes, os 1530
acessos voltaram idênticos (fora espaços sobrando em nome/host/usuário).

---

## Agendamento

```python
# crm/celery.py
'enviar-pdf-credenciais': {
    'task': 'clientes.tasks.enviar_pdf_credenciais',
    'schedule': timedelta(days=2),
},
```

---

## Task `enviar_pdf_credenciais`

```python
@shared_task(bind=True)
def enviar_pdf_credenciais(self):
    pdf_bytes = _gerar_pdf_todos_acessos()
    # envia para cada destinatário via SMTP configurado em ConfiguracaoSistema
```

### SMTP

Usa a mesma configuração SMTP do sistema (`ConfiguracaoSistema.get()`):
- `smtp_host`, `smtp_port`, `smtp_user`, `smtp_pass`, `smtp_from`, `smtp_use_tls`
- Fallback para `settings.EMAIL_HOST` / `settings.EMAIL_HOST_PASSWORD` se não configurado no banco

Se o SMTP não estiver configurado, a task loga o erro e retorna sem enviar.

### Assunto e nome do arquivo

| Campo | Valor |
|---|---|
| Assunto | `[CRM] Relatório de Credenciais — DD/MM/AAAA` |
| Arquivos anexos | `credenciais_AAAAMMDD.pdf` e `credenciais_AAAAMMDD.xlsx` |

---

## Execução Manual (para teste)

```python
# Via Django shell
from clientes.tasks import enviar_pdf_credenciais
result = enviar_pdf_credenciais()
# {'enviados': 3, 'erros': []}
```

Ou via Celery:
```bash
celery -A crm call clientes.tasks.enviar_pdf_credenciais
```

---

## Segurança

- O PDF e o Excel contêm senhas em texto plano — enviado apenas para os e-mails listados em `DESTINATARIOS_SENHAS`
- O e-mail inclui aviso: *"Documento confidencial — não repasse este arquivo"*
- A task não expõe nenhum endpoint HTTP, só é acionável via Celery Beat ou shell
