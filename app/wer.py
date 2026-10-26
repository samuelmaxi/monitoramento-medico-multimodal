"""Word Error Rate (WER) da transcrição contra a referência manual (US11).

WER = (substituições + deleções + inserções) / palavras da referência.

A normalização é deliberadamente simples: minúsculas, sem pontuação e com
espaços colapsados. Números escritos em algarismos ("120") e por extenso
("cento e vinte") contam como erro, e isso deve ser descrito no relatório.
"""

import re
import unicodedata
from dataclasses import dataclass

_NAO_PALAVRA = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class ResultadoWER:
    wer: float
    palavras_referencia: int
    substituicoes: int
    delecoes: int
    insercoes: int

    @property
    def erros(self) -> int:
        return self.substituicoes + self.delecoes + self.insercoes


def normalizar(texto: str) -> list[str]:
    """Devolve as palavras do texto em minúsculas, sem pontuação."""
    texto = unicodedata.normalize("NFC", texto).lower()
    texto = _NAO_PALAVRA.sub(" ", texto)
    return texto.split()


def calcular_wer(referencia: str, hipotese: str) -> ResultadoWER:
    ref = normalizar(referencia)
    hip = normalizar(hipotese)

    if not ref:
        # Sem palavras na referência, qualquer palavra reconhecida é inserção.
        return ResultadoWER(
            wer=0.0 if not hip else 1.0,
            palavras_referencia=0,
            substituicoes=0,
            delecoes=0,
            insercoes=len(hip),
        )

    subst, dele, inser = _alinhar(ref, hip)
    return ResultadoWER(
        wer=(subst + dele + inser) / len(ref),
        palavras_referencia=len(ref),
        substituicoes=subst,
        delecoes=dele,
        insercoes=inser,
    )


def wer_agregado(resultados: list[ResultadoWER]) -> float:
    """WER global: soma dos erros dividida pela soma das palavras de referência."""
    total_ref = sum(r.palavras_referencia for r in resultados)
    if total_ref == 0:
        return 0.0
    return sum(r.erros for r in resultados) / total_ref


def classificar_wer(wer: float) -> str:
    """Faixas do DoD da US11 (valores em fração, 0.20 = 20%)."""
    if wer <= 0.10:
        return "boa qualidade (meta desejável)"
    if wer <= 0.20:
        return "aceitável"
    if wer < 0.30:
        return "acima do limite aceitável: aplicar phrase list ou Custom Speech e medir de novo"
    return "ruim: não aceito"


def _alinhar(ref: list[str], hip: list[str]) -> tuple[int, int, int]:
    """Distância de edição entre palavras, com contagem de cada tipo de erro."""
    n, m = len(ref), len(hip)
    # custo[i][j] = edições para transformar ref[:i] em hip[:j]
    custo = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        custo[i][0] = i
    for j in range(1, m + 1):
        custo[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hip[j - 1]:
                custo[i][j] = custo[i - 1][j - 1]
            else:
                custo[i][j] = 1 + min(
                    custo[i - 1][j - 1],  # substituição
                    custo[i - 1][j],  # deleção
                    custo[i][j - 1],  # inserção
                )

    subst = dele = inser = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hip[j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and custo[i][j] == custo[i - 1][j - 1] + 1:
            subst += 1
            i, j = i - 1, j - 1
        elif i > 0 and custo[i][j] == custo[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            inser += 1
            j -= 1
    return subst, dele, inser
