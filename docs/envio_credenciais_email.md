# Envio Periódico de Credenciais por E-mail

**Arquivos:**
- `clientes/tasks.py` — funções `_gerar_pdf_todos_acessos` e `enviar_pdf_credenciais`
- `crm/celery.py` — agendamento Celery Beat

**Adicionado em:** 2026-05-27

---

## Visão Geral

A cada **2 dias**, o sistema gera automaticamente um PDF com todas as credenciais de acesso
de todos os clientes e envia para uma lista de destinatários configurados.

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
| Arquivo anexo | `credenciais_AAAAMMDD.pdf` |

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

- O PDF contém senhas em texto plano — enviado apenas para os e-mails listados em `DESTINATARIOS_SENHAS`
- O e-mail inclui aviso: *"Documento confidencial — não repasse este arquivo"*
- A task não expõe nenhum endpoint HTTP, só é acionável via Celery Beat ou shell
