"""US09: relatório automático de desvios, gerado ao final da análise de cada vídeo.

    python -m video.relatorio.run --perfil internacao_leito \\
        --pose outputs/pose/<video>.json --eventos-areas saida/video/eventos_<video>.jsonl \\
        --patient-id pt_<24 hex> --video <video>

Em um comando, sem edição manual, fecha a análise de um vídeo:

1. aplica o padrão esperado do perfil à pose (US06) e aos eventos de área crítica (US07): é a
   US08 (``video.desvios.run.executar``);
2. publica os eventos no barramento: os desvios de severidade alta chegam ao motor de alertas
   (US19), que aplica prioridade, deduplicação e canais;
3. grava ``<fonte>_relatorio_desvios.json`` e ``<fonte>_relatorio_desvios.html`` em
   ``--saida`` (padrão ``saida/video/desvios``), ao lado dos arquivos da US08. Reexecutar
   sobrescreve.

Vídeo sem desvios gera o relatório "sem desvios detectados", não um erro. A publicação é a
última etapa que pode falhar e **nunca impede o relatório**: se ela falhar, a falha entra no
relatório e a saída é 2.

Saída: 0 = tudo certo; 1 = entrada ou configuração inválida; 2 = o relatório foi gerado, mas a
publicação dos eventos falhou. O motor de alertas precisa das variáveis do ``.env``
(``ALERTA_*``) para enviar por e-mail ou webhook; sem canal configurado, o alerta fica no painel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from contratos import BarramentoLocal, Emissor, EventoAchado, agora_utc
from video.desvios.config import CAMINHO_PADRAO as CONFIG_DESVIOS_PADRAO
from video.desvios.run import Execucao, executar

from . import construcao, html
from .config import CAMINHO_PADRAO, ConfigRelatorio, ErroDeConfiguracao, carregar_config
from .construcao import Publicacao

log = logging.getLogger("video.relatorio.run")

SUFIXO = "_relatorio_desvios"
FORMATOS = ("json", "html")


@dataclass(frozen=True)
class ResultadoRelatorio:
    execucao: Execucao
    relatorio: dict[str, Any]
    caminho_json: Path | None
    caminho_html: Path | None
    publicacao: Publicacao


def _gravar(caminho: Path, texto: str) -> None:
    """Grava inteiro ou não grava: nunca deixa um relatório pela metade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".part")
    temporario.write_text(texto, encoding="utf-8")
    temporario.replace(caminho)


def publicar(eventos: Sequence[EventoAchado], emissor: Emissor | None) -> Publicacao:
    """Publica os eventos do vídeo e resume o resultado, sem nunca levantar exceção.

    O relatório precisa sair mesmo se o barramento falhar (disco cheio, assinante com erro):
    a falha é registrada no log e volta em ``Publicacao.falhas``, para entrar no relatório.
    """
    if emissor is None:
        return Publicacao()
    ids = {str(e.event_id) for e in eventos}
    try:
        emissor.emitir_varios(eventos)
    except Exception as erro:
        log.exception("a publicação dos eventos no barramento falhou")
        return Publicacao(True, 0, (f"barramento: {type(erro).__name__}: {erro}",))
    falhas = tuple(
        f"{f.assinante}: {f.erro}" for f in getattr(emissor, "falhas", ()) if f.event_id in ids
    )
    return Publicacao(True, len(eventos), falhas)


