"""Inventário dos vídeos de ``conteudos/videos`` — base do dataset da US07.

Percorre a árvore de vídeos, extrai metadados do contêiner (via OpenCV e, quando
disponível, ``ffprobe``), interpreta o nome com :mod:`video.nomes_video` e calcula
o SHA-256 de cada arquivo para rastreabilidade e detecção de duplicatas exatas.

Nada é descartado por nome inesperado, resolução diferente ou sufixo extra: cada
arquivo vira uma linha. Arquivos ilegíveis entram com ``estado="erro"`` e o
motivo, em vez de sumirem da contagem.

O inventário é salvo em CSV e JSON; o resumo agrega totais por cenário, condição,
modalidade e resolução, e aponta duplicatas exatas (mesmo SHA-256).
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from .nomes_video import NomeVideo, extensao_video_suportada, parsear_nome

TAMANHO_BLOCO_HASH = 1 << 20
"""1 MiB por leitura; streaming evita carregar vídeos grandes na memória."""


@dataclass(frozen=True, slots=True)
class InventarioVideo:
    """Uma linha do inventário: um arquivo de vídeo e tudo que foi possível medir."""

    caminho_relativo: str
    nome: str
    tamanho_bytes: int
    duracao_s: float | None
    largura: int | None
    altura: int | None
    fps: float | None
    total_frames: int | None
    codec: str | None
    cenario: str | None
    condicao: str | None
    modalidade: str | None
    grupo: str | None
    redimensionado: bool
    identificador: str | None
    tokens_desconhecidos: tuple[str, ...]
    ambiguidades: tuple[str, ...]
    requer_revisao: bool
    chave_grupo: str
    estado: str
    erro: str | None
    sha256: str
    duplicata_de: str | None = None

    @property
    def resolucao(self) -> str:
        if self.largura is None or self.altura is None:
            return "desconhecida"
        return f"{self.largura}x{self.altura}"

    @property
    def legivel(self) -> bool:
        return self.estado == "ok"

    def para_dict(self) -> dict[str, object]:
        dados = asdict(self)
        dados["tokens_desconhecidos"] = list(self.tokens_desconhecidos)
        dados["ambiguidades"] = list(self.ambiguidades)
        dados["resolucao"] = self.resolucao
        return dados


def _sha256(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(TAMANHO_BLOCO_HASH):
            digest.update(bloco)
    return digest.hexdigest()


def _codec_ffprobe(caminho: Path) -> str | None:
    """Nome do codec de vídeo via ``ffprobe``; ``None`` se o binário não existir/falhar."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        saida = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(caminho),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    codec = saida.stdout.strip()
    return codec or None


def _codec_fourcc(capture: cv2.VideoCapture) -> str | None:
    """Fallback: quatro caracteres do FourCC do OpenCV (``FMP4``, ``avc1``...)."""
    try:
        valor = int(capture.get(cv2.CAP_PROP_FOURCC))
    except (TypeError, ValueError):
        return None
    if valor <= 0:
        return None
    texto = "".join(chr((valor >> (8 * i)) & 0xFF) for i in range(4)).strip()
    return texto or None


def _medir(caminho: Path) -> dict[str, object]:
    """Lê os metadados do contêiner. Devolve ``estado`` e ``erro`` quando falha."""
    capture = cv2.VideoCapture(str(caminho))
    try:
        if not capture.isOpened():
            return {
                "estado": "erro",
                "erro": "OpenCV não conseguiu abrir (codec/container/permissão)",
                "largura": None,
                "altura": None,
                "fps": None,
                "total_frames": None,
                "codec": None,
            }
        fps_bruto = float(capture.get(cv2.CAP_PROP_FPS))
        largura = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        altura = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, _ = capture.read()
        fps = fps_bruto if fps_bruto > 0 else None
        return {
            "estado": "ok" if ok else "erro",
            "erro": None if ok else "contêiner abriu, mas nenhum frame foi lido",
            "largura": largura or None,
            "altura": altura or None,
            "fps": fps,
            "total_frames": total if total > 0 else None,
            "codec": _codec_ffprobe(caminho) or _codec_fourcc(capture),
        }
    finally:
        capture.release()


def inventariar_video(caminho: Path, *, raiz: Path | None = None) -> InventarioVideo:
    """Monta a linha de inventário de um único arquivo (não deve lançar)."""
    caminho = caminho.resolve()
    raiz = (raiz or caminho.parent).resolve()
    try:
        relativo = str(caminho.relative_to(raiz))
    except ValueError:
        relativo = caminho.name

    nome = parsear_nome(caminho)
    tamanho = caminho.stat().st_size
    sha = _sha256(caminho)
    medidos = _medir(caminho)

    fps = medidos["fps"]
    total = medidos["total_frames"]
    duracao = (
        round(total / fps, 3)
        if isinstance(fps, (int, float)) and fps and isinstance(total, int)
        else None
    )

    return InventarioVideo(
        caminho_relativo=relativo,
        nome=caminho.name,
        tamanho_bytes=tamanho,
        duracao_s=duracao,
        largura=medidos["largura"],
        altura=medidos["altura"],
        fps=fps if isinstance(fps, (int, float)) else None,
        total_frames=total if isinstance(total, int) else None,
        codec=medidos["codec"] if isinstance(medidos["codec"], str) else None,
        cenario=nome.cenario,
        condicao=nome.condicao,
        modalidade=nome.modalidade,
        grupo=nome.grupo,
        redimensionado=nome.redimensionado,
        identificador=nome.identificador,
        tokens_desconhecidos=nome.tokens_desconhecidos,
        ambiguidades=nome.ambiguidades,
        requer_revisao=nome.requer_revisao,
        chave_grupo=nome.chave_grupo,
        estado=str(medidos["estado"]),
        erro=medidos["erro"] if isinstance(medidos["erro"], str) else None,
        sha256=sha,
    )


