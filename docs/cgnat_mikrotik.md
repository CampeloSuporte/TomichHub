# Gerador de CGNAT — MikroTik (RouterOS v6 e v7)

Gera dentro do CRM o script de CGNAT determinístico que antes era feito em
<https://cgnat.redesbrasil.com.br/>, com a tabela de mapeamento
**IP privado → IP público + faixa de portas** que o NOC usa para responder a
notificações de abuso (IP público:porta → assinante).

## Onde fica

| Lugar | O que faz |
|---|---|
| **Ferramentas → Gerador CGNAT MikroTik** (`/clientes/cgnat/`) | Tela completa: v6/v7, resumo, script, download `.rsc`/`.csv`, tabela de mapeamento com busca (IP privado ou `IP público:porta`) e opção de salvar nos Documentos de um cliente. `?cliente=<id>` já deixa o cliente escolhido. |
| **Terminal web → ⚡ Scripts → aba CGNAT** | Gera para o equipamento selecionado na aba Executar, salva o mapeamento nos Documentos do cliente dele e cola o script no terminal ativo (▶ Colar no terminal — uma linha a cada 60 ms, depois de confirmar; colar tudo de uma vez perde caracteres no console do RouterOS). |

Permissão: a mesma ferramenta **Scripts** da instância (`ferramenta_instancia_required('scripts')`).
Salvar nos Documentos exige acesso ao cliente (`pode_acessar_cliente`) ou ao
acesso do terminal (`pode_acessar_acesso`); sem isso o script é gerado e a
resposta avisa que não salvou.

## O modelo (igual ao da Redes Brasil)

Conferido linha a linha contra a saída real do gerador da Redes Brasil para
`131.100.240.192/29` + `100.64.0.0/23` (gabarito em `clientes/fixtures_cgnat/`,
teste `clientes/tests_cgnat.py`):

- O bloco privado é fatiado em sub-blocos do tamanho do público
  (`/23` em `/29` = **64 fatias = 64 clientes por IP público**).
- A faixa de portas (padrão 1024-65535 = 64512 portas) é dividida pelas fatias:
  `64512 / 64` = **1008 portas por cliente**; fatia 0 = `1024-2031`, fatia 63 = `64528-65535`.
- `srcnat` → `jump cgnat` (address-list `rede-privada-para-cgnat`) → uma chain por
  fatia, com `netmap` TCP e UDP para o bloco público e `to-ports` da faixa da fatia.
  O netmap preserva o deslocamento: `100.64.0.3` sai sempre por `.195`.
- No fim, sem porta: regra **ICMP** (opcional) e **fallback** `netmap` do bloco
  privado inteiro (cobre GRE, ESP e o resto).

Resultado para o exemplo: 2 + 64×3 + ICMP + fallback = 196 regras (a Redes Brasil
não tem a regra ICMP; desmarcando ICMP a saída v6 fica idêntica à dela).

### v6 x v7

As regras são as mesmas. O que muda:

| | v6 | v7 |
|---|---|---|
| Caminho | `/ip firewall nat add ...` (idêntico à Redes Brasil) | `/ip/firewall/nat/add ...` (sintaxe só do v7) |
| Nome da chain da fatia | o próprio CIDR (`100.64.0.8/29`) | `cgnat-100.64.0.8-29` (sem `/`) |

O v6 ficou exatamente no formato da Redes Brasil para não divergir de quem já
tem aquele script aplicado. A saída v7 ainda não foi aplicada num equipamento real.

## Entradas e validação (`clientes/cgnat.py → planejar`)

| Campo | Regra |
|---|---|
| Bloco público | CIDR IPv4 começando no endereço de rede; não pode ser RFC 1918 nem 100.64.0.0/10 |
| Bloco privado | CIDR IPv4 RFC 1918 ou 100.64.0.0/10; ≥ público; até /16 por geração; não sobrepõe o público |
| Porta inicial / final | 1-65535, inicial < final (padrão 1024-65535) |
| Portas por cliente | vazio = máximo que cabe; se informado, `fatias × portas ≤ faixa` (senão "Portas insuficientes … máximo N por cliente") |

Avisos (gera mesmo assim): menos de 1000 portas por cliente; faixa começando abaixo de 1024.
Teto de 4096 fatias (clientes por IP público).

## Documentos do cliente

Ao gerar com cliente (tela) ou com equipamento selecionado (terminal) e "Salvar"
marcado, entram dois `Documento` na aba Documentos do cliente:

- `CGNAT_<público>__<privado>_<v6|v7>_<AAAAMMDD_HHMM>.rsc` — o script;
- `CGNAT_<público>__<privado>_<v6|v7>_<AAAAMMDD_HHMM>.csv` — mapeamento
  (`IP Privado;IP Público;Porta inicial;Porta final;Portas`, `;` + BOM para abrir no Excel).

## API

- `POST /clientes/cgnat/gerar/` (JSON): `publico, privado, versao, porta_inicial,
  porta_final, portas_por_cliente, icmp, fallback, salvar, cliente_id | acesso_id`.
  Devolve `script`, `resumo`, `avisos`, `fatias`, `mapeamento` (prévia de até 2048
  linhas; `mapeamento_total` diz o total) e `documentos` salvos. Erro de validação = 400 com `erro`.
- `GET /clientes/cgnat/baixar/?formato=rsc|csv&publico=..&privado=..&versao=..` — download.

## Fora do escopo

- O painel ⚡ Scripts do terminal em modal (`modal_ssh_terminal.html`) não tem a aba
  CGNAT; use o terminal web completo ou a tela.
- Não há consulta reversa salva no banco: a busca `IP público:porta` da tela calcula
  a partir das fatias do script gerado na hora (o `.csv` nos Documentos é o registro).
