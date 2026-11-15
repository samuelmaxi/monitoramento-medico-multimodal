"""Avaliação da US14 em casos injetados: recall por classe e falso positivo em voz normal.

Cada caso passa pelo pipeline inteiro (ingestão → segmentos → biomarcadores do Praat → regras),
com a transcrição sintética no formato da US11 e os eventos respiratórios injetados como scores
do YAMNet (``audio.vocal.injecao``).

**O que isto mede e o que não mede.** Mede se as regras e a implementação respondem às alterações
que elas procuram, sob parâmetros controlados. Não valida os limiares clinicamente (os
experimentais foram escolhidos pelos mesmos autores dos casos) e não mede o YAMNet. Voz de
pacientes reais, rotulada por fonoaudiólogo, é o que falta para isso.

Uma classe pode disparar junto com outra (fala lenta e pausada é comum a fadiga e dificuldade
respiratória); por isso o relatório traz, além do recall, quais classes dispararam em cada rótulo.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import soundfile as sf

from audio.ingestao.config import ConfigIngestao

from .config import ConfigVocal
from .injecao import (
    NOMES_YAMNET,
    ROTULOS,
    TAXA_HZ,
    CasoInjetado,
    transcricao_sintetica,
)
from .respiratorio import QUADRO_S, DetectorRespiratorio
from .run import processar

CLASSE_NORMAL = "normal"
INSTANTE = datetime(2026, 10, 1, 13, 0, 0, tzinfo=UTC)


@dataclass
class Contagem:
    casos: int = 0
    acertos: int = 0
    classe_principal_certa: int = 0
    disparos: Counter = field(default_factory=Counter)
    perdidos: list[str] = field(default_factory=list)

    def para_dict(self) -> dict[str, Any]:
        return {
            "casos": self.casos,
            "acertos": self.acertos,
            "recall": round(self.acertos / self.casos, 4) if self.casos else None,
            "classe_principal_certa": self.classe_principal_certa,
            "disparos": dict(sorted(self.disparos.items())),
            "perdidos": self.perdidos[:10],
        }


def _detector(caso: CasoInjetado, cfg: ConfigVocal) -> DetectorRespiratorio:
    from .injecao import sessao_sintetica

    quadros = int(caso.sinal.size / TAXA_HZ / QUADRO_S) + 2
    return DetectorRespiratorio(
        sessao_sintetica(caso.respiratorio, quadros), NOMES_YAMNET, cfg.respiratorio)


def avaliar(
    casos: list[CasoInjetado],
    cfg_ingestao: ConfigIngestao,
    cfg: ConfigVocal,
    pasta: Path | None = None,
) -> dict[str, Contagem]:
    """Roda cada caso e conta, por rótulo, quantos foram reconhecidos.

    Para um rótulo de classe, "acerto" é a classe ter disparado (outras podem disparar junto);
    para ``normal``, é nenhuma classe ter disparado.
    """
    contagens = {rotulo: Contagem() for rotulo in ROTULOS}
    with tempfile.TemporaryDirectory(prefix="avaliacao-us14-") as temporario:
        raiz = pasta or Path(temporario)
        for caso in casos:
            origem = raiz / "entrada" / f"{caso.id}.wav"
            origem.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(origem), caso.sinal, TAXA_HZ, subtype="PCM_16")
            execucao = processar(
                origem, patient_id="avaliacao-us14", recorded_at=INSTANTE, saida=raiz / "saida",
                cfg_ingestao=cfg_ingestao, cfg=cfg, audio_id=caso.id,
                transcricao=transcricao_sintetica(caso.falas, audio_id=caso.id),
                detector=_detector(caso, cfg), sintetico=True)
            resultado = execucao.resultado
            disparadas = {c.event_type for c in resultado.classificacao.classes}
            contagem = contagens[caso.rotulo]
            contagem.casos += 1
            contagem.disparos.update(disparadas or {CLASSE_NORMAL})
            e_normal = caso.rotulo == CLASSE_NORMAL
            acertou = not disparadas if e_normal else caso.rotulo in disparadas
            contagem.acertos += acertou
            contagem.classe_principal_certa += resultado.classe == caso.rotulo
            if not acertou:
                contagem.perdidos.append(caso.id)
    return contagens


def somar(destino: dict[str, Contagem], origem: dict[str, Contagem]) -> None:
    for rotulo, c in origem.items():
        d = destino.setdefault(rotulo, Contagem())
        d.casos += c.casos
        d.acertos += c.acertos
        d.classe_principal_certa += c.classe_principal_certa
        d.disparos.update(c.disparos)
        d.perdidos.extend(c.perdidos)
