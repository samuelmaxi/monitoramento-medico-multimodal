"""Montagem do relatório da US09: um dicionário serializável em JSON.

Reaproveita a análise da US08 (:class:`video.desvios.run.Execucao`), sem refazê-la, e
acrescenta o que o DoD pede e a US08 não tinha: identificação do vídeo sem dado pessoal,
confiança de cada desvio, % do tempo em desvio, versões dos modelos e limiares usados e o
resultado do envio dos desvios graves ao motor de alertas (US19).

Um vídeo sem desvios gera o mesmo relatório, com ``status = "sem_desvios_detectados"``:
nunca um documento vazio nem um erro.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import cv2

from video.desvios import MODEL_VERSION as VERSAO_REGRAS
from video.desvios.config import ConfigDesvios, Perfil
from video.desvios.modelos import ResultadoAnalise
from video.desvios.run import Execucao
from video.pose.sequence import PoseSequence

from . import MODEL_VERSION, SCHEMA_VERSION, confianca, privacidade, tempo
from .config import ConfigRelatorio

SEVERIDADES = ("alta", "media", "baixa")

ROTULOS_TIPO = {
    "postura_fora_da_faixa": "Postura fora da faixa",
    "assimetria_movimento": "Assimetria de movimento",
    "mudanca_brusca_ou_queda": "Mudança brusca ou queda",
    "presenca_indevida_area_critica": "Presença indevida em área crítica",
}

AVISO_LEGAL = (
    "Relatório gerado automaticamente pela análise de vídeo (OpenPose e YOLOv8). É um indicativo "
    "para avaliação da equipe, não um diagnóstico, e não substitui o julgamento profissional. "
    "Os limiares são provisórios e não foram validados clinicamente."
)

# Metadados da pose que identificam a versão do modelo e os parâmetros da extração.
_CAMPOS_OPENPOSE = {
    "model": "modelo",
    "model_files": "arquivos",
    "input_height": "entrada_altura_px",
    "backend": "backend",
    "conf_threshold": "limiar_confianca_keypoint",
    "min_valid_keypoints": "min_keypoints_validos",
    "module_version": "versao_modulo_pose",
    "opencv_version": "opencv",
}


@dataclass(frozen=True)
class Publicacao:
    """O que aconteceu com os eventos do vídeo no barramento (e, por ele, no motor de alertas)."""

    solicitada: bool = False
    publicados: int = 0
    falhas: tuple[str, ...] = ()


def rotulo_do_tipo(tipo: str) -> str:
    return ROTULOS_TIPO.get(tipo, tipo.replace("_", " ").capitalize())


def _plano(valor: Any) -> Any:
    """Dataclasses, enums, conjuntos e caminhos como tipos de JSON."""
    if is_dataclass(valor) and not isinstance(valor, type):
        return {campo.name: _plano(getattr(valor, campo.name)) for campo in fields(valor)}
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, Mapping):
        return {str(chave): _plano(item) for chave, item in valor.items()}
    if isinstance(valor, (set, frozenset)):
        return sorted(_plano(item) for item in valor)
    if isinstance(valor, (list, tuple)):
        return [_plano(item) for item in valor]
    if isinstance(valor, Path):
        return valor.as_posix()
    return valor


def _relativo(caminho: Path | None, raiz: Path) -> str | None:
    """Caminho relativo à raiz; fora dela, só o nome. Nunca um caminho absoluto."""
    if caminho is None:
        return None
    caminho = Path(caminho)
    try:
        return caminho.resolve().relative_to(raiz.resolve()).as_posix()
    except ValueError:
        return caminho.name


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def _info_video(video: Path | None, pose: PoseSequence | None) -> dict[str, Any]:
    """FPS, resolução e duração do vídeo; da pose quando há, do arquivo quando está acessível."""
    meta = pose.metadata if pose is not None else {}
    fps = meta.get("source_fps")
    largura, altura = meta.get("frame_width"), meta.get("frame_height")
    quadros = meta.get("n_source_frames")
    sha = None
    if video is not None and video.is_file():
        captura = cv2.VideoCapture(str(video))
        try:
            fps_arquivo = captura.get(cv2.CAP_PROP_FPS)
            quadros_arquivo = captura.get(cv2.CAP_PROP_FRAME_COUNT)
            largura_arquivo = captura.get(cv2.CAP_PROP_FRAME_WIDTH)
            altura_arquivo = captura.get(cv2.CAP_PROP_FRAME_HEIGHT)
        finally:
            captura.release()
        fps = fps_arquivo if fps_arquivo > 0 else fps
        quadros = int(quadros_arquivo) if quadros_arquivo > 0 else quadros
        largura = int(largura_arquivo) if largura_arquivo > 0 else largura
        altura = int(altura_arquivo) if altura_arquivo > 0 else altura
        sha = _sha256(video)
    duracao = round(quadros / fps, 3) if fps and quadros else None
    return {
        "fps": round(float(fps), 3) if fps else None,
        "resolucao": f"{largura}x{altura}" if largura and altura else None,
        "quadros_totais": int(quadros) if quadros else None,
        "duracao_s": duracao,
        "sha256": sha,
    }


def _item_desvio(
    numero: int,
    desvio,
    evento,
    pose: PoseSequence | None,
    cfg: ConfigRelatorio,
) -> dict[str, Any]:
    conf = confianca.avaliar(desvio, pose, cfg.confianca)
    quadros = [a for a in evento.evidence.artifacts if a.kind == "frame"]
    uri = quadros[0].uri if quadros else None
    dados = evento.para_dict()
    janela = dados.get("window") or {}
    return {
        "numero": numero,
        "event_id": str(evento.event_id),
        "tipo": desvio.event_type,
        "tipo_rotulo": rotulo_do_tipo(desvio.event_type),
        "regra": desvio.regra,
        "severidade": desvio.severidade.value,
        "inicio_s": round(desvio.inicio_s, 3),
        "fim_s": round(desvio.fim_s, 3),
        "duracao_s": round(desvio.duracao_s, 3),
        "inicio_utc": janela.get("start") or dados["timestamp"],
        "fim_utc": janela.get("end"),
        "score": evento.score,
        "confianca": conf.valor,
        "confianca_origem": conf.origem,
        "cobertura_trecho_pct": conf.cobertura_pct,
        "baixa_confianca": conf.baixa,
        "medida": desvio.medida,
        "valor": desvio.valor,
        "limiar": desvio.limiar,
        "unidade": desvio.unidade,
        "janela_minima_s": desvio.janela_s,
        "resumo": desvio.resumo,
        "evidencia": {
            "disponivel": uri is not None,
            "frame_index": desvio.frame_idx,
            "tempo_s": round(desvio.pico_s, 3),
            "uri": uri,
        },
        "eventos_relacionados": list(desvio.relacionados),
    }


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _resumo(
    itens: list[dict[str, Any]], duracao: float | None, resultado: ResultadoAnalise
) -> dict[str, Any]:
    unidos = tempo.unir([(i["inicio_s"], i["fim_s"]) for i in itens], limite=duracao)
    em_desvio = tempo.total(unidos)
    por_tipo: dict[str, dict[str, Any]] = {}
    for tipo in sorted({i["tipo"] for i in itens}):
        do_tipo = [i for i in itens if i["tipo"] == tipo]
        t = tempo.total(
            tempo.unir([(i["inicio_s"], i["fim_s"]) for i in do_tipo], limite=duracao)
        )
        por_tipo[tipo] = {
            "rotulo": rotulo_do_tipo(tipo),
            "total": len(do_tipo),
            "tempo_em_desvio_s": round(t, 3),
            "pct_do_tempo": tempo.percentual(t, duracao),
        }
    if itens:
        status = "desvios_detectados"
        mensagem = _plural(len(itens), "desvio detectado", "desvios detectados") + "."
    else:
        status = "sem_desvios_detectados"
        mensagem = "Sem desvios detectados."
        if not resultado.confiavel:
            mensagem = (
                "Sem desvios detectados, mas a análise é pouco confiável: a ausência de "
                "desvios não é conclusiva."
            )
    return {
        "status": status,
        "mensagem": mensagem,
        "total_desvios": len(itens),
        "por_tipo": por_tipo,
        "por_severidade": {s: sum(1 for i in itens if i["severidade"] == s) for s in SEVERIDADES},
        "duracao_video_s": duracao,
        "tempo_em_desvio_s": round(em_desvio, 3),
        "pct_tempo_em_desvio": tempo.percentual(em_desvio, duracao),
    }


def _modelos(execucao: Execucao) -> dict[str, Any]:
    pose = execucao.pose
    if pose is None:
        openpose: dict[str, Any] = {"utilizado": False, "motivo": "pose da US06 não informada"}
    else:
        openpose = {"utilizado": True}
        openpose.update(
            {novo: pose.metadata[velho] for velho, novo in _CAMPOS_OPENPOSE.items()
             if velho in pose.metadata}
        )
    areas = execucao.eventos_areas
    if not areas:
        yolo: dict[str, Any] = {
            "utilizado": False,
            "motivo": "eventos de área crítica da US07 não informados",
        }
    else:
        descricoes = {e.evidence.models["yolov8"] for e in areas if "yolov8" in e.evidence.models}
        yolo = {
            "utilizado": True,
            "descricao": ", ".join(sorted(descricoes)) or None,
            "versao_modulo": ", ".join(sorted({e.model_version for e in areas})),
        }
    return {
        "openpose": openpose,
        "yolov8": yolo,
        "regras_de_desvio": VERSAO_REGRAS,
        "relatorio": MODEL_VERSION,
    }


def _limiares(execucao: Execucao, cfg: ConfigDesvios, perfil: Perfil, raiz: Path) -> dict[str, Any]:
    areas = execucao.eventos_areas
    return {
        "versao": cfg.versao,
        "arquivo": _relativo(cfg.fonte, raiz),
        "geral": _plano(cfg.geral),
        "perfil": _plano(perfil),
        "us07": dict(areas[0].evidence.thresholds) if areas else None,
    }


def _alertas(itens: list[dict[str, Any]], publicacao: Publicacao) -> dict[str, Any]:
    graves = [i for i in itens if i["severidade"] == "alta"]
    entregues = publicacao.solicitada and not publicacao.falhas
    if not publicacao.solicitada:
        observacao = "Publicação não solicitada nesta execução: nenhum desvio foi ao motor."
    elif publicacao.falhas:
        observacao = "A publicação falhou; os desvios graves podem não ter chegado ao motor."
    else:
        observacao = (
            "Eventos publicados no barramento; o motor de alertas (US19) aplica prioridade, "
            "deduplicação e canais, e registra cada alerta no painel."
        )
    return {
        "motor": "US19",
        "publicacao_solicitada": publicacao.solicitada,
        "eventos_publicados": publicacao.publicados,
        "desvios_severidade_alta": len(graves),
        "enviados_ao_motor": [
            {
                "event_id": i["event_id"],
                "tipo": i["tipo"],
                "tipo_rotulo": i["tipo_rotulo"],
                "inicio_s": i["inicio_s"],
            }
            for i in graves
        ]
        if entregues
        else [],
        "falhas": list(publicacao.falhas),
        "observacao": observacao,
    }


def _avisos(
    resultado: ResultadoAnalise, itens: list[dict[str, Any]], duracao: float | None
) -> list[str]:
    avisos = list(resultado.avisos)
    baixas = sum(1 for i in itens if i["baixa_confianca"])
    if baixas:
        avisos.append(
            f"{_plural(baixas, 'desvio', 'desvios')} com baixa confiança: conferir no quadro de "
            "evidência antes de agir."
        )
    sem_quadro = sum(1 for i in itens if not i["evidencia"]["disponivel"])
    if sem_quadro:
        avisos.append(
            f"{_plural(sem_quadro, 'desvio', 'desvios')} sem quadro de evidência (vídeo "
            "indisponível ou quadro ilegível)."
        )
    if duracao is None:
        avisos.append(
            "Duração do vídeo desconhecida: o % do tempo em desvio não pôde ser calculado."
        )
    return avisos


def construir(
    execucao: Execucao,
    cfg: ConfigRelatorio,
    *,
    gerado_em: datetime,
    raiz: Path | None = None,
    publicacao: Publicacao | None = None,
) -> dict[str, Any]:
    """O relatório do vídeo analisado em ``execucao``, pronto para virar JSON e HTML."""
    resultado, perfil, cfg_desvios = execucao.resultado, execucao.perfil, execucao.config
    if resultado is None or perfil is None or cfg_desvios is None:
        raise ValueError("a execução da US08 não traz a análise: use video.desvios.run.executar")
    raiz = (raiz or Path.cwd()).resolve()
    publicacao = publicacao or Publicacao()

    info = _info_video(execucao.video, execucao.pose)
    duracao = resultado.duracao_s if resultado.duracao_s else info["duracao_s"]
    duracao = round(duracao, 3) if duracao else None

    # A US08 monta um evento por desvio, na mesma ordem, e só depois um eventual sem_achados.
    eventos_dos_desvios = execucao.eventos[: len(resultado.desvios)]
    itens = [
        _item_desvio(i, desvio, evento, execucao.pose, cfg)
        for i, (desvio, evento) in enumerate(
            zip(resultado.desvios, eventos_dos_desvios, strict=True), start=1
        )
    ]
    relatorio = {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": gerado_em.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model_version": MODEL_VERSION,
        "versao_config_relatorio": cfg.versao,
        "video": {
            "id": execucao.source_id,
            "tipo_procedimento": perfil.tipo_video,
            "perfil": perfil.id,
            "padrao_esperado": perfil.descricao,
            "duracao_s": duracao,
            "quadros_analisados": resultado.quadros,
            "quadros_totais": info["quadros_totais"],
            "fps": info["fps"],
            "resolucao": info["resolucao"],
            "sha256": info["sha256"],
        },
        "analise": {
            "confiavel": resultado.confiavel,
            "cobertura_pose_pct": resultado.cobertura_pct,
            "cobertura_minima_pct": cfg_desvios.geral.cobertura_minima_pct,
        },
        "resumo": _resumo(itens, duracao, resultado),
        "desvios": itens,
        "repeticoes": resultado.repeticoes.para_dict() if resultado.repeticoes else None,
        "avisos": _avisos(resultado, itens, duracao),
        "modelos": _modelos(execucao),
        "limiares": _limiares(execucao, cfg_desvios, perfil, raiz),
        "alertas": _alertas(itens, publicacao),
        "arquivos": {
            "eventos": _relativo(execucao.caminho_eventos, raiz),
            "analise_us08": _relativo(execucao.caminho_relatorio, raiz),
        },
        "aviso_legal": AVISO_LEGAL,
    }
    limpo, redacoes = privacidade.limpar(relatorio)
    limpo["privacidade"] = {
        "redacoes": redacoes,
        "identificacao": "id do vídeo e SHA-256; sem nome de arquivo, caminho, paciente ou leito",
    }
    return limpo


__all__ = ["AVISO_LEGAL", "ROTULOS_TIPO", "Publicacao", "construir", "rotulo_do_tipo"]
