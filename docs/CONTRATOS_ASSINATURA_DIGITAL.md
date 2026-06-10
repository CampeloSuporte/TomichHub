# Contratos de Aluguel com Assinatura Digital

## Visão Geral

Sistema completo de geração, envio e assinatura digital de contratos de aluguel de blocos IPv4/IPv6. Permite que o cliente assine remotamente via link único, e que o locador mantenha uma assinatura permanente aplicada automaticamente em todos os contratos.

**Data de Implementação:** 10/06/2026  
**Módulo:** `financeiro/`  
**Status:** ✅ Produção

---

## Funcionalidades

### 1. Assinatura do Locador (Permanente)
- Salva a assinatura digital da Campelo Suporte uma única vez
- Aplicada automaticamente na coluna "Locador" de todos os contratos gerados
- Interface com canvas de desenho no dashboard financeiro (aba Aluguéis)

### 2. Geração de Contrato PDF
- Gera PDF A4 com texto completo do contrato de aluguel
- Inclui dados do cliente (nome, CNPJ, endereço) e do bloco (IPv4 e/ou IPv6)
- Colunas de assinatura: Locatário (cliente) e Locador (Campelo Suporte)
- Assinatura do locador inserida automaticamente se cadastrada

### 3. Link de Assinatura Digital
- Gera URL única com UUID para o cliente assinar remotamente
- Link com validade configurável (padrão: sem expiração, pode ser definida)
- Página pública acessível sem login
- Canvas de assinatura responsivo (mouse + touch)
- Exporta assinatura com fundo branco (evita problema de transparência no PDF)

### 4. Envio por E-mail
- Ao gerar o link, envia e-mail ao cliente com o link de assinatura
- Usa e-mail cadastrado no perfil do cliente

### 5. PDF Assinado
- Após o cliente assinar, gera PDF com ambas assinaturas (locador + cliente)
- PDF salvo em `media/contratos/`
- Botão "PDF Assinado" aparece diretamente na linha do aluguel na listagem

---

## Modelos

### `ContratoAluguel`
```python
class ContratoAluguel(models.Model):
    aluguel     = ForeignKey(AluguelIPv4, on_delete=CASCADE)
    token       = UUIDField(unique=True)          # URL único de assinatura
    status      = CharField(choices=['pendente', 'assinado', 'cancelado'])
    nome_assinante   = CharField(blank=True)       # Nome digitado pelo cliente
    assinatura_data  = TextField(blank=True)        # Base64 PNG da assinatura cliente
    assinado_em      = DateTimeField(null=True)
    pdf_assinado     = FileField(upload_to='contratos/', null=True)
    expira_em        = DateTimeField(null=True)
    criado_em        = DateTimeField(auto_now_add=True)
```

### `ConfiguracaoFinanceira` (campo adicionado)
```python
assinatura_locador = TextField(blank=True, default='')
# Armazena Base64 PNG da assinatura da Campelo Suporte
```

---

## API Endpoints

| Método | Endpoint | Descrição | Auth |
|--------|----------|-----------|------|
| GET | `/financeiro/api/aluguel/{id}/contrato/` | Gera PDF do contrato | Login |
| POST | `/financeiro/api/aluguel/{id}/link-assinatura/` | Gera link para cliente assinar | Login |
| GET | `/financeiro/api/aluguel/{id}/contratos/` | Lista contratos do aluguel | Login |
| GET | `/financeiro/contrato/{token}/assinar/` | Página de assinatura (pública) | Pública |
| POST | `/financeiro/contrato/{token}/confirmar/` | Recebe assinatura e gera PDF | Pública |
| GET | `/financeiro/contrato/{token}/download/` | Download do PDF assinado | Pública |
| GET/POST | `/financeiro/api/assinatura-locador/` | Lê/salva assinatura do locador | Login |

---

## Fluxo de Uso

