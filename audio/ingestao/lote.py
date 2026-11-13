"""Ingestão de um arquivo e de um lote: um áudio ruim é registrado e o lote continua."""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from .config import ConfigIngestao
from .conversao import SUBTIPO_SAIDA, converter
from .erros import AudioIdInvalido, ErroDeAudio
from .leitura import ler_audio
from .metadados import (
    STATUS_ERRO,
    STATUS_OK,
    MetadadosAudio,
    agora_iso,
    gravar_csv,
    gravar_json,
    sha256_arquivo,
    uri_relativa,
)
from .preparo import preparar_copia_features

log = logging.getLogger("audio.ingestao")

NOME_CONVERTIDO = "convertido.wav"
NOME_FEATURES = "features.wav"
NOME_METADADOS = "metadados.json"
NOME_CSV = "metadados.csv"

_ID_INVALIDO = re.compile(r"[^A-Za-z0-9_.-]+")
_ID_VALIDO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_TAMANHO_MAX_ID = 48


@dataclass(frozen=True)
class ResultadoIngestao:
    metadados: MetadadosAudio
    convertido: Path | None
    features: Path | None
    erro: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None


def gerar_audio_id(caminho: Path, usados: Collection[str] = ()) -> str:
    """ID curto no padrão do contrato (``[A-Za-z0-9_.-]{1,64}``), único dentro de ``usados``."""
    base = _ID_INVALIDO.sub("_", caminho.stem).strip("._-")[:_TAMANHO_MAX_ID] or "audio"
    if base not in usados:
        return base
    sufixo = sha256_arquivo(caminho)[:8] if caminho.is_file() else "dup"
    candidato = f"{base}-{sufixo}"
    contador = 2
    while candidato in usados:
        candidato = f"{base}-{sufixo}-{contador}"
        contador += 1
    return candidato


def validar_audio_id(audio_id: str) -> str:
    """O ``audio_id`` vira nome de pasta: 1 a 64 caracteres ``[A-Za-z0-9_.-]``, começando por
    letra ou número (o padrão do contrato aceitaria ``..``, que sairia da pasta de saída)."""
    if not _ID_VALIDO.fullmatch(audio_id):
        raise AudioIdInvalido(
            f"audio_id {audio_id!r} inválido: use 1 a 64 caracteres [A-Za-z0-9_.-], "
            "começando por letra ou número"
        )
    return audio_id


def _nome_registrado(caminho: Path, raiz: Path | None) -> str:
    if raiz is not None:
        try:
            return caminho.resolve().relative_to(raiz.resolve()).as_posix()
        except ValueError:
            pass
    return caminho.name


def _sha256_ou_none(caminho: Path) -> str | None:
    try:
        return sha256_arquivo(caminho)
    except OSError:
        return None


def ingerir(
    caminho: str | Path,
    saida_dir: str | Path,
    cfg: ConfigIngestao,
    *,
    audio_id: str | None = None,
    origem: str | None = None,
    licenca: str | None = None,
    raiz: Path | None = None,
) -> ResultadoIngestao:
    """Converte ``caminho`` e grava ``convertido.wav``, ``features.wav`` e ``metadados.json``.

    Levanta :class:`ErroDeAudio` se o áudio for vazio, corrompido, curto/longo demais ou de
    formato não suportado. O original é só lido (o sha256 antes e depois prova isso).
    """
    caminho = Path(caminho)
    saida_dir = Path(saida_dir)
    audio_id = validar_audio_id(audio_id) if audio_id else gerar_audio_id(caminho)
    origem = origem or cfg.proveniencia.origem_padrao
    licenca = licenca or cfg.proveniencia.licenca_padrao
    pasta = saida_dir / audio_id
    pasta_nova = not pasta.exists()

    try:
        sha_antes = sha256_arquivo(caminho) if caminho.is_file() else None
        audio = ler_audio(caminho, cfg)
        convertido = pasta / NOME_CONVERTIDO
        sinal = converter(audio, convertido, cfg)
        copia = preparar_copia_features(sinal, cfg.taxa_amostragem_hz, cfg.copia_features)
        features = pasta / NOME_FEATURES
        sf.write(str(features), copia.sinal, cfg.taxa_amostragem_hz, subtype=SUBTIPO_SAIDA,
                 format="WAV")
        sha_depois = sha256_arquivo(caminho)
        metadados = MetadadosAudio(
            audio_id=audio_id,
            arquivo=_nome_registrado(caminho, raiz),
            status=STATUS_OK,
            erro_tipo=None,
            erro=None,
            sha256=sha_antes,
            tamanho_bytes=caminho.stat().st_size,
            formato_origem=audio.formato_origem,
            codec_origem=audio.codec_origem,
            decodificador=audio.decodificador,
            duracao_s=_arredondar(audio.duracao_origem_s),
            taxa_amostragem_hz=audio.taxa_origem_hz,
            canais=audio.canais_origem,
            origem=origem,
            licenca=licenca,
            convertido_uri=uri_relativa(saida_dir, convertido),
            convertido_duracao_s=_arredondar(sinal.size / cfg.taxa_amostragem_hz),
            convertido_taxa_hz=cfg.taxa_amostragem_hz,
            convertido_canais=1,
            convertido_codificacao=SUBTIPO_SAIDA,
            features_uri=uri_relativa(saida_dir, features),
            features_duracao_s=_arredondar(copia.duracao_s),
            silencio_removido_s=copia.silencio_removido_s,
            ganho_aplicado_db=copia.ganho_db,
            original_preservado=sha_antes == sha_depois,
            versao_config=cfg.versao,
            processado_em=agora_iso(),
        )
        gravar_json(metadados, pasta / NOME_METADADOS)
    except BaseException:
        if pasta_nova:
            shutil.rmtree(pasta, ignore_errors=True)
        raise
    return ResultadoIngestao(metadados=metadados, convertido=convertido, features=features)


