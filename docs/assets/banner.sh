#!/usr/bin/env bash
# Emits the two README banners from one source, so the VOID and PAPER variants
# cannot drift apart. Palette values are the design kit's own tokens
# (design/css/tokens.css) — this is the same retheming a variable swap does there.
#
# Run from the repository root: `bash docs/assets/banner.sh`. Output is byte-stable,
# so re-running it on an unchanged palette produces no diff.
#
# Note for editors: SVG is XML, and XML forbids `--` inside a comment. A `<!-- ruled
# heading ------ -->` here parses locally and renders as a blank image on GitHub.
set -euo pipefail

emit() {
  local out=$1 VOID=$2 RAISED=$3 LINE=$4 LINEDIM=$5 HOT=$6 TEXT=$7 DIM=$8 FAINT=$9 LIVE=${10} GOOD=${11} GRID=${12}
  cat > "$out" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 280" width="1280" height="280"
     role="img" aria-label="Printorian — management system for an automated 3D print farm">
  <title>Printorian</title>
  <desc>Management system for an automated 3D print farm. Three printers reporting into one backend.</desc>

  <defs>
    <!-- The graph-paper backdrop the whole design kit sits on. -->
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="$GRID" stroke-width="1"/>
    </pattern>
    <style>
      .display { font-family: 'Orbitron','Bank Gothic','Eurostile','Arial Black',sans-serif;
                 font-weight: 700; fill: $TEXT; }
      .mono    { font-family: 'Share Tech Mono','Cascadia Mono',Consolas,ui-monospace,monospace; }
      .hair    { stroke: $LINE; stroke-width: 1; fill: none; }
      .hairdim { stroke: $LINEDIM; stroke-width: 1; fill: none; }
    </style>
  </defs>

  <rect width="1280" height="280" fill="$VOID"/>
  <rect width="1280" height="280" fill="url(#grid)"/>

  <!-- Outer frame. The PAPER ground (#f4f3ef) is within a few percent of GitHub's
       white page, so without this the light banner reads as an empty gap until you
       zoom in. It also covers the case prefers-color-scheme gets it wrong: GitHub's
       own light/dark setting does not drive that media query, so a reader on a dark
       GitHub with a light OS is served the PAPER variant regardless. A defined edge
       makes either variant read as a deliberate panel rather than a rendering fault. -->
  <rect x="0.5" y="0.5" width="1279" height="279" fill="none" stroke="$LINE" stroke-width="1"/>

  <!-- Corner brackets: the frame every panel in the kit is drawn inside. -->
  <g class="hair">
    <path d="M24 52V24h28"/><path d="M1256 52V24h-28"/>
    <path d="M24 228v28h28"/><path d="M1256 228v28h-28"/>
  </g>

  <!-- Wordmark -->
  <rect x="72" y="80" width="3" height="62" fill="$LIVE"/>
  <text class="display" x="100" y="134" font-size="58" letter-spacing="9">PRINTORIAN</text>
  <path class="hair" d="M100 162h500"/>
  <text class="mono" x="100" y="188" font-size="13" letter-spacing="2.4" fill="$DIM">MANAGEMENT SYSTEM FOR AN AUTOMATED 3D PRINT FARM</text>
  <text class="mono" x="100" y="218" font-size="10" letter-spacing="2" fill="$FAINT">PYTHON &#183; FASTAPI &#183; POSTGRESQL &#183; REACT &#183; TYPESCRIPT</text>

  <!-- Topology: three machines, one backend. Rule 1, drawn. -->
  <g>
    <g class="hair">
      <rect x="850" y="52" width="56" height="52" rx="2"/>
      <rect x="966" y="52" width="56" height="52" rx="2"/>
      <rect x="1082" y="52" width="56" height="52" rx="2"/>
      <path d="M858 68h40M974 68h40M1090 68h40"/>
    </g>
    <g fill="$DIM">
      <rect x="874" y="64" width="8" height="12"/><rect x="990" y="64" width="8" height="12"/>
      <rect x="1106" y="64" width="8" height="12"/>
    </g>

    <!-- The bed line carries the state. Grey is Offline, and it is drawn
         rather than left blank: a machine that is not reporting is a fact. -->
    <g stroke-width="3" fill="none">
      <path d="M860 90h36" stroke="$LIVE"/>
      <path d="M976 90h36" stroke="$GOOD"/>
      <path d="M1092 90h36" stroke="$FAINT"/>
    </g>

    <g class="hairdim"><path d="M878 104v44M994 104v44M1110 104v44"/></g>
    <path class="hair" d="M878 148h232"/>
    <path class="hair" d="M994 148v30"/>

    <rect x="904" y="178" width="180" height="40" rx="2" fill="$RAISED" stroke="$HOT" stroke-width="1"/>
    <text class="mono" x="994" y="203" font-size="11" letter-spacing="2.6" fill="$TEXT" text-anchor="middle">PRINTORIAN CORE</text>
    <text class="mono" x="994" y="242" font-size="9" letter-spacing="2" fill="$FAINT" text-anchor="middle">ONE BACKEND &#183; ONE DATABASE &#183; ONE DOMAIN MODEL</text>
  </g>
</svg>
SVG
  echo "wrote $out"
}

#          out                          void      raised    line      line-dim  hot       text      dim       faint     live      good      grid
emit docs/assets/banner.svg        '#000000' '#0c0d0f' '#2a2d33' '#17191d' '#ffffff' '#f2f4f7' '#8b929d' '#767d89' '#4cd7e8' '#58e08b' 'rgba(255,255,255,0.035)'
emit docs/assets/banner-light.svg  '#f4f3ef' '#ffffff' '#b9b6ad' '#d8d5cc' '#000000' '#0d0d0d' '#4a4a48' '#6d6b63' '#0b6f7d' '#17703f' 'rgba(0,0,0,0.045)'
