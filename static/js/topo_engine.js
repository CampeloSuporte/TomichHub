// Topology Editor Engine
//
// ── Linguagem visual dos ícones (redesign 2026-08-13) ────────────────────────
// Todo ícone é desenhado num viewBox 48×48 e ocupa a área ótica x:3..45 / y:6..42
// — assim nenhum device "pesa" mais que o outro na paleta nem dentro do node.
// Regras que valem para o set inteiro:
//   • `currentColor` é a cor do device (vem de DEVICES[].color). Corpo em .92 de
//     opacidade, faces secundárias em .55–.75.
//   • Recessos (baias, janelas, painéis de porta) são `#04121a` com opacidade —
//     escurecem sem virar buraco preto sobre o fundo do canvas.
//   • Detalhes/LEDs/portas em `#fff` com opacidade .9/.6/.35 (três níveis, nunca
//     branco chapado) — é o que dá profundidade sem sombra.
//   • Traço principal 2.4, detalhe 1.4–1.8, sempre `stroke-linecap="round"`.
//   • Nada de `url(#...)` aqui dentro: o mesmo markup é injetado no SVG do canvas
//     E em <svg> soltos da paleta, onde os <defs> do template não existem.
const DEVICES = {
  router:    {label:'Roteador',     color:'#00d9ff', icon:'router',    group:'Rede / Core'},
  switch_l3: {label:'Switch L3',    color:'#58a6ff', icon:'switch_l3', group:'Rede / Core'},
  switch_l2: {label:'Switch L2',    color:'#3fb950', icon:'switch',    group:'Rede / Core'},
  firewall:  {label:'Firewall',     color:'#f85149', icon:'firewall',  group:'Rede / Core'},
  cgnat:     {label:'CGNAT',        color:'#ff6b35', icon:'cgnat',     group:'Rede / Core'},
  dwdm:      {label:'DWDM',         color:'#bc8cff', icon:'dwdm',      group:'Rede / Core'},
  olt:       {label:'OLT',          color:'#e3b341', icon:'olt',       group:'Acesso / FTTH'},
  splitter:  {label:'Splitter',     color:'#2dd4bf', icon:'splitter',  group:'Acesso / FTTH'},
  onu:       {label:'ONU/ONT',      color:'#63e6be', icon:'onu',       group:'Acesso / FTTH'},
  cpe:       {label:'CPE',          color:'#d2a8ff', icon:'cpe',       group:'Acesso / FTTH'},
  radio:     {label:'Rádio PTP',    color:'#ffa657', icon:'radio',     group:'Wireless'},
  ap:        {label:'Access Point', color:'#ffd166', icon:'ap',        group:'Wireless'},
  internet:  {label:'Internet/WAN', color:'#38bdf8', icon:'internet',  group:'Trânsito / Peering'},
  ix:        {label:'IX / PTT',     color:'#f778ba', icon:'ix',        group:'Trânsito / Peering'},
  cloud:     {label:'Cloud/ISP',    color:'#8b98a5', icon:'cloud',     group:'Trânsito / Peering'},
  server:    {label:'Servidor',     color:'#8b949e', icon:'server',    group:'Servidores'},
  vm:        {label:'VM',           color:'#a78bfa', icon:'vm',        group:'Servidores'},
  host:      {label:'Host/PC',      color:'#79c0ff', icon:'host',      group:'Servidores'},
  grupo:     {label:'Grupo de hosts',color:'#7ee787', icon:'grupo',    group:'Agrupamento'},
  text_box:  {label:'Texto/Legenda',color:'#e3b341', icon:'text_box',  group:'Anotações'},
};
const IFACES = {
  '100m':  {label:'100 Mbps', color:'#8b949e', w:2},
  '1g':    {label:'1 Gbps',   color:'#3fb950', w:2.5},
  '10g':   {label:'10 Gbps',  color:'#00d9ff', w:3},
  '20g':   {label:'20 Gbps',  color:'#22d3ee', w:3.3},
  '30g':   {label:'30 Gbps',  color:'#3b82f6', w:3.6},
  '40g':   {label:'40 Gbps',  color:'#58a6ff', w:4},
  '50g':   {label:'50 Gbps',  color:'#8b5cf6', w:4.3},
  '100g':  {label:'100 Gbps', color:'#bc8cff', w:5},
  'sfp':   {label:'SFP 1G',   color:'#e3b341', w:2.5},
  'sfp+':  {label:'SFP+ 10G', color:'#ffa657', w:3},
  'gpon':  {label:'GPON',     color:'#f78166', w:2.5},
  'xpon':  {label:'XGS-PON', color:'#d2a8ff', w:3},
  'wifi':  {label:'Wireless', color:'#e3b341', w:2},
  'mw':    {label:'Microwave',color:'#ff9800', w:2.5},
  'other': {label:'Outro',    color:'#6e7681', w:2},
};

