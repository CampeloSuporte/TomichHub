# Documentos do cliente — pré-visualização em modal

Na aba **Documentos** da tela do cliente, o ícone de olho (ou o nome do arquivo) abre o
documento num modal, sem sair da página. Ctrl/⌘+clique e o botão do meio continuam abrindo
o arquivo em nova aba. No modal há também **Abrir em nova aba** e **Download**.

## Como funciona

- `GET /clientes/documento/<id>/preview/` (`views.preview_documento`) devolve um JSON.
  Exige login, o módulo `documentos` habilitado e `perms.pode_acessar_cliente` sobre o
  cliente do documento (403 se não puder).
- A conversão vive em `clientes/documento_preview.py` (`montar_preview`). O servidor **não
  tem LibreOffice**, então tudo é lido em Python com o que já está no venv.

| Tipo devolvido | Formatos | Como aparece |
|---|---|---|
| `imagem` | png, jpg, gif, webp, bmp, svg, ico, avif | `<img>` (clique alterna zoom 100%) |
| `pdf` | pdf | `<iframe>` com o leitor do navegador |
| `video` / `audio` | mp4, webm, mov… / mp3, wav, ogg, m4a… | player nativo |
| `html` | xlsx/xlsm (openpyxl, uma aba por planilha), docx (python-docx: títulos, negrito, listas, tabelas), pptx (texto por slide), odt/ods/odp, csv/tsv (tabela), md (Markdown), zip/tar (lista de arquivos) | HTML montado no servidor, sempre com `html.escape` |
| `iframe_html` | html/htm | `<iframe sandbox srcdoc>` — sem script, sem formulário |
| `texto` | txt, log, conf, rsc, json, xml, py, sh, yaml, cfg… e **qualquer** arquivo sem extensão conhecida que pareça texto | `<pre>` via `textContent` |
| `nao_suportado` | doc/xls/ppt (binários 97-2003), executáveis e outros binários | aviso + botão de baixar |

Limites: texto lê até 2 MB; planilhas até 1000 linhas × 60 colunas por aba; zip/office
acima de 30 MB não são abertos. Quando algo é cortado, o modal mostra uma faixa de aviso.

Texto que não é UTF-8 cai em cp1252 (arquivo salvo no Windows em português) e depois latin-1.

## Segurança

- Nada vindo do arquivo entra no DOM sem escape: texto vai por `textContent`; HTML de
  planilha/Word é gerado com `html.escape`; Markdown tem a fonte escapada **antes** de
  renderizar (tag HTML crua aparece como texto).
- Arquivos `.html` enviados como documento rodam num iframe `sandbox` vazio (origem opaca,
  scripts bloqueados).

## Para abrir .doc / .xls / .ppt antigos

Seria preciso instalar o LibreOffice no servidor (`soffice --headless --convert-to pdf`) e
devolver `pdf`. Hoje o modal orienta a salvar como .docx/.xlsx/.pptx ou baixar.
