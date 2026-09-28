"""Gera os exemplos de eventos em contratos/exemplos/ (um por modalidade).

Todos contam a mesma história de demonstração: um paciente com SpO2 em
queda e fala com sinais de dispneia, cuja combinação eleva o risco além de
cada sinal isolado. Os pseudônimos usam uma chave de exemplo pública; nunca
use essa chave com dados reais.

    python scripts/gerar_exemplos_contrato.py
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from contratos import EventoAchado, pseudonimizar_internacao, pseudonimizar_paciente  # noqa: E402

CHAVE_EXEMPLO = "chave-de-exemplo-publica-nao-usar-em-producao-0000"
DESTINO = RAIZ / "contratos" / "exemplos"

PACIENTE = pseudonimizar_paciente(10000032, chave=CHAVE_EXEMPLO)
INTERNACAO = pseudonimizar_internacao(22595853, chave=CHAVE_EXEMPLO)
CONTEXTO = {"encounter_id": INTERNACAO, "bed_id": "UTI-07"}


def t(hora: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-26T{hora}+00:00").astimezone(timezone.utc)


def uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


EXEMPLOS: dict[str, dict] = {
    "sinais_vitais_news2_resposta_urgente": dict(
        event_id=uid(1), patient_id=PACIENTE, modality="sinais_vitais",
        event_type="news2_resposta_urgente",
        timestamp=t("17:02:00"), detected_at=t("17:02:03.412"), ingested_at=t("17:02:01.050"),
        window=dict(start=t("16:57:00"), end=t("17:02:00")),
        score=0.71, severity="media",
        evidence=dict(
            summary="NEWS2 = 6 (SpO2 92%, FR 23 irpm, FC 112 bpm): resposta urgente",
            features={"news2_total": 6, "spo2_pct": 92, "fr_irpm": 23, "fc_bpm": 112,
                      "pas_mmhg": 118, "news2_spo2": 2, "news2_fr": 2, "news2_fc": 2},
            thresholds={"news2_urgente_total": 5, "news2_emergencia_total": 7, "news2_parametro_unico": 3},
            models={"news2": "RCP-2017"},
            artifacts=[dict(kind="serie_temporal", media_type="application/json",
                            uri=f"saida/sinais/{PACIENTE}/2026-09-26T17-02.json")],
        ),
        model_version="anomalias-sinais@0.1.0", context=CONTEXTO,
    ),
    "audio_dificuldade_respiratoria": dict(
        event_id=uid(2), patient_id=PACIENTE, modality="audio",
        event_type="dificuldade_respiratoria",
        timestamp=t("17:05:12"), detected_at=t("17:06:40.120"), ingested_at=t("17:05:30.000"),
        window=dict(start=t("17:05:12"), end=t("17:05:41")),
        score=0.78, severity="alta",
        evidence=dict(
            summary="Pausas respiratórias frequentes e HNR baixo na fala do paciente; indicativo para avaliação clínica",
            features={"hnr_db": 14.2, "jitter_local_pct": 1.31, "shimmer_local_pct": 4.6,
                      "proporcao_pausas": 0.41, "yamnet_breathing": 0.83},
            thresholds={"hnr_db_min": 20, "jitter_local_pct_max": 1.04, "shimmer_local_pct_max": 3.81},
            models={"opensmile": "eGeMAPSv02", "yamnet": "tfhub-1"},
            artifacts=[dict(kind="audio_segment", media_type="audio/wav", start_ms=192000, end_ms=221000,
                            uri="saida/audio/consulta-0042/seg-0192000.wav")],
        ),
        model_version="audio-achados@0.1.0",
        context={**CONTEXTO, "source_id": "consulta-0042"},
    ),
    "texto_termo_critico": dict(
        event_id=uid(3), patient_id=PACIENTE, modality="texto",
        event_type="termo_critico",
        timestamp=t("17:05:20"), detected_at=t("17:06:52.870"), ingested_at=t("17:05:30.000"),
        score=0.95, severity="alta",
        evidence=dict(
            summary="Paciente relata falta de ar ao falar (afirmado, sem negação)",
            features={"termo": "falta de ar", "entidade_en": "shortness of breath",
                      "categoria": "MEDICAL_CONDITION", "negacao": False, "falante": "paciente",
                      "confianca": 0.95},
            models={"amazon-transcribe": "pt-BR", "amazon-translate": "pt-en",
                    "comprehend-medical": "DetectEntitiesV2"},
            artifacts=[dict(kind="transcricao", media_type="application/json", start_ms=200000, end_ms=204500,
                            uri="saida/audio/consulta-0042/transcricao.json")],
        ),
        model_version="texto-nlp@0.1.0",
        context={**CONTEXTO, "source_id": "consulta-0042"},
    ),
    "video_postura_fora_da_faixa": dict(
        event_id=uid(4), patient_id=PACIENTE, modality="video",
        event_type="postura_fora_da_faixa",
        timestamp=t("15:20:08.400"), detected_at=t("15:31:02.004"), ingested_at=t("15:25:00.000"),
        window=dict(start=t("15:20:08.400"), end=t("15:20:10.100")),
        score=0.66, severity="media",
        evidence=dict(
            summary="Flexão de joelho direito de 148° por 1,7 s, acima da faixa esperada de 0–135°",
            features={"articulacao": "joelho_direito", "angulo_max_graus": 148.0, "duracao_s": 1.7,
                      "confianca_keypoints_media": 0.81},
            thresholds={"faixa_max_graus": 135, "janela_min_s": 1.0, "confianca_min_keypoint": 0.3},
            models={"openpose": "BODY_25", "yolov8": "yolov8n-8.3.40"},
            artifacts=[dict(kind="frame", media_type="image/jpeg", start_ms=488400, end_ms=488400,
                            uri="saida/video/fisio-003/frame-0488400.jpg")],
        ),
        model_version="video-desvios@0.1.0",
        context={**CONTEXTO, "source_id": "fisio-003", "procedure_type": "fisioterapia"},
    ),
    "video_sem_achados": dict(
        event_id=uid(5), patient_id=PACIENTE, modality="video", event_type="sem_achados",
        timestamp=t("10:00:00"), detected_at=t("10:14:31.500"),
        window=dict(start=t("10:00:00"), end=t("10:12:45")),
        score=0.0, severity="info",
        evidence=dict(summary="Sessão de fisioterapia sem desvios detectados",
                      features={"duracao_s": 765, "frames_processados": 3825, "fps_processado": 5},
                      models={"openpose": "BODY_25", "yolov8": "yolov8n-8.3.40"}),
        model_version="video-desvios@0.1.0",
        context={**CONTEXTO, "source_id": "fisio-002", "procedure_type": "fisioterapia"},
    ),
    "prescricoes_medicamento_alta_vigilancia": dict(
        event_id=uid(6), patient_id=PACIENTE, modality="prescricoes",
        event_type="medicamento_alta_vigilancia",
        timestamp=t("16:40:00"), detected_at=t("16:40:02.310"), ingested_at=t("16:40:01.000"),
        score=0.6, severity="media",
        evidence=dict(
            summary="Introdução de morfina IV (opioide, lista ISMP de alta vigilância)",
            features={"medicamento": "Morphine Sulfate", "classe_ismp": "opioide",
                      "dose": 2.0, "unidade": "mg", "via": "IV", "frequencia": "q4h"},
            thresholds={"lista": "ISMP-Brasil"},
        ),
        model_version="anomalias-prescricoes@0.1.0", context=CONTEXTO,
    ),
    "movimentacao_imobilidade_prolongada": dict(
        event_id=uid(7), patient_id=PACIENTE, modality="movimentacao",
        event_type="imobilidade_prolongada",
        timestamp=t("16:55:00"), detected_at=t("16:55:04.900"),
        window=dict(start=t("14:15:00"), end=t("16:55:00")),
        score=0.55, severity="media",
        evidence=dict(
            summary="2 h 40 min sem mudança de posição no leito",
            features={"minutos_sem_mudanca": 160, "reposicionamentos_ultimas_6h": 1},
            thresholds={"minutos_max_sem_mudanca": 120},
        ),
        model_version="anomalias-movimentacao@0.1.0",
        context={**CONTEXTO, "source_id": "cam-uti-07"},
    ),
    "fusao_risco_multimodal": dict(
        event_id=uid(8), patient_id=PACIENTE, modality="fusao", event_type="risco_multimodal",
        timestamp=t("17:05:20"), detected_at=t("17:06:53.210"), ingested_at=t("17:02:01.050"),
        window=dict(start=t("16:35:20"), end=t("17:05:20")),
        score=0.86, severity="alta",
        evidence=dict(
            summary="Risco alto: SpO2 em queda (NEWS2 6) combinada a fala com dispneia e relato de falta de ar",
            features={"contrib_sinais_vitais": 0.34, "contrib_audio": 0.27, "contrib_texto": 0.19,
                      "contrib_prescricoes": 0.06, "contrib_video": 0.0, "modalidades_presentes": 4,
                      "score_max_isolado": 0.78},
            thresholds={"janela_min": 30, "risco_alto": 0.7, "risco_medio": 0.4},
        ),
        model_version="fusao-late-rules@0.1.0", context=CONTEXTO,
        related_event_ids=[uid(1), uid(2), uid(3), uid(6)],
    ),
}


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for nome, campos in EXEMPLOS.items():
        evento = EventoAchado.model_validate(campos)
        texto = json.dumps(evento.para_dict(), ensure_ascii=False, indent=2) + "\n"
        (DESTINO / f"{nome}.json").write_text(texto, encoding="utf-8")
    print(f"{len(EXEMPLOS)} exemplos gravados em {DESTINO}")


if __name__ == "__main__":
    main()
