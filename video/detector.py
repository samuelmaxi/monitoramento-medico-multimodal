"""Detector de objetos com YOLOv8 (Ultralytics) — US07.

A camada de domínio fala com :class:`Detector`, um ``Protocol`` estreito
(``detectar`` → ``list[Deteccao]``). Isso mantém a lógica de ROI e de estado
testável sem baixar pesos e sem GPU: os testes usam um detector falso, e só a
validação real instancia a Ultralytics.

Classes de interesse
---------------------
``CLASSES_INTERESSE_COCO`` são as classes da US07 dentro do COCO-80 (o conjunto do
YOLOv8 pré-treinado). ``CLASSES_EXIGEM_FINETUNING`` lista o que a US07 precisa e
o COCO **não** tem — instrumento cirúrgico, monitor multiparamétrico, máquina de
fisioterapia, soro, cadeira de rodas. Essas não existem nos pesos atuais: só
aparecem depois de um fine-tuning com dataset médico anotado (ver
``docs/arquitetura/us07_deteccao_areas_criticas.md``). Nenhuma classe fictícia é
declarada aqui.

Estratégia: rastreamento sempre ligado
--------------------------------------
A US07 precisa saber se é a *mesma* pessoa que entrou e saiu. Rastreamento
frame-a-frame simples (ByteTrack) dá um ``track_id`` estável e é a solução
mínima correta: ``Detector.track`` é a mesma chamada do YOLO, só mantém o estado
interno entre quadros (``persist=True``). Rastreamento é opcional no construtor
porque os testes unitários não precisam dele.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .modelos import Caixa, Deteccao, MetadadosVideo

log = logging.getLogger(__name__)

CONFIDENCIA_PADRAO = 0.25
"""Limiar de confiança padrão do YOLO. Também é o default do Ultralytics.

Fica declarado aqui porque a US07 exige que o valor seja explícito e não um
número mágico espalhado pelo código. Subir o limiar derruba falsos positivos de
áreas críticas; baixar recall de entradas reais.
"""

IOU_NMS_PADRAO = 0.50
"""Limiar de IoU do NMS. A US07 fixa 0,50; o default do Ultralytics é 0,7.

Com 0,50 o NMS é mais permissivo entre caixas sobrepostas da mesma classe, o
que ajuda em cenas de leito com gente sobreposta, ao custo de caixas duplicadas
próximas.
"""

IMGSZ_PADRAO = 640
"""Lado da entrada do YOLOv8, em pixels."""

CLASSES_INTERESSE_COCO: tuple[str, ...] = (
    "person",
    "bed",
    "chair",
    "couch",
    "toilet",
    "bottle",
    "backpack",
    "handbag",
    "suitcase",
    "scissors",
    "laptop",
    "tv",
    "sink",
    "clock",
)
"""Classes COCO usadas pela US07, com a justificativa em ``docs/arquitetura``.

- ``person``: paciente, familiar e equipe. É o objeto de interesse principal da
  área crítica.
- ``bed``/``couch``: mobiliário do leito; ROI de borda de leito e de centro do
  quarto se apoiam neles.
- ``chair``/``toilet``: apoio e higiene; relevantes para queda e saída de leito
  em fases posteriores.
- ``bottle``/``backpack``/``handbag``/``suitcase``: objetos pessoais deixados na
  área crítica.
- ``scissors``: único instrumento cortante do COCO — cobre parte do cenário de
  campo cirúrgico.
- ``laptop``/``tv``: equipamento de apoio.
- ``sink``/``clock``: contexto de higiene e tempo de permanência.
"""

CLASSES_EXIGEM_FINETUNING: tuple[str, ...] = (
    "instrumento_cirurgico",
    "monitor_multiparametro",
    "bomba_infusao",
    "suporte_soro",
    "cadeira_de_rodas",
    "prancheta_fisioterapia",
    "balao_fisioterapia",
    "colete_vestibular",
)
"""Necessidades da US07 que o COCO-80 não cobre (nomes de uso interno).

