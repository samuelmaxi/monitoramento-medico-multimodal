"""Preparação do dataset YOLOv8 da US07: divisão, estrutura, ``data.yaml`` e gate.

Separação **sem vazamento**: a divisão é feita por grupo de origem
(``chave_grupo`` do nome do vídeo) e não por frame. Vídeos ``_resized``, ``raw``,
``mask`` e duplicatas exatas (mesmo SHA-256) do mesmo original ficam sempre no
mesmo conjunto. Dividir frames de um mesmo vídeo entre treino e teste vazaria
contexto e inflaria as métricas.

Taxonomia: a detecção espacial usa uma classe mínima (``person`` e,
opcionalmente, mobiliário COCO). As condições ``F``/``NF`` e os cenários
``B``/``C``/``S`` permanecem **metadados** — não viram classes YOLO sem
justificativa técnica e anotação específica. A classificação temporal de queda é
um problema à parte.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .anotacoes import ProblemaAnotacao, ler_label

log = logging.getLogger(__name__)

CLASSES_PADRAO: tuple[str, ...] = ("person",)
"""Taxonomia mínima da detecção: localizar pessoas. Ver docstring do módulo."""

PROPORCOES_PADRAO: dict[str, float] = {"train": 0.70, "val": 0.15, "test": 0.15}
SPLITS_PADRAO: tuple[str, ...] = ("train", "val", "test")

NOME_DATA_YAML = "data.yaml"
NOME_DIVISAO = "divisao.json"
NOME_FRAMES_DIVISAO = "frames_divisao.jsonl"
NOME_RELATORIO_APTIDAO = "aptidao_treinamento.json"


@dataclass(frozen=True, slots=True)
class RegistroFrame:
    """Projeção do manifesto de frames usada para dividir o dataset."""

    frame_id: str
    video_id: str
    chave_grupo: str
    arquivo_imagem: str
    condicao: str | None
    cenario: str | None
    modalidade: str | None
    duplicado: bool = False

    @classmethod
    def de_dict(cls, dados: Mapping[str, object]) -> RegistroFrame:
        return cls(
            frame_id=str(dados["frame_id"]),
            video_id=str(dados["video_id"]),
            chave_grupo=str(dados.get("chave_grupo") or dados["video_id"]),
            arquivo_imagem=str(dados.get("arquivo_imagem") or ""),
            condicao=_ou_none(dados.get("condicao")),
            cenario=_ou_none(dados.get("cenario")),
            modalidade=_ou_none(dados.get("modalidade")),
            duplicado=bool(dados.get("duplicado")),
        )

    @property
    def anotavel(self) -> bool:
        return bool(self.arquivo_imagem) and not self.duplicado


def _ou_none(valor: object) -> str | None:
    return None if valor is None else str(valor)


def carregar_registros(manifesto: str | Path) -> list[RegistroFrame]:
    """Lê o manifesto JSONL de frames em registros prontos para a divisão."""
    caminho = Path(manifesto)
    if not caminho.is_file():
        raise FileNotFoundError(f"manifesto de frames não encontrado: {caminho}")
    registros: list[RegistroFrame] = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha:
            registros.append(RegistroFrame.de_dict(json.loads(linha)))
    return registros


@dataclass(frozen=True, slots=True)
class GrupoVideo:
    """Agrupamento de frames de um mesmo original (ou duplicatas exatas dele)."""

    chave: str
    registros: tuple[RegistroFrame, ...]

    @property
    def condicao(self) -> str:
        return self.registros[0].condicao or "desconhecida"

    @property
    def cenario(self) -> str:
        return self.registros[0].cenario or "desconhecido"

    @property
    def estratificacao(self) -> tuple[str, str]:
        return (self.condicao, self.cenario)


class _UniaoBusca:
    def __init__(self, chaves: Iterable[str]) -> None:
        self._pai = {c: c for c in chaves}

    def encontrar(self, x: str) -> str:
        while self._pai[x] != x:
            self._pai[x] = self._pai[self._pai[x]]
            x = self._pai[x]
        return x

    def unir(self, a: str, b: str) -> None:
        raiz_a, raiz_b = self.encontrar(a), self.encontrar(b)
        if raiz_a != raiz_b:
            self._pai[raiz_b] = raiz_a


def agrupar_por_origem(
    registros: Sequence[RegistroFrame],
    *,
    hashes_por_grupo: Mapping[str, Sequence[str]] | None = None,
) -> list[GrupoVideo]:
    """Agrupa frames por origem, unindo grupos que compartilham um SHA-256 de vídeo.

    Args:
        registros: frames do manifesto (duplicados são úteis para o vínculo, mas
            não entram na materialização).
        hashes_por_grupo: ``chave_grupo → hashes SHA-256`` dos vídeos de origem.
            Permite fundir grupos de arquivos diferentes que são o mesmo vídeo.
    """
    por_chave: dict[str, list[RegistroFrame]] = {}
    for registro in registros:
        por_chave.setdefault(registro.chave_grupo, []).append(registro)

    if hashes_por_grupo:
        busca = _UniaoBusca(por_chave)
        sha_para_chave: dict[str, str] = {}
        for chave, hashes in hashes_por_grupo.items():
            if chave not in por_chave:
                continue
            for sha in hashes:
                anterior = sha_para_chave.get(sha)
                if anterior is None:
                    sha_para_chave[sha] = chave
                else:
                    busca.unir(anterior, chave)
        agrupado: dict[str, list[RegistroFrame]] = {}
        for chave, registros_chave in por_chave.items():
            raiz = busca.encontrar(chave)
            agrupado.setdefault(raiz, []).extend(registros_chave)
        por_chave = agrupado

    return [
        GrupoVideo(chave=chave, registros=tuple(ordenados))
        for chave, ordenados in sorted(por_chave.items())
    ]


def _contagens_por_split(n: int, proporcoes: Mapping[str, float]) -> dict[str, int]:
    """Distribui ``n`` grupos entre splits respeitando as proporções.

    Garante ao menos um grupo em cada split quando houver grupos suficientes
    (``n >= len(proporcoes)``), retirando das sobras de treino.
    """
    splits = list(proporcoes)
    if n <= 0:
        return dict.fromkeys(splits, 0)
    if n == 1:
        return {s: (1 if s == splits[0] else 0) for s in splits}

    brutos = {s: max(n * proporcoes[s], 0.0) for s in splits}
    contagens = {s: int(valor) for s, valor in brutos.items()}
    resto = n - sum(contagens.values())
    fracionarios = sorted(
        splits, key=lambda s: (brutos[s] - contagens[s], s), reverse=True
    )
    for i in range(resto):
        contagens[fracionarios[i % len(splits)]] += 1

    if n >= len(splits):
        for s in splits:
            if contagens[s] == 0:
                doador = max(splits, key=lambda x: contagens[x])
                contagens[doador] -= 1
                contagens[s] += 1
    return contagens


def planejar_divisao(
    grupos: Sequence[GrupoVideo],
    *,
    proporcoes: Mapping[str, float] | None = None,
    semente: int = 42,
) -> tuple[dict[str, str], list[str]]:
    """Atribui cada grupo de origem a um split, estratificando por condição/cenário.

    Returns:
        ``(divisao, avisos)`` em que ``divisao`` mapeia ``chave_grupo → split``.
    """
    proporcoes = dict(proporcoes or PROPORCOES_PADRAO)
    if not proporcoes or abs(sum(proporcoes.values()) - 1.0) > 1e-6:
        raise ValueError(f"proporções inválidas (devem somar 1.0): {proporcoes}")

    avisos: list[str] = []
    por_estrato: dict[tuple[str, str], list[GrupoVideo]] = {}
    for grupo in grupos:
        por_estrato.setdefault(grupo.estratificacao, []).append(grupo)

    rng = random.Random(semente)
    divisao: dict[str, str] = {}
    for estrato, itens in sorted(por_estrato.items()):
        chaves = sorted(g.chave for g in itens)
        rng.shuffle(chaves)
        contagens = _contagens_por_split(len(chaves), proporcoes)
        indice = 0
        for split in proporcoes:
            for _ in range(contagens[split]):
                if indice < len(chaves):
                    divisao[chaves[indice]] = split
                    indice += 1
        if len(chaves) < len(proporcoes):
            avisos.append(
                f"estrato {estrato} tem apenas {len(chaves)} grupo(s): "
                "não é possível representar todos os conjuntos nele"
            )
    if len({g.chave for g in grupos}) < 3:
        avisos.append(
            "menos de 3 grupos de origem: a divisão treino/val/teste não é "
            "estatisticamente confiável"
        )
    return divisao, avisos


def precedencia_split(divisao: Mapping[str, str]) -> str:
    """Split de menor precedência presente, usado para resolver conflitos de grupo."""
    for split in SPLITS_PADRAO:
        if split in divisao.values():
            return split
    return SPLITS_PADRAO[0]


def nome_imagem_frame(frame_id: str) -> str:
    """Nome de arquivo único por frame.

    O ``frame_id`` carrega o vídeo de origem (ex.: ``B_D_0001/frame_000001``), mas
    o nome base do arquivo (``frame_000001.jpg``) se repete entre vídeos. Prefixar
    com o ``video_id`` evita colisões na materialização.
    """
    return frame_id.replace("/", "__").replace("\\", "__") + ".jpg"


@dataclass(frozen=True, slots=True)
class ResultadoMaterializacao:
    """Números da materialização da divisão em ``images/`` e ``labels/``."""

    por_split: dict[str, int]
    grupos_por_split: dict[str, int]
    avisos: list[str]
    data_yaml: Path


def criar_estrutura(raiz: str | Path, *, splits: Sequence[str] = SPLITS_PADRAO) -> None:
    """Cria a árvore de diretórios do dataset (idempotente)."""
    base = Path(raiz)
    for pasta in ("images", "labels", "metadata", "reports"):
        (base / pasta).mkdir(parents=True, exist_ok=True)
    for split in splits:
        (base / "images" / split).mkdir(parents=True, exist_ok=True)
        (base / "labels" / split).mkdir(parents=True, exist_ok=True)


def materializar_divisao(
    raiz: str | Path,
    registros: Sequence[RegistroFrame],
    grupos: Sequence[GrupoVideo],
    divisao: Mapping[str, str],
    *,
    classes: Sequence[str] = CLASSES_PADRAO,
    copiar: bool = True,
    avisos: Sequence[str] = (),
) -> ResultadoMaterializacao:
    """Copia os frames para ``images/<split>`` e reserva ``labels/<split>``.

    As labels existentes (se houver) são preservadas; caso contrário o ``.txt`` do
    mesmo nome não é criado — o validador reporta a imagem como pendente.
    """
    base = Path(raiz)
    splits = tuple(dict.fromkeys(divisao.values()))
    criar_estrutura(base, splits=splits or SPLITS_PADRAO)

    fonte_imagens = base / "images"
    existentes: dict[str, Path] = {}
    for caminho in fonte_imagens.rglob("*.jpg"):
        if caminho.parent.name in SPLITS_PADRAO:
            existentes.setdefault(caminho.name, caminho)

    por_split: dict[str, int] = dict.fromkeys(SPLITS_PADRAO, 0)
    grupos_por_split: dict[str, set[str]] = {s: set() for s in SPLITS_PADRAO}
    colocacoes: list[dict[str, str]] = []

    for registro in registros:
        if not registro.anotavel:
            continue
        split = divisao.get(registro.chave_grupo)
        if split is None:
            continue
        nome_imagem = nome_imagem_frame(registro.frame_id)
        destino = base / "images" / split / nome_imagem
        if not destino.exists():
            relativo = base / registro.arquivo_imagem
            origem = relativo if relativo.is_file() else None
            if origem is None and nome_imagem in existentes:
                origem = existentes[nome_imagem]
            if origem is not None:
                if copiar:
                    shutil.copy2(origem, destino)
                else:
                    if destino.exists():
                        destino.unlink()
                    destino.symlink_to(origem.resolve())
        por_split[split] = por_split.get(split, 0) + 1
        grupos_por_split.setdefault(split, set()).add(registro.chave_grupo)
        colocacoes.append(
            {"frame_id": registro.frame_id, "chave_grupo": registro.chave_grupo, "split": split}
        )

    data_yaml = gerar_data_yaml(base, classes=classes)
    _salvar_divisao(base, divisao, grupos, por_split, colocacoes)
    return ResultadoMaterializacao(
        por_split={s: por_split.get(s, 0) for s in SPLITS_PADRAO},
        grupos_por_split={s: len(grupos_por_split.get(s, set())) for s in SPLITS_PADRAO},
        avisos=list(avisos),
        data_yaml=data_yaml,
    )


def _salvar_divisao(
    raiz: Path,
    divisao: Mapping[str, str],
    grupos: Sequence[GrupoVideo],
    por_split: Mapping[str, int],
    colocacoes: Sequence[Mapping[str, str]] = (),
) -> None:
    metadados = {
        "divisao": dict(sorted(divisao.items())),
        "grupos": {
            grupo.chave: {
                "split": divisao.get(grupo.chave),
                "condicao": grupo.condicao,
                "cenario": grupo.cenario,
                "n_frames": sum(1 for r in grupo.registros if r.anotavel),
            }
            for grupo in grupos
        },
        "frames_por_split": dict(por_split),
    }
    caminho = raiz / "metadata" / NOME_DIVISAO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(metadados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if colocacoes:
        caminho_frames = raiz / "metadata" / NOME_FRAMES_DIVISAO
        caminho_frames.write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in colocacoes)
            + ("\n" if colocacoes else ""),
            encoding="utf-8",
        )


def gerar_data_yaml(
    raiz: str | Path,
    *,
    classes: Sequence[str] = CLASSES_PADRAO,
    caminho: str | Path | None = None,
    path_relativo: str | None = None,
) -> Path:
    """Escreve o ``data.yaml`` relativo, carregável pela Ultralytics.

    ``path`` aponta para a raiz do dataset de forma relativa. O comando de
    treinamento/avaliação deve rodar da raiz do repositório para a resolução
    relativa funcionar (comportamento do Ultralytics descrito em
    ``docs/arquitetura/us07_dataset.md``).
    """
    base = Path(raiz)
    destino = Path(caminho) if caminho is not None else base / NOME_DATA_YAML
    destino.parent.mkdir(parents=True, exist_ok=True)
    if path_relativo is None:
        try:
            path_relativo = f"./{base.resolve().relative_to(Path.cwd().resolve()).as_posix()}"
        except ValueError:
            path_relativo = f"./{base.name}"
    linhas = [
        f"path: {path_relativo}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        f"nc: {len(classes)}",
        "names:",
    ]
    linhas.extend(f"  {i}: {nome}" for i, nome in enumerate(classes))
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return destino


@dataclass(frozen=True, slots=True)
class AvaliacaoAptidao:
    """Se o dataset pode ser levado a treinamento supervisionado e por quê."""

    apto: bool
    motivos: tuple[str, ...]
    estatisticas: dict[str, object] = field(default_factory=dict)

    def para_dict(self) -> dict[str, object]:
        return {"apto": self.apto, "motivos": list(self.motivos), "estatisticas": self.estatisticas}


def verificar_aptidao(
    raiz: str | Path,
    *,
    classes: Sequence[str] = CLASSES_PADRAO,
    minimo_anotados_por_split: int = 1,
    problemas_estruturais: Sequence[ProblemaAnotacao] = (),
) -> AvaliacaoAptidao:
    """Gate de prontidão: exige labels reais, splits populados e ausência de vazamento.

    Não inventa dados: sem labels anotados, ``apto=False`` com o motivo explícito.
    """
    base = Path(raiz)
    motivos: list[str] = []
    estatisticas: dict[str, object] = {}
    if not base.is_dir():
        return AvaliacaoAptidao(False, (f"raiz do dataset inexistente: {base}",), {})

    if not (base / NOME_DATA_YAML).is_file():
        motivos.append("data.yaml ausente; rode 'gerar-data-yaml'")

    erros = [p for p in problemas_estruturais if p.severidade == "erro"]
    if erros:
        motivos.append(f"{len(erros)} problema(s) estrutural(is) de erro no dataset")

    por_split: dict[str, dict[str, int]] = {}
    for split in SPLITS_PADRAO:
        pasta_img = base / "images" / split
        pasta_lbl = base / "labels" / split
        imagens = list(pasta_img.glob("*.jpg")) if pasta_img.is_dir() else []
        com_objetos = 0
        negativas = 0
        for imagem in imagens:
            label = pasta_lbl / f"{imagem.stem}.txt"
            if not label.is_file():
                continue
            try:
                anotacoes = ler_label(label)
            except Exception:
                continue
            if anotacoes:
                com_objetos += 1
            else:
                negativas += 1
        por_split[split] = {
            "imagens": len(imagens),
            "com_objetos": com_objetos,
            "negativas": negativas,
            "sem_label": len(imagens) - com_objetos - negativas,
        }
        if not imagens:
            motivos.append(f"split '{split}' sem imagens")
        elif com_objetos < minimo_anotados_por_split:
            motivos.append(
                f"split '{split}' sem anotações com objetos "
                f"(0 < mínimo {minimo_anotados_por_split})"
            )
    estatisticas["por_split"] = por_split

    vazamentos = verificar_vazamento(base)
    estatisticas["vazamentos"] = vazamentos
    if vazamentos:
        motivos.append(f"vazamento de origem entre conjuntos: {len(vazamentos)} caso(s)")

    return AvaliacaoAptidao(apto=not motivos, motivos=tuple(motivos), estatisticas=estatisticas)


def imagem_para_split(raiz: str | Path) -> dict[str, str]:
    """Mapa ``stem da imagem → split`` na árvore materializada."""
    base = Path(raiz)
    mapa: dict[str, str] = {}
    for split in SPLITS_PADRAO:
        pasta = base / "images" / split
        if pasta.is_dir():
            for imagem in pasta.glob("*.jpg"):
                mapa[imagem.stem] = split
    return mapa


def verificar_vazamento(raiz: str | Path) -> list[dict[str, str]]:
    """Detecta o mesmo ``chave_grupo`` (ou imagem) presente em mais de um split."""
    base = Path(raiz)
    colocacoes = base / "metadata" / NOME_FRAMES_DIVISAO
    vistos: dict[str, set[str]] = {}
    if colocacoes.is_file():
        for linha in colocacoes.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            registro = json.loads(linha)
            vistos.setdefault(str(registro["chave_grupo"]), set()).add(str(registro["split"]))
    else:
        caminho = base / "metadata" / NOME_DIVISAO
        if not caminho.is_file():
            return []
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        for grupo, info in (dados.get("grupos") or {}).items():
            split = str(info.get("split"))
            vistos.setdefault(grupo, set()).add(split)
    return [
        {"grupo": grupo, "splits": ",".join(sorted(splits))}
        for grupo, splits in sorted(vistos.items())
        if len(splits) > 1
    ]


@dataclass(frozen=True, slots=True)
class ResultadoGate:
    """Resultado do gate ``mAP@0.5 >= mínimo`` medido em ground truth válido."""

    aprovado: bool
    mapa_50: float | None
    minimo: float
    conjunto: str
    pesos: str
    medido: bool
    motivo: str

    def para_dict(self) -> dict[str, object]:
        return {
            "aprovado": self.aprovado,
            "medido": self.medido,
            "mAP@0.5": self.mapa_50,
            "minimo": self.minimo,
            "conjunto": self.conjunto,
            "pesos": self.pesos,
            "motivo": self.motivo,
        }


def avaliar_gate(
    *,
    mapa_50: float | None,
    minimo: float = 0.50,
    conjunto: str = "test",
    pesos: str = "",
) -> ResultadoGate:
    """Aplica o gate do projeto sem permitir aprovação sem medição real.

    ``mapa_50=None`` significa "não medido": o gate fica **bloqueado**, nunca
    aprovado por omissão.
    """
    if mapa_50 is None:
        return ResultadoGate(
            aprovado=False,
            mapa_50=None,
            minimo=minimo,
            conjunto=conjunto,
            pesos=pesos,
            medido=False,
            motivo="métrica mAP@0.5 não medida: avalie com ground truth válido antes de aprovar",
        )
    aprovado = mapa_50 >= minimo
    motivo = (
        f"mAP@0.5={mapa_50:.4f} >= {minimo:.2f}"
        if aprovado
        else f"mAP@0.5={mapa_50:.4f} < {minimo:.2f}"
    )
    return ResultadoGate(
        aprovado=aprovado,
        mapa_50=mapa_50,
        minimo=minimo,
        conjunto=conjunto,
        pesos=pesos,
        medido=True,
        motivo=motivo,
    )


__all__ = [
    "CLASSES_PADRAO",
    "NOME_DATA_YAML",
    "NOME_DIVISAO",
    "NOME_FRAMES_DIVISAO",
    "PROPORCOES_PADRAO",
    "SPLITS_PADRAO",
    "AvaliacaoAptidao",
    "GrupoVideo",
    "RegistroFrame",
    "ResultadoGate",
    "ResultadoMaterializacao",
    "agrupar_por_origem",
    "avaliar_gate",
    "carregar_registros",
    "criar_estrutura",
    "gerar_data_yaml",
    "imagem_para_split",
    "materializar_divisao",
    "nome_imagem_frame",
    "planejar_divisao",
    "verificar_aptidao",
    "verificar_vazamento",
]
