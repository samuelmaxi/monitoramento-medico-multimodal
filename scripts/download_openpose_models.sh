#!/usr/bin/env bash
# Baixa os pesos oficiais do OpenPose (CMU) para uso via cv2.dnn.readNetFromCaffe.
#
# Fontes:
#   - .prototxt: repositório oficial CMU-Perceptual-Computing-Lab/openpose no
#     GitHub, fixado no commit abaixo.
#   - .caffemodel: o servidor original da CMU (posefs1.perception.cs.cmu.edu)
#     está fora do ar. Usamos o espelho do repositório OpenPose no Hugging Face
#     (camenduru/openpose), fixado numa revisão. Os SHA-256 abaixo coincidem com
#     os de um segundo espelho independente (dylanholmes/openpose-caffemodels)
#     e os prototxt do espelho têm o mesmo hash git dos arquivos oficiais.
#
# Uso: scripts/download_openpose_models.sh [body_25|coco|all] [diretorio_destino]
#   padrão: body_25 em models/openpose/
#
# Licença: os modelos do OpenPose são de uso acadêmico/não comercial
# (https://github.com/CMU-Perceptual-Computing-Lab/openpose/blob/master/LICENSE).
set -euo pipefail

MODEL="${1:-body_25}"
DEST="${2:-models/openpose}"

GITHUB_COMMIT="5c5d96523ef917bd30301245fdc8343937cae48d"
GITHUB_BASE="https://raw.githubusercontent.com/CMU-Perceptual-Computing-Lab/openpose/${GITHUB_COMMIT}/models/pose"
HF_REVISION="8fadec2b54f35105fc9929a484af4e739ea1f8d3"
HF_BASE="https://huggingface.co/camenduru/openpose/resolve/${HF_REVISION}/models/pose"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# fetch <url> <arquivo_destino> <sha256_esperado>
fetch() {
  local url="$1" out="$2" expected="$3"
  if [[ -f "$out" ]] && [[ "$(sha256_of "$out")" == "$expected" ]]; then
    echo "[ok] $out já existe e o checksum confere"
    return
  fi
  echo "[..] baixando $url"
  curl -fL --retry 3 --progress-bar -o "$out.part" "$url"
  local got
  got="$(sha256_of "$out.part")"
  if [[ "$got" != "$expected" ]]; then
    rm -f "$out.part"
    echo "[erro] checksum inválido para $out" >&2
    echo "       esperado: $expected" >&2
    echo "       obtido:   $got" >&2
    exit 1
  fi
  mv "$out.part" "$out"
  echo "[ok] $out (sha256 $got)"
}

download_body_25() {
  local dir="$DEST/body_25"
  mkdir -p "$dir"
  fetch "$GITHUB_BASE/body_25/pose_deploy.prototxt" "$dir/pose_deploy.prototxt" \
    "44d6ed3a5268d8d41ca59b3a040491277d876975c3234d82cf7ec0539b4b1f61"
  fetch "$HF_BASE/body_25/pose_iter_584000.caffemodel" "$dir/pose_iter_584000.caffemodel" \
    "44e3d7ebd8c8b62d4366d67127f1b562611a9e8fd0f4f3cdeeb4bb4a6ed12be6"
}

download_coco() {
  local dir="$DEST/coco"
  mkdir -p "$dir"
  fetch "$GITHUB_BASE/coco/pose_deploy_linevec.prototxt" "$dir/pose_deploy_linevec.prototxt" \
    "17051b87f709aa094e09c5da7b78e9016a1f37b2b452ed1f190fe74cce70b1ad"
  fetch "$HF_BASE/coco/pose_iter_440000.caffemodel" "$dir/pose_iter_440000.caffemodel" \
    "b4cf475576abd7b15d5316f1ee65eb492b5c9f5865e70a2e7882ed31fb682549"
}

case "$MODEL" in
  body_25) download_body_25 ;;
  coco) download_coco ;;
  all) download_body_25; download_coco ;;
  *) echo "uso: $0 [body_25|coco|all] [diretorio_destino]" >&2; exit 2 ;;
esac
