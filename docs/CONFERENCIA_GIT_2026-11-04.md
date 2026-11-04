# Conferência do Git — 04/11/2026

Levantamento do que está no `main`, no GitHub e do que ficou parado em branches/worktrees.
Nada foi mesclado nem descartado nesta conferência: só registrado.

## 1. `main` × GitHub — em dia

| | Commit |
|---|---|
| `main` local (`/opt/crm`) | `b30c900db` merge: mencao recebida mostra o nome |
| `origin/main` (GitHub `CampeloSuporte/TomichHub`) | `b30c900db` — idêntico |

- Nenhum commit local fora do GitHub (`git log origin/main..main` vazio).
- Nenhuma alteração não commitada no `/opt/crm`, exceto `vsi.png` na raiz (arquivo solto, sem
  relação com código; não versionado de propósito).
- O push das 13:xx funcionou; no fim do dia o token voltou a ser recusado
  ("Invalid username or token"). Próximo push precisa de `gh auth login`.

### Alterações publicadas hoje (04/11/2026)

| Commit | O quê | Documentação |
|---|---|---|
| `744c59c12` / `b30c900db` | Menção recebida no grupo mostra o nome (`@Alex`) em vez do `@lid` (DIGICONN #2043) | `CHANGELOG.md`, `docs/ATENDIMENTO.md` (Histórico) |
| `268f73cce` / `933a20e4d` | Disjuntor do Zabbix (CRM lento com Zabbix de cliente oscilando) | `CHANGELOG.md`, `docs/ZABBIX_VINCULO_ACESSOS.md` |
| `7fbd0aa31` / `8774c3020` | Túnel do proxy pede gzip (iDRAC9 dava 404 nos assets) | `CHANGELOG.md`, `docs/proxy_web_acessos.md` |

Dados corrigidos direto no banco: 3 mensagens antigas do grupo DIGICONN com menção numérica
(#1467 `@Tomich Tecnologia`; #2043 `@Alex` e `@+55 27 92001-0085`). Os demais grupos não foram
reprocessados — só as mensagens novas já chegam com nome.

## 2. Branches com commits fora do `main`

Para cada commit, conferido quanto das linhas adicionadas existe hoje no `main` (o diff bruto é
enganoso porque o `main` andou muito desde então).

| Branch | Data | Situação |
|---|---|---|
| `claude/404-acesso-update-error-b6df83` | 18/08 | ✅ Conteúdo já está no `main` (páginas de erro próprias, SPA do proxy). Pode apagar. |
| `claude/crm-proxy-access-fixes-827bcf` | 18/08 | ✅ Código já está no `main` (proxy, 2FA, backups, Kanban de tarefas); só o texto antigo dos docs difere, e os docs foram reescritos depois. Pode apagar. |
| `claude/geofeed-update-error-3679ba` | 30/07 | ✅ Equivalente já aplicado (`git cherry` marca `-`). Pode apagar. |
| `claude/irr-route-update-query-67d35d` | 10/08 | ✅ Código (descr/member-of por rota, migração 0103) está no `main`; só o texto do doc é o antigo — `docs/IRR_ATUALIZACAO_TC.md` foi reescrito para o envio por API. Pode apagar. |
| `claude/consultant-user-delete-permission-eed128` | 18/08 | ⚠️ **Não está no `main`.** Botão/rota `deletar_usuario` (excluir usuário, Consultor limitado à própria Instância). Hoje não existe exclusão de usuário pela tela. Decidir se entra. |
| `claude/ipinfo-geolocation-enrichment-a8b290` | 28/07 | ⚠️ **Não está no `main`.** Token do ipinfo.io, correção automática via IPligence, atalho `/geofeed.csv` e remoção da automação MaxMind. O `main` segue consultando ipinfo.io sem token e MaxMind via RIPE. Decidir se entra ou se foi abandonado. |

## 3. Worktrees com código editado e nunca commitado

(Fora os `.pyc`/`__pycache__`, que aparecem modificados em quase todos os worktrees e não importam.)

| Worktree | Base | Situação |
|---|---|---|
| `host-access-protocol-button-1fca41` | 11/09 | ✅ Superado: o `main` já checa permissão no cadastrar/editar/excluir acesso (`pode_acessar_cliente`, `pode_alterar_acesso`, `acessos_somente_leitura`). O arquivo `clientes/tests_permissao_acessos.py` só existe lá. |
| `password-mask-reveal-click-777f5c` | 18/08 | ✅ Praticamente todo no `main` (10/12 e 10/10 linhas). |
| `ip-address-documentation-195a6a` | 02/08 | ⚠️ Reescrita grande de `clientes/templates/listar.html` (233+/123−) que **não** está no `main` (27/179 linhas). Base de agosto — aplicar hoje exigiria refazer sobre o `listar.html` atual. Provavelmente abandonada. |
| `ecstatic-banach-3a0435` | 18/06 | Só `.claude/settings.json` (permissões locais do Claude Code). Irrelevante para o app. |

## 4. Pendências de decisão

1. Exclusão de usuário (`consultant-user-delete-permission-eed128`) — trazer para o `main`?
2. ipinfo token / IPligence (`ipinfo-geolocation-enrichment-a8b290`) — trazer ou descartar?
3. Reescrita do `listar.html` (`ip-address-documentation-195a6a`) — descartar?
4. Apagar os branches/worktrees marcados ✅ (limpeza; nada se perde).
5. Renovar a credencial do GitHub no servidor (`gh auth login`).
