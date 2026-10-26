from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import yaml

from .anotacoes import Anotacao
from .dataset import gerar_data_yaml
from .detector import CLASSES_EXIGEM_FINETUNING

SPLITS = ("train", "val", "test")
EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True, slots=True)
class DatasetOrigem:
    tag: str
    caminho: Path


@dataclass(frozen=True, slots=True)
class ResultadoCombinacao:
    destino: Path
    imagens_por_split: Mapping[str, int]
    objetos_por_classe: Mapping[str, Mapping[str, int]]
    anotacoes_nao_mapeadas_ignoradas: int
    imagens_com_classes_nao_mapeadas_descartadas: int
    imagens_duplicadas: int

    def para_dict(self) -> dict[str, object]:
        return {
            "destino": str(self.destino),
            "imagens_por_split": dict(self.imagens_por_split),
            "objetos_por_classe": {
                split: dict(contagens) for split, contagens in self.objetos_por_classe.items()
            },
            "anotacoes_nao_mapeadas_ignoradas": self.anotacoes_nao_mapeadas_ignoradas,
            "imagens_com_classes_nao_mapeadas_descartadas": (
                self.imagens_com_classes_nao_mapeadas_descartadas
            ),
            "imagens_duplicadas": self.imagens_duplicadas,
        }


def _ler_yaml(caminho: Path) -> tuple[dict[str, object], tuple[str, ...], Path]:
    yaml_path = caminho / "data.yaml" if caminho.is_dir() else caminho
    if not yaml_path.is_file():
        raise FileNotFoundError(f"data.yaml não encontrado: {yaml_path}")
    config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"data.yaml inválido: {yaml_path}")
    nomes = config.get("names")
    if isinstance(nomes, list):
        classes = tuple(str(nome) for nome in nomes)
    elif isinstance(nomes, dict):
        try:
            ordenados = sorted((int(indice), str(nome)) for indice, nome in nomes.items())
        except (TypeError, ValueError) as erro:
            raise ValueError(f"taxonomia names inválida em {yaml_path}") from erro
        if [indice for indice, _ in ordenados] != list(range(len(ordenados))):
            raise ValueError(f"ids names devem começar em 0 e ser contíguos: {yaml_path}")
        classes = tuple(nome for _, nome in ordenados)
    else:
        raise ValueError(f"data.yaml sem lista/mapa names: {yaml_path}")
    if not classes or len(set(classes)) != len(classes):
        raise ValueError(f"classes ausentes ou duplicadas em {yaml_path}")
    raiz_configurada = config.get("path")
    raiz = Path(str(raiz_configurada)) if raiz_configurada else yaml_path.parent
    if not raiz.is_absolute():
        raiz = (yaml_path.parent / raiz).resolve()
    return config, classes, raiz


def _diretorio_split(
    config: Mapping[str, object], yaml_path: Path, raiz: Path, split: str
) -> Path:
    chaves = ("val", "valid", "validation") if split == "val" else (split,)
    caminhos = [config[chave] for chave in chaves if config.get(chave)]
    candidatos: list[Path] = []
    for valor in caminhos:
        if isinstance(valor, list):
            candidatos.extend(Path(str(item)) for item in valor)
        else:
            candidatos.append(Path(str(valor)))
    candidatos.extend((Path(split) / "images", Path("images") / split))
    if split == "val":
        candidatos.append(Path("valid") / "images")
    resolvidos: list[Path] = []
    for candidato in candidatos:
        if candidato.is_absolute():
            resolvidos.append(candidato)
        else:
            resolvidos.extend((raiz / candidato, yaml_path.parent / candidato))
    for candidato in resolvidos:
        if candidato.is_dir() and any(
            arquivo.suffix.lower() in EXTENSOES_IMAGEM for arquivo in candidato.rglob("*")
        ):
            return candidato
    return next((c for c in resolvidos if c.is_dir()), resolvidos[0])


def _diretorio_labels(imagens: Path, raiz: Path, split: str) -> Path:
    candidatos = (imagens.parent / "labels", raiz / split / "labels", raiz / "labels" / split)
    return next((candidato for candidato in candidatos if candidato.is_dir()), candidatos[0])


def _nome_imagem(tag: str, imagem: Path, pasta_imagens: Path) -> str:
    relativo = imagem.relative_to(pasta_imagens).with_suffix("").as_posix()
    seguro = re.sub(r"[^A-Za-z0-9_.-]+", "_", relativo.replace("/", "__"))
    return f"{tag}__{seguro}.jpg"


