"""Extração de frames dos vídeos inventariados — US07.

Amostra um frame a cada intervalo de segundos (configurável), limita o total por
vídeo e detecta frames quase idênticos para não inflar o dataset com redundância.
Cada frame guarda o rastro completo: vídeo de origem, índice do quadro, timestamp
original, hash de conteúdo e metadados do nome.

Estrutura de saída::

    dataset_us07/frames/<video_id>/frame_000001.jpg
    dataset_us07/metadata/frames_manifest.jsonl

O manifesto é a fonte da verdade: relaciona cada ``frame_id`` ao arquivo de
origem e permite retomar a execução sem repetir trabalho. Nenhum frame é extraído
sem entrar no manifesto, e o timestamp vem do ``indice/fps`` do leitor — nunca de
um chute.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2

from .inventario import InventarioVideo
from .leitor_video import ErroDeVideo, LeitorVideo

log = logging.getLogger(__name__)

NOME_MANIFESTO = "frames_manifest.jsonl"
"""Arquivo JSONL (um registro por linha) dentro de ``metadata/``."""

INTERVALO_PADRAO_S = 1.0
MAX_FRAMES_PADRAO = 30
LARGURA_MAX_PADRAO: int | None = None
QUALIDADE_JPEG_PADRAO = 90
DISTANCIA_SEMELHANCA_PADRAO = 3
"""Distância de Hamming (aHash 8x8) a partir da qual dois frames são redundantes."""

MIN_FRAMES_POR_VIDEO_PADRAO: int | None = None
"""Mínimo de frames únicos por vídeo; se o dedup retiver menos, os descartes mais
diversos são promovidos de volta. ``None`` desativa a garantia."""


@dataclass(frozen=True, slots=True)
class ConfiguracaoExtracao:
    """Parâmetros da extração; tudo explícito e reproduzível."""

    intervalo_s: float = INTERVALO_PADRAO_S
    max_frames: int | None = MAX_FRAMES_PADRAO
    limite_quadros_leitura: int | None = None
    largura_max: int | None = LARGURA_MAX_PADRAO
    qualidade_jpeg: int = QUALIDADE_JPEG_PADRAO
    detectar_semelhantes: bool = True
    distancia_semelhanca: int = DISTANCIA_SEMELHANCA_PADRAO
    min_frames_por_video: int | None = MIN_FRAMES_POR_VIDEO_PADRAO
    retomar: bool = True

    def __post_init__(self) -> None:
        if self.intervalo_s <= 0:
            raise ValueError("intervalo_s precisa ser maior que zero")
        if self.max_frames is not None and self.max_frames <= 0:
            raise ValueError("max_frames precisa ser positivo quando definido")
        if not 0 < self.qualidade_jpeg <= 100:
            raise ValueError("qualidade_jpeg precisa estar em (0, 100]")
        if self.distancia_semelhanca < 0:
            raise ValueError("distancia_semelhanca não pode ser negativa")
        if self.min_frames_por_video is not None and self.min_frames_por_video < 1:
            raise ValueError("min_frames_por_video precisa ser positivo quando definido")
        if (
            self.min_frames_por_video is not None
            and self.max_frames is not None
            and self.min_frames_por_video > self.max_frames
        ):
            raise ValueError("min_frames_por_video não pode exceder max_frames")


@dataclass(frozen=True, slots=True)
class FrameExtraido:
    """Um frame amostrado (ou descartado por redundância) e seu rastro."""

    frame_id: str
    video_id: str
    origem: str
    indice_quadro: int
    timestamp_s: float
    largura: int
    altura: int
    sha256: str
    a_hash: str
    duplicado: bool
    arquivo_imagem: str | None
    cenario: str | None
    condicao: str | None
    modalidade: str | None
    grupo: str | None
    chave_grupo: str

    def para_dict(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "video_id": self.video_id,
            "origem": self.origem,
            "indice_quadro": self.indice_quadro,
            "timestamp_s": round(self.timestamp_s, 4),
            "largura": self.largura,
            "altura": self.altura,
            "sha256": self.sha256,
            "a_hash": self.a_hash,
            "duplicado": self.duplicado,
            "arquivo_imagem": self.arquivo_imagem,
            "cenario": self.cenario,
            "condicao": self.condicao,
            "modalidade": self.modalidade,
            "grupo": self.grupo,
            "chave_grupo": self.chave_grupo,
        }


@dataclass(slots=True)
class RelatorioExtracao:
    """Resultado agregado de uma execução de extração."""

    frames: list[FrameExtraido] = field(default_factory=list)
    videos_processados: int = 0
    videos_com_erro: int = 0
    falhas: list[dict[str, str]] = field(default_factory=list)
    videos_pulados: int = 0

    @property
    def frames_unicos(self) -> int:
        return sum(1 for f in self.frames if not f.duplicado)

    @property
    def frames_duplicados(self) -> int:
        return sum(1 for f in self.frames if f.duplicado)

    def para_dict(self) -> dict[str, object]:
        return {
            "videos_processados": self.videos_processados,
            "videos_pulados": self.videos_pulados,
            "videos_com_erro": self.videos_com_erro,
            "frames_extraidos": self.frames_unicos,
            "frames_duplicados_descartados": self.frames_duplicados,
            "falhas": self.falhas,
        }


def video_id_de(caminho: str | Path) -> str:
    """Identificador estável e seguro para nome de pasta a partir do nome do vídeo."""
    stem = Path(caminho).stem
    seguro = "".join(c if c.isalnum() or c in "-." else "_" for c in stem)
    return seguro or "video"


def _a_hash(imagem: Any) -> str:
    """Hash perceptual 8x8 (average hash), robusto a variações leves de compressão."""
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    reduzido = cv2.resize(cinza, (8, 8), interpolation=cv2.INTER_AREA)
    media = float(reduzido.mean())
    bits = (reduzido > media).flatten()
    return "".join(str(int(b)) for b in bits)


def _distancia_hamming(a: str, b: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(a, b, strict=False))


def _sha256_imagem(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(1 << 20):
            digest.update(bloco)
    return digest.hexdigest()


def _redimensionar(imagem: Any, largura_max: int | None) -> Any:
    if largura_max is None:
        return imagem
    altura, largura = imagem.shape[:2]
    if largura <= largura_max:
        return imagem
    escala = largura_max / largura
    return cv2.resize(
        imagem,
        (largura_max, max(1, round(altura * escala))),
        interpolation=cv2.INTER_AREA,
    )


class ManifestoFrames:
    """Manifesto JSONL de frames, com leitura para retomada e escrita determinística."""

    def __init__(self, caminho: str | Path) -> None:
        self.caminho = Path(caminho)
        self._registros: dict[str, dict[str, object]] = {}
        if self.caminho.is_file():
            self._carregar()

    def _carregar(self) -> None:
        for linha in self.caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            registro = json.loads(linha)
            self._registros[str(registro["frame_id"])] = registro

    def __contains__(self, frame_id: str) -> bool:
        return frame_id in self._registros

    def __len__(self) -> int:
        return len(self._registros)

    def ids_do_video(self, video_id: str) -> set[str]:
        return {fid for fid, reg in self._registros.items() if reg.get("video_id") == video_id}

    def adicionar(self, frame: FrameExtraido) -> None:
        self._registros[frame.frame_id] = frame.para_dict()

    def adicionar_varios(self, frames: Iterable[FrameExtraido]) -> None:
        for frame in frames:
            self.adicionar(frame)

    def frames(self) -> list[dict[str, object]]:
        return [self._registros[fid] for fid in sorted(self._registros)]

    def salvar(self) -> Path:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        linhas = [
            json.dumps(registro, ensure_ascii=False, sort_keys=True)
            for registro in self.frames()
        ]
        self.caminho.write_text("\n".join(linhas) + ("\n" if linhas else ""), encoding="utf-8")
        return self.caminho


def extrair_frames(
    caminho_video: str | Path,
    *,
    destino_raiz: str | Path,
    config: ConfiguracaoExtracao | None = None,
    manifesto: ManifestoFrames | None = None,
    meta: InventarioVideo | None = None,
) -> list[FrameExtraido]:
    """Extrai frames de um vídeo e grava as imagens sob ``destino_raiz/frames/``.

    Args:
        caminho_video: caminho físico do arquivo de vídeo.
        destino_raiz: raiz do dataset (contém ``frames/`` e ``metadata/``).
        config: parâmetros de amostragem.
        manifesto: manifesto a preencher; útil para reutilizar entre vídeos.
        meta: linha do inventário do vídeo, quando disponível. Fornece cenário,
            condição, modalidade e a chave de grupo ao manifesto.

    Returns:
        Lista de :class:`FrameExtraido` (inclui os descartados por redundância).

    Raises:
        FileNotFoundError: o vídeo não existe.
        ErroDeVideo: o OpenCV não abriu ou não devolveu frames.
    """
    config = config or ConfiguracaoExtracao()
    caminho_video = Path(caminho_video)
    if not caminho_video.is_file():
        raise FileNotFoundError(f"vídeo não encontrado para extração: {caminho_video}")

    raiz = Path(destino_raiz)
    vid = video_id_de(caminho_video)
    pasta_frames = raiz / "frames" / vid
    pasta_frames.mkdir(parents=True, exist_ok=True)

    origem = meta.caminho_relativo if meta is not None else str(caminho_video)

    extraidos: list[FrameExtraido] = []
    hash_anterior: str | None = None
    indice_imagem = 0
    proximo_alvo = 0.0
    eps = 1e-9
    hashes_mantidos: list[str] = []
    garantir_minimo = config.detectar_semelhantes and config.min_frames_por_video is not None
    descartados_buffer: list[tuple[int, str, Any]] = []

    with LeitorVideo(caminho_video, max_quadros=config.limite_quadros_leitura) as leitor:
        for quadro in leitor.quadros():
            if quadro.tempo_s + eps < proximo_alvo:
                continue
            proximo_alvo = quadro.tempo_s + config.intervalo_s

            imagem = _redimensionar(quadro.imagem, config.largura_max)
            altura, largura = imagem.shape[:2]
            a_hash = _a_hash(imagem)
            duplicado = (
                config.detectar_semelhantes
                and hash_anterior is not None
                and _distancia_hamming(a_hash, hash_anterior) <= config.distancia_semelhanca
            )

            if duplicado:
                frame_dup = _frame(
                    vid,
                    origem,
                    meta,
                    indice_quadro=quadro.indice,
                    timestamp_s=quadro.tempo_s,
                    largura=largura,
                    altura=altura,
                    sha256="",
                    a_hash=a_hash,
                    duplicado=True,
                    arquivo_imagem=None,
                )
                if garantir_minimo:
                    descartados_buffer.append((len(extraidos), a_hash, imagem))
                extraidos.append(frame_dup)
                continue

            indice_imagem += 1
            frame_id = f"{vid}/frame_{indice_imagem:06d}"
            destino = pasta_frames / f"frame_{indice_imagem:06d}.jpg"
            _gravar_jpeg(destino, imagem, config.qualidade_jpeg)
            relativo_imagem = (
                str(destino.relative_to(raiz)) if _sob(destino, raiz) else str(destino)
            )

            frame = _frame(
                vid,
                origem,
                meta,
                indice_quadro=quadro.indice,
                timestamp_s=quadro.tempo_s,
                largura=largura,
                altura=altura,
                sha256=_sha256_imagem(destino),
                a_hash=a_hash,
                duplicado=False,
                arquivo_imagem=relativo_imagem,
                frame_id=frame_id,
            )
            extraidos.append(frame)
            hashes_mantidos.append(a_hash)
            hash_anterior = a_hash

            if config.max_frames is not None and indice_imagem >= config.max_frames:
                break

    if garantir_minimo:
        _promover_minimo(
            extraidos,
            descartados_buffer,
            hashes_mantidos,
            pasta_frames=pasta_frames,
            raiz=raiz,
            vid=vid,
            config=config,
            indice_imagem=indice_imagem,
        )

    if manifesto is not None:
        manifesto.adicionar_varios(extraidos)
    return extraidos


def _promover_minimo(
    extraidos: list[FrameExtraido],
    descartados_buffer: list[tuple[int, str, Any]],
    hashes_mantidos: list[str],
    *,
    pasta_frames: Path,
    raiz: Path,
    vid: str,
    config: ConfiguracaoExtracao,
    indice_imagem: int,
) -> None:
    """Promove descartes de volta para garantir ``min_frames_por_video``.

    Escolhe sempre o descartado mais diverso dos frames já mantidos (maior
    distância de Hamming mínima ao conjunto mantido), de forma determinística.
    """
    minimo = config.min_frames_por_video or 0
    while indice_imagem < minimo and descartados_buffer:
        melhor = max(
            descartados_buffer,
            key=lambda item: min(
                (_distancia_hamming(item[1], h) for h in hashes_mantidos), default=0
            ),
        )
        pos, _, imagem = melhor
        descartados_buffer.remove(melhor)
        indice_imagem += 1
        frame_id = f"{vid}/frame_{indice_imagem:06d}"
        destino = pasta_frames / f"frame_{indice_imagem:06d}.jpg"
        _gravar_jpeg(destino, imagem, config.qualidade_jpeg)
        relativo_imagem = str(destino.relative_to(raiz)) if _sob(destino, raiz) else str(destino)
        extraidos[pos] = replace(
            extraidos[pos],
            frame_id=frame_id,
            arquivo_imagem=relativo_imagem,
            sha256=_sha256_imagem(destino),
            duplicado=False,
        )
        hashes_mantidos.append(extraidos[pos].a_hash)


def _frame(
    video_id: str,
    origem: str,
    meta: InventarioVideo | None,
    *,
    indice_quadro: int,
    timestamp_s: float,
    largura: int,
    altura: int,
    sha256: str,
    a_hash: str,
    duplicado: bool,
    arquivo_imagem: str | None,
    frame_id: str | None = None,
) -> FrameExtraido:
    return FrameExtraido(
        frame_id=frame_id or f"{video_id}/dup_{indice_quadro:06d}",
        video_id=video_id,
        origem=origem,
        indice_quadro=indice_quadro,
        timestamp_s=timestamp_s,
        largura=largura,
        altura=altura,
        sha256=sha256,
        a_hash=a_hash,
        duplicado=duplicado,
        arquivo_imagem=arquivo_imagem,
        cenario=meta.cenario if meta else None,
        condicao=meta.condicao if meta else None,
        modalidade=meta.modalidade if meta else None,
        grupo=meta.grupo if meta else None,
        chave_grupo=meta.chave_grupo if meta else video_id,
    )


def _gravar_jpeg(caminho: Path, imagem: Any, qualidade: int) -> None:
    parametros = [int(cv2.IMWRITE_JPEG_QUALITY), qualidade]
    if not cv2.imwrite(str(caminho), imagem, parametros):
        raise ErroDeVideo(f"não foi possível gravar o frame {caminho}")


def _sob(caminho: Path, raiz: Path) -> bool:
    try:
        caminho.relative_to(raiz)
    except ValueError:
        return False
    return True


def caminho_videos_candidato(item: InventarioVideo, raiz_videos: Path) -> Path:
    """Resolve o arquivo físico de uma linha de inventário contra a raiz de vídeos."""
    return raiz_videos / item.caminho_relativo


def extrair_lote(
    itens: Sequence[InventarioVideo],
    *,
    raiz_videos: str | Path,
    destino_raiz: str | Path,
    config: ConfiguracaoExtracao | None = None,
) -> RelatorioExtracao:
    """Extrai frames de vários vídeos, retomando pelo manifesto e tolerando falhas."""
    config = config or ConfiguracaoExtracao()
    raiz = Path(destino_raiz)
    manifesto = ManifestoFrames(raiz / "metadata" / NOME_MANIFESTO)
    relatorio = RelatorioExtracao()
    raiz_videos = Path(raiz_videos)

    for item in itens:
        caminho = caminho_videos_candidato(item, raiz_videos)
        if config.retomar and manifesto.ids_do_video(video_id_de(item.nome)):
            relatorio.videos_pulados += 1
            continue
        try:
            frames = extrair_frames(
                caminho,
                destino_raiz=raiz,
                config=config,
                manifesto=manifesto,
                meta=item,
            )
        except Exception as erro:
            log.error("falha ao extrair frames de %s: %s", item.nome, erro)
            relatorio.videos_com_erro += 1
            relatorio.falhas.append({"video": item.caminho_relativo, "erro": str(erro)})
            continue
        relatorio.videos_processados += 1
        relatorio.frames.extend(frames)

    manifesto.salvar()
    return relatorio


def ler_manifesto(caminho: str | Path) -> list[dict[str, object]]:
    """Lê o manifesto JSONL e devolve os registros em ordem de ``frame_id``."""
    return ManifestoFrames(caminho).frames()


__all__ = [
    "DISTANCIA_SEMELHANCA_PADRAO",
    "INTERVALO_PADRAO_S",
    "MAX_FRAMES_PADRAO",
    "MIN_FRAMES_POR_VIDEO_PADRAO",
    "NOME_MANIFESTO",
    "ConfiguracaoExtracao",
    "FrameExtraido",
    "ManifestoFrames",
    "RelatorioExtracao",
    "caminho_videos_candidato",
    "extrair_frames",
    "extrair_lote",
    "ler_manifesto",
    "video_id_de",
]
