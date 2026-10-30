"""Barramento local compartilhado pelo processo. Os detectores publicam e o motor de alertas assina.

Uma instância por processo: o lifespan do FastAPI assina o motor nela, e os
roteadores publicam nela. Se cada requisição criasse a sua, o motor não receberia nada.
"""

from functools import lru_cache

from contratos import BarramentoLocal


@lru_cache
def get_barramento() -> BarramentoLocal:
    return BarramentoLocal.do_ambiente()
