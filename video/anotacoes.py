"""Infraestrutura de anotação YOLO (detecção de objetos) — US07.

Define o formato YOLO de detecção (``class_id x_center y_center width height``,
tudo normalizado em ``[0, 1]``), a leitura/escrita de ``.txt``, a validação
estrutural de uma caixa e o preparo/importação de anotações para o CVAT.

Regra central: **nenhuma anotação é inventada**. Este módulo nunca deriva caixas
do nome do vídeo nem marca todos os frames de um vídeo ``F`` como queda. Quando um
modelo pré-treinado é usado para propor caixas, o resultado vai para uma pasta
separada (``propostas_auto``) e marcado como automático, pendente de revisão.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

NOME_PENDENTES = "pendentes_anotacao.csv"


class ErroAnotacao(ValueError):
    """Linha de rótulo malformada ou caixa inválida."""


@dataclass(frozen=True, slots=True)
class Anotacao:
    """Uma caixa YOLO normalizada (formato de detecção)."""

    class_id: int
    x_center: float
    y_center: float
    largura: float
    altura: float

    def __post_init__(self) -> None:
        if self.class_id < 0:
            raise ErroAnotacao(f"class_id negativo: {self.class_id}")
        for nome, valor in (
            ("x_center", self.x_center),
            ("y_center", self.y_center),
            ("largura", self.largura),
            ("altura", self.altura),
        ):
            if not 0.0 <= valor <= 1.0:
                raise ErroAnotacao(f"{nome} fora de [0, 1]: {valor}")
        if self.largura <= 0.0 or self.altura <= 0.0:
            raise ErroAnotacao(
                f"caixa com largura/altura nula: w={self.largura}, h={self.altura}"
            )

    @property
    def x1(self) -> float:
        return self.x_center - self.largura / 2.0

    @property
    def y1(self) -> float:
        return self.y_center - self.altura / 2.0

    @property
    def x2(self) -> float:
        return self.x_center + self.largura / 2.0

    @property
    def y2(self) -> float:
        return self.y_center + self.altura / 2.0

    def para_linha(self) -> str:
        return (
            f"{self.class_id} {self.x_center:.6f} {self.y_center:.6f} "
            f"{self.largura:.6f} {self.altura:.6f}"
        )

    @classmethod
    def de_linha(cls, linha: str, *, numero_linha: int = 0) -> Anotacao:
        """Interpreta uma linha YOLO. Levanta :class:`ErroAnotacao` se inválida."""
        partes = linha.split()
        if len(partes) != 5:
            raise ErroAnotacao(
                f"linha {numero_linha}: esperadas 5 colunas, recebidas {len(partes)}: {linha!r}"
            )
        try:
            class_id = int(partes[0])
            x_center, y_center, largura, altura = (float(p) for p in partes[1:])
        except ValueError as erro:
            raise ErroAnotacao(f"linha {numero_linha}: valor não numérico em {linha!r}") from erro
        return cls(class_id, x_center, y_center, largura, altura)


def ler_label(caminho: str | Path) -> list[Anotacao]:
    """Lê um ``.txt`` YOLO. Arquivo vazio é válido (imagem negativa intencional)."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError(f"label não encontrado: {caminho}")
    anotacoes: list[Anotacao] = []
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
        linha = linha.strip()
        if not linha:
            continue
        anotacoes.append(Anotacao.de_linha(linha, numero_linha=numero))
    return anotacoes


