# Hotspot — Remoção do módulo

**Data:** 2026-10-08

## O que saiu

- Aba **📡 Hotspot** da tela do cliente (`clientes/templates/listar.html`): HTML da aba, modais
  "Aplicar no MikroTik" e "Interface Adicional" e todo o JS `hs*`.
- Todas as rotas internas `/clientes/<id>/hotspot/...` (listar, salvar, detalhe, deletar,
  interfaces, aplicar, preview, banners, logo, fundo, leads, disparo). Hoje respondem 404.
- Chave `hotspot` de `UsuarioModulo.MODULO_CHOICES` e `InstanciaFerramenta.FERRAMENTA_CHOICES`.
  A migração `usuario/0014_remove_hotspot` apaga as linhas de permissão com essa chave
  (22 `UsuarioModulo` e 2 `InstanciaFerramenta` na data da remoção).

## O que ficou, e por quê

No momento da remoção havia hotspot em produção: a Villaggionet capturou lead em 25/09/2026
(3.449 leads no total) e a Conecta ISP tinha disparo Chatmix habilitado. O `login.html` desses
MikroTiks redireciona para o CRM, então tirar as rotas públicas derrubaria o WiFi dos clientes
finais deles. Por isso:

- `clientes/hotspot_views.py` ficou só com os endpoints públicos (sem login):
  `hotspot/login-html/<uuid>/`, `hotspot/portal/<uuid>/`, `hotspot/portal/<uuid>/conectar/` e
  `hotspot/pixel/<uuid>/`, mais os geradores de HTML que eles usam.
- Os modelos `HotspotConfig`, `HotspotInterface`, `HotspotBanner`, `HotspotLead` e
  `ClienteIntegracaoDisparo` continuam, com os dados. Nenhuma tabela foi apagada.
- O sinal `post_save` de `HotspotLead` e a task `enviar_disparo_hotspot_lead` continuam: lead novo
  da Conecta ainda dispara o WhatsApp. Não há mais tela para desligar; se precisar, mude
  `ClienteIntegracaoDisparo.habilitado` pelo shell.
- O CSS `.hs-input` / `.hs-combo*` continua no `listar.html` porque o combo "Host MikroTik" da aba
  OpenVPN usa essas classes.

## Para apagar de vez

Quando nenhum MikroTik apontar mais para o portal (conferir acessos a `/clientes/hotspot/` no
log do nginx): remover `hotspot_views.py`, as 4 rotas públicas, o sinal e a task de disparo, os
modelos e gerar a migração que exclui as tabelas. Fazer backup antes: os leads são dados pessoais.
