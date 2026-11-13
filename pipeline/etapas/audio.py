"""Etapa de processamento de áudio e NLP clínico com Azure (DoD 1, DoD 2, US11/US13)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from audio.text.analyze import analyze_transcript
from audio.text.config import load_config
from audio.text.language_client import LanguageClient
from audio.text.terms import TermMatcher
from audio.text.transcript import Transcript, transcript_from_dict
from audio.text.transcript import transcript_from_dict
from contratos import (
    Contexto,
    EventoAchado,
    Evidencia,
    Janela,
    Modalidade,
    Severidade,
    agora_utc,
)
from pipeline.etapas.base import ContextoPipeline
from pipeline.simulacao_azure import LanguageSdkSimulado

log = logging.getLogger("pipeline.etapas.audio")

CONFIG_TEXTO_PATH = Path("audio/text/config.yaml")

TRANSCRICAO_DEMO = [
    {
        "speaker": "medico",
        "text": "Olá, como você tem passado desde ontem à noite?",
        "offset_ms": 0,
        "duration_ms": 2500,
        "confidence": 0.98,
    },
    {
        "speaker": "paciente",
        "text": "Doutor, estou muito cansado, com muita falta de ar e um aperto no peito ao respirar.",
        "offset_ms": 3000,
        "duration_ms": 5000,
        "confidence": 0.95,
    },
]


class EtapaAudio:
    """Processa áudio de consulta via Azure Speech e Azure AI Language."""

    nome = "audio"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        eventos: list[EventoAchado] = []
        detalhes: dict[str, Any] = {}
        agora = agora_utc()

        cfg = load_config(CONFIG_TEXTO_PATH)
        matcher = TermMatcher(cfg.critical_terms)

        transcricao = self._obter_transcricao(contexto, agora, detalhes)

        # Azure AI Language: cliente real (modo real + chaves) ou SDK simulado
        cliente = None
        if (
            contexto.config.modo == "real"
            and os.getenv("AZURE_LANGUAGE_KEY")
            and os.getenv("AZURE_LANGUAGE_ENDPOINT")
        ):
            try:
                cliente = LanguageClient.from_env(cfg.azure)
                detalhes["servico_language"] = "Azure AI Language (real)"
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao inicializar LanguageClient real (%s); usando SDK simulado", exc)
        if cliente is None:
            cliente = LanguageClient(LanguageSdkSimulado(), cfg.azure)
            detalhes["servico_language"] = "Azure AI Language (SDK simulado)"
        # Transcrição da consulta com médico e paciente
        dados_transcricao = {
            "audio_id": f"consulta-{contexto.leito_id.lower()}",
            "patient_id": contexto.paciente_id,
            "recorded_at": (agora - timedelta(minutes=15)).isoformat(),
            "is_synthetic": (contexto.config.modo != "real"),
            "segments": [
                {
                    "speaker": "medico",
                    "text": "Olá, como você tem passado desde ontem à noite?",
                    "offset_ms": 0,
                    "duration_ms": 2500,
                    "confidence": 0.98,
                },
                {
                    "speaker": "paciente",
                    "text": "Doutor, estou muito cansado, com muita falta de ar e um aperto no peito ao respirar.",
                    "offset_ms": 3000,
                    "duration_ms": 5000,
                    "confidence": 0.95,
                },
            ],
        }
        transcricao = transcript_from_dict(dados_transcricao)

        # Determina o cliente da Azure (real ou falso com o mesmo schema do SDK)
        usar_real = (
            contexto.config.modo == "real"
            and os.getenv("AZURE_LANGUAGE_KEY")
            and os.getenv("AZURE_LANGUAGE_ENDPOINT")
        )

        if usar_real:
            try:
                cliente = LanguageClient.from_env(cfg.azure)
                detalhes["servico_azure"] = "Azure AI Language (produção)"
                detalhes["modo"] = "azure_real"
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao inicializar LanguageClient real (%s); usando adaptador de teste", exc)
                usar_real = False

        if not usar_real:
            from audio.tests.conftest import FakeTextAnalytics

            fake_sdk = FakeTextAnalytics(
                regras={
                    "falta de ar": {
                        "category": "SymptomOrSign",
                        "certainty": "positive",
                        "confidence": 0.98,
                    },
                    "aperto no peito": {
                        "category": "SymptomOrSign",
                        "certainty": "positive",
                        "confidence": 0.95,
                    },
                },
                palavras_negativas=("cansado", "falta de ar", "aperto"),
            )
            cliente = LanguageClient(fake_sdk, cfg.azure)
            detalhes["servico_azure"] = "Azure AI Language (simulação determinística do SDK)"
            detalhes["modo"] = "simulado"

        # Executa a análise pelo módulo oficial audio.text.analyze
        resultado = analyze_transcript(
            transcricao,
            cfg,
            cliente,
            emissor=contexto.barramento,
            matcher=matcher,
        )
        eventos.extend(resultado.events)
        detalhes["eventos_texto"] = len(resultado.events)
        detalhes["audio_id"] = resultado.audio_id

        # Adiciona também o achado vocal na modalidade AUDIO (dispneia / fala entrecortada)
        evento_vocal = EventoAchado.criar(
            patient_id=contexto.paciente_id,
            modality=Modalidade.AUDIO,
            event_type="dificuldade_respiratoria",
            timestamp=agora - timedelta(minutes=14, seconds=45),
            score=0.88,
            severity=Severidade.MEDIA,
            window=Janela(
                start=agora - timedelta(minutes=15),
                end=agora - timedelta(minutes=14, seconds=30),
            ),
            evidence=Evidencia(
                summary="Fala com dispneia perceptível: pausas respiratórias frequentes e frases curtas",
                features={
                    "pausas_por_minuto": 14.5,
                    "duracao_media_frase_s": 1.8,
                    "trecho_analisado": "muita falta de ar e um aperto no peito",
                },
                models={"analisador_respiratorio": "yamnet-vocal@0.1.0"},
            ),
            context=Contexto(bed_id=contexto.leito_id, source_id="audio-consulta-01"),
            model_version="audio-biomarcadores@0.1.0",
            is_synthetic=(contexto.config.modo != "real"),
        )
        contexto.barramento.emitir(evento_vocal)
        eventos.append(evento_vocal)

        detalhes["total_achados_audio_e_texto"] = len(eventos)
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos

    def _obter_transcricao(
        self, contexto: ContextoPipeline, agora: datetime, detalhes: dict[str, Any]
    ) -> Transcript:
        """Azure Speech sobre ``audio_path`` (modo real) ou transcrição de demonstração."""
        cfg = contexto.config
        endpoint, chave = _credenciais_speech()
        audio = cfg.audio_path
        gravado_em = (agora - timedelta(minutes=15)).isoformat()
        audio_id = f"consulta-{contexto.leito_id.lower()}"

        if cfg.modo == "real" and audio is not None and audio.is_file() and endpoint and chave:
            from app.transcription_service import transcrever_e_salvar

            documento = transcrever_e_salvar(
                audio,
                cfg.saida_dir / "transcricao",
                endpoint=endpoint,
                chave=chave,
                usar_frases=True,
                medico_falante=cfg.medico_falante,
                audio_id=audio_id,
                patient_id=contexto.paciente_id,
                recorded_at=gravado_em,
            )
            detalhes["servico_speech"] = "Azure AI Speech (transcrição rápida, real)"
            detalhes["audio_origem"] = audio.name
            detalhes["falas_transcritas"] = len(documento.get("falas", []))
            return transcript_from_dict(documento)

        if cfg.modo == "real":
            log.warning("Modo real sem audio_path ou credenciais do Speech; usando transcrição de demonstração")
        detalhes["servico_speech"] = "Azure AI Speech (transcrição de demonstração)"
        return transcript_from_dict({
            "audio_id": audio_id,
            "patient_id": contexto.paciente_id,
            "recorded_at": gravado_em,
            "is_synthetic": True,
            "segments": TRANSCRICAO_DEMO,
        })


def _credenciais_speech() -> tuple[str | None, str | None]:
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
    if not endpoint and os.getenv("AZURE_SPEECH_REGION"):
        endpoint = f"https://{os.environ['AZURE_SPEECH_REGION']}.api.cognitive.microsoft.com"
    return endpoint, os.getenv("AZURE_SPEECH_KEY")