def escrever_label(caminho: str | Path, anotacoes: Iterable[Anotacao]) -> Path:
    """Grava um ``.txt`` YOLO; lista vazia grava arquivo vazio (negativa)."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    linhas = [a.para_linha() for a in anotacoes]
    caminho.write_text("\n".join(linhas) + ("\n" if linhas else ""), encoding="utf-8")
    return caminho


@dataclass(frozen=True, slots=True)
class ProblemaAnotacao:
    """Um problema estrutural encontrado em um arquivo de label."""

    caminho: str
    severidade: str
    mensagem: str


def validar_arquivo_label(
    caminho: str | Path,
    *,
    num_classes: int,
    permitir_vazias: bool = True,
) -> list[ProblemaAnotacao]:
    """Valida um ``.txt`` YOLO: sintaxe, ids de classe e caixas fora da imagem.

    Imagens sem objetos (arquivo vazio) são válidas quando ``permitir_vazias``.
    """
    caminho = Path(caminho)
    relativo = str(caminho)
    problemas: list[ProblemaAnotacao] = []
    if not caminho.is_file():
        return [ProblemaAnotacao(relativo, "erro", "arquivo de label ausente")]
    texto = caminho.read_text(encoding="utf-8").strip()
    if not texto:
        if not permitir_vazias:
            problemas.append(ProblemaAnotacao(relativo, "erro", "imagem sem objetos anotados"))
        return problemas

    for numero, linha in enumerate(texto.splitlines(), start=1):
        linha = linha.strip()
        if not linha:
            continue
        try:
            anotacao = Anotacao.de_linha(linha, numero_linha=numero)
        except ErroAnotacao as erro:
            problemas.append(ProblemaAnotacao(relativo, "erro", str(erro)))
            continue
        if anotacao.class_id >= num_classes:
            problemas.append(
                ProblemaAnotacao(
                    relativo,
                    "erro",
                    f"linha {numero}: class_id {anotacao.class_id} fora do intervalo "
                    f"[0, {num_classes - 1}]",
                )
            )
        fora = (
            anotacao.x1 < -1e-6
            or anotacao.y1 < -1e-6
            or anotacao.x2 > 1 + 1e-6
            or anotacao.y2 > 1 + 1e-6
        )
        if fora:
            problemas.append(
                ProblemaAnotacao(
                    relativo,
                    "aviso",
                    f"linha {numero}: caixa ultrapassa os limites da imagem "
                    f"({anotacao.x1:.3f}, {anotacao.y1:.3f}, {anotacao.x2:.3f}, {anotacao.y2:.3f})",
                )
            )
    return problemas


@dataclass(slots=True)
class ResultadoExportacao:
    destino: Path
    imagens: int
    splits: dict[str, int] = field(default_factory=dict)

    def para_dict(self) -> dict[str, object]:
        return {"destino": str(self.destino), "imagens": self.imagens, "splits": self.splits}


def exportar_para_anotacao(
    raiz: str | Path,
    destino: str | Path,
    *,
    atribuicoes: Mapping[str, Sequence[str]],
    classes: Sequence[str],
) -> ResultadoExportacao:
    """Prepara uma cópia anotável (CVAT/Ultralytics) a partir do dataset de trabalho.

    Copia as imagens para ``destino/images/<split>/`` e cria ``labels/<split>/``
    vazio, pronto para o anotador preencher. Um ``pendentes_anotacao.csv`` lista
    cada imagem a anotar e o ``data.yaml`` descreve as classes.

    Args:
        raiz: raiz do dataset de trabalho (contém ``images/``).
        destino: pasta de saída para anotação.
        atribuicoes: ``split → lista de frame_ids`` (ex.: ``{"train": [...]}``).
        classes: nomes das classes na ordem dos ids YOLO.
    """
    origem = Path(raiz)
    destino = Path(destino)
    resultado = ResultadoExportacao(destino=destino, imagens=0)
    pendentes: list[dict[str, str]] = []

    from .dataset import nome_imagem_frame

    for split, frame_ids in atribuicoes.items():
        pasta_img = destino / "images" / split
        pasta_lbl = destino / "labels" / split
        pasta_img.mkdir(parents=True, exist_ok=True)
        pasta_lbl.mkdir(parents=True, exist_ok=True)
        for frame_id in frame_ids:
            nome = nome_imagem_frame(frame_id)
            candidatos = list((origem / "images").glob(f"**/{nome}"))
            if not candidatos:
                candidatos = list((origem / "images" / split).glob(nome))
            if not candidatos:
                log.warning("imagem %s não encontrada ao exportar", frame_id)
                continue
            arquivo = candidatos[0]
            shutil.copy2(arquivo, pasta_img / arquivo.name)
            (pasta_lbl / f"{arquivo.stem}.txt").touch()
            pendentes.append(
                {"frame_id": frame_id, "split": split, "imagem": f"images/{split}/{arquivo.name}"}
            )
        resultado.splits[split] = len(frame_ids)

    resultado.imagens = len(pendentes)
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / NOME_PENDENTES).open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=["frame_id", "split", "imagem"])
        escritor.writeheader()
        escritor.writerows(pendentes)
    _escrever_data_yaml_simples(destino / "data.yaml", classes)
    return resultado


def importar_anotacoes(origem_labels: str | Path, destino_labels: str | Path) -> list[str]:
    """Importa ``.txt`` de uma exportação (CVAT/Ultralytics) para o dataset de trabalho.

    Aceita a árvore ``images/labels`` ou uma pasta plana de ``.txt``. Só copia
    arquivos válidos estruturalmente; devolve a lista de nomes importados.
    """
    origem = Path(origem_labels)
    destino = Path(destino_labels)
    destino.mkdir(parents=True, exist_ok=True)
    if not origem.is_dir():
        raise FileNotFoundError(f"pasta de labels de origem não encontrada: {origem}")

    importados: list[str] = []
    for arquivo in sorted(origem.rglob("*.txt")):
        if arquivo.name == "data.txt":
            continue
        shutil.copy2(arquivo, destino / arquivo.name)
        importados.append(arquivo.name)
    return importados


def _escrever_data_yaml_simples(caminho: Path, classes: Sequence[str]) -> None:
    linhas = [
        "path: .",
        "train: images/train",
        "val: images/val",
        "",
        f"nc: {len(classes)}",
        "names:",
    ]
    linhas.extend(f"  {i}: {nome}" for i, nome in enumerate(classes))
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def manifesto_anotacoes(
    raiz: str | Path, *, splits: Sequence[str] = ("train", "val", "test")
) -> dict[str, object]:
    """Resume imagens anotadas, pendentes e com problemas por split."""
    raiz = Path(raiz)
    resultado: dict[str, object] = {"por_split": {}}
    total_img = total_lbl = total_vazias = 0
    for split in splits:
        pasta_img = raiz / "images" / split
        pasta_lbl = raiz / "labels" / split
        imagens = sorted(p.name for p in pasta_img.glob("*.jpg")) if pasta_img.is_dir() else []
        anotadas = vazias = 0
        for nome in imagens:
            label = pasta_lbl / f"{Path(nome).stem}.txt"
            if not label.is_file() or label.stat().st_size == 0:
                if label.is_file():
                    vazias += 1
                continue
            anotadas += 1
        pendentes = len(imagens) - anotadas - vazias
        resultado["por_split"][split] = {  # type: ignore[index]
            "imagens": len(imagens),
            "com_objetos": anotadas,
            "negativas_vazias": vazias,
            "pendentes": pendentes,
        }
        total_img += len(imagens)
        total_lbl += anotadas
        total_vazias += vazias
    resultado["imagens_total"] = total_img
    resultado["anotadas_com_objetos"] = total_lbl
    resultado["negativas_vazias"] = total_vazias
    resultado["pendentes"] = total_img - total_lbl - total_vazias
    return resultado


def salvar_json(caminho: str | Path, dados: Mapping[str, object]) -> Path:
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destino


__all__ = [
    "NOME_PENDENTES",
    "Anotacao",
    "ErroAnotacao",
    "ProblemaAnotacao",
    "ResultadoExportacao",
    "escrever_label",
    "exportar_para_anotacao",
    "importar_anotacoes",
    "ler_label",
    "manifesto_anotacoes",
    "salvar_json",
    "validar_arquivo_label",
]
