"""Detecta os desvios de um vídeo e grava eventos (US04), relatório e evidências.

    # fisioterapia: pose da US06
    python -m video.desvios.run --perfil fisioterapia_abducao_ombro_frontal \\
        --pose outputs/pose/PM_001-Camera17-30fps.json --patient-id 1

    # leito: pose da US06 + eventos de área crítica da US07
    python -m video.desvios.run --perfil internacao_leito \\
        --pose outputs/pose/<video>.json --eventos-areas saida/video/eventos_<video>.jsonl

Saídas em ``--saida`` (padrão ``saida/video/desvios``): ``<fonte>_eventos.jsonl``
(um ``EventoAchado`` por desvio, ou um ``sem_achados``), ``<fonte>_relatorio.json``
e ``<fonte>/*.jpg`` com o quadro de evidência de cada desvio. Os arquivos da
mesma fonte são sobrescritos a cada execução.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import cv2

from contratos import (
    Contexto,
    EmissorJsonl,
    EventoAchado,
    agora_utc,
    e_pseudonimo_de_paciente,
    pseudonimizar_internacao,
    pseudonimizar_paciente,
)
from video.pose.config import load_config as carregar_config_pose
from video.pose.sequence import PoseSequence

from .config import CAMINHO_PADRAO, carregar_config
from .eventos import ContextoEmissao, montar_evento, montar_sem_achados, relatorio
from .evidencia import salvar_evidencias
from .regras import analisar

log = logging.getLogger("video.desvios.run")


@dataclass(frozen=True)
class Execucao:
    eventos: list[EventoAchado]
    relatorio: dict[str, Any]
    caminho_eventos: Path
    caminho_relatorio: Path


def ler_eventos_jsonl(caminho: Path) -> list[EventoAchado]:
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    return [EventoAchado.de_json(linha) for linha in linhas if linha.strip()]


def _duracao_video(video: Path) -> float | None:
    captura = cv2.VideoCapture(str(video))
    try:
        fps, quadros = captura.get(cv2.CAP_PROP_FPS), captura.get(cv2.CAP_PROP_FRAME_COUNT)
    finally:
        captura.release()
    return quadros / fps if fps > 0 and quadros > 0 else None


def _paciente(bruto: str | None, eventos_areas: list[EventoAchado]) -> str:
    if bruto is None:
        if not eventos_areas:
            raise ValueError("informe --patient-id (ou --eventos-areas, que já traz o pseudônimo)")
        return eventos_areas[0].patient_id
    return bruto if e_pseudonimo_de_paciente(bruto) else pseudonimizar_paciente(bruto)


def _inicio_utc(bruto: str | None, eventos_areas: list[EventoAchado]) -> datetime:
    """Instante do quadro 0: o informado, o que a US07 usou, ou o início da análise."""
    if bruto is not None:
        return datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    for evento in eventos_areas:
        tempo = evento.evidence.features.get("tempo_video_s")
        if isinstance(tempo, (int, float)):
            return evento.timestamp - timedelta(seconds=tempo)
    return agora_utc()


def executar(
    *,
    perfil_id: str,
    config: Path = CAMINHO_PADRAO,
    pose_path: Path | None = None,
    eventos_areas_path: Path | None = None,
    video: Path | None = None,
    patient_id: str | None = None,
    inicio_utc: str | None = None,
    source_id: str | None = None,
    bed_id: str | None = None,
    encounter_id: str | None = None,
    saida: Path = Path("saida/video/desvios"),
    pose_config: Path = Path("video/pose/config.yaml"),
    evidencias: bool = True,
) -> Execucao:
    cfg = carregar_config(config)
    perfil = cfg.perfil(perfil_id)
    pose = PoseSequence.load_json(pose_path) if pose_path is not None else None
    eventos_areas = ler_eventos_jsonl(eventos_areas_path) if eventos_areas_path is not None else []
    if perfil.areas and eventos_areas_path is None:
        log.warning(
            "perfil '%s' tem regras de área, mas --eventos-areas não foi informado", perfil.id
        )
    if video is None and pose is not None:
        video = Path(pose.source_path)
    fim_video_s = None if pose is not None or video is None else _duracao_video(video)

    resultado = analisar(
        perfil, cfg, pose=pose, eventos_areas=eventos_areas, fim_video_s=fim_video_s
    )

    contexto_areas = eventos_areas[0].context if eventos_areas else None
    source_id = (
        source_id
        or (pose.video_id if pose is not None else None)
        or (contexto_areas.source_id if contexto_areas else None)
    )
    if not source_id:
        raise ValueError("informe --source-id")
    modelos: dict[str, str] = {}
    if pose is not None:
        modelos["openpose"] = f"{pose.metadata['model']}@{pose.metadata.get('module_version', '?')}"
    for evento in eventos_areas:
        modelos.update(evento.evidence.models)
    ctx = ContextoEmissao(
        patient_id=_paciente(patient_id, eventos_areas),
        inicio_utc=_inicio_utc(inicio_utc, eventos_areas),
        contexto=Contexto(
            source_id=source_id[:64],
            bed_id=bed_id or (contexto_areas.bed_id if contexto_areas else None),
            encounter_id=pseudonimizar_internacao(encounter_id) if encounter_id else None,
            procedure_type=perfil.tipo_video,
        ),
        modelos=modelos,
    )

    artefatos = {}
    if evidencias and resultado.desvios:
        if video is not None and video.is_file():
            artefatos = salvar_evidencias(
                resultado.desvios,
                video=video,
                destino=saida / source_id,
                vis=carregar_config_pose(pose_config).visualization,
                pose=pose,
            )
        else:
            log.warning("vídeo %s indisponível: desvios sem quadro de evidência", video)

    eventos = [
        montar_evento(
            d, ctx, perfil=perfil, cfg=cfg, artefatos=[artefatos[i]] if i in artefatos else []
        )
        for i, d in enumerate(resultado.desvios)
    ]
    if not eventos and resultado.confiavel:
        eventos.append(montar_sem_achados(resultado, ctx, perfil=perfil, cfg=cfg))

    caminho_eventos = saida / f"{source_id}_eventos.jsonl"
    caminho_eventos.unlink(missing_ok=True)
    EmissorJsonl(caminho_eventos).emitir_varios(eventos)
    rel = relatorio(
        resultado,
        eventos,
        perfil=perfil,
        cfg=cfg,
        fonte={
            "source_id": source_id,
            "video": str(video) if video else None,
            "pose": str(pose_path) if pose_path else None,
            "eventos_areas": str(eventos_areas_path) if eventos_areas_path else None,
        },
    )
    caminho_relatorio = saida / f"{source_id}_relatorio.json"
    caminho_relatorio.write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")
    return Execucao(eventos, rel, caminho_eventos, caminho_relatorio)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--perfil", required=True, help="perfil de config/us08_padroes.yaml")
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--pose", type=Path, help="JSON de pose da US06 (video.pose.run)")
    parser.add_argument("--eventos-areas", type=Path, help="JSONL de eventos da US07")
    parser.add_argument(
        "--video",
        type=Path,
        help="vídeo original, para o quadro de evidência (padrão: o source_path da pose)",
    )
    parser.add_argument(
        "--patient-id",
        help="pseudônimo pt_<24 hex> ou ID de origem (pseudonimizado com PSEUDONYM_KEY)",
    )
    parser.add_argument("--inicio-utc", help="início da gravação, ISO-8601 em UTC")
    parser.add_argument("--source-id")
    parser.add_argument("--bed-id")
    parser.add_argument("--encounter-id")
    parser.add_argument("--saida", type=Path, default=Path("saida/video/desvios"))
    parser.add_argument("--pose-config", type=Path, default=Path("video/pose/config.yaml"))
    parser.add_argument("--sem-evidencia", action="store_true", help="não grava os quadros JPG")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.pose is None and args.eventos_areas is None:
        parser.error("informe --pose e/ou --eventos-areas")
    execucao = executar(
        perfil_id=args.perfil,
        config=args.config,
        pose_path=args.pose,
        eventos_areas_path=args.eventos_areas,
        video=args.video,
        patient_id=args.patient_id,
        inicio_utc=args.inicio_utc,
        source_id=args.source_id,
        bed_id=args.bed_id,
        encounter_id=args.encounter_id,
        saida=args.saida,
        pose_config=args.pose_config,
        evidencias=not args.sem_evidencia,
    )
    rel = execucao.relatorio
    print(
        f"perfil {rel['perfil']} | {rel['total_desvios']} desvio(s) | "
        f"cobertura {'-' if rel['cobertura_pct'] is None else str(rel['cobertura_pct']) + '%'} | "
        f"limiares v{rel['versao_limiares']}"
    )
    for aviso in rel["avisos"]:
        print(f"  aviso: {aviso}")
    for d in rel["desvios"]:
        print(f"  [{d['severity']}] {d['event_type']} @ {d['inicio_video_s']:.1f}s: {d['resumo']}")
    print(f"eventos: {execucao.caminho_eventos}\nrelatório: {execucao.caminho_relatorio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