```
1. Admin acessa aba Aluguéis no dashboard financeiro
2. Clica "Minha Assinatura" → desenha no canvas → salva
   └─ POST /financeiro/api/assinatura-locador/ (salva em ConfiguracaoFinanceira)

3. Para cada aluguel, clica "Gerar Link de Assinatura"
   └─ POST /financeiro/api/aluguel/{id}/link-assinatura/
   └─ Cria ContratoAluguel com token UUID único
   └─ Envia e-mail ao cliente com o link

4. Cliente recebe e-mail, acessa o link
   └─ GET /financeiro/contrato/{token}/assinar/
   └─ Lê o contrato completo
   └─ Digita nome e desenha assinatura no canvas
   └─ Clica "Confirmar Assinatura"

5. Sistema gera PDF com assinaturas
   └─ POST /financeiro/contrato/{token}/confirmar/
   └─ PIL: composite assinatura RGBA em fundo branco → RGB → ReportLab
   └─ PDF salvo em media/contratos/
   └─ ContratoAluguel.status = 'assinado'

6. Admin vê botão "PDF Assinado" na linha do aluguel
   └─ Clica para baixar o PDF com as duas assinaturas
```

---

## Detalhes Técnicos

### Problema de Transparência no PDF (Resolvido)
Canvas HTML5 exporta PNG com fundo transparente (RGBA). ReportLab converte pixels transparentes para **preto**, tornando a assinatura escura invisível contra fundo preto.

**Solução aplicada em dois pontos:**

**1. Template `contrato_assinar.html` (frontend):**
```javascript
const assinatura = (() => {
    const tmp = document.createElement('canvas');
    tmp.width = canvas.width;
    tmp.height = canvas.height;
    const tc = tmp.getContext('2d');
    tc.fillStyle = '#ffffff';        // fundo branco antes de copiar
    tc.fillRect(0, 0, tmp.width, tmp.height);
    tc.drawImage(canvas, 0, 0);
    return tmp.toDataURL('image/png');
})();
```

**2. View `confirmar_assinatura` (backend):**
```python
from PIL import Image as _PILImage
_img_pil = _PILImage.open(BytesIO(img_bytes))
if _img_pil.mode in ('RGBA', 'LA', 'P'):
    _img_pil = _img_pil.convert('RGBA')
    _bg = _PILImage.new('RGBA', _img_pil.size, (255, 255, 255, 255))
    _bg.paste(_img_pil, mask=_img_pil.split()[3])
    _img_pil = _bg.convert('RGB')
img_buffer = BytesIO()
_img_pil.save(img_buffer, format='PNG')
```

### Geração do PDF (ReportLab)
- Formato A4
- Texto completo do contrato com cláusulas
- Tabela de assinaturas 2 colunas (Locatário | Locador)
- Assinatura do locador buscada de `ConfiguracaoFinanceira.assinatura_locador`
- Assinatura do cliente do campo `ContratoAluguel.assinatura_data`

---

## Arquivos Relevantes

| Arquivo | Descrição |
|---------|-----------|
| `financeiro/views.py` | Views: `gerar_contrato_aluguel`, `gerar_link_assinatura`, `assinar_contrato`, `confirmar_assinatura`, `assinatura_locador` |
| `financeiro/models.py` | Modelos `ContratoAluguel` e campo `assinatura_locador` em `ConfiguracaoFinanceira` |
| `financeiro/templates/financeiro/contrato_assinar.html` | Página pública de assinatura (canvas + formulário) |
| `financeiro/templates/financeiro/dashboard.html` | Modal de assinatura do locador + botão "PDF Assinado" |
| `financeiro/migrations/0013_configuracaofinanceira_assinatura_locador.py` | Migration do campo |
| `media/contratos/` | PDFs assinados armazenados |

---

## Problemas Conhecidos / Resolvidos

| Problema | Causa | Solução |
|----------|-------|---------|
| Assinatura invisível no PDF | Canvas transparente → ReportLab fundo preto | Composite PIL em fundo branco (frontend + backend) |
| `NameError: HttpResponse` | Import faltando na view | Adicionado import no topo |
| Link com token expirado | Token sem validade definida | Validade opcional; verificar `expira_em` na view |
