"""Configuração geral do pipeline ponta a ponta da US20."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from contratos.pseudonimizacao import e_pseudonimo_de_paciente, pseudonimizar_paciente


@dataclass(frozen=True)
class ConfigPipeline:
    """Parâmetros de execução do pipeline integrado."""

    modo: str = "simulado"  # "real" | "simulado"
    paciente_id: str = "pt_8e5b4d1cfc60a16434dc03f0"
    leito_id: str = "UTI-07"
    fator_velocidade: float = 30.0  # 0.0 = instantâneo; > 0 = reprodução temporizada
    saida_dir: Path = Path("saida/demo_ponta_a_ponta")
    notificar_smtp: bool = False
    video_path: Path | None = None
    audio_path: Path | None = None
    max_frames_video: int = 50
    chave_pseudonimizacao: str = "chave-padrao-demonstracao-us20-32bytes-000000"

    @classmethod
    def from_env(cls, **sobrescritas) -> ConfigPipeline:
        chave = (
            os.getenv("PSEUDONYM_KEY")
            or "chave-padrao-demonstracao-us20-32bytes-000000"
        )
        paciente_raw = sobrescritas.get("paciente_id") or "demo-uti07"
        if not e_pseudonimo_de_paciente(str(paciente_raw)):
            paciente = pseudonimizar_paciente(paciente_raw, chave=chave)
        else:
            paciente = str(paciente_raw)

        modo = sobrescritas.get("modo") or os.getenv("PIPELINE_MODO", "simulado")
        velocidade = sobrescritas.get("fator_velocidade")
        if velocidade is None:
            velocidade = float(os.getenv("PIPELINE_VELOCIDADE", "30.0"))

        saida = Path(
            sobrescritas.get("saida_dir")
            or os.getenv("PIPELINE_SAIDA_DIR", "saida/demo_ponta_a_ponta")
        )

        return cls(
            modo=modo.strip().lower(),
            paciente_id=paciente,
            leito_id=sobrescritas.get("leito_id") or "UTI-07",
            fator_velocidade=float(velocidade),
            saida_dir=saida,
            notificar_smtp=bool(sobrescritas.get("notificar_smtp", False)),
            video_path=sobrescritas.get("video_path"),
            audio_path=sobrescritas.get("audio_path"),
            max_frames_video=int(sobrescritas.get("max_frames_video", 50)),
            chave_pseudonimizacao=chave,
        )
