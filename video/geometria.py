"""Primitivas geométricas da US07: IoU e interseção caixa/polígono.

Tudo em Python puro, sem numpy nem OpenCV, para que a lógica geométrica seja
determinística e testável isoladamente. A origem dos pontos é o OpenCV, mas a
convenção de eixos (x→direita, y→baixo, origem no canto superior esquerdo) é a
mesma do COCO/YOLO, então as coordenadas não precisam ser convertidas.

Para a interseção caixa/polígono usamos o algoritmo de Sutherland-Hodgman: o
polígono da ROI é recortado pelos quatro lados da caixa, que formam um recorte
convexo. Com recorte convexo, a área resultante via fórmula do cordão (shoelace)
é exatamente a área de interseção, mesmo para ROIs côncavas.
"""

from __future__ import annotations

import math

from .modelos import Caixa, Poligono, Ponto

EPSILON = 1e-9
"""Tolerância para comparações de ponto sobre a borda do polígono."""


class PoligonoInvalido(ValueError):
    """O polígono da ROI não pode ser usado (vértices de menos, área nula...)."""


def iou(caixa_a: Caixa, caixa_b: Caixa) -> float:
    """Interseção sobre união entre duas caixas.

    Devolve ``1.0`` para caixas idênticas e ``0.0`` quando não há interseção
    (inclusive quando só se tocam nas bordas, sem sobreposição de área). É o
    critério de acerto da US07: uma detecção só casa com uma anotação quando
    ``iou >= 0.5``.
    """
    esquerda = max(caixa_a.x1, caixa_b.x1)
    cima = max(caixa_a.y1, caixa_b.y1)
    direita = min(caixa_a.x2, caixa_b.x2)
    baixo = min(caixa_a.y2, caixa_b.y2)

    largura = max(0.0, direita - esquerda)
    altura = max(0.0, baixo - cima)
    inter = largura * altura
    if inter <= 0.0:
        return 0.0

    uniao = caixa_a.area + caixa_b.area - inter
    if uniao <= EPSILON:
        return 0.0
    return inter / uniao


def validar_poligono(pontos: Poligono) -> Poligono:
    """Confere que o polígono tem ao menos três vértices e área não nula.

    Args:
        pontos: vértices em pixels.

    Returns:
        O mesmo polígono, como tupla, quando válido.

    Raises:
        PoligonoInvalido: com menos de três vértices, coordenada não finita ou
            área (ou perímetro) degenerado.
    """
    if len(pontos) < 3:
        raise PoligonoInvalido(
            f"polígono precisa de ao menos 3 vértices, recebeu {len(pontos)}"
        )
    for indice, (x, y) in enumerate(pontos):
        if not (math.isfinite(x) and math.isfinite(y)):
            raise PoligonoInvalido(f"vértice {indice} não finito: ({x}, {y})")

    if abs(area_poligono(pontos)) <= EPSILON:
        raise PoligonoInvalido(
            "polígono com área nula (vértices colineares ou duplicados)"
        )
    return tuple(pontos)