// SVG icon paths (48x48 viewBox)
const ICONS = {
  // Cilindro clássico de roteador (linguagem Cisco) com as duas setas de fluxo
  // contrário na face de cima — é o desenho que engenheiro de rede reconhece
  // antes de ler o rótulo.
  router:`<path d="M4 20v7a20 8 0 0 0 40 0v-7z" fill="currentColor" opacity=".55"/>
    <path d="M4 27a20 8 0 0 0 40 0" fill="none" stroke="#fff" stroke-opacity=".16" stroke-width="1.4"/>
    <ellipse cx="24" cy="20" rx="20" ry="8" fill="currentColor" opacity=".95"/>
    <ellipse cx="24" cy="18.4" rx="17" ry="5.6" fill="#fff" opacity=".1"/>
    <g stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity=".95">
      <path d="M11.5 15.8h13"/><path d="M21.2 12.6l3.6 3.2-3.6 3.2"/>
      <path d="M36.5 24.6h-13"/><path d="M26.8 21.4l-3.6 3.2 3.6 3.2"/>
    </g>`,

  // Switch L2/L3: mesmo chassi 1U — quatro portas gordas de um lado e o
  // silkscreen da camada num "pill" escuro do outro. A tentação era encher de
  // portinha e seta de fluxo; a 22px na paleta isso vira ruído, então o desenho
  // ficou em 5 formas grandes e o par L2/L3 se separa pelo pill + pela cor.
  switch:`<rect x="3" y="14" width="42" height="23" rx="5" fill="currentColor" opacity=".92"/>
    <path d="M7.5 15.4h33" stroke="#fff" stroke-opacity=".3" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="6" y="19" width="15" height="13" rx="3.5" fill="#04121a" opacity=".5"/>
    <text x="13.5" y="29.2" text-anchor="middle" fill="#fff" fill-opacity=".95" font-size="10" font-weight="800" font-family="'Segoe UI',sans-serif">L2</text>
    <g fill="#fff">
      <rect x="24" y="19.4" width="4.2" height="12.2" rx="1.2" opacity=".9"/>
      <rect x="29.4" y="19.4" width="4.2" height="12.2" rx="1.2" opacity=".75"/>
      <rect x="34.8" y="19.4" width="4.2" height="12.2" rx="1.2" opacity=".6"/>
      <rect x="40.2" y="19.4" width="2.4" height="12.2" rx="1.2" opacity=".35"/>
    </g>`,

  switch_l3:`<rect x="3" y="14" width="42" height="23" rx="5" fill="currentColor" opacity=".92"/>
    <path d="M7.5 15.4h33" stroke="#fff" stroke-opacity=".3" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="6" y="19" width="15" height="13" rx="3.5" fill="#04121a" opacity=".5"/>
    <text x="13.5" y="29.2" text-anchor="middle" fill="#fff" fill-opacity=".95" font-size="10" font-weight="800" font-family="'Segoe UI',sans-serif">L3</text>
    <g fill="#fff">
      <rect x="24" y="19.4" width="4.2" height="12.2" rx="1.2" opacity=".9"/>
      <rect x="29.4" y="19.4" width="4.2" height="12.2" rx="1.2" opacity=".75"/>
      <rect x="34.8" y="19.4" width="4.2" height="12.2" rx="1.2" opacity=".6"/>
      <rect x="40.2" y="19.4" width="2.4" height="12.2" rx="1.2" opacity=".35"/>
    </g>
    <g stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity=".85">
      <path d="M12 5.8h11"/><path d="M20.2 3l3 2.8-3 2.8"/>
      <path d="M36 11.4H25"/><path d="M27.8 8.6l-3 2.8 3 2.8"/>
    </g>`,

  // Rádio PTP: parabólica de perfil (arco grosso), alimentador, mastro e as
  // frentes de onda saindo do foco.
  radio:`<g fill="none" stroke="currentColor" stroke-linecap="round">
      <path d="M34 13.5a13 13 0 0 1 0 19" stroke-width="2.4" opacity=".5"/>
      <path d="M39 9a19 19 0 0 1 0 28" stroke-width="2.4" opacity=".26"/>
    </g>
    <path d="M15.4 27h3.2l1.8 14.5h-6.8z" fill="currentColor" opacity=".7"/>
    <path d="M11 43h12" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
    <path d="M17 9.5a13.5 13.5 0 0 0 0 25" fill="none" stroke="currentColor" stroke-width="5.5" stroke-linecap="round" opacity=".95"/>
    <path d="M19.5 13a10 10 0 0 0 0 18" fill="none" stroke="#fff" stroke-opacity=".22" stroke-width="1.8"/>
    <path d="M18 22h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".85"/>
    <circle cx="27" cy="22" r="2.8" fill="currentColor"/>
    <circle cx="27" cy="22" r="1.2" fill="#fff" opacity=".85"/>`,

  // AP: disco montado no teto + cone de cobertura Wi-Fi.
  ap:`<g fill="none" stroke="currentColor" stroke-linecap="round" opacity=".85">
      <path d="M16 30a11 11 0 0 1 16 0" stroke-width="2.2" opacity=".35"/>
      <path d="M18.5 35.5a7 7 0 0 1 11 0" stroke-width="2.2" opacity=".6"/>
    </g>
    <circle cx="24" cy="41.5" r="2.6" fill="currentColor"/>
    <path d="M8 20h32" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".35"/>
    <path d="M9 19.5a15 15 0 0 1 30 0v1.5a2 2 0 0 1-2 2H11a2 2 0 0 1-2-2z" fill="currentColor" opacity=".92"/>
    <path d="M13 17.5a11 11 0 0 1 22 0" fill="none" stroke="#fff" stroke-opacity=".22" stroke-width="1.6"/>
    <circle cx="24" cy="17" r="2.4" fill="#fff" opacity=".85"/>
    <circle cx="17" cy="19.6" r="1.3" fill="#fff" opacity=".5"/>
    <circle cx="31" cy="19.6" r="1.3" fill="#fff" opacity=".5"/>`,

  // DWDM: o feixe único entra, o prisma separa em comprimentos de onda. As
  // quatro cores são literais (é disso que o equipamento trata), não decoração.
  dwdm:`<rect x="3" y="10" width="42" height="28" rx="5" fill="currentColor" opacity=".92"/>
    <path d="M7.5 11.4h33" stroke="#fff" stroke-opacity=".3" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="6" y="13.5" width="36" height="21" rx="3" fill="#04121a" opacity=".6"/>
    <path d="M8.5 28l6-2.6" stroke="#fff" stroke-width="2.2" stroke-linecap="round" opacity=".9"/>
    <path d="M13 31.5L22 15l9 16.5z" fill="#fff" fill-opacity=".16" stroke="#fff" stroke-opacity=".5" stroke-width="1.3" stroke-linejoin="round"/>
    <g stroke-width="2.4" stroke-linecap="round" fill="none">
      <path d="M27.5 22.5l12-4.5" stroke="#f85149"/>
      <path d="M28.5 25.5l11-1.5" stroke="#3fb950"/>
      <path d="M29.5 28.5l10 2" stroke="#58a6ff"/>
    </g>`,

  // OLT: chassi com placas de linha à esquerda e o leque PON saindo à direita.
  olt:`<rect x="3" y="8" width="42" height="32" rx="5" fill="currentColor" opacity=".92"/>
    <path d="M7.5 9.4h33" stroke="#fff" stroke-opacity=".3" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="6" y="12.5" width="20" height="10" rx="2.5" fill="#04121a" opacity=".5"/>
    <rect x="6" y="25.5" width="20" height="10" rx="2.5" fill="#04121a" opacity=".5"/>
    <g fill="#fff">
      <rect x="8.5" y="15.5" width="4" height="4" rx="1" opacity=".9"/><rect x="14" y="15.5" width="4" height="4" rx="1" opacity=".75"/><rect x="19.5" y="15.5" width="4" height="4" rx="1" opacity=".6"/>
      <rect x="8.5" y="28.5" width="4" height="4" rx="1" opacity=".9"/><rect x="14" y="28.5" width="4" height="4" rx="1" opacity=".75"/><rect x="19.5" y="28.5" width="4" height="4" rx="1" opacity=".6"/>
    </g>
    <g fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="2" stroke-linecap="round">
      <path d="M29 24c5.5 0 5.5-9 10-9"/><path d="M29 24h10"/><path d="M29 24c5.5 0 5.5 9 10 9"/>
    </g>
    <g fill="#fff" opacity=".95">
      <circle cx="40.5" cy="15" r="2"/><circle cx="40.5" cy="24" r="2"/><circle cx="40.5" cy="33" r="2"/>
    </g>`,

  // Splitter óptico: uma fibra entra, N saem — o elemento passivo mais comum
  // de uma planta FTTH e que antes não existia na paleta.
  splitter:`<path d="M4 24h9" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" opacity=".8"/>
    <rect x="13" y="14" width="13" height="20" rx="3.5" fill="currentColor" opacity=".92"/>
    <path d="M16 15.4h7" stroke="#fff" stroke-opacity=".3" stroke-width="1.4" stroke-linecap="round"/>
    <path d="M19.5 19v10" stroke="#fff" stroke-opacity=".5" stroke-width="1.3" stroke-linecap="round"/>
    <path d="M17 24h5" stroke="#fff" stroke-opacity=".5" stroke-width="1.3" stroke-linecap="round"/>
    <g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" opacity=".8">
      <path d="M26 24c6 0 6-13 12-13"/>
      <path d="M26 24c6 0 6-6.5 12-6.5"/>
      <path d="M26 24h12"/>
      <path d="M26 24c6 0 6 6.5 12 6.5"/>
      <path d="M26 24c6 0 6 13 12 13"/>
    </g>
    <g fill="currentColor">
      <circle cx="39.5" cy="11" r="2"/><circle cx="39.5" cy="17.5" r="2"/>
      <circle cx="39.5" cy="24" r="2"/><circle cx="39.5" cy="30.5" r="2"/><circle cx="39.5" cy="37" r="2"/>
    </g>`,

  // ONU/ONT: caixinha do assinante com o rabicho de fibra e a porta LAN.
  onu:`<path d="M9 27c-6 1-6 10 1 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".65"/>
    <circle cx="11" cy="40.6" r="2" fill="currentColor" opacity=".8"/>
    <rect x="8" y="16" width="32" height="15" rx="4" fill="currentColor" opacity=".92"/>
    <path d="M12 17.3h24" stroke="#fff" stroke-opacity=".3" stroke-width="1.4" stroke-linecap="round"/>
    <g fill="#fff">
      <circle cx="14" cy="24" r="1.8" opacity=".9"/>
      <circle cx="19.5" cy="24" r="1.8" opacity=".6"/>
      <circle cx="25" cy="24" r="1.8" opacity=".35"/>
    </g>
    <rect x="30" y="20.5" width="7" height="7" rx="1.6" fill="#04121a" opacity=".45"/>
    <path d="M32 22.5h3v3h-3z" fill="#fff" opacity=".7"/>`,

  // CPE: gateway do cliente — antenas, LEDs frontais e o leque Wi-Fi.
  cpe:`<g fill="none" stroke="currentColor" stroke-linecap="round" opacity=".55">
      <path d="M19 12a7 7 0 0 1 10 0" stroke-width="2"/>
      <path d="M15.5 8a12 12 0 0 1 17 0" stroke-width="2" opacity=".6"/>
    </g>
    <g stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
      <path d="M14 28l-3.5-10"/><path d="M34 28l3.5-10"/>
    </g>
    <circle cx="10.5" cy="16.6" r="2" fill="currentColor"/>
    <circle cx="37.5" cy="16.6" r="2" fill="currentColor"/>
    <rect x="6" y="27" width="36" height="14" rx="4" fill="currentColor" opacity=".92"/>
    <path d="M10 28.3h28" stroke="#fff" stroke-opacity=".3" stroke-width="1.4" stroke-linecap="round"/>
    <g fill="#fff">
      <circle cx="13" cy="34.2" r="1.7" opacity=".9"/>
      <circle cx="18.5" cy="34.2" r="1.7" opacity=".6"/>
      <circle cx="24" cy="34.2" r="1.7" opacity=".35"/>
    </g>
    <rect x="30" y="31.4" width="8" height="5.6" rx="1.4" fill="#04121a" opacity=".4"/>`,

  // Firewall: parede de tijolos (o símbolo nativo de rede) com o escudo em
  // contorno branco por cima — legível mesmo em 24px na paleta.
  firewall:`<g fill="currentColor">
      <rect x="4" y="10" width="12.5" height="8" rx="1.6" opacity=".92"/>
      <rect x="17.8" y="10" width="12.5" height="8" rx="1.6" opacity=".82"/>
      <rect x="31.6" y="10" width="12.4" height="8" rx="1.6" opacity=".9"/>
      <rect x="4" y="19.4" width="6" height="8" rx="1.6" opacity=".8"/>
      <rect x="11.3" y="19.4" width="12.5" height="8" rx="1.6" opacity=".9"/>
      <rect x="25.1" y="19.4" width="12.5" height="8" rx="1.6" opacity=".8"/>
      <rect x="38.9" y="19.4" width="5.1" height="8" rx="1.6" opacity=".9"/>
      <rect x="4" y="28.8" width="12.5" height="8" rx="1.6" opacity=".85"/>
      <rect x="17.8" y="28.8" width="12.5" height="8" rx="1.6" opacity=".92"/>
      <rect x="31.6" y="28.8" width="12.4" height="8" rx="1.6" opacity=".8"/>
    </g>
    <path d="M24 11l9 3.3v7.8c0 6.2-5.2 9.7-9 11.1-3.8-1.4-9-4.9-9-11.1v-7.8z" fill="#04121a" fill-opacity=".55" stroke="#fff" stroke-opacity=".92" stroke-width="2.3" stroke-linejoin="round"/>
    <path d="M20 23.2l3 3 5.5-5.8" fill="none" stroke="#fff" stroke-opacity=".92" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>`,

  // CGNAT: muitos privados entrando, um público saindo.
  cgnat:`<g stroke="currentColor" stroke-width="2.2" stroke-linecap="round" fill="none" opacity=".85">
      <path d="M4 14h2c3.5 0 3.5 10 6 10"/><path d="M4 24h8"/><path d="M4 34h2c3.5 0 3.5-10 6-10"/>
    </g>
    <rect x="12" y="12" width="24" height="24" rx="5" fill="currentColor" opacity=".95"/>
    <path d="M15.5 13.4h17" stroke="#fff" stroke-opacity=".3" stroke-width="1.5" stroke-linecap="round"/>
    <text x="24" y="27.6" text-anchor="middle" fill="#fff" fill-opacity=".95" font-size="9.5" font-weight="800" font-family="'Segoe UI',sans-serif" letter-spacing=".4">NAT</text>
    <path d="M36 24h6" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>
    <path d="M39.5 20.2l4 3.8-4 3.8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`,

  // Internet / WAN: globo com meridianos — o "resto do mundo".
  internet:`<circle cx="24" cy="24" r="19" fill="currentColor" opacity=".9"/>
    <circle cx="24" cy="20" r="15" fill="#fff" opacity=".08"/>
    <g fill="none" stroke="#04121a" stroke-opacity=".55" stroke-width="1.8" stroke-linecap="round">
      <path d="M5 24h38"/>
      <path d="M24 5c5.5 5.4 8 12 8 19s-2.5 13.6-8 19c-5.5-5.4-8-12-8-19s2.5-13.6 8-19z"/>
      <path d="M9.4 13.5c4 2.6 9.1 4 14.6 4s10.6-1.4 14.6-4"/>
      <path d="M9.4 34.5c4-2.6 9.1-4 14.6-4s10.6 1.4 14.6 4"/>
    </g>`,

  // IX / PTT: fabric central com vários participantes conectados — a forma
  // hexagonal separa na hora de um switch comum.
  ix:`<g stroke="currentColor" stroke-width="1.8" stroke-linecap="round" opacity=".75">
      <path d="M24 24L8 12"/><path d="M24 24L40 12"/><path d="M24 24L8 36"/><path d="M24 24L40 36"/><path d="M24 24V6"/><path d="M24 24v18"/>
    </g>
    <g fill="currentColor" opacity=".95">
      <circle cx="7" cy="11" r="3.2"/><circle cx="41" cy="11" r="3.2"/>
      <circle cx="7" cy="37" r="3.2"/><circle cx="41" cy="37" r="3.2"/>
      <circle cx="24" cy="5" r="3.2"/><circle cx="24" cy="43" r="3.2"/>
    </g>
    <path d="M24 11.5l11 6.35v12.7L24 36.5l-11-6.35V17.85z" fill="currentColor" opacity=".95" stroke="#04121a" stroke-opacity=".35" stroke-width="1.2"/>
    <path d="M24 13.8l9 5.2" stroke="#fff" stroke-opacity=".28" stroke-width="1.5" stroke-linecap="round" fill="none"/>
    <text x="24" y="28" text-anchor="middle" fill="#fff" fill-opacity=".95" font-size="12" font-weight="800" font-family="'Segoe UI',sans-serif" letter-spacing=".5">IX</text>`,

  // Cloud/ISP: nuvem com a malha interna — "outra rede", não só uma nuvem.
  cloud:`<path d="M36.5 37H14a9.5 9.5 0 0 1-1.8-18.8 11.5 11.5 0 0 1 21.4-3.6A9.7 9.7 0 0 1 36.5 37z" fill="currentColor" opacity=".92"/>
    <path d="M14.6 19.6a11.5 11.5 0 0 1 18.4-4.6" fill="none" stroke="#fff" stroke-opacity=".25" stroke-width="1.6" stroke-linecap="round"/>
    <g stroke="#04121a" stroke-opacity=".5" stroke-width="1.6" stroke-linecap="round"><path d="M17 30l7-5 7 5"/></g>
    <g fill="#04121a" fill-opacity=".55"><circle cx="24" cy="24" r="2.6"/><circle cx="16.5" cy="30.5" r="2.6"/><circle cx="31.5" cy="30.5" r="2.6"/></g>`,

  // Servidor: rack 3U com baias, LEDs e a moldura fina de cada unidade.
  server:`<g>
      <rect x="7" y="8" width="34" height="10" rx="2.5" fill="currentColor" opacity=".92"/>
      <rect x="7" y="19.5" width="34" height="10" rx="2.5" fill="currentColor" opacity=".8"/>
      <rect x="7" y="31" width="34" height="10" rx="2.5" fill="currentColor" opacity=".68"/>
    </g>
    <g stroke="#fff" stroke-opacity=".28" stroke-width="1.3" stroke-linecap="round">
      <path d="M10.5 9.2h27"/><path d="M10.5 20.7h27"/><path d="M10.5 32.2h27"/>
    </g>
    <g fill="#04121a" fill-opacity=".4">
      <rect x="10.5" y="11.5" width="13" height="3.4" rx="1.2"/>
      <rect x="10.5" y="23" width="13" height="3.4" rx="1.2"/>
      <rect x="10.5" y="34.5" width="13" height="3.4" rx="1.2"/>
    </g>
    <g fill="#fff">
      <circle cx="36.5" cy="13.2" r="1.7" opacity=".9"/><circle cx="31.5" cy="13.2" r="1.7" opacity=".45"/>
      <circle cx="36.5" cy="24.7" r="1.7" opacity=".9"/><circle cx="31.5" cy="24.7" r="1.7" opacity=".45"/>
      <circle cx="36.5" cy="36.2" r="1.7" opacity=".9"/><circle cx="31.5" cy="36.2" r="1.7" opacity=".45"/>
    </g>`,

  // VM: host com três guests em cima da camada de virtualização.
  vm:`<rect x="14" y="7" width="28" height="9" rx="3" fill="currentColor" opacity=".4"/>
    <rect x="9.5" y="15" width="32" height="10" rx="3.2" fill="currentColor" opacity=".65"/>
    <circle cx="36" cy="20" r="1.6" fill="#fff" opacity=".6"/>
    <rect x="5" y="24" width="38" height="17" rx="4" fill="currentColor" opacity=".95"/>
    <path d="M9 25.3h30" stroke="#fff" stroke-opacity=".3" stroke-width="1.5" stroke-linecap="round"/>
    <text x="18" y="36.4" text-anchor="middle" fill="#fff" fill-opacity=".95" font-size="10.5" font-weight="800" font-family="'Segoe UI',sans-serif">VM</text>
    <g fill="#fff"><circle cx="31" cy="32.8" r="1.9" opacity=".85"/><circle cx="37" cy="32.8" r="1.9" opacity=".5"/></g>`,

  // Host/PC: estação de trabalho com um prompt na tela.
  host:`<rect x="4" y="8" width="40" height="27" rx="3.5" fill="currentColor" opacity=".92"/>
    <rect x="7.5" y="11.5" width="33" height="20" rx="2" fill="#04121a" opacity=".55"/>
    <g stroke="#fff" stroke-linecap="round" stroke-width="1.7">
      <path d="M11.5 18l3.5 3.5-3.5 3.5" stroke-opacity=".85" fill="none"/>
      <path d="M18.5 25.5h8" stroke-opacity=".55"/>
    </g>
    <path d="M20 35h8l1.6 5h-11.2z" fill="currentColor" opacity=".7"/>
    <rect x="14" y="40" width="20" height="3.4" rx="1.7" fill="currentColor" opacity=".85"/>`,

  // Grupo de hosts: pilha de chassis (os hosts que ficaram dentro) com a seta
  // de "abrir" à direita — o nó não é um equipamento, é a porta de entrada do
  // sub-mapa. Criado só pela ação "Agrupar"; não aparece na paleta.
  grupo:`<rect x="9" y="7" width="30" height="8" rx="3" fill="currentColor" opacity=".35"/>
    <rect x="6" y="13" width="36" height="9" rx="3.2" fill="currentColor" opacity=".62"/>
    <rect x="3" y="20" width="42" height="21" rx="5" fill="currentColor" opacity=".95"/>
    <rect x="7" y="26" width="21" height="9" rx="2.4" fill="#04121a" opacity=".5"/>
    <g fill="#fff"><circle cx="11.5" cy="30.5" r="1.8" opacity=".9"/><circle cx="17.5" cy="30.5" r="1.8" opacity=".6"/><circle cx="23.5" cy="30.5" r="1.8" opacity=".35"/></g>
    <path d="M33.5 26.3l4.8 4.2-4.8 4.2" fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`,

  // Legenda: cartão pontilhado com "texto" dentro.
  text_box:`<rect x="3.5" y="9" width="41" height="30" rx="5" fill="currentColor" fill-opacity=".07" stroke="currentColor" stroke-width="2.2" stroke-dasharray="6,3.5"/>
    <g stroke="currentColor" stroke-linecap="round" stroke-width="2.4">
      <path d="M10 18h28" stroke-opacity=".75"/>
      <path d="M10 24.5h22" stroke-opacity=".5"/>
      <path d="M10 31h15" stroke-opacity=".3"/>
    </g>`,
};

window.TOPO_DEVICES = DEVICES;
window.TOPO_IFACES  = IFACES;
window.TOPO_ICONS   = ICONS;