Não são classes do modelo atual: são o alvo do fine-tuning médico described em
``docs/arquitetura/us07_deteccao_areas_criticas.md``. Passar qualquer uma delas
em ``classes=`` hoje faz a Ultralytics recusar o argumento, então o erro aparece
cedo em vez de virar uma lista de métricas vazia.
"""

MODELOS_YOLO_V8: tuple[str, ...] = ("yolov8n.pt", "yolov8s.pt", "yolov8m.pt")
"""Pesos YOLOv8 COCO. ``n`` para CPU/Colab sem GPU, ``s``/``m`` se houver GPU."""


class ErroDeDetector(RuntimeError):
    """Falha na carga do modelo ou na inferência."""


class ErroDeClasse(ErroDeDetector):
    """A configuração pediu uma classe que os pesos atuais não conhecem."""


@dataclass(frozen=True, slots=True)
class ConfiguracaoDetector:
    """Parâmetros de inferência. Tudo configurável, nada fixo no código."""

    pesos: str = "yolov8n.pt"
    confianca: float = CONFIDENCIA_PADRAO
    iou: float = IOU_NMS_PADRAO
    imgsz: int = IMGSZ_PADRAO
    classes: tuple[str, ...] = CLASSES_INTERESSE_COCO
    dispositivo: str | None = None
    """``None`` deixa a Ultralytics escolher (CUDA se disponível, senão CPU)."""

    max_detecoes: int = 300
    meio: bool = True
    """``True`` roda em float16, que acelera GPU; sem efeito prático em CPU."""

    rastrear: bool = True
    """Mantém ByteTrack para dar identidade estável entre quadros."""

    tracker: str = "bytetrack.yaml"
    """Configuração do tracker usada quando ``rastrear`` está ligado."""

    def ids_das_classes(self, nomes_disponiveis: Mapping[int, str]) -> tuple[int, ...]:
        """Traduz nomes de classe em IDs do modelo.

        Args:
            nomes_disponiveis: mapa ``id → nome`` dos pesos carregados.

        Raises:
            ErroDeClasse: alguma classe pedida não existe nos pesos.
        """
        disponiveis = set(nomes_disponiveis.values())
        desconhecidas = [c for c in self.classes if c not in disponiveis]
        if desconhecidas:
            raise ErroDeClasse(
                f"classes ausentes nos pesos '{self.pesos}': {desconhecidas}. "
                f"Disponíveis: {sorted(disponiveis)}. "
                "Classes como 'instrumento_cirurgico' exigem fine-tuning médico."
            )
        return tuple(
            id_classe for id_classe, nome in nomes_disponiveis.items()
            if nome in self.classes
        )


@runtime_checkable
class Detector(Protocol):
    """O que a US07 precisa de um detector. Implemented por :class:`DetectorYolo`."""

    def detectar(
        self, imagem: Any, *, indice_quadro: int, tempo_s: float
    ) -> list[Deteccao]: ...

    @property
    def nomes_classe(self) -> Mapping[int, str]: ...


class DetectorYolo:
    """Detecção de objetos com YOLOv8 via Ultralytics, com rastreamento opcional.

    A Ultralytics é importada dentro do construtor de propósito: os testes que
    só usam ROI e estado não precisam da biblioteca carregada, e a camada de
    domínio continua desacoplada da biblioteca.
    """

    def __init__(
        self,
        config: ConfiguracaoDetector | None = None,
        *,
        rastrear: bool | None = None,
        tracker: str | None = None,
        modelo: Any | None = None,
    ) -> None:
        """
        Args:
            config: parâmetros de inferência; usa os defaults da US07 se omitido.
            rastrear: substitui a opção de rastreamento da configuração.
            tracker: substitui o arquivo de configuração do tracker.
            modelo: instância ``YOLO`` já carregada (injeção para testes).

        Raises:
            ErroDeDetector: os pesos não puderam ser carregados.
        """
        self.config = config or ConfiguracaoDetector()
        self.rastreiar = self.config.rastrear if rastrear is None else rastrear
        self.tracker = tracker or self.config.tracker
        self._modelo = modelo if modelo is not None else self._carregar()
        nomes = getattr(self._modelo, "names", {}) or {}
        self._nomes: Mapping[int, str] = {int(k): str(v) for k, v in dict(nomes).items()}
        self._ids_classe = self.config.ids_das_classes(self._nomes)
        self._versao: str | None = None

    def _carregar(self) -> Any:
        try:
            from ultralytics import YOLO
        except ImportError as erro:  # pragma: no cover — dependência declarada
            raise ErroDeDetector(
                "ultralytics não está instalado; rode 'uv sync' ou "
                "'uv add ultralytics opencv-python'"
            ) from erro
        try:
            return YOLO(self.config.pesos)
        except Exception as erro:
            raise ErroDeDetector(
                f"não foi possível carregar os pesos '{self.config.pesos}': {erro}"
            ) from erro

    @property
    def nomes_classe(self) -> Mapping[int, str]:
        return self._nomes

    @property
    def modelo(self) -> Any:
        """Instância ``YOLO`` underlying (apenas para avaliação/exportação)."""
        return self._modelo

    @property
    def versao(self) -> str:
        """Versão instalada do pacote ``ultralytics``, lida sob demanda."""
        if self._versao is None:
            try:
                from importlib.metadata import version

                self._versao = version("ultralytics")
            except Exception:
                self._versao = "desconhecida"
        return self._versao

    @property
    def descricao_modelo(self) -> str:
        """Identificador ``pesos@versão`` usado em ``evidence.models``."""
        nome = self.config.pesos.removesuffix(".pt")
        return f"{nome}@{self.versao}"

    def detectar(
        self, imagem: Any, *, indice_quadro: int, tempo_s: float
    ) -> list[Deteccao]:
        """Roda a inferência em um frame e devolve as detecções do domínio.

        Args:
            imagem: frame BGR (o que o OpenCV devolve).
            indice_quadro: índice 0-based do quadro, copiado para a detecção.
            tempo_s: instante do quadro em segundos.

        Returns:
            Detecções com ``confianca >= config.confianca`` e classe na lista de
            interesse. Uma detecção por objeto, com ``track_id`` quando o
            rastreamento está ligado.
        """
        resultados = self._inferir(imagem)
        return self.interpretar(resultados, indice_quadro=indice_quadro, tempo_s=tempo_s)

    def _inferir(self, imagem: Any) -> Any:
        argumentos: dict[str, Any] = {
            "conf": self.config.confianca,
            "iou": self.config.iou,
            "imgsz": self.config.imgsz,
            "classes": list(self._ids_classe) or None,
            "verbose": False,
        }
        if self.config.dispositivo:
            argumentos["device"] = self.config.dispositivo
        if self.config.max_detecoes:
            argumentos["max_det"] = self.config.max_detecoes
        if self.config.meio and self.config.dispositivo not in {"cpu", "mps"}:
            argumentos["half"] = True

        if self.rastreiar:
            return self._modelo.track(
                imagem, persist=True, tracker=self.tracker, **argumentos
            )[0]
        return self._modelo.predict(imagem, **argumentos)[0]

    def interpretar(
        self, resultado: Any, *, indice_quadro: int, tempo_s: float
    ) -> list[Deteccao]:
        """Converte um ``Results`` da Ultralytics em :class:`~video.modelos.Deteccao`.

        Isolado do resto para ser testável com um ``Results`` construído à mão:
        os testes de parsing passam um objeto falso no mesmo formato, sem pesos.
        """
        caixas = getattr(resultado, "boxes", None)
        if caixas is None:
            return []

        xyxy = _para_lista(caixas.xyxy)
        confiancas = _para_lista(caixas.conf)
        classes = _para_lista(caixas.cls)
        if xyxy is None or confiancas is None or classes is None:
            raise ErroDeDetector("saída do modelo incompleta: boxes.xyxy/conf/cls ausentes")
        if not (len(xyxy) == len(confiancas) == len(classes)):
            raise ErroDeDetector("saída do modelo inconsistente: quantidade de caixas diferente")
        identificadores = _para_lista(getattr(caixas, "id", None))

        deteccoes: list[Deteccao] = []
        for indice_caixa in range(len(confiancas)):
            nome = str(self._nomes.get(int(classes[indice_caixa]), "desconhecido"))
            confianca = float(confiancas[indice_caixa])
            if nome not in self.config.classes or confianca < self.config.confianca:
                continue
            track_id = None
            if identificadores is not None and len(identificadores) > indice_caixa:
                valor = identificadores[indice_caixa]
                if valor is not None:
                    track_id = int(valor)
            deteccoes.append(
                Deteccao(
                    class_id=int(classes[indice_caixa]),
                    class_name=nome,
                    confianca=confianca,
                    caixa=Caixa(*(float(v) for v in xyxy[indice_caixa])),
                    frame_index=indice_quadro,
                    tempo_s=tempo_s,
                    track_id=track_id,
                )
            )
        return deteccoes

    def val_metricas(
        self, data_yaml: str, **argumentos: Any
    ) -> Mapping[str, float]:
        """Avalia o modelo em um dataset YOLO e devolve mAP50, mAP50-95, P e R.

        Usado por ``scripts/avaliar_us07.py`` para medir com o validator
        Ultralytics. Só é chamado com dataset real; os testes não dependem dele.
        """
        modelo = self._modelo
        opcoes = {"conf": 0.001, "iou": 0.5, **argumentos}
        resultado = modelo.val(data=data_yaml, verbose=False, **opcoes)
        box = resultado.box
        return {
            "mAP@0.5": float(box.map50),
            "mAP@0.5:0.95": float(box.map),
            "precision": float(box.mp),
            "recall": float(box.mr),
        }


def _para_lista(valor: Any) -> list[Any] | None:
    """Converte tensor numpy/lista da Ultralytics em lista Python.

    ``None`` passa adiante, para que a ausência de ``boxes.id`` (sem tracker)
    não vire erro.
    """
    if valor is None:
        return None
    tolist = getattr(valor, "tolist", None)
    if callable(tolist):
        return tolist()
    return list(valor)


class DetectorFalso:
    """Detector de teste: devolve detecções programadas, quadro a quadro.

    Existe para os testes de ROI, transição, evento e integração não dependerem
    de download de pesos nem de GPU. Não é usado em produção.
    """

    def __init__(
        self,
        quadros: Sequence[Sequence[Deteccao]],
        *,
        nomes_classe: Mapping[int, str] | None = None,
    ) -> None:
        self._quadros = list(quadros)
        self._nomes = dict(nomes_classe or {0: "person"})
        self.chamadas = 0

    @property
    def nomes_classe(self) -> Mapping[int, str]:
        return self._nomes

    def detectar(
        self, imagem: Any = None, *, indice_quadro: int, tempo_s: float
    ) -> list[Deteccao]:
        self.chamadas += 1
        if indice_quadro < len(self._quadros):
            return list(self._quadros[indice_quadro])
        return []


def metadados_para_evento(metadados: MetadadosVideo) -> dict[str, Any]:
    """Dados do vídeo que ajudam a equipe a localizar o achado no arquivo."""
    return {
        "video": metadados.origem,
        "fps": metadados.fps,
        "resolucao": f"{metadados.largura}x{metadados.altura}",
    }


__all__ = [
    "CLASSES_EXIGEM_FINETUNING",
    "CLASSES_INTERESSE_COCO",
    "CONFIDENCIA_PADRAO",
    "IMGSZ_PADRAO",
    "IOU_NMS_PADRAO",
    "MODELOS_YOLO_V8",
    "ConfiguracaoDetector",
    "Detector",
    "DetectorFalso",
    "DetectorYolo",
    "ErroDeClasse",
    "ErroDeDetector",
    "metadados_para_evento",
]
