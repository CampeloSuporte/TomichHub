# Wiki de Artigos Técnicos — Documentação Técnica

**Data de Implementação:** 2026-07-20
**Arquivos principais:** `wiki/models.py`, `wiki/views.py`, `wiki/admin.py`, `wiki/urls.py`,
`wiki/templates/wiki/*.html`, `clientes/templates/terminal.html`
**Status:** ✅ Produção

---

## Visão Geral

Base de artigos técnicos (procedimentos, troubleshooting, configs por fabricante) consultável
tanto pelo dashboard próprio (`/wiki/`) quanto de dentro do Terminal SSH, via painel de busca
lateral — útil para consultar um procedimento sem sair da sessão ativa no equipamento.

Esta rodada de mudanças: anexo de PDF por artigo, simplificação da busca/listagem (templates
unificados) e registro completo no Django Admin (antes inexistente).

---

## PDF Anexado ao Artigo

Novo campo `ArtigoWiki.pdf` (`FileField`, `upload_to='wiki/pdfs/%Y/%m/'`, opcional). Ao visualizar
um artigo que tem PDF, ele abre automaticamente (painel com zoom — `wikiPdfZoomIn/Out/Reset` em
`terminal.html`, 50%–400%).

- **Upload/troca** (`cadastrar_artigo`/`editar_artigo` em `wiki/views.py`): valida extensão
  `.pdf` no nome do arquivo (fora isso, ignora com aviso via `messages.warning`); trocar o PDF
  deleta o anterior do storage antes de salvar o novo (`artigo.pdf.delete(save=False)`).
- **Remoção**: campo hidden `remover_pdf=1` no form de edição (`wed-pdf-rm` → `WikiEditor.removePdf()`
  no JS) — o backend deleta o arquivo do storage e limpa o campo.
- **API do terminal** (`api_visualizar_artigo`): retorna `pdf_url` e `pdf_nome` (via property
  `ArtigoWiki.pdf_nome`, que extrai só o basename do path armazenado) para o painel do terminal
  montar o visualizador inline.
- **Indicador visual**: ícone de PDF vermelho ao lado do artigo em `dashboard.html` quando
  `artigo.pdf` existe.

---

## Busca e Listagem — Unificação de Templates

Antes: três templates quase idênticos (`listar_categoria.html`, `listar_tag.html`,
`listar_fabricante.html`), cada um renderizado por uma view diferente
(`listar_por_categoria`/`listar_por_tag`/`listar_por_fabricante`). Agora as três views renderizam
o mesmo `wiki/templates/wiki/listar_artigos.html`, passando apenas o que muda entre elas:

```python
return render(request, 'wiki/listar_artigos.html', {
    'artigos': artigos,
    'page_title': categoria.nome,      # ou f'Tag: {tag.nome}' / f'Fabricante: {nome}'
    'page_icon': categoria.icone,      # ou 'fa-tag' / 'fa-microchip'
    'empty_msg': 'Nenhum artigo encontrado nesta categoria ainda.',
})
```

Os três templates antigos (mais `_search_delete_styles.html`, órfão) foram removidos — nada mais
os referenciava.

Nova página `wiki/templates/wiki/buscar.html` (view `buscar_wiki`) ganhou filtro por categoria
(`categoria_id`) além dos já existentes (`query`, `fabricante`); `api_buscar_wiki` (usada pelo
painel do terminal) passou a buscar também no `conteudo` do artigo (`Q(conteudo__icontains=query)`),
não só título/descrição/tags, e retorna `pdf: bool(a.pdf)` por artigo pro terminal mostrar o ícone.

Criação de categoria via AJAX (`cadastrar_categoria_ajax`) agora deduplica também pelo slug que o
nome geraria (`slugify(nome)`), não só por `nome__iexact` — evita duas categorias com nomes
diferentes que colidiriam no mesmo slug (ex: "Rede" e "rede!").

---

## Remoção — Blocos de Código como CRUD Separado

Views `adicionar_bloco_codigo` / `editar_bloco_codigo` / `deletar_bloco_codigo` e as respectivas
rotas em `wiki/urls.py` foram removidas — o fluxo de blocos de código passou a ser só leitura via
`api_visualizar_artigo` (campo `blocos_codigo`), sem telas próprias de CRUD.

---

## Admin (`wiki/admin.py`)

Antes vazio (`# Register your models here.`). Agora registra `CategoriaWiki`, `TagWiki` e
`ArtigoWiki`:

- `ArtigoWikiAdmin`: `list_display`/`list_filter` por categoria/fabricante/favorito/destaque/ativo,
  busca por título/descrição/conteúdo/modelo, `prepopulated_fields` do slug, inlines de
  `BlocoCodigoWiki` e `AnexoWiki`. `save_model` preenche `criado_por` (só na criação) e
  `atualizado_por` (sempre) a partir do `request.user`.
- `CategoriaWikiAdmin`/`TagWikiAdmin`: slug pré-populado a partir do nome; `TagWiki` com
  `autocomplete_fields` habilitado (usado no `ArtigoWikiAdmin.tags`).

---

## Correção — mesmo crash latente em `criado_por` (2026-08-06)

`ArtigoWiki.criado_por` é `ForeignKey(User, on_delete=models.SET_NULL, null=True)`, então um
artigo cujo autor foi excluído tem `criado_por=None`. `visualizar_artigo.html` fazia:

```django
Criado por {{ artigo.criado_por.get_full_name|default:artigo.criado_por.username }}
```

que quebra com `VariableDoesNotExist` quando `criado_por` é `None` — mesma armadilha do Django
encontrada e corrigida em [TAREFAS.md](TAREFAS.md#correção--variabledoesnotexist-em-homegeral-com-tarefa-sem-responsável-2026-08-06)
(`t.assigned_to.username` como argumento de `|default:` não tem o lookup falho suprimido, só a
variável principal tem). Corrigido com guard:

```django
{% if artigo.criado_por %}{{ artigo.criado_por.get_full_name|default:artigo.criado_por.username }}{% else %}—{% endif %}
```

---

**Última atualização:** 06/08/2026
**Autor:** CampeloSuporte
