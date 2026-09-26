#!/usr/bin/env bash
# Regenera docs/arquitetura/fluxo_multimodal.{png,svg} a partir do .mmd.
# Requer: npm i -g @mermaid-js/mermaid-cli
# Em ambiente sem sandbox de Chrome, exporte PUPPETEER_CONFIG apontando para um JSON
# com {"args": ["--no-sandbox"]} (e "executablePath", se o Chrome não for encontrado).
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)/docs/arquitetura"
ARGS=(-i "$DIR/fluxo_multimodal.mmd" -c "$DIR/mermaid.config.json" -b white -s 2 -w 2400 -q)
if [[ -n "${PUPPETEER_CONFIG:-}" ]]; then ARGS+=(-p "$PUPPETEER_CONFIG"); fi

for ext in png svg; do
  mmdc "${ARGS[@]}" -o "$DIR/fluxo_multimodal.$ext"
done
echo "Diagrama atualizado em $DIR"
