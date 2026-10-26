"""Gate de fine-tuning da US07: ``mAP@0.5 >= 0,50`` no conjunto de validação.

O item 5 do DoD exige que, **havendo fine-tuning em classes médicas**, o
``mAP@0.5`` no conjunto de **validação** seja de pelo menos ``0,50``. Este módulo
traduz esse critério em código: mede o modelo treinado no ``split`` de validação,
aplica o gate (``avaliar_gate``) e devolve um relatório rastreável com as métricas
e as classes médicas do treino.

Nada é fabricado: sem ``data.yaml`` anotado a medição falha explicitamente e o
gate **não** é aprovado por omissão. As classes médicas são as que o COCO não
cobre (``CLASSES_EXIGEM_FINETUNING``); o relatório registra essa condição em vez
de presumi-la.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .dataset import ResultadoGate, avaliar_gate
from .detector import CLASSES_EXIGEM_FINETUNING
from .metricas import MetricasDeteccao, avaliar_pesos

LIMIAR_MAP_PADRAO = 0.50
"""Meta mínima do projeto para o ``mAP@0.5`` no conjunto de validação."""

SPLIT_GATE_PADRAO = "val"
"""O DoD exige a medição no conjunto de validação."""

CLASSES_MEDICAS: tuple[str, ...] = CLASSES_EXIGEM_FINETUNING
"""Classes clínicas que o COCO não tem e que exigem dataset + fine-tuning."""

NOME_RELATORIO_FINE_TUNING = "fine_tuning_val.json"


@dataclass(frozen=True, slots=True)
class ConfiguracaoDataset:
    """Subconjunto de classes e caminho de validação declarados em ``data.yaml``."""

    classes: tuple[str, ...]
    split_val: str
    train_split: str
    test_split: str


def ler_configuracao_dataset(data_yaml: str | Path) -> ConfiguracaoDataset:
    """Extrai classes e split ``val`` do YAML simples gerado pelo projeto."""
    caminho = Path(data_yaml)
    if not caminho.is_file():
        raise FileNotFoundError(f"data.yaml do conjunto anotado não encontrado: {caminho}")

    linhas = caminho.read_text(encoding="utf-8").splitlines()
    nomes: list[tuple[int, str]] = []
    val: str | None = None
    train: str | None = None
    test: str | None = None
    dentro_names = False
    for linha in linhas:
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        if not linha[0].isspace():
            chave, separador, valor = linha.partition(":")
            if not separador:
                continue
            chave, valor = chave.strip(), valor.strip()
            if chave == "names":
                if valor.startswith("{") and valor.endswith("}"):
                    for par in valor[1:-1].split(","):
                        indice, separador_nome, nome = par.partition(":")
                        if separador_nome and indice.strip().isdigit():
                            nomes.append(
                                (int(indice.strip()), nome.strip().strip("'\\\""))
                            )
                    dentro_names = False
                else:
                    dentro_names = True
            elif chave == "val":
                val = valor.strip("'\\\"")
                dentro_names = False
            elif chave == "train":
                train = valor.strip("'\\\"")
                dentro_names = False
            elif chave == "test":
                test = valor.strip("'\\\"")
                dentro_names = False
            else:
                dentro_names = False
            continue
        if dentro_names:
            correspondencia = re.fullmatch(r"\s*(\d+)\s*:\s*(.+?)\s*", linha)
            if correspondencia:
                nomes.append(
                    (int(correspondencia.group(1)), correspondencia.group(2).strip("'\\\""))
                )

    if not nomes:
        raise ValueError(f"data.yaml sem taxonomia names válida: {caminho}")
    nomes.sort()
    if [indice for indice, _ in nomes] != list(range(len(nomes))):
        raise ValueError(f"data.yaml tem ids de classe não contíguos: {caminho}")
    if not val:
        raise ValueError(f"data.yaml sem split val: {caminho}")
    if not train or not test:
        raise ValueError(f"data.yaml deve declarar os splits train e test: {caminho}")
    return ConfiguracaoDataset(
        classes=tuple(nome for _, nome in nomes),
        split_val=val,
        train_split=train,
        test_split=test,
    )


def classes_medicas_presentes(classes: Sequence[str]) -> tuple[str, ...]:
    """Interseção estável entre ``classes`` (do treino) e ``CLASSES_MEDICAS``."""
    medicas = set(CLASSES_MEDICAS)
    return tuple(nome for nome in classes if nome in medicas)


def tem_classes_medicas(classes: Sequence[str]) -> bool:
    """True quando o treino inclui ao menos uma classe médica (COCO-ausente)."""
    return bool(classes_medicas_presentes(classes))


@dataclass(frozen=True, slots=True)
class ResultadoFineTuning:
    """Medição no val + gate do fine-tuning, com as classes do treino."""

    metricas: MetricasDeteccao
    gate: ResultadoGate
    classes: tuple[str, ...]

    @property
    def classes_medicas(self) -> tuple[str, ...]:
        return classes_medicas_presentes(self.classes)

    @property
    def tem_classes_medicas(self) -> bool:
        return bool(self.classes_medicas)

    @property
    def aprovado(self) -> bool:
        """Espelha o gate: aprovado só com ``mAP@0.5`` medido e acima do mínimo."""
        return self.gate.aprovado

    def para_dict(self) -> dict[str, object]:
        return {
            "criterio": (
                "mAP@0.5 >= "
                f"{self.gate.minimo:.2f} no conjunto de {self.gate.conjunto}"
            ),
            "split": self.gate.conjunto,
            "classes": list(self.classes),
            "classes_medicas": list(self.classes_medicas),
            "tem_classes_medicas": self.tem_classes_medicas,
            "metrica_avaliada": self.metricas.para_dict()["mAP@0.5"],
            "aprovado": self.gate.aprovado,
            "metricas": self.metricas.para_dict(),
            "gate": self.gate.para_dict(),
        }

    def salvar_json(self, caminho: str | Path) -> Path:
        destino = Path(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(self.para_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destino


def validar_fine_tuning(
    pesos: str,
    data_yaml: str | Path,
    *,
    classes: Sequence[str] | None = None,
    minimo: float = LIMIAR_MAP_PADRAO,
    split: str = SPLIT_GATE_PADRAO,
    dispositivo: str | None = None,
    avaliador: Callable[..., MetricasDeteccao] | None = None,
) -> ResultadoFineTuning:
    """Mede ``pesos`` no ``split`` (val por padrão) e aplica o gate do projeto.

    As classes vêm de ``data.yaml`` para garantir que o relatório identifica as
    classes realmente avaliadas. O parâmetro ``classes`` permite fixar/validar a
    taxonomia explicitamente. ``avaliador`` injeta um medidor em testes.
    """
    if not 0.0 <= minimo <= 1.0:
        raise ValueError("o mínimo de mAP@0.5 deve estar entre 0.0 e 1.0")
    if split not in {"val", "test", "train"}:
        raise ValueError(f"split inválido: {split}")

    configuracao = ler_configuracao_dataset(data_yaml)
    classes_dataset = configuracao.classes
    split_declarado = {
        "train": configuracao.train_split,
        "val": configuracao.split_val,
        "test": configuracao.test_split,
    }[split]
    if split_declarado is None:
        raise ValueError(f"data.yaml não declara o split {split}")
    split_normalizado = Path(split_declarado.removeprefix("./"))
    esperado = Path("images") / split
    if split_normalizado != esperado:
        raise ValueError(
            f"o split {split} do data.yaml deve apontar para {esperado}; "
            f"recebido: {split_declarado!r}"
        )
    if classes is not None and tuple(classes) != classes_dataset:
        raise ValueError(
            "as classes informadas não correspondem à taxonomia do data.yaml: "
            f"{tuple(classes)!r} != {classes_dataset!r}"
        )
    classes_avaliadas = classes_dataset
    avaliar = avaliador or avaliar_pesos
    if avaliador is None:
        metricas = avaliar(
            pesos,
            data_yaml,
            split=split,
            dispositivo=dispositivo,
            classes=classes_dataset,
        )
    else:
        metricas = avaliar(pesos, data_yaml, split=split, dispositivo=dispositivo)
    mapas_por_classe = dict(metricas.mapa_50_por_classe)
    medicao_medica_ausente = [
        nome
        for nome in classes_medicas_presentes(classes_avaliadas)
        if mapas_por_classe.get(nome) is None
    ]
    if medicao_medica_ausente:
        gate = ResultadoGate(
            aprovado=False,
            mapa_50=None,
            minimo=minimo,
            conjunto=split,
            pesos=pesos,
            medido=False,
            motivo=(
                "AP@0.5 por classe não foi reportada para classes médicas: "
                + ", ".join(medicao_medica_ausente)
            ),
        )
    elif classes_medicas_presentes(classes_avaliadas):
        mapa_medio_medico = sum(
            float(mapas_por_classe[nome])
            for nome in classes_medicas_presentes(classes_avaliadas)
        ) / len(classes_medicas_presentes(classes_avaliadas))
        gate = avaliar_gate(
            mapa_50=mapa_medio_medico, minimo=minimo, conjunto=split, pesos=pesos
        )
    else:
        gate = avaliar_gate(
            mapa_50=metricas.mapa_50, minimo=minimo, conjunto=split, pesos=pesos
        )
    return ResultadoFineTuning(metricas=metricas, gate=gate, classes=classes_avaliadas)


__all__ = [
    "CLASSES_MEDICAS",
    "LIMIAR_MAP_PADRAO",
    "NOME_RELATORIO_FINE_TUNING",
    "SPLIT_GATE_PADRAO",
    "ConfiguracaoDataset",
    "ResultadoFineTuning",
    "classes_medicas_presentes",
    "ler_configuracao_dataset",
    "tem_classes_medicas",
    "validar_fine_tuning",
]