def area_poligono(pontos: Poligono) -> float:
    """Área absoluta do polígono pela fórmula do cordão."""
    if len(pontos) < 3:
        return 0.0
    total = 0.0
    for indice, (x1, y1) in enumerate(pontos):
        x2, y2 = pontos[(indice + 1) % len(pontos)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def ponto_esta_dentro(ponto: Ponto, pontos: Poligono, *, incluir_borda: bool = True) -> bool:
    """Teste ponto-em-polígono por cruzamento de raio (ray casting).

    Args:
        ponto: ``(x, y)`` a testar.
        pontos: vértices do polígono.
        incluir_borda: quando ``True`` (padrão), pontos sobre uma aresta ou
            vértice contam como dentro — evita o vaivém indefinido quando a
            detecção está exatamente sobre a borda da ROI.

    Returns:
        ``True`` se o ponto está dentro (ou na borda, se ``incluir_borda``).
    """
    x, y = ponto
    dentro = False
    total = len(pontos)
    for indice in range(total):
        x1, y1 = pontos[indice]
        x2, y2 = pontos[(indice + 1) % total]
        if _ponto_na_aresta(ponto, (x1, y1), (x2, y2)):
            return incluir_borda
        # Regra do cruzeiro: a aresta atravessa o semiplano à direita do ponto.
        if (y1 > y) != (y2 > y):
            x_cruze = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x_cruze > x:
                dentro = not dentro
    return dentro


def fracao_da_caixa_dentro(caixa: Caixa, pontos: Poligono) -> float:
    """Fração da área da caixa contida no polígono, de ``0.0`` a ``1.0``.

    É o critério de contenção padrão da US07: a detecção pertence à ROI quando
    a fração é ``>= limiar_area`` (padrão ``0.5``). Usar fração em vez do centro
    evita o caso em que o centro está fora mas metade da pessoa já está dentro da
    área crítica.
    """
    if caixa.area <= EPSILON:
        return 0.0
    interseccao = area_interseccao_caixa_poligono(caixa, pontos)
    if interseccao <= 0.0:
        return 0.0
    return min(1.0, interseccao / caixa.area)


def area_interseccao_caixa_poligono(caixa: Caixa, pontos: Poligono) -> float:
    """Área exata da interseção entre a caixa e o polígono."""
    recortado = _recortar_por_caixa(pontos, caixa)
    if len(recortado) < 3:
        return 0.0
    return area_poligono(recortado)


def _classificar(ponto: Ponto, inicio: Ponto, fim: Ponto) -> int:
    """Posição do ponto em relação à aresta ``inicio``→``fim``.

    Usa produto vetorial: positivo à esquerda (sentido anti-horário em y para
    cima), negativo à direita, zero sobre a reta. Em coordenadas de imagem o eixo
    ``y`` aponta para baixo, então o sinal aparece invertido — por isso o
    chamador compara com ``>=``/``<=`` e não com ``>``/``<`` sobre o sinal.
    """
    (x, y), (x1, y1), (x2, y2) = ponto, inicio, fim
    return (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)


def _ponto_na_aresta(ponto: Ponto, inicio: Ponto, fim: Ponto) -> bool:
    """``True`` se o ponto estiver sobre o segmento (tolerância relativa)."""
    if _classificar(ponto, inicio, fim) != 0:
        return False
    x, y = ponto
    x1, y1 = inicio
    x2, y2 = fim
    menor_x, maior_x = min(x1, x2), max(x1, x2)
    menor_y, maior_y = min(y1, y2), max(y1, y2)
    tolerancia = EPSILON * max(1.0, abs(x2 - x1), abs(y2 - y1))
    return (
        menor_x - tolerancia <= x <= maior_x + tolerancia
        and menor_y - tolerancia <= y <= maior_y + tolerancia
    )


def _interseccao_arestas(
    a_inicio: Ponto, a_fim: Ponto, b_inicio: Ponto, b_fim: Ponto
) -> Ponto | None:
    """Interseção entre dois segmentos, ou ``None`` se não se cruzarem."""
    d1x, d1y = a_fim[0] - a_inicio[0], a_fim[1] - a_inicio[1]
    d2x, d2y = b_fim[0] - b_inicio[0], b_fim[1] - b_inicio[1]
    denominador = d1x * d2y - d1y * d2x
    if abs(denominador) <= EPSILON:
        return None
    diff_x = b_inicio[0] - a_inicio[0]
    diff_y = b_inicio[1] - a_inicio[1]
    t = (diff_x * d2y - diff_y * d2x) / denominador
    u = (diff_x * d1y - diff_y * d1x) / denominador
    if -EPSILON <= t <= 1.0 + EPSILON and -EPSILON <= u <= 1.0 + EPSILON:
        return (a_inicio[0] + t * d1x, a_inicio[1] + t * d1y)
    return None


def _recortar_por_caixa(pontos: Poligono, caixa: Caixa) -> Poligono:
    """Recorta o polígono pelos quatro limites da caixa (Sutherland-Hodgman)."""
    recortado = tuple(pontos)
    limites = (
        (lambda p: p[0] >= caixa.x1, lambda a, b: _cruzar_x(a, b, caixa.x1)),
        (lambda p: p[0] <= caixa.x2, lambda a, b: _cruzar_x(a, b, caixa.x2)),
        (lambda p: p[1] >= caixa.y1, lambda a, b: _cruzar_y(a, b, caixa.y1)),
        (lambda p: p[1] <= caixa.y2, lambda a, b: _cruzar_y(a, b, caixa.y2)),
    )
    for dentro, cruzamento in limites:
        if not recortado:
            return ()
        saida: list[Ponto] = []
        anterior = recortado[-1]
        anterior_dentro = dentro(anterior)
        for atual in recortado:
            atual_dentro = dentro(atual)
            if atual_dentro != anterior_dentro:
                saida.append(cruzamento(anterior, atual))
            if atual_dentro:
                saida.append(atual)
            anterior, anterior_dentro = atual, atual_dentro
        recortado = tuple(saida)
    return recortado


def _cruzar_x(inicio: Ponto, fim: Ponto, x: float) -> Ponto:
    fracao = (x - inicio[0]) / (fim[0] - inicio[0])
    return (x, inicio[1] + fracao * (fim[1] - inicio[1]))


def _cruzar_y(inicio: Ponto, fim: Ponto, y: float) -> Ponto:
    fracao = (y - inicio[1]) / (fim[1] - inicio[1])
    return (inicio[0] + fracao * (fim[0] - inicio[0]), y)


def _recortar_por_aresta(pontos: Poligono, inicio: Ponto, fim: Ponto) -> Poligono:
    """Passa uma aresta do recorte sobre o polígono, mantendo o lado de ``inicio``."""
    if not pontos:
        return ()

    def dentro(p: Ponto) -> bool:
        return _classificar(p, inicio, fim) <= 0

    saida: list[Ponto] = []
    total = len(pontos)
    for indice in range(total):
        atual = pontos[indice]
        seguinte = pontos[(indice + 1) % total]
        atual_dentro = dentro(atual)
        seguinte_dentro = dentro(seguinte)

        if atual_dentro:
            saida.append(atual)
            if not seguinte_dentro:
                cruzamento = _interseccao_arestas(atual, seguinte, inicio, fim)
                if cruzamento is not None:
                    saida.append(cruzamento)
        elif seguinte_dentro:
            cruzamento = _interseccao_arestas(atual, seguinte, inicio, fim)
            if cruzamento is not None:
                saida.append(cruzamento)
    return tuple(saida)


__all__ = [
    "EPSILON",
    "PoligonoInvalido",
    "area_interseccao_caixa_poligono",
    "area_poligono",
    "fracao_da_caixa_dentro",
    "iou",
    "ponto_esta_dentro",
    "validar_poligono",
]
