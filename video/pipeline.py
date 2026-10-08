"""Pipeline da US07: vídeo → YOLOv8 → ROI → transição → evento da US04.

Orquestra as camadas, sem misturá-las:

.. code-block:: text

    LeitorVideo (OpenCV)
        → DetectorYolo (Ultralytics)
        → filtro de classe/confiança
        → MonitorAreas (geometria + estado)
        → TradutorEventos
        → Emissor (contratos da US04)

Os passos de inferência, geometria, estado e evento são injetáveis, então o
teste de integração roda o fluxo inteiro com um ``DetectorFalso`` e um vídeo
sintético, sem baixar pesos.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from contratos import Emissor, EmissorMemoria, EventoAchado, agora_utc

from .areas import ConjuntoAreas
from .config import Configuracao
from .detector import Detector, DetectorYolo
from .eventos import TradutorEventos
from .leitor_video import LeitorVideo
from .modelos import Deteccao, MetadadosVideo
from .monitor import EventoAreaCritica, MonitorAreas

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResumoExecucao:
    """Números da execução, para o log e para o relatório da US07."""

    quadros_lidos: int
    deteccoes_total: int
    transicoes: int
    eventos: int
    entradas: int
    saidas: int
    fps: float
    resolucao: tuple[int, int]
    classes_detectadas: dict[str, int]
    duracao_s: float
    video_anotado: Path | None = None
    imagem_anotada: Path | None = None

    def para_dict(self) -> dict[str, object]:
        return {
            "quadros_lidos": self.quadros_lidos,
            "deteccoes_total": self.deteccoes_total,
            "transicoes": self.transicoes,
            "eventos": self.eventos,
            "entradas": self.entradas,
            "saidas": self.saidas,
            "fps": self.fps,
            "resolucao": f"{self.resolucao[0]}x{self.resolucao[1]}",
            "classes_detectadas": self.classes_detectadas,
            "duracao_s": round(self.duracao_s, 3),
            "video_anotado": str(self.video_anotado) if self.video_anotado else None,
        }


@dataclass(frozen=True, slots=True)
class ResultadoPipeline:
    """Transições detectadas, eventos emitidos e o resumo da execução."""

    eventos: list[EventoAchado]
    transicoes: list[EventoAreaCritica]
    resumo: ResumoExecucao
    metadados: MetadadosVideo

    @property
    def entradas(self) -> list[EventoAchado]:
        return [e for e in self.eventos if e.event_type == "entrada_area_critica"]

    @property
    def saidas(self) -> list[EventoAchado]:
        return [e for e in self.eventos if e.event_type == "saida_area_critica"]


class PipelineAreaCritica:
    """Executa a US07 sobre um vídeo configurado.

    Args:
        config: configuração lida de arquivo ou dicionário.
        detector: detector a usar; sem injeção, cria um :class:`DetectorYolo`
            conforme ``config.detector``.
        emissor: barramento da US04; sem injeção, usa :class:`EmissorMemoria`.
        Areas e monitor são derivados da configuração e podem ser substituídos
        em teste.
    """

    def __init__(
        self,
        config: Configuracao,
        *,
        detector: Detector | None = None,
        emissor: Emissor | None = None,
        monitor: MonitorAreas | None = None,
    ) -> None:
        self.config = config
        self.detector = detector if detector is not None else self._criar_detector()
        self.emissor: Emissor = emissor if emissor is not None else self._emissor_padrao()
        self.monitor = monitor or MonitorAreas(
            areas=config.areas,
            criterio=config.monitor.criterio,
            persistencia_quadros=config.monitor.persistencia_quadros,
            primeira_observacao_entra=config.monitor.primeira_observacao_entra,
        )
        self.tradutor = TradutorEventos(config)

    def _criar_detector(self) -> Detector:
        return DetectorYolo(self.config.detector)

    def _emissor_padrao(self) -> Emissor:
        if self.config.evento.emitir_jsonl:
            from contratos import EmissorJsonl

            caminho = self.config.caminho_resolvido(self.config.evento.caminho_jsonl)
            if caminho is not None:
                return EmissorJsonl(caminho)
        return EmissorMemoria()

    @property
    def descricao_modelo(self) -> str:
        """``pesos@versão`` para ``evidence.models``; genérico se não houver YOLO."""
        detector = self.detector
        if isinstance(detector, DetectorYolo):
            return detector.descricao_modelo
        return f"{self.config.detector.pesos.removesuffix('.pt')}@fake"

    def executar(
        self,
        *,
        ate_quadro: int | None = None,
        limite_eventos: int | None = None,
        detectado_em: datetime | None = None,
        escritor_video: object | None = None,
    ) -> ResultadoPipeline:
        """Processa o vídeo e emite os eventos de entrada/saída.

        Args:
            ate_quadro: para em ``indice + 1`` quadros (validação rápida).
            limite_eventos: para assim que este número de eventos for emitido.
            detectado_em: instante de referência do ``timestamp``; por padrão o
                início da execução.
            escritor_video: recebe um
                :class:`~video.visualizacao.GravadorVideo` para os frames anotados.

        Returns:
            :class:`ResultadoPipeline` com eventos, transições e o resumo.
        """
        inicio = detectado_em or agora_utc()
        if inicio.tzinfo is None or inicio.utcoffset() is None:
            raise ValueError("detectado_em precisa ser timezone-aware")
        inicio = inicio.astimezone(UTC)
        self.monitor.reiniciar()
        momento_inicio = time.perf_counter()
        eventos: list[EventoAchado] = []
        transicoes: list[EventoAreaCritica] = []
        contagem_classes: dict[str, int] = {}
        deteccoes_total = 0
        quadros_lidos = 0

        max_quadros = ate_quadro if ate_quadro is not None else self.config.fonte.max_quadros

        with LeitorVideo(self.config.video, max_quadros=max_quadros) as leitor:
            metadados = leitor.metadados
            areas_escaladas: ConjuntoAreas = self.config.areas.para_escala(
                *metadados.resolucao
            )
            self.monitor.areas = areas_escaladas
            contexto_base = self.tradutor.contexto_de(
                descricao_modelo=self.descricao_modelo, metadados=metadados
            )

            log.info(
                "US07: %s | %dx%d @ %.2f fps | pesos=%s conf=%.2f iou=%.2f | %d área(s)",
                metadados.origem,
                metadados.largura,
                metadados.altura,
                metadados.fps,
                self.config.detector.pesos,
                self.config.detector.confianca,
                self.config.detector.iou,
                len(areas_escaladas),
            )

            for quadro in leitor.quadros():
                quadros_lidos = quadro.indice + 1
                tempo_evento_s = quadro.tempo_s + self.config.fonte.inicio_segundos
                deteccoes = self.detector.detectar(
                    quadro.imagem, indice_quadro=quadro.indice, tempo_s=tempo_evento_s
                )
                deteccoes_total += len(deteccoes)
                for deteccao in deteccoes:
                    contagem_classes[deteccao.class_name] = (
                        contagem_classes.get(deteccao.class_name, 0) + 1
                    )

                do_quadro = self.monitor.processar(deteccoes)
                transicoes.extend(do_quadro)

                for transicao in do_quadro:
                    evento = self.tradutor.emitir(
                        transicao,
                        self.emissor,
                        contexto=contexto_base,
                        detectado_em=inicio,
                    )
                    eventos.append(evento)

                if escritor_video is not None:
                    self._anotar(escritor_video, quadro, areas_escaladas, deteccoes, do_quadro)

                if limite_eventos is not None and len(eventos) >= limite_eventos:
                    log.info("limite de %d evento(s) atingido", limite_eventos)
                    break

        if quadros_lidos == 0:
            raise ValueError("pipeline não processou nenhum quadro")

        resumo = ResumoExecucao(
            quadros_lidos=quadros_lidos,
            deteccoes_total=deteccoes_total,
            transicoes=len(transicoes),
            eventos=len(eventos),
            entradas=sum(1 for t in transicoes if "entrada" in t.event_type),
            saidas=sum(1 for t in transicoes if "saida" in t.event_type),
            fps=metadados.fps,
            resolucao=metadados.resolucao,
            classes_detectadas=dict(sorted(contagem_classes.items())),
            duracao_s=time.perf_counter() - momento_inicio,
        )
        log.info(
            "US07 concluída: %d quadros, %d detecções, %d transições, %d eventos em %.2fs",
            resumo.quadros_lidos,
            resumo.deteccoes_total,
            resumo.transicoes,
            resumo.eventos,
            resumo.duracao_s,
        )
        if not eventos:
            self.tradutor.emitir_sem_achados(
                self.emissor,
                resumo=resumo,
                contexto=contexto_base,
                detectado_em=inicio,
            )
        return ResultadoPipeline(
            eventos=eventos,
            transicoes=transicoes,
            resumo=resumo,
            metadados=metadados,
        )

    def _anotar(
        self,
        escritor_video: object,
        quadro: object,
        areas: ConjuntoAreas,
        deteccoes: Sequence[Deteccao],
        transicoes: Sequence[EventoAreaCritica],
    ) -> None:
        """Anota e envia o quadro ao gravador, se a visualização estiver ligada."""
        from .visualizacao import anotar  # import local: OpenCV só é necessário aqui

        imagem = quadro.imagem
        carimbo = f"f{quadro.indice} · {quadro.tempo_s:.2f}s"
        quadro_anotado = anotar(
            imagem,
            areas=areas,
            deteccoes=deteccoes,
            transicoes=transicoes,
            mostrar_rois=self.config.visualizacao.mostrar_rois,
            mostrar_labels=self.config.visualizacao.mostrar_labels,
            carimbo=carimbo,
        )
        escrever = getattr(escritor_video, "escrever", None)
        if callable(escrever):
            escrever(quadro_anotado)


__all__ = ["PipelineAreaCritica", "ResultadoPipeline", "ResumoExecucao"]
