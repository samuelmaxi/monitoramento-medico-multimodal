"""Orquestrador do pipeline ponta a ponta (US20)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alertas import ConfigAlertas, construir
from contratos import BarramentoLocal
from fusao import FusaoMultimodal, carregar_config
from pipeline.config import ConfigPipeline
from pipeline.etapas.alertas import EtapaAlertas
from pipeline.etapas.audio import EtapaAudio
from pipeline.etapas.base import ContextoPipeline
from pipeline.etapas.fusao import EtapaFusao
from pipeline.etapas.movimentacao import EtapaMovimentacao
from pipeline.etapas.prescricoes import EtapaPrescricoes
from pipeline.etapas.sinais import EtapaSinaisVitais
from pipeline.etapas.video import EtapaVideo
from pipeline.logger import LoggerPipeline

log = logging.getLogger("pipeline.orquestrador")


@dataclass(frozen=True)
class ResumoExecucao:
    """Resultado consolidado da execução ponta a ponta."""

    paciente_id: str
    leito_id: str
    duracao_total_ms: float
    total_eventos_barramento: int
    total_alertas: int
    score_risco_final: float
    nivel_risco_final: str
    caminho_relatorio: Path
    caminho_eventos: Path
    caminho_alertas: Path | None
    etapas_com_falha: tuple[str, ...] = ()
    caminho_log: Path | None = None


class OrquestradorPipeline:
    """Coordena todas as modalidades, barramento, fusão e alertas em comando único."""

    def __init__(self, config: ConfigPipeline | None = None) -> None:
        self.config = config or ConfigPipeline()

    def executar(self) -> ResumoExecucao:
        saida_dir = self.config.saida_dir
        saida_dir.mkdir(parents=True, exist_ok=True)

        caminho_eventos = saida_dir / "eventos.jsonl"
        caminho_eventos.unlink(missing_ok=True)
        (saida_dir / "log_execucao.jsonl").unlink(missing_ok=True)  # cada execução reescreve o seu

        barramento = BarramentoLocal(caminho_eventos)
        logger = LoggerPipeline(saida_dir)

        # 1. Configura Fusão Multimodal (US18)
        config_fusao = carregar_config()
        fusao = FusaoMultimodal(config_fusao)
        fusao.assinar(barramento)

        # 2. Configura Motor de Alertas (US19)
        if self.config.notificar_smtp:
            cfg_alertas = ConfigAlertas.from_env({
                "ALERTA_SAIDA_DIR": str(saida_dir),
                "ALERTA_STORE": "jsonl",
            })
        else:
            cfg_alertas = ConfigAlertas(
                armazenamento="jsonl",
                saida_dir=saida_dir,
            )
        servico_alertas = construir(cfg_alertas)
        servico_alertas.assinar(barramento)

        # 3. Prepara contexto
        contexto = ContextoPipeline(
            config=self.config,
            barramento=barramento,
            logger=logger,
            paciente_id=self.config.paciente_id,
            leito_id=self.config.leito_id,
            dados={
                "fusao": fusao,
                "alertas": servico_alertas,
            },
        )

        # 4. Sequência orquestrada de etapas
        etapas = [
            EtapaVideo(),
            EtapaAudio(),
            EtapaSinaisVitais(),
            EtapaPrescricoes(),
            EtapaMovimentacao(),
            EtapaFusao(),
            EtapaAlertas(),
        ]

        for etapa in etapas:
            try:
                etapa.executar(contexto)
            except Exception as erro:
                log.exception("etapa %s falhou", etapa.nome)
                logger.falhar_etapa(etapa.nome, erro)
                if self.config.abortar_na_falha:
                    break

        # 5. Gera relatório estruturado e consolida resumo
        caminho_relatorio = logger.salvar_relatorio()
        total_ms = sum(r.duracao_ms for r in logger.registros)

        risco_paciente = fusao.risco(contexto.paciente_id)
        alertas_gerados = servico_alertas.armazenamento.listar()

        caminho_alertas = saida_dir / "alertas.jsonl"
        if not caminho_alertas.is_file():
            caminho_alertas = None

        return ResumoExecucao(
            paciente_id=contexto.paciente_id,
            leito_id=contexto.leito_id,
            duracao_total_ms=round(total_ms, 2),
            total_eventos_barramento=barramento.total_publicados,
            total_alertas=len(alertas_gerados),
            score_risco_final=round(risco_paciente.score, 4) if risco_paciente else 0.0,
            nivel_risco_final=risco_paciente.nivel if risco_paciente else "indisponivel",
            caminho_relatorio=caminho_relatorio,
            caminho_eventos=caminho_eventos,
            caminho_alertas=caminho_alertas,
            etapas_com_falha=tuple(r.etapa for r in logger.registros if r.status == "falha"),
            caminho_log=logger.caminho_log,
        )

