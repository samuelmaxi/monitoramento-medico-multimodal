"""Etapa de processamento de áudio e NLP clínico com Azure (DoD 1, DoD 2, US11/US13)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from audio.text.analyze import analyze_transcript
from audio.text.config import load_config
from audio.text.language_client import LanguageClient
from audio.text.terms import TermMatcher
from audio.text.transcript import Transcript, transcript_from_dict
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

        transcricao, documento_speech = self._obter_transcricao(contexto, agora, detalhes)

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
            except Exception as exc:
                log.warning("Falha ao inicializar LanguageClient real (%s); usando SDK simulado", exc)
        if cliente is None:
            cliente = LanguageClient(LanguageSdkSimulado(), cfg.azure)
            detalhes["servico_language"] = "Azure AI Language (SDK simulado)"

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

        # Biomarcadores vocais (US12/US14) sobre o áudio; sem áudio, achado sintético de demonstração
        eventos_vocais = self._analisar_voz(contexto, agora, documento_speech, detalhes)
        if eventos_vocais is None:
            eventos_vocais = [self._evento_vocal_demo(contexto, agora)]
            for evento in eventos_vocais:
                contexto.barramento.emitir(evento)
        eventos.extend(eventos_vocais)

        detalhes["total_achados_audio_e_texto"] = len(eventos)
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos

    def _evento_vocal_demo(self, contexto: ContextoPipeline, agora: datetime) -> EventoAchado:
        return EventoAchado.criar(
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

    def _analisar_voz(
        self,
        contexto: ContextoPipeline,
        agora: datetime,
        documento_speech: dict[str, Any] | None,
        detalhes: dict[str, Any],
    ) -> list[EventoAchado] | None:
        """Roda ``audio.vocal`` sobre ``audio_path``. ``None`` se não há áudio ou a análise falha."""
        cfg = contexto.config
        audio = cfg.audio_path
        if audio is None or not audio.is_file():
            detalhes["biomarcadores_vocais"] = "sem áudio: achado sintético de demonstração"
            return None

        from audio.ingestao.config import carregar_config as carregar_config_ingestao
        from audio.ingestao.erros import ErroDeAudio
        from audio.vocal.baseline import RepositorioBaseline
        from audio.vocal.config import carregar_config as carregar_config_vocal
        from audio.vocal.erros import ErroDeAnalise
        from audio.vocal.respiratorio import carregar_detector
        from audio.vocal.run import processar

        saida = cfg.saida_dir / "audio"
        try:
            cfg_vocal = carregar_config_vocal()
            execucao = processar(
                audio,
                patient_id=contexto.paciente_id,
                recorded_at=agora - timedelta(minutes=15),
                saida=saida,
                cfg_ingestao=carregar_config_ingestao(),
                cfg=cfg_vocal,
                medico_falante=cfg.medico_falante if documento_speech else None,
                transcricao=documento_speech,
                detector=carregar_detector(cfg_vocal.respiratorio, cfg.modelo_yamnet),
                repositorio=RepositorioBaseline(saida / "baseline"),
                emissor=contexto.barramento,
                bed_id=contexto.leito_id,
                sintetico=(cfg.modo != "real"),
            )
        except (ErroDeAudio, ErroDeAnalise) as erro:
            log.warning("Análise vocal falhou (%s); usando achado sintético de demonstração", erro)
            detalhes["biomarcadores_vocais"] = f"falhou ({erro}): achado sintético de demonstração"
            return None

        resultado = execucao.resultado
        detalhes["biomarcadores_vocais"] = "audio.vocal (US12/US14)"
        detalhes["classe_vocal"] = resultado.classe
        detalhes["eventos_vocais"] = len(resultado.eventos)
        detalhes["yamnet"] = (
            "disponível" if resultado.relatorio.get("respiratorio", {}).get("disponivel") else "indisponível"
        )
        return list(resultado.eventos)

    def _obter_transcricao(
        self, contexto: ContextoPipeline, agora: datetime, detalhes: dict[str, Any]
    ) -> tuple[Transcript, dict[str, Any] | None]:
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
            return transcript_from_dict(documento), documento

        if cfg.modo == "real":
            log.warning("Modo real sem audio_path ou credenciais do Speech; usando transcrição de demonstração")
        detalhes["servico_speech"] = "Azure AI Speech (transcrição de demonstração)"
        demo = transcript_from_dict({
            "audio_id": audio_id,
            "patient_id": contexto.paciente_id,
            "recorded_at": gravado_em,
            "is_synthetic": True,
            "segments": TRANSCRICAO_DEMO,
        })
        return demo, None


def _credenciais_speech() -> tuple[str | None, str | None]:
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
    if not endpoint and os.getenv("AZURE_SPEECH_REGION"):
        endpoint = f"https://{os.environ['AZURE_SPEECH_REGION']}.api.cognitive.microsoft.com"
    return endpoint, os.getenv("AZURE_SPEECH_KEY")