def _arredondar(valor: float | None) -> float | None:
    return None if valor is None else round(valor, 3)


def _metadados_de_erro(
    caminho: Path, audio_id: str, erro: Exception, cfg: ConfigIngestao, origem: str, licenca: str,
    raiz: Path | None,
) -> MetadadosAudio:
    return MetadadosAudio(
        audio_id=audio_id,
        arquivo=_nome_registrado(caminho, raiz),
        status=STATUS_ERRO,
        erro_tipo=getattr(erro, "codigo", type(erro).__name__),
        erro=str(erro),
        sha256=_sha256_ou_none(caminho) if caminho.is_file() else None,
        tamanho_bytes=caminho.stat().st_size if caminho.is_file() else None,
        formato_origem=caminho.suffix.lower().lstrip(".") or None,
        codec_origem=None,
        decodificador=None,
        duracao_s=None,
        taxa_amostragem_hz=None,
        canais=None,
        origem=origem,
        licenca=licenca,
        convertido_uri=None,
        convertido_duracao_s=None,
        convertido_taxa_hz=None,
        convertido_canais=None,
        convertido_codificacao=None,
        features_uri=None,
        features_duracao_s=None,
        silencio_removido_s=None,
        ganho_aplicado_db=None,
        original_preservado=None,
        versao_config=cfg.versao,
        processado_em=agora_iso(),
    )


@dataclass(frozen=True)
class Entrada:
    """Arquivo a ingerir. ``raiz`` é a pasta de entrada: o nome registrado é relativo a ela."""

    caminho: Path
    raiz: Path | None = None


def ingerir_lote(
    entradas: Iterable[str | Path | Entrada],
    saida_dir: str | Path,
    cfg: ConfigIngestao,
    *,
    origem: str | None = None,
    licenca: str | None = None,
    raiz: Path | None = None,
) -> list[ResultadoIngestao]:
    """Ingere cada arquivo; falha de um (inclusive inesperada) é logada e não interrompe o lote.

    Grava ``metadados.csv`` em ``saida_dir`` com uma linha por arquivo, inclusive os que falharam.
    Caminhos soltos usam ``raiz``; um :class:`Entrada` traz a sua.
    """
    saida_dir = Path(saida_dir)
    origem = origem or cfg.proveniencia.origem_padrao
    licenca = licenca or cfg.proveniencia.licenca_padrao
    usados: set[str] = set()
    resultados: list[ResultadoIngestao] = []

    for item in entradas:
        entrada = item if isinstance(item, Entrada) else Entrada(Path(item), raiz)
        caminho, raiz_do_arquivo = entrada.caminho, entrada.raiz
        audio_id = gerar_audio_id(caminho, usados)
        usados.add(audio_id)
        try:
            resultado = ingerir(caminho, saida_dir, cfg, audio_id=audio_id, origem=origem,
                                licenca=licenca, raiz=raiz_do_arquivo)
        except ErroDeAudio as erro:
            log.error("[%s] %s: %s", audio_id, erro.codigo, erro)
            resultado = ResultadoIngestao(
                _metadados_de_erro(caminho, audio_id, erro, cfg, origem, licenca, raiz_do_arquivo),
                None, None, erro)
        except Exception as erro:
            log.exception("[%s] erro inesperado ao ingerir '%s'", audio_id, caminho.name)
            resultado = ResultadoIngestao(
                _metadados_de_erro(caminho, audio_id, erro, cfg, origem, licenca, raiz_do_arquivo),
                None, None, erro)
        else:
            log.info("[%s] ok: %.1f s, %s", audio_id, resultado.metadados.convertido_duracao_s,
                     resultado.metadados.formato_origem)
        resultados.append(resultado)

    gravar_csv([r.metadados for r in resultados], saida_dir / NOME_CSV)
    return resultados
