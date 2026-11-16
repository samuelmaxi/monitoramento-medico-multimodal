"""Etapa de processamento de áudio e NLP clínico com Azure (DoD 1, DoD 2, US11/US13).

Modo real: só o áudio enviado. Transcrição pelo Azure Speech e termos críticos e sentimento pelo
Azure AI Language (US11/US13), se as chaves existirem; biomarcadores vocais (US12/US14) sobre o
áudio. Sem áudio, a etapa fica ausente; o que não pôde rodar fica dito em ``detalhes``, e nada é
substituído por demonstração. Modo simulado: a transcrição e o SDK de demonstração da US20.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.transcription_format import SEM_MEDICO
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
        audio = contexto.config.audio_path
        if contexto.modo_real and (audio is None or not audio.is_file()):
            contexto.logger.ausentar_etapa(self.nome, "sem áudio enviado")
            return []

        contexto.logger.iniciar_etapa(self.nome)
        detalhes: dict[str, Any] = {}
        agora = agora_utc()
        if contexto.modo_real:
            eventos, analisou = self._executar_real(contexto, agora, detalhes)
        else:
            eventos, analisou = self._executar_demonstracao(contexto, agora, detalhes), True

        detalhes["total_achados_audio_e_texto"] = len(eventos)
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            # Áudio enviado em que nem o texto nem a voz puderam ser analisados.
            status="sucesso" if analisou else "falha",
            detalhes=detalhes,
        )
        return eventos

    def _executar_real(
        self, contexto: ContextoPipeline, agora: datetime, detalhes: dict[str, Any]
    ) -> tuple[list[EventoAchado], bool]:
        eventos: list[EventoAchado] = []
        cfg = load_config(CONFIG_TEXTO_PATH)

        documento = self._transcrever(contexto, agora, detalhes)
        texto_analisado = False
        if documento is not None:
            cliente = self._language_real(cfg, detalhes)
            if cliente is not None:
                try:
                    resultado = analyze_transcript(
                        transcript_from_dict(documento),
                        cfg,
                        cliente,
                        emissor=contexto.barramento,
                        matcher=TermMatcher(cfg.critical_terms),
                    )
                except Exception as exc:
                    log.warning("análise de texto (Azure AI Language) falhou: %s", exc)
                    detalhes["analise_texto"] = f"falhou: {type(exc).__name__}: {exc}"
                else:
                    eventos.extend(resultado.events)
                    detalhes["eventos_texto"] = len(resultado.events)
                    detalhes["audio_id"] = resultado.audio_id
                    texto_analisado = True

        if documento is None and contexto.config.medico_falante != SEM_MEDICO:
            # Sem a diarização, a voz do médico seria medida como a do paciente.
            detalhes["biomarcadores_vocais"] = (
                "não analisado: sem transcrição não há como separar a voz do médico "
                "(use medico_falante=0 se a gravação for só do paciente)"
            )
            vocais = None
        else:
            vocais = self._analisar_voz(contexto, agora, documento, detalhes)
        eventos.extend(vocais or [])
        return eventos, texto_analisado or vocais is not None

    def _executar_demonstracao(
        self, contexto: ContextoPipeline, agora: datetime, detalhes: dict[str, Any]
    ) -> list[EventoAchado]:
        eventos: list[EventoAchado] = []
        cfg = load_config(CONFIG_TEXTO_PATH)
        detalhes["servico_speech"] = "Azure AI Speech (transcrição de demonstração)"
        detalhes["servico_language"] = "Azure AI Language (SDK simulado)"
        resultado = analyze_transcript(
            self._transcricao_demo(contexto, agora),
            cfg,
            LanguageClient(LanguageSdkSimulado(), cfg.azure),
            emissor=contexto.barramento,
            matcher=TermMatcher(cfg.critical_terms),
        )
        eventos.extend(resultado.events)
        detalhes["eventos_texto"] = len(resultado.events)
        detalhes["audio_id"] = resultado.audio_id

        # Biomarcadores sobre o áudio, se veio; senão (ou se falhar), o achado de demonstração.
        vocais = self._analisar_voz(contexto, agora, None, detalhes)
        if vocais is None:
            vocais = [self._evento_vocal_demo(contexto, agora)]
            for evento in vocais:
                contexto.barramento.emitir(evento)
            detalhes["biomarcadores_vocais"] = (
                detalhes.get("biomarcadores_vocais", "sem áudio")
                + ": achado sintético de demonstração"
            )
        eventos.extend(vocais)
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
            is_synthetic=True,  # achado de demonstração: nunca é medição real
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
            detalhes["biomarcadores_vocais"] = "sem áudio"
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
            log.warning("análise vocal falhou: %s", erro)
            detalhes["biomarcadores_vocais"] = f"falhou ({erro})"
            return None

        resultado = execucao.resultado
        detalhes["biomarcadores_vocais"] = "audio.vocal (US12/US14)"
        detalhes["classe_vocal"] = resultado.classe
        detalhes["eventos_vocais"] = len(resultado.eventos)
        detalhes["yamnet"] = (
            "disponível" if resultado.relatorio.get("respiratorio", {}).get("disponivel") else "indisponível"
        )
        return list(resultado.eventos)

    def _transcrever(
        self, contexto: ContextoPipeline, agora: datetime, detalhes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Azure Speech sobre o áudio enviado; ``None`` (e o motivo em ``detalhes``) se não deu."""
        cfg = contexto.config
        endpoint, chave = _credenciais_speech()
        if not (endpoint and chave):
            detalhes["servico_speech"] = (
                "não usado: faltam AZURE_SPEECH_KEY e AZURE_SPEECH_ENDPOINT (ou AZURE_SPEECH_REGION)"
            )
            return None

        from app.transcription_service import transcrever_e_salvar

        try:
            documento = transcrever_e_salvar(
                cfg.audio_path,
                cfg.saida_dir / "transcricao",
                endpoint=endpoint,
                chave=chave,
                usar_frases=True,
                medico_falante=cfg.medico_falante,
                audio_id=f"consulta-{contexto.leito_id.lower()}",
                patient_id=contexto.paciente_id,
                recorded_at=(agora - timedelta(minutes=15)).isoformat(),
            )
        except Exception as exc:
            log.warning("transcrição (Azure Speech) falhou: %s", exc)
            detalhes["servico_speech"] = f"falhou: {type(exc).__name__}: {exc}"
            return None

        falas = documento.get("falas", [])
        detalhes["servico_speech"] = "Azure AI Speech (transcrição rápida, real)"
        detalhes["audio_origem"] = cfg.audio_path.name
        detalhes["falas_transcritas"] = len(falas)
        detalhes["falantes_diarizacao"] = len({f.get("falante") for f in falas} - {None})
        detalhes["falas_do_paciente"] = sum(1 for f in falas if f.get("papel") == "paciente")
        detalhes["medico_falante"] = (
            "nenhum (gravação só do paciente)"
            if cfg.medico_falante == SEM_MEDICO
            else cfg.medico_falante
        )
        return documento

    def _language_real(self, cfg: Any, detalhes: dict[str, Any]) -> LanguageClient | None:
        if not (os.getenv("AZURE_LANGUAGE_KEY") and os.getenv("AZURE_LANGUAGE_ENDPOINT")):
            detalhes["servico_language"] = (
                "não usado: faltam AZURE_LANGUAGE_KEY e AZURE_LANGUAGE_ENDPOINT"
            )
            return None
        try:
            cliente = LanguageClient.from_env(cfg.azure)
        except Exception as exc:
            log.warning("cliente do Azure AI Language não pôde ser criado: %s", exc)
            detalhes["servico_language"] = f"falhou: {type(exc).__name__}: {exc}"
            return None
        detalhes["servico_language"] = "Azure AI Language (real)"
        return cliente

    def _transcricao_demo(self, contexto: ContextoPipeline, agora: datetime) -> Transcript:
        return transcript_from_dict({
            "audio_id": f"consulta-{contexto.leito_id.lower()}",
            "patient_id": contexto.paciente_id,
            "recorded_at": (agora - timedelta(minutes=15)).isoformat(),
            "is_synthetic": True,
            "segments": TRANSCRICAO_DEMO,
        })


def _credenciais_speech() -> tuple[str | None, str | None]:
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
    if not endpoint and os.getenv("AZURE_SPEECH_REGION"):
        endpoint = f"https://{os.environ['AZURE_SPEECH_REGION']}.api.cognitive.microsoft.com"
    return endpoint, os.getenv("AZURE_SPEECH_KEY")