def gerar(
    *,
    perfil_id: str,
    config: Path = CONFIG_DESVIOS_PADRAO,
    config_relatorio: Path | ConfigRelatorio = CAMINHO_PADRAO,
    pose_path: Path | None = None,
    eventos_areas_path: Path | None = None,
    video: Path | None = None,
    patient_id: str | None = None,
    inicio_utc: str | None = None,
    source_id: str | None = None,
    bed_id: str | None = None,
    encounter_id: str | None = None,
    saida: Path = Path("saida/video/desvios"),
    pose_config: Path = Path("video/pose/config.yaml"),
    evidencias: bool = True,
    emissor: Emissor | None = None,
    formatos: Sequence[str] = FORMATOS,
    relogio: Callable[[], datetime] = agora_utc,
    raiz: Path | None = None,
    falhas_previas: Sequence[str] = (),
) -> ResultadoRelatorio:
    """Analisa o vídeo (US08), publica os eventos e grava o relatório (US09).

    ``falhas_previas`` são falhas de etapas anteriores à publicação (por exemplo, o motor de
    alertas que não pôde ser construído): entram em ``alertas.falhas`` do relatório.

    Levanta ``ErroDeConfiguracao`` ou ``ValueError``/``OSError`` se a entrada for inválida
    (perfil que não existe, pose ilegível). Depois que a análise roda, nada impede o relatório.
    """
    desconhecidos = set(formatos) - set(FORMATOS)
    if desconhecidos:
        raise ValueError(f"formato(s) desconhecido(s): {sorted(desconhecidos)}; use {FORMATOS}")
    cfg_rel = (
        config_relatorio
        if isinstance(config_relatorio, ConfigRelatorio)
        else carregar_config(config_relatorio)
    )
    raiz = (raiz or Path.cwd()).resolve()

    execucao = executar(
        perfil_id=perfil_id,
        config=config,
        pose_path=pose_path,
        eventos_areas_path=eventos_areas_path,
        video=video,
        patient_id=patient_id,
        inicio_utc=inicio_utc,
        source_id=source_id,
        bed_id=bed_id,
        encounter_id=encounter_id,
        saida=saida,
        pose_config=pose_config,
        evidencias=evidencias,
    )
    publicacao = publicar(execucao.eventos, emissor)
    if falhas_previas:
        publicacao = replace(publicacao, falhas=(*falhas_previas, *publicacao.falhas))
    relatorio = construcao.construir(
        execucao, cfg_rel, gerado_em=relogio(), raiz=raiz, publicacao=publicacao
    )

    pasta = execucao.caminho_relatorio.parent
    nome = f"{execucao.source_id}{SUFIXO}"
    caminho_json = caminho_html = None
    if "json" in formatos:
        caminho_json = pasta / f"{nome}.json"
        _gravar(caminho_json, json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n")
    if "html" in formatos:
        imagens = html.embutir_imagens(relatorio, raiz, cfg_rel.evidencia)
        caminho_html = pasta / f"{nome}.html"
        _gravar(caminho_html, html.renderizar(relatorio, imagens))
    return ResultadoRelatorio(execucao, relatorio, caminho_json, caminho_html, publicacao)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--perfil", required=True, help="perfil de config/us08_padroes.yaml")
    parser.add_argument("--config", type=Path, default=CONFIG_DESVIOS_PADRAO)
    parser.add_argument("--config-relatorio", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--pose", type=Path, help="JSON de pose da US06 (video.pose.run)")
    parser.add_argument("--eventos-areas", type=Path, help="JSONL de eventos da US07")
    parser.add_argument(
        "--video",
        type=Path,
        help="vídeo original, para os quadros de evidência (padrão: o source_path da pose)",
    )
    parser.add_argument(
        "--patient-id",
        help="pseudônimo pt_<24 hex> ou ID de origem (pseudonimizado com PSEUDONYM_KEY)",
    )
    parser.add_argument("--inicio-utc", help="início da gravação, ISO-8601 em UTC")
    parser.add_argument("--source-id")
    parser.add_argument("--bed-id")
    parser.add_argument("--encounter-id")
    parser.add_argument("--saida", type=Path, default=Path("saida/video/desvios"))
    parser.add_argument("--pose-config", type=Path, default=Path("video/pose/config.yaml"))
    parser.add_argument("--sem-evidencia", action="store_true", help="não grava os quadros JPG")
    parser.add_argument(
        "--formato",
        choices=("json", "html", "ambos"),
        default="ambos",
        help="formato do relatório (padrão: ambos)",
    )
    parser.add_argument(
        "--sem-alertas",
        action="store_true",
        help="não liga o motor de alertas (os eventos ainda vão ao barramento)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.pose is None and args.eventos_areas is None:
        parser.error("informe --pose e/ou --eventos-areas")

    try:
        cfg_rel = carregar_config(args.config_relatorio)
    except ErroDeConfiguracao as erro:
        log.error("%s", erro)
        return 1

    barramento = BarramentoLocal.do_ambiente()
    servico = None
    falhas_previas: list[str] = []
    if not args.sem_alertas:
        try:
            from alertas import ConfigAlertas, construir

            servico = construir(ConfigAlertas.from_env())
            servico.assinar(barramento)
        except (OSError, ValueError) as erro:
            # Sem o motor (regras ausentes, configuração inválida) os desvios graves não
            # viram alerta: isso é grave e fica dito no log, no relatório e na saída 2, mas
            # não pode impedir a análise nem o relatório.
            log.error("motor de alertas indisponível: %s", erro)
            falhas_previas.append(f"motor de alertas indisponível: {type(erro).__name__}: {erro}")

    try:
        resultado = gerar(
            perfil_id=args.perfil,
            config=args.config,
            config_relatorio=cfg_rel,
            pose_path=args.pose,
            eventos_areas_path=args.eventos_areas,
            video=args.video,
            patient_id=args.patient_id,
            inicio_utc=args.inicio_utc,
            source_id=args.source_id,
            bed_id=args.bed_id,
            encounter_id=args.encounter_id,
            saida=args.saida,
            pose_config=args.pose_config,
            evidencias=not args.sem_evidencia,
            emissor=barramento,
            formatos=FORMATOS if args.formato == "ambos" else (args.formato,),
            falhas_previas=falhas_previas,
        )
    except (ValueError, OSError) as erro:  # ErroDeConfiguracao é ValueError
        log.error("%s", erro)
        return 1

    if servico is not None:
        asyncio.run(servico.processar_pendentes())

    resumo = resultado.relatorio["resumo"]
    pct = resumo["pct_tempo_em_desvio"]
    print(
        f"{resumo['mensagem']} | {resumo['total_desvios']} desvio(s) | "
        f"{'-' if pct is None else f'{pct}%'} do tempo em desvio | "
        f"{resumo['por_severidade']['alta']} de severidade alta"
    )
    for aviso in resultado.relatorio["avisos"]:
        print(f"  aviso: {aviso}")
    for falha in resultado.publicacao.falhas:
        print(f"  falha na publicação: {falha}")
    for rotulo, caminho in (("json", resultado.caminho_json), ("html", resultado.caminho_html)):
        if caminho is not None:
            print(f"{rotulo}: {caminho}")
    return 2 if resultado.publicacao.falhas else 0


if __name__ == "__main__":
    sys.exit(main())
