"""Validador estrutural do dataset YOLO da US07 (independente do treinamento).

Confere imagem ↔ label, integridade dos ``.txt``, ids de classe, coordenadas,
caixas degeneradas/fora da imagem, imagens sem objetos (negativas válidas),
classes sem exemplos, distribuição por split/grupo e vazamento entre conjuntos.

Não substitui a revisão visual: aponta o que é estruturalmente inconsistente e
gera um relatório legível (``reports/validacao_dataset.json`` e resumo textual).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2

from .anotacoes import ProblemaAnotacao, validar_arquivo_label
from .dataset import SPLITS_PADRAO, verificar_vazamento


@dataclass(slots=True)
class RelatorioValidacao:
    """Erros, avisos e estatísticas da validação estrutural."""

    erros: list[ProblemaAnotacao] = field(default_factory=list)
    avisos: list[ProblemaAnotacao] = field(default_factory=list)
    estatisticas: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.erros

    def para_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "total_erros": len(self.erros),
            "total_avisos": len(self.avisos),
            "erros": [asdict(p) for p in self.erros],
            "avisos": [asdict(p) for p in self.avisos],
            "estatisticas": self.estatisticas,
        }

    def resumo_texto(self) -> str:
        linhas = [
            f"Validação estrutural do dataset — {'OK' if self.ok else 'COM ERROS'}",
            f"  erros: {len(self.erros)} | avisos: {len(self.avisos)}",
        ]
        for chave, valor in self.estatisticas.items():
            linhas.append(f"  {chave}: {valor}")
        for problema in self.erros[:50]:
            linhas.append(f"  [erro] {problema.caminho}: {problema.mensagem}")
        for problema in self.avisos[:20]:
            linhas.append(f"  [aviso] {problema.caminho}: {problema.mensagem}")
        return "\n".join(linhas)


def _validar_imagem(caminho: Path) -> bool:
    imagem = cv2.imread(str(caminho))
    return imagem is not None


def validar_dataset(
    raiz: str | Path,
    *,
    classes: Sequence[str],
    splits: Sequence[str] = SPLITS_PADRAO,
    checar_imagens: bool = True,
    pasta_propostas: str | Path | None = None,
    metadados: Mapping[str, Mapping[str, object]] | None = None,
) -> RelatorioValidacao:
    """Executa a validação estrutural completa e devolve o relatório.

    Args:
        raiz: raiz do dataset (``images/``, ``labels/``, ``metadata/``).
        classes: nomes das classes na ordem dos ids YOLO.
        splits: conjuntos a validar.
        checar_imagens: abre cada imagem com o OpenCV para detectar arquivos inválidos.
        pasta_propostas: pasta de propostas automáticas (contagem de não revisadas).
        metadados: ``frame_id/stem → metadados`` (grupo, cenário, condição) para
            distribuição por subgrupo.
    """
    base = Path(raiz)
    relatorio = RelatorioValidacao()
    num_classes = len(classes)

    distribuicao_classe: dict[str, dict[str, int]] = {}
    por_split: dict[str, dict[str, int]] = {}
    total_imagens = total_com_objetos = total_negativas = total_sem_label = 0
    leituras: dict[str, list[list[float]]] = {}
    stems_por_split: dict[str, set[str]] = {}

    for split in splits:
        pasta_img = base / "images" / split
        pasta_lbl = base / "labels" / split
        imagens = sorted(pasta_img.glob("*.jpg")) if pasta_img.is_dir() else []
        contagem_classe = dict.fromkeys(classes, 0)
        com_objetos = negativas = sem_label = 0
        stems_por_split[split] = {p.stem for p in imagens}

        for imagem in imagens:
            total_imagens += 1
            label = pasta_lbl / f"{imagem.stem}.txt"
            if not label.is_file():
                sem_label += 1
                relatorio.erros.append(
                    ProblemaAnotacao(str(imagem), "erro", "imagem sem arquivo de label")
                )
                continue
            if checar_imagens and not _validar_imagem(imagem):
                relatorio.erros.append(
                    ProblemaAnotacao(str(imagem), "erro", "arquivo de imagem inválido/ilegível")
                )
            problemas = validar_arquivo_label(label, num_classes=num_classes)
            relatorio.erros.extend(p for p in problemas if p.severidade == "erro")
            relatorio.avisos.extend(p for p in problemas if p.severidade != "erro")
            try:
                from .anotacoes import ler_label

                anotacoes = ler_label(label)
            except Exception:
                anotacoes = []
            if anotacoes:
                com_objetos += 1
                leituras.setdefault(imagem.stem, []).append(
                    [a.x_center for a in anotacoes] + [a.y_center for a in anotacoes]
                )
                for anotacao in anotacoes:
                    if 0 <= anotacao.class_id < num_classes:
                        contagem_classe[classes[anotacao.class_id]] += 1
            else:
                negativas += 1

        distribuicao_classe[split] = contagem_classe
        por_split[split] = {
            "imagens": len(imagens),
            "com_objetos": com_objetos,
            "negativas": negativas,
            "sem_label": sem_label,
        }
        total_com_objetos += com_objetos
        total_negativas += negativas
        total_sem_label += sem_label

    relatorio.estatisticas["por_split"] = por_split
    relatorio.estatisticas["distribuicao_classes"] = distribuicao_classe
    relatorio.estatisticas["imagens_total"] = total_imagens
    relatorio.estatisticas["com_objetos"] = total_com_objetos
    relatorio.estatisticas["negativas"] = total_negativas
    relatorio.estatisticas["sem_label"] = total_sem_label
    relatorio.estatisticas["percentual_anotado"] = (
        round(100.0 * (total_com_objetos + total_negativas) / total_imagens, 2)
        if total_imagens
        else 0.0
    )

    for classe in classes:
        total_classe = sum(distribuicao_classe[s][classe] for s in splits)
        if total_classe == 0:
            relatorio.avisos.append(
                ProblemaAnotacao("dataset", "aviso", f"classe '{classe}' sem exemplos anotados")
            )

    vazamentos = verificar_vazamento(base)
    relatorio.estatisticas["vazamentos"] = vazamentos
    for vazamento in vazamentos:
        relatorio.erros.append(
            ProblemaAnotacao(
                "dataset",
                "erro",
                f"grupo {vazamento['grupo']} aparece em múltiplos splits: {vazamento['splits']}",
            )
        )

    _contar_duplicados(base, relatorio)
    if metadados:
        relatorio.estatisticas["por_grupo_cenario_condicao"] = _distribuicao_metadados(
            base, splits, metadados
        )
    if pasta_propostas is not None:
        relatorio.estatisticas["anotacoes_automaticas_nao_revisadas"] = _contar_propostas(
            Path(pasta_propostas)
        )
    return relatorio


def _contar_duplicados(base: Path, relatorio: RelatorioValidacao) -> None:
    """Conta imagens com o mesmo conteúdo (nome repetido em splits diferentes)."""
    imagens_por_nome: dict[str, list[str]] = {}
    for split in SPLITS_PADRAO:
        pasta = base / "images" / split
        if pasta.is_dir():
            for imagem in pasta.glob("*.jpg"):
                imagens_por_nome.setdefault(imagem.name, []).append(split)
    repetidas = {
        nome: splits for nome, splits in imagens_por_nome.items() if len(splits) > 1
    }
    relatorio.estatisticas["imagens_repetidas_entre_splits"] = len(repetidas)
    for nome, splits in sorted(repetidas.items())[:20]:
        relatorio.avisos.append(
            ProblemaAnotacao(
                "dataset", "aviso", f"imagem '{nome}' aparece em vários splits: {splits}"
            )
        )


def _distribuicao_metadados(
    base: Path,
    splits: Sequence[str],
    metadados: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, int]]:
    resultado: dict[str, dict[str, int]] = {}
    for split in splits:
        pasta = base / "images" / split
        if not pasta.is_dir():
            continue
        for imagem in pasta.glob("*.jpg"):
            info = metadados.get(imagem.stem, {})
            chave = (
                f"grupo={info.get('grupo')}|cenario={info.get('cenario')}"
                f"|condicao={info.get('condicao')}"
            )
            resultado.setdefault(split, {})
            resultado[split][chave] = resultado[split].get(chave, 0) + 1
    return resultado


def _contar_propostas(pasta: Path) -> int:
    if not pasta.is_dir():
        return 0
    return sum(1 for _ in pasta.rglob("*.txt"))


def salvar_relatorio(relatorio: RelatorioValidacao, caminho: str | Path) -> Path:
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(relatorio.para_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destino


__all__ = ["RelatorioValidacao", "salvar_relatorio", "validar_dataset"]
