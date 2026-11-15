"""Biomarcadores vocais (US12) e achados de áudio (US14) a partir de áudios de consultas.

    # áudio só do paciente (ex.: áudio de voz do WhatsApp); sem transcrição, segmentos por energia
    python -m audio.vocal.run --audio voz.ogg --patient-id 10000032 \
        --recorded-at 2026-10-01T13:00:00Z

    # consulta com médico e paciente: transcrição do Azure Speech (US11) com diarização
    python -m audio.vocal.run --audio consulta.wav --patient-id 10000032 \\
        --recorded-at 2026-10-01T13:00:00Z --transcrever --medico-falante 1

Cada áudio é ingerido (``audio.ingestao``: WAV 16 kHz mono, original intacto) e analisado. Saídas em
``--saida`` (padrão ``saida/audio``), em ``<audio_id>/``: ``convertido.wav``, ``features.wav``,
``metadados.json``, ``eventos.jsonl`` (``EventoAchado``) e ``relatorio.json``. Os eventos também
vão ao barramento local (``EVENTOS_JSONL``); o motor de alertas (US19) recebe os de severidade
baixa ou acima e envia os alertas ao fim da execução.

Vários ``--audio`` são tratados como do mesmo paciente e da mesma gravação (``--recorded-at``), na
ordem dada; a baseline do paciente usa os anteriores. Para gravações em horários diferentes,
rode o comando uma vez por gravação.

``PSEUDONYM_KEY`` precisa estar no ambiente (``.env``). Saída: 0 = tudo certo; 1 = configuração ou
ambiente inválido; 2 = algum áudio falhou.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from audio.ingestao.config import CAMINHO_PADRAO as CONFIG_INGESTAO_PADRAO
from audio.ingestao.config import ConfigIngestao
from audio.ingestao.config import carregar_config as carregar_config_ingestao
from audio.ingestao.erros import ErroDeAudio
from audio.ingestao.lote import ResultadoIngestao, ingerir
from audio.text.events import patient_pseudonym, relative_uri
from contratos import BarramentoLocal, ChavePseudonimizacaoAusente, Emissor, EmissorJsonl

from .analise import EntradaAnalise, ResultadoVocal, analisar
from .baseline import RepositorioBaseline
from .config import CAMINHO_PADRAO, ConfigVocal, ErroDeConfiguracao, carregar_config
from .erros import ErroDeAnalise
from .respiratorio import DetectorRespiratorio, ErroDeModelo, carregar_detector

log = logging.getLogger("audio.vocal.run")

NOME_EVENTOS = "eventos.jsonl"
NOME_RELATORIO = "relatorio.json"
Transcritor = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, eq=False)
class Execucao:
    ingestao: ResultadoIngestao
    resultado: ResultadoVocal
    caminho_eventos: Path
    caminho_relatorio: Path
    caminho_transcricao: Path | None


def transcrever_azure(
    convertido: Path, *, audio_id: str, patient_id: str, recorded_at: str,
    medico_falante: int | None,
) -> Mapping[str, Any]:
    """Transcrição rápida do Azure Speech (US11), com diarização, sobre o WAV convertido.

    O JSON é gravado ao lado do áudio (``convertido.json``) com o ``patient_id`` já
    pseudonimizado: o ID de origem nunca vai para o disco.
    """
    from app import config as app_config
    from app.transcription_client import TranscricaoError
    from app.transcription_service import transcrever_e_salvar

    try:
        return transcrever_e_salvar(
            convertido, convertido.parent,
            endpoint=app_config.require_azure_speech_endpoint(),
            chave=app_config.require_azure_speech_key(),
            medico_falante=medico_falante, audio_id=audio_id, patient_id=patient_id,
            recorded_at=recorded_at,
        )
    except (TranscricaoError, RuntimeError) as erro:
        raise ErroDeAnalise(f"a transcrição (Azure Speech) falhou: {erro}") from erro


def processar(
    origem: str | Path,
    *,
    patient_id: str,
    recorded_at: datetime,
    saida: Path,
    cfg_ingestao: ConfigIngestao,
    cfg: ConfigVocal,
    audio_id: str | None = None,
    medico_falante: int | None = None,
    transcricao: Mapping[str, Any] | None = None,
    transcrever: bool = False,
    transcritor: Transcritor = transcrever_azure,
    detector: DetectorRespiratorio | None = None,
    repositorio: RepositorioBaseline | None = None,
    atualizar_baseline: bool = True,
    emissor: Emissor | None = None,
    bed_id: str | None = None,
    encounter_id: str | None = None,
    origem_audio: str | None = None,
    licenca: str | None = None,
    sintetico: bool = False,
) -> Execucao:
    """Ingere ``origem``, analisa e grava os arquivos do áudio.

    Levanta ``ErroDeAudio`` (áudio inválido) ou ``ErroDeAnalise`` (não dá para analisar).
    """
    paciente = patient_pseudonym(patient_id)  # sem a chave, falha antes de converter qualquer coisa
    ingestao = ingerir(origem, saida, cfg_ingestao, audio_id=audio_id, origem=origem_audio,
                       licenca=licenca)
    convertido = ingestao.convertido
    if convertido is None:  # ingerir levanta em vez de devolver sem arquivo; só para o tipo
        raise ErroDeAnalise("a ingestão não gerou o áudio convertido")
    id_do_audio = ingestao.metadados.audio_id
    pasta = convertido.parent

    caminho_transcricao = None
    if transcricao is None and transcrever:
        transcricao = transcritor(
            convertido, audio_id=id_do_audio, patient_id=paciente,
            recorded_at=recorded_at.strftime("%Y-%m-%dT%H:%M:%SZ"), medico_falante=medico_falante)
        caminho_transcricao = pasta / "convertido.json"

    caminho_eventos = pasta / NOME_EVENTOS
    caminho_relatorio = pasta / NOME_RELATORIO
    resultado = analisar(
        convertido,
        EntradaAnalise(
            audio_id=id_do_audio, patient_id=paciente, recorded_at=recorded_at,
            medico_falante=medico_falante, transcricao=transcricao, bed_id=bed_id,
            encounter_id=encounter_id, sintetico=sintetico,
            formato_origem=ingestao.metadados.formato_origem),
        cfg_ingestao, cfg, detector=detector, repositorio=repositorio,
        atualizar_baseline=atualizar_baseline, emissor=emissor,
        relatorio_uri=relative_uri(caminho_relatorio),
    )
    caminho_eventos.unlink(missing_ok=True)
    EmissorJsonl(caminho_eventos).emitir_varios(resultado.eventos)
    caminho_relatorio.write_text(
        json.dumps(resultado.relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    return Execucao(ingestao, resultado, caminho_eventos, caminho_relatorio, caminho_transcricao)


def _instante_utc(texto: str) -> datetime:
    try:
        instante = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"data/hora inválida: {texto!r}") from None
    deslocamento = instante.utcoffset()
    if deslocamento is None or deslocamento.total_seconds() != 0:
        raise argparse.ArgumentTypeError("use UTC, ex.: 2026-10-01T13:00:00Z")
    return instante


def _resumo(execucao: Execucao) -> str:
    r = execucao.resultado
    tipos = ", ".join(f"{t}={n}" for t, n in r.relatorio["por_tipo"].items())
    return (f"[{r.audio_id}] classe: {r.classe} | eventos: {tipos} | alertáveis: "
            f"{r.relatorio['alertaveis']} | {execucao.caminho_relatorio}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # antes do parser: os padrões abaixo leem variáveis do .env
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audio", "-a", type=Path, nargs="+", required=True)
    parser.add_argument("--patient-id", required=True,
                        help="ID de origem ou pseudônimo (pt_...); o de origem é pseudonimizado")
    parser.add_argument("--recorded-at", required=True, type=_instante_utc,
                        help="início da gravação, ISO-8601 em UTC")
    parser.add_argument("--audio-id",
                        help="ID do áudio (só com um --audio); padrão: nome do arquivo")
    parser.add_argument("--medico-falante", type=int,
                        help="número do falante que é o médico, na diarização")
    parser.add_argument("--transcricao", type=Path,
                        help="JSON da US11 já pronto (só com um --audio)")
    parser.add_argument("--transcrever", action="store_true",
                        help="transcreve com o Azure Speech (US11) antes de analisar")
    parser.add_argument("--saida", type=Path,
                        default=Path(os.getenv("AUDIO_SAIDA_DIR", "saida/audio")))
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--config-ingestao", type=Path, default=CONFIG_INGESTAO_PADRAO)
    parser.add_argument("--modelo-yamnet", type=Path,
                        default=Path(os.getenv("AUDIO_YAMNET_MODEL_PATH",
                                               "models/audio/yamnet.onnx")))
    parser.add_argument("--bed-id")
    parser.add_argument("--encounter-id")
    parser.add_argument("--origem", help="origem da gravação (registrada nos metadados)")
    parser.add_argument("--licenca", help="licença ou base legal de uso (registrada nos metadados)")
    parser.add_argument("--sem-atualizar-baseline", action="store_true")
    parser.add_argument("--sem-alertas", action="store_true",
                        help="não liga o motor de alertas (os eventos ainda vão ao barramento)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    if len(args.audio) > 1 and (args.audio_id or args.transcricao):
        parser.error("--audio-id e --transcricao só valem com um único --audio")

    try:
        cfg_ingestao = carregar_config_ingestao(args.config_ingestao)
        cfg = carregar_config(args.config)
        patient_pseudonym(args.patient_id)
        detector = carregar_detector(cfg.respiratorio, args.modelo_yamnet)
        transcricao = (json.loads(args.transcricao.read_text(encoding="utf-8"))
                       if args.transcricao else None)
    except ChavePseudonimizacaoAusente as erro:
        log.error("%s", erro)
        return 1
    except (ErroDeConfiguracao, ErroDeModelo, OSError, ValueError) as erro:
        log.error("%s", erro)
        return 1

    barramento = BarramentoLocal.do_ambiente()
    servico = None
    if not args.sem_alertas:
        from alertas import ConfigAlertas, construir

        servico = construir(ConfigAlertas.from_env())
        servico.assinar(barramento)
    repositorio = RepositorioBaseline(args.saida / "baseline")

    erros = 0
    for caminho in args.audio:
        try:
            execucao = processar(
                caminho, patient_id=args.patient_id, recorded_at=args.recorded_at,
                saida=args.saida, cfg_ingestao=cfg_ingestao, cfg=cfg, audio_id=args.audio_id,
                medico_falante=args.medico_falante, transcricao=transcricao,
                transcrever=args.transcrever, detector=detector, repositorio=repositorio,
                atualizar_baseline=not args.sem_atualizar_baseline, emissor=barramento,
                bed_id=args.bed_id, encounter_id=args.encounter_id, origem_audio=args.origem,
                licenca=args.licenca)
        except (ErroDeAudio, ErroDeAnalise) as erro:
            log.error("[%s] %s", caminho.name, erro)
            erros += 1
        except Exception:
            log.exception("[%s] erro inesperado", caminho.name)
            erros += 1
        else:
            log.info("%s", _resumo(execucao))

    if servico is not None:
        asyncio.run(servico.processar_pendentes())
    for falha in barramento.falhas:
        log.error("assinante %s falhou no evento %s: %s", falha.assinante, falha.event_id,
                  falha.erro)
    return 0 if erros == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