def inventariar(pasta: str | Path, *, recursivo: bool = True) -> list[InventarioVideo]:
    """Inventaria todos os vídeos suportados sob ``pasta``.

    Não exige nome reconhecível: qualquer extensão de vídeo conhecida entra.
    Ordenado por caminho relativo para saída determinística.
    """
    raiz = Path(pasta).resolve()
    if not raiz.is_dir():
        raise FileNotFoundError(f"pasta de vídeos não encontrada: {raiz}")
    padrao = "**/*" if recursivo else "*"
    arquivos = sorted(
        (p for p in raiz.glob(padrao) if p.is_file() and extensao_video_suportada(p.suffix)),
        key=lambda p: str(p.relative_to(raiz)),
    )
    return _marcar_duplicatas([inventariar_video(p, raiz=raiz) for p in arquivos])


def _marcar_duplicatas(itens: list[InventarioVideo]) -> list[InventarioVideo]:
    """Aponta duplicatas exatas (mesmo SHA-256) sem removê-las do inventário."""
    primeiro: dict[str, str] = {}
    resultado: list[InventarioVideo] = []
    for item in itens:
        anterior = primeiro.get(item.sha256)
        if anterior is not None:
            resultado.append(_substituir(item, duplicata_de=anterior))
        else:
            primeiro[item.sha256] = item.caminho_relativo
            resultado.append(item)
    return resultado


def _substituir(item: InventarioVideo, **mudancas: object) -> InventarioVideo:
    from dataclasses import replace

    return replace(item, **mudancas)


def resumo_inventario(itens: Sequence[InventarioVideo]) -> dict[str, object]:
    """Totais por cenário, condição, modalidade, resolução, codec e revisão."""

    def contar(campo: str) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for item in itens:
            valor = getattr(item, campo)
            chave = valor if valor is not None else "desconhecido"
            contagem[str(chave)] = contagem.get(str(chave), 0) + 1
        return dict(sorted(contagem.items()))

    resolucoes: dict[str, int] = {}
    for item in itens:
        resolucoes[item.resolucao] = resolucoes.get(item.resolucao, 0) + 1

    hash_unicos = len({i.sha256 for i in itens})
    grupos = len({i.chave_grupo for i in itens})
    return {
        "total_videos": len(itens),
        "videos_legiveis": sum(1 for i in itens if i.legivel),
        "videos_com_erro": sum(1 for i in itens if not i.legivel),
        "duplicatas_exatas": sum(1 for i in itens if i.duplicata_de is not None),
        "hashes_unicos": hash_unicos,
        "grupos_origem": grupos,
        "requerem_revisao": sum(1 for i in itens if i.requer_revisao),
        "por_cenario": contar("cenario"),
        "por_condicao": contar("condicao"),
        "por_modalidade": contar("modalidade"),
        "por_grupo": contar("grupo"),
        "por_resolucao": dict(sorted(resolucoes.items())),
        "por_codec": contar("codec"),
        "por_estado": contar("estado"),
    }


_COLUNAS_CSV = (
    "caminho_relativo",
    "nome",
    "tamanho_bytes",
    "duracao_s",
    "largura",
    "altura",
    "fps",
    "total_frames",
    "codec",
    "cenario",
    "condicao",
    "modalidade",
    "grupo",
    "redimensionado",
    "identificador",
    "requer_revisao",
    "estado",
    "erro",
    "sha256",
    "duplicata_de",
    "chave_grupo",
)


def salvar_inventario(
    itens: Sequence[InventarioVideo],
    *,
    caminho_csv: str | Path | None = None,
    caminho_json: str | Path | None = None,
) -> dict[str, str]:
    """Grava o inventário em CSV e/ou JSON. Devolve os caminhos efetivamente escritos."""
    escritos: dict[str, str] = {}
    if caminho_csv is not None:
        destino = Path(caminho_csv)
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("w", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=list(_COLUNAS_CSV), extrasaction="ignore")
            escritor.writeheader()
            for item in itens:
                escritor.writerow(item.para_dict())
        escritos["csv"] = str(destino)
    if caminho_json is not None:
        destino = Path(caminho_json)
        destino.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {
            "resumo": resumo_inventario(itens),
            "videos": [item.para_dict() for item in itens],
        }
        destino.write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        escritos["json"] = str(destino)
    return escritos


def ler_inventario(caminho: str | Path) -> list[InventarioVideo]:
    """Relê um inventário salvo em JSON (formato de :func:`salvar_inventario`)."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    registros: Iterable[dict[str, object]] = dados["videos"] if isinstance(dados, dict) else dados
    itens: list[InventarioVideo] = []
    for registro in registros:
        registro = dict(registro)
        registro.pop("resolucao", None)
        registro["tokens_desconhecidos"] = tuple(registro.get("tokens_desconhecidos") or ())
        registro["ambiguidades"] = tuple(registro.get("ambiguidades") or ())
        itens.append(InventarioVideo(**registro))  # type: ignore[arg-type]
    return itens


def nomes_para_inventario(nomes: Iterable[NomeVideo]) -> list[dict[str, object]]:
    """Atalho de inspeção: converte nomes parseados em dicionários, sem tocar em I/O."""
    return [nome.para_dict() for nome in nomes]


__all__ = [
    "TAMANHO_BLOCO_HASH",
    "InventarioVideo",
    "inventariar",
    "inventariar_video",
    "ler_inventario",
    "nomes_para_inventario",
    "resumo_inventario",
    "salvar_inventario",
]
