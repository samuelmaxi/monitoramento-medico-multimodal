"""Etapa do relatório de desvios do vídeo (US08 + US09) dentro do pipeline ponta a ponta.

Roda logo depois da :class:`~pipeline.etapas.video.EtapaVideo` e só quando o YOLO (US07) analisou
um vídeo de verdade: aplica o padrão esperado do leito (US08) aos eventos de área crítica, publica
os desvios no barramento da execução (a fusão e o motor de alertas já estão assinados) e grava o
relatório da US09 em ``<execução>/video/video_leito_relatorio_desvios.{json,html}``.

Sem vídeo real não há o que relatar. No modo real (sem vídeo enviado ou YOLO que falhou) a etapa
fica ausente; no simulado ela registra o motivo e não gera nada. Um relatório sobre o achado
sintético de demonstração descreveria um vídeo que não existe.

Só áreas críticas: o pipeline não roda o OpenPose (US06), então queda e mudança brusca ficam fora
do relatório, que diz isso nos avisos.
"""

from __future__ import annotations

import logging
from pathlib import Path

from contratos import EmissorJsonl, EventoAchado
from pipeline.etapas.base import ContextoPipeline
from pipeline.etapas.video import CHAVE_VIDEO, PERFIL_US08, SOURCE_ID_VIDEO, VideoAnalisado
from video.relatorio.run import SUFIXO, ResultadoRelatorio, gerar

log = logging.getLogger("pipeline.etapas.relatorio_video")

# Fixo, nunca derivado do nome do arquivo enviado (que pode trazer nome de paciente): é o id do
# vídeo no relatório e o nome da pasta dos quadros de evidência.
SOURCE_ID = SOURCE_ID_VIDEO
PASTA = "video"  # dentro do diretório da execução
CHAVE_RELATORIO = "relatorio_video"  # contexto.dados[CHAVE_RELATORIO]: ResultadoRelatorio


def caminhos_relatorio(saida_dir: Path) -> tuple[Path, Path]:
    """Onde a etapa grava o relatório da execução: ``(json, html)``."""
    base = saida_dir / PASTA / f"{SOURCE_ID}{SUFIXO}"
    return base.with_suffix(".json"), base.with_suffix(".html")


class EtapaRelatorioVideo:
    """Gera o relatório de desvios do vídeo analisado pelo YOLO e publica os desvios."""

    nome = "relatorio_video"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        analisado: VideoAnalisado | None = contexto.dados.get(CHAVE_VIDEO)
        if analisado is None and contexto.modo_real:
            contexto.logger.ausentar_etapa(self.nome, "nenhum vídeo analisado pelo YOLO")
            return []
        contexto.logger.iniciar_etapa(self.nome)
        if analisado is None:
            contexto.logger.concluir_etapa(
                eventos_emitidos=0,
                detalhes={
                    "relatorio_gerado": False,
                    "motivo": "modo simulado: nenhum vídeo real analisado",
                },
            )
            return []

        pasta = contexto.config.saida_dir / PASTA
        pasta.mkdir(parents=True, exist_ok=True)
        # A US08 lê os eventos da US07 de um JSONL: o mesmo formato que a CLI da US07 grava.
        caminho_areas = pasta / "eventos_areas.jsonl"
        caminho_areas.unlink(missing_ok=True)
        caminho_areas.touch()
        EmissorJsonl(caminho_areas).emitir_varios(analisado.eventos_areas)

        resultado = gerar(
            perfil_id=PERFIL_US08,
            eventos_areas_path=caminho_areas,
            video=analisado.video_path,
            patient_id=contexto.paciente_id,
            bed_id=contexto.leito_id,
            source_id=SOURCE_ID,
            saida=pasta,
            fim_video_s=analisado.fim_analisado_s,
            emissor=contexto.barramento,
        )
        contexto.dados[CHAVE_RELATORIO] = resultado

        falhas = list(resultado.publicacao.falhas)
        if falhas:
            log.error("relatório gerado, mas a publicação dos desvios falhou: %s", falhas)
        contexto.logger.concluir_etapa(
            eventos_emitidos=resultado.publicacao.publicados,
            # O relatório está no disco, mas desvio que não chegou ao barramento não vira alerta.
            status="falha" if falhas else "sucesso",
            detalhes=_detalhes(resultado, falhas),
        )
        return list(resultado.execucao.eventos)


def _detalhes(resultado: ResultadoRelatorio, falhas: list[str]) -> dict[str, object]:
    rel = resultado.relatorio
    resumo = rel["resumo"]
    return {
        "relatorio_gerado": True,
        "perfil": rel["video"]["perfil"],
        "mensagem": resumo["mensagem"],
        "total_desvios": resumo["total_desvios"],
        "por_severidade": resumo["por_severidade"],
        "pct_tempo_em_desvio": resumo["pct_tempo_em_desvio"],
        "duracao_analisada_s": rel["video"]["duracao_s"],
        "avisos": list(rel["avisos"]),
        # Só nomes: o detalhe vai para a resposta da API e para o log.
        "arquivos": [c.name for c in (resultado.caminho_json, resultado.caminho_html) if c],
        "falhas_publicacao": falhas,
    }
