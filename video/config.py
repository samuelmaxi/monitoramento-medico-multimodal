"""Configuração da US07, tipada e externa.

Um arquivo JSON (ou TOML) descreve fonte, modelo, limiares e ROIs. Nenhum número
mágico no código: os defaults ficam em :mod:`video.detector`,
:mod:`video.areas` e aqui.

Exemplo mínimo::

    {
      "fonte": {"id": "fisio-003", "video": "conteudos/videos/fisio.mp4",
                "patient_id": "pt_...", "bed_id": "UTI-07"},
      "detector": {"pesos": "yolov8n.pt", "confianca": 0.25, "iou": 0.5},
      "areas": [{"id": "bed_side", "nome": "Borda do leito",
                 "points": [[100, 100], [500, 100], [500, 400], [100, 400]]}]
    }

O ``patient_id`` precisa ser o pseudônimo ``pt_<24 hex>`` do contrato da US04; use
:func:`contratos.pseudonimizacao.pseudonimizar_paciente` no lugar do ID cru. O
validador recusa ID cru em vez de deixar vazar dado pessoal.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contratos import e_pseudonimo_de_paciente

from .areas import ConjuntoAreas, areas_de_dados
from .detector import (
    CONFIDENCIA_PADRAO,
    IOU_NMS_PADRAO,
    MODELOS_YOLO_V8,
    ConfiguracaoDetector,
)

SEVERIDADE_PADRAO = "baixa"
"""Severidade dos eventos de área crítica.