def _ler_anotacoes(
    caminho: Path,
    classes_origem: Sequence[str],
    mapa: Mapping[str, str],
    classes_destino: Sequence[str],
) -> tuple[list[Anotacao], int]:
    if not caminho.is_file():
        raise FileNotFoundError(f"label ausente para imagem {caminho.with_suffix('')}: {caminho}")
    resultado: list[Anotacao] = []
    ignoradas = 0
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
        if not linha.strip():
            continue
        anotacao = Anotacao.de_linha(linha, numero_linha=numero)
        if anotacao.class_id >= len(classes_origem):
            raise ValueError(f"class_id {anotacao.class_id} fora da taxonomia em {caminho}")
        nome_origem = classes_origem[anotacao.class_id]
        classe_destino = mapa.get(nome_origem)
        if classe_destino is None:
            ignoradas += 1
            continue
        resultado.append(
            Anotacao(
                class_id=classes_destino.index(classe_destino),
                x_center=anotacao.x_center,
                y_center=anotacao.y_center,
                largura=anotacao.largura,
                altura=anotacao.altura,
            )
        )
    return resultado, ignoradas


def combinar_datasets_roboflow(
    datasets: Sequence[DatasetOrigem],
    *,
    mapa_classes: Mapping[str, Mapping[str, str]],
    destino: str | Path,
) -> ResultadoCombinacao:
    """Combina exports YOLOv8 com mapeamento explícito e splits preservados."""
    if not datasets:
        raise ValueError("informe pelo menos um dataset de origem")
    tags = [origem.tag for origem in datasets]
    if len(tags) != len(set(tags)) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]+", tag) for tag in tags
    ):
        raise ValueError("tags devem ser únicas e conter somente letras, números, _ ou -")
    if set(tags) != set(mapa_classes):
        raise ValueError("deve haver um mapa de classes para cada tag de dataset")

    classes_destino = set()
    configuracoes: list[
        tuple[DatasetOrigem, Path, tuple[str, ...], Path, dict[str, Path], Mapping[str, str]]
    ] = []
    for origem in datasets:
        mapa = mapa_classes[origem.tag]
        if not mapa:
            raise ValueError(f"mapa vazio para {origem.tag}")
        desconhecidas_destino = set(mapa.values()) - set(CLASSES_EXIGEM_FINETUNING)
        if desconhecidas_destino:
            raise ValueError(
                f"classes destino não reconhecidas como médicas: {sorted(desconhecidas_destino)}"
            )
        classes_destino.update(mapa.values())
        yaml_path = origem.caminho / "data.yaml" if origem.caminho.is_dir() else origem.caminho
        config, classes_origem, raiz = _ler_yaml(yaml_path)
        classes_ausentes = set(mapa) - set(classes_origem)
        if classes_ausentes:
            raise ValueError(
                f"classes de origem não encontradas em {yaml_path}: {sorted(classes_ausentes)}; "
                f"disponíveis: {list(classes_origem)}"
            )
        pastas = {split: _diretorio_split(config, yaml_path, raiz, split) for split in SPLITS}
        configuracoes.append((origem, yaml_path, classes_origem, raiz, pastas, mapa))

    classes_saida = tuple(
        classe for classe in CLASSES_EXIGEM_FINETUNING if classe in classes_destino
    )
    if not {"instrumento_cirurgico", "monitor_multiparametro"}.issubset(classes_saida):
        raise ValueError("os datasets devem mapear as duas classes médicas exigidas")

    destino_path = Path(destino).resolve()
    if destino_path.exists() and any(destino_path.iterdir()):
        raise FileExistsError(f"destino deve estar vazio ou não existir: {destino_path}")
    destino_path.parent.mkdir(parents=True, exist_ok=True)
    imagens_por_split: Counter[str] = Counter()
    objetos_por_classe = {split: Counter() for split in SPLITS}
    anotacoes_ignoradas = 0
    imagens_descartadas = 0
    duplicatas = 0
    hashes: dict[str, tuple[str, Path, Path]] = {}

    with tempfile.TemporaryDirectory(
        prefix=f".{destino_path.name}-", dir=destino_path.parent
    ) as tmp:
        staging = Path(tmp) / "dataset"
        for split in SPLITS:
            (staging / "images" / split).mkdir(parents=True)
            (staging / "labels" / split).mkdir(parents=True)

        for origem, _yaml_path, classes_origem, raiz, pastas, mapa in configuracoes:
            for split, pasta_imagens in pastas.items():
                if not pasta_imagens.is_dir():
                    continue
                pasta_labels = _diretorio_labels(pasta_imagens, raiz, split)
                imagens = sorted(
                    p for p in pasta_imagens.rglob("*")
                    if p.is_file() and p.suffix.lower() in EXTENSOES_IMAGEM
                )
                if not imagens:
                    raise ValueError(f"split {split} sem imagens em {origem.tag}: {pasta_imagens}")
                for imagem in imagens:
                    label_origem = pasta_labels / f"{imagem.stem}.txt"
                    anotacoes, quantidade_ignoradas = _ler_anotacoes(
                        label_origem, classes_origem, mapa, classes_saida
                    )
                    anotacoes_ignoradas += quantidade_ignoradas
                    if quantidade_ignoradas:
                        imagens_descartadas += 1
                        continue
                    digest = hashlib.sha256(imagem.read_bytes()).hexdigest()
                    nome = _nome_imagem(origem.tag, imagem, pasta_imagens)
                    if digest in hashes:
                        split_anterior, caminho_anterior, label_anterior = hashes[digest]
                        if split_anterior != split:
                            raise ValueError(
                                "imagem idêntica encontrada em splits diferentes: "
                                f"{caminho_anterior} ({split_anterior}) e {imagem} ({split})"
                            )
                        conteudo_anterior = [
                            Anotacao.de_linha(linha)
                            for linha in label_anterior.read_text(encoding="utf-8").splitlines()
                            if linha.strip()
                        ]
                        if conteudo_anterior != anotacoes:
                            raise ValueError(
                                "imagem duplicada com rótulos conflitantes: "
                                f"{caminho_anterior} e {imagem}"
                            )
                        duplicatas += 1
                        continue
                    hashes[digest] = (
                        split,
                        imagem,
                        staging / "labels" / split / f"{Path(nome).stem}.txt",
                    )
                    saida_imagem = staging / "images" / split / nome
                    conteudo = cv2.imread(str(imagem))
                    if conteudo is None:
                        raise ValueError(f"imagem ilegível: {imagem}")
                    if not cv2.imwrite(str(saida_imagem), conteudo):
                        raise OSError(f"não foi possível converter imagem para JPEG: {imagem}")
                    label_saida = staging / "labels" / split / f"{Path(nome).stem}.txt"
                    label_saida.write_text(
                        "\n".join(anotacao.para_linha() for anotacao in anotacoes)
                        + ("\n" if anotacoes else ""),
                        encoding="utf-8",
                    )
                    imagens_por_split[split] += 1
                    for anotacao in anotacoes:
                        objetos_por_classe[split][classes_saida[anotacao.class_id]] += 1

        classes_saida = tuple(
            classe for classe in CLASSES_EXIGEM_FINETUNING
            if any(classe in mapa.values() for mapa in mapa_classes.values())
        )
        for split in SPLITS:
            ausentes = [
                classe for classe in classes_saida if objetos_por_classe[split][classe] == 0
            ]
            if ausentes:
                raise ValueError(
                    f"split {split!r} sem objetos anotados para classes: {ausentes}; "
                    "confira classes e labels de origem antes do treino"
                )
        gerar_data_yaml(
            staging,
            classes=classes_saida,
            path_relativo=str(destino_path),
        )
        destino_path.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            shutil.move(str(item), destino_path / item.name)

    return ResultadoCombinacao(
        destino=destino_path,
        imagens_por_split={split: imagens_por_split[split] for split in SPLITS},
        objetos_por_classe={
            split: {classe: objetos_por_classe[split][classe] for classe in classes_saida}
            for split in SPLITS
        },
        anotacoes_nao_mapeadas_ignoradas=anotacoes_ignoradas,
        imagens_com_classes_nao_mapeadas_descartadas=imagens_descartadas,
        imagens_duplicadas=duplicatas,
    )


def analisar_mapeamentos(datasets: Sequence[DatasetOrigem]) -> dict[str, list[str]]:
    """Lista classes exportadas nos YAMLs sem contatar API externa."""
    resultado: dict[str, list[str]] = {}
    for origem in datasets:
        yaml_path = origem.caminho / "data.yaml" if origem.caminho.is_dir() else origem.caminho
        _, classes, _ = _ler_yaml(yaml_path)
        resultado[origem.tag] = list(classes)
    return resultado