``baixa`` porque entrada/saída de área crítica é um achado informativo para a
equipe — vira alerta na US19 só quando o nível sobe (``presenca_indevida_area_
critica`` com regra de permanência). Configurável por ``eventos.severidade``.
"""

VERSAO_MODULO = "video-areas-criticas@0.1.0"
"""``model_version`` gravado no evento, no formato do contrato da US04."""


class ErroDeConfiguracao(ValueError):
    """A configuração da US07 está ausente, incompleta ou inconsistente."""


@dataclass(frozen=True, slots=True)
class ConfiguracaoFonte:
    """De onde vêm os quadros e a quem o evento pertence."""

    video: Path
    id: str
    patient_id: str
    encounter_id: str | None = None
    bed_id: str | None = None
    procedure_type: str | None = None
    inicio_utc: str | None = None
    """Instante de gravação em ISO-8601 UTC, quando conhecido do sistema externo.

    Sem esse campo, o pipeline usa o instante em que a análise começa como
    ``timestamp`` e grava o tempo relativo ao vídeo em ``features``. É a
    diferença entre "o evento ocorreu às 15:20 na câmera" e "o evento ocorreu aos
    8,4 s do arquivo" — a segunda é a que dá para assumir sem metadado externo.
    """

    fps: float | None = None
    """FPS real do fluxo (câmera/stream). Para arquivo, quem manda é o OpenCV."""

    inicio_segundos: float = 0.0
    max_quadros: int | None = None


@dataclass(frozen=True, slots=True)
class ConfiguracaoMonitor:
    """Regras de transição de estado."""

    criterio: str = "fracao"
    persistencia_quadros: int = 2
    primeira_observacao_entra: bool = False


@dataclass(frozen=True, slots=True)
class ConfiguracaoEvento:
    """Parâmetros do evento emitido no contrato da US04."""

    severidade: str = SEVERIDADE_PADRAO
    emitir_jsonl: bool = True
    caminho_jsonl: Path | None = None


@dataclass(frozen=True, slots=True)
class ConfiguracaoVisualizacao:
    """Saída visual opcional (nunca afeta a lógica)."""

    ativa: bool = False
    caminho_video: Path | None = None
    caminho_imagem: Path | None = None
    mostrar_labels: bool = True
    mostrar_rois: bool = True


@dataclass(frozen=True, slots=True)
class Configuracao:
    """Configuração completa da execução da US07."""

    fonte: ConfiguracaoFonte
    detector: ConfiguracaoDetector = field(default_factory=ConfiguracaoDetector)
    areas: ConjuntoAreas = field(default_factory=ConjuntoAreas)
    monitor: ConfiguracaoMonitor = field(default_factory=ConfiguracaoMonitor)
    evento: ConfiguracaoEvento = field(default_factory=ConfiguracaoEvento)
    visualizacao: ConfiguracaoVisualizacao = field(default_factory=ConfiguracaoVisualizacao)
    base: Path | None = None
    """Diretório contra o qual os caminhos relativos da configuração são resolvidos."""

    def caminho_resolvido(self, caminho: Path | None) -> Path | None:
        """Resolve um caminho relativo contra a raiz da configuração."""
        if caminho is None:
            return None
        if caminho.is_absolute() or self.base is None:
            return caminho
        return self.base / caminho

    @property
    def video(self) -> Path:
        return self.caminho_resolvido(self.fonte.video)  # type: ignore[return-value]

    @classmethod
    def de_arquivo(cls, caminho: str | Path) -> Configuracao:
        """Lê a configuração de um JSON ou TOML.

        Raises:
            ErroDeConfiguracao: arquivo ausente, formato inválido ou campos
                obrigatórios faltando.
        """
        caminho = Path(caminho)
        if not caminho.is_file():
            raise ErroDeConfiguracao(f"configuração não encontrada: {caminho}")
        texto = caminho.read_text(encoding="utf-8")
        if caminho.suffix.lower() == ".toml":
            dados: Any = tomllib.loads(texto)
        else:
            try:
                dados = json.loads(texto)
            except json.JSONDecodeError as erro:
                raise ErroDeConfiguracao(f"{caminho} não é JSON válido: {erro}") from erro
        base = caminho.parent
        return cls.de_dict(dados, base=base)

    @classmethod
    def de_dict(cls, dados: Mapping[str, Any], *, base: Path | None = None) -> Configuracao:
        """Constrói a configuração a partir de um dicionário já carregado."""
        if not isinstance(dados, Mapping):
            raise ErroDeConfiguracao(
                f"configuração precisa ser um objeto, recebeu {type(dados).__name__}"
            )
        fonte = cls._fonte(dados.get("fonte"), base=base)
        detector = cls._detector(dados.get("detector") or {})
        areas = areas_de_dados(dados.get("areas") or [])
        return cls(
            fonte=fonte,
            detector=detector,
            areas=ConjuntoAreas(tuple(areas)),
            monitor=cls._monitor(dados.get("monitor") or {}),
            evento=cls._evento(dados.get("eventos") or {}, base=base),
            visualizacao=cls._visualizacao(dados.get("visualizacao") or {}, base=base),
            base=base,
        )

    # ------------------------------------------------------------------ #
    # Blocos
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fonte(bruto: Any, *, base: Path | None) -> ConfiguracaoFonte:
        if not isinstance(bruto, Mapping):
            raise ErroDeConfiguracao("bloco 'fonte' é obrigatório")
        faltando = [chave for chave in ("video", "id", "patient_id") if not bruto.get(chave)]
        if faltando:
            raise ErroDeConfiguracao(f"bloco 'fonte' sem {faltando}")

        patient_id = str(bruto["patient_id"])
        if not e_pseudonimo_de_paciente(patient_id):
            raise ErroDeConfiguracao(
                f"patient_id '{patient_id}' não é um pseudônimo pt_<24 hex>. "
                "Gere com contratos.pseudonimizacao.pseudonimizar_paciente(patient_id) "
                "— o ID cru não entra no evento (LGPD)."
            )

        return ConfiguracaoFonte(
            video=Path(str(bruto["video"])),
            id=str(bruto["id"]),
            patient_id=patient_id,
            encounter_id=_texto_ou_none(bruto.get("encounter_id")),
            bed_id=_texto_ou_none(bruto.get("bed_id")),
            procedure_type=_texto_ou_none(bruto.get("procedure_type")),
            inicio_utc=_validar_inicio_utc(_texto_ou_none(bruto.get("inicio_utc"))),
            fps=_validar_fps(_float_ou_none(bruto.get("fps"))),
            inicio_segundos=float(bruto.get("inicio_segundos", 0.0)),
            max_quadros=_validar_max_quadros(bruto.get("max_quadros")),
        )

    @staticmethod
    def _detector(bruto: Mapping[str, Any]) -> ConfiguracaoDetector:
        if not isinstance(bruto, Mapping):
            raise ErroDeConfiguracao("bloco 'detector' precisa ser um objeto")
        desconhecidos = set(bruto) - {
            "pesos", "confianca", "iou", "imgsz", "classes", "dispositivo",
            "max_detecoes", "meio", "rastrear", "tracker",
        }
        if desconhecidos:
            raise ErroDeConfiguracao(
                f"chaves desconhecidas em 'detector': {sorted(desconhecidos)}"
            )

        confianca = float(bruto.get("confianca", CONFIDENCIA_PADRAO))
        iou = float(bruto.get("iou", IOU_NMS_PADRAO))
        if not 0.0 <= confianca <= 1.0:
            raise ErroDeConfiguracao(f"confianca fora de [0, 1]: {confianca}")
        if not 0.0 <= iou <= 1.0:
            raise ErroDeConfiguracao(f"iou fora de [0, 1]: {iou}")

        classes = bruto.get("classes")
        if isinstance(classes, str):
            classes = [classes]
        if classes is not None and not isinstance(classes, Sequence):
            raise ErroDeConfiguracao("'classes' precisa ser uma lista de nomes COCO")

        pesos = str(bruto.get("pesos", "yolov8n.pt"))
        imgsz = int(bruto.get("imgsz", 640))
        max_detecoes = int(bruto.get("max_detecoes", 300))
        if not pesos.strip():
            raise ErroDeConfiguracao("detector.pesos não pode ser vazio")
        if imgsz <= 0 or max_detecoes <= 0:
            raise ErroDeConfiguracao("imgsz e max_detecoes precisam ser positivos")

        return ConfiguracaoDetector(
            pesos=pesos,
            confianca=confianca,
            iou=iou,
            imgsz=imgsz,
            classes=tuple(str(c) for c in classes) if classes else ConfiguracaoDetector().classes,
            dispositivo=_texto_ou_none(bruto.get("dispositivo")),
            max_detecoes=max_detecoes,
            meio=bool(bruto.get("meio", True)),
            rastrear=bool(bruto.get("rastrear", True)),
            tracker=str(bruto.get("tracker", "bytetrack.yaml")),
        )

    @staticmethod
    def _monitor(bruto: Mapping[str, Any]) -> ConfiguracaoMonitor:
        criterio = str(bruto.get("criterio", "fracao"))
        if criterio not in {"fracao", "centro"}:
            raise ErroDeConfiguracao(
                f"criterio de contenção inválido: {criterio!r} (use 'fracao' ou 'centro')"
            )
        persistencia_quadros = int(bruto.get("persistencia_quadros", 2))
        if persistencia_quadros < 0:
            raise ErroDeConfiguracao("monitor.persistencia_quadros não pode ser negativo")
        return ConfiguracaoMonitor(
            criterio=criterio,
            persistencia_quadros=persistencia_quadros,
            primeira_observacao_entra=bool(bruto.get("primeira_observacao_entra", False)),
        )

    @staticmethod
    def _evento(bruto: Mapping[str, Any], *, base: Path | None) -> ConfiguracaoEvento:
        severidade = str(bruto.get("severidade", SEVERIDADE_PADRAO))
        caminho = bruto.get("caminho_jsonl")
        return ConfiguracaoEvento(
            severidade=severidade,
            emitir_jsonl=bool(bruto.get("emitir_jsonl", True)),
            caminho_jsonl=Path(str(caminho)) if caminho else None,
        )

    @staticmethod
    def _visualizacao(
        bruto: Mapping[str, Any], *, base: Path | None
    ) -> ConfiguracaoVisualizacao:
        video = bruto.get("caminho_video")
        imagem = bruto.get("caminho_imagem")
        return ConfiguracaoVisualizacao(
            ativa=bool(bruto.get("ativa", False)),
            caminho_video=Path(str(video)) if video else None,
            caminho_imagem=Path(str(imagem)) if imagem else None,
            mostrar_labels=bool(bruto.get("mostrar_labels", True)),
            mostrar_rois=bool(bruto.get("mostrar_rois", True)),
        )

    def resumo(self) -> dict[str, Any]:
        """Resumo legível da configuração, para log e para o relatório."""
        return {
            "video": str(self.video),
            "fonte_id": self.fonte.id,
            "pesos": self.detector.pesos,
            "confianca": self.detector.confianca,
            "iou": self.detector.iou,
            "imgsz": self.detector.imgsz,
            "classes": list(self.detector.classes),
            "criterio_roi": self.monitor.criterio,
            "persistencia_quadros": self.monitor.persistencia_quadros,
            "areas": [area.id for area in self.areas],
            "severidade": self.evento.severidade,
        }


def _validar_inicio_utc(valor: str | None) -> str | None:
    if valor is None:
        return None
    from datetime import datetime, timedelta

    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError as erro:
        raise ErroDeConfiguracao(f"fonte.inicio_utc inválido: {valor!r}") from erro
    if instante.tzinfo is None or instante.utcoffset() != timedelta(0):
        raise ErroDeConfiguracao("fonte.inicio_utc precisa estar em UTC")
    return valor


def _validar_fps(valor: float | None) -> float | None:
    if valor is not None and valor <= 0:
        raise ErroDeConfiguracao("fonte.fps precisa ser maior que zero")
    return valor


def _validar_max_quadros(valor: Any) -> int | None:
    if valor is None:
        return None
    quadros = int(valor)
    if quadros <= 0:
        raise ErroDeConfiguracao("fonte.max_quadros precisa ser maior que zero")
    return quadros


def _texto_ou_none(valor: Any) -> str | None:
    return None if valor is None else str(valor)


def _float_ou_none(valor: Any) -> float | None:
    return None if valor is None else float(valor)


__all__ = [
    "MODELOS_YOLO_V8",
    "SEVERIDADE_PADRAO",
    "VERSAO_MODULO",
    "Configuracao",
    "ConfiguracaoDetector",
    "ConfiguracaoEvento",
    "ConfiguracaoFonte",
    "ConfiguracaoMonitor",
    "ConfiguracaoVisualizacao",
    "ErroDeConfiguracao",
]
