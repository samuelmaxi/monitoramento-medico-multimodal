"""Converte a resposta da API de transcrição rápida no JSON salvo pelo projeto (US11).

Os nomes de campo do JSON do projeto são em português. `confianca` existe por
frase: a API não devolve confiança por palavra, então `palavras` traz só
texto, offset e duração.

`audio_id`, `patient_id` e `recorded_at` usam os nomes do contrato de eventos
(US04): são o que a análise de termos críticos e sentimento (US13) precisa para
montar os eventos direto a partir deste JSON.
"""

import re
from datetime import datetime, timedelta, timezone

from contratos.evento import PADRAO_ID_CURTO

SCHEMA_VERSION = "1.1"


def validar_metadados(audio_id: str | None, recorded_at: str | None) -> str | None:
    """Valida `audio_id` e devolve `recorded_at` normalizado (ISO-8601 UTC, sufixo Z).

    Levanta `ValueError` com a mensagem para o usuário.
    """
    if audio_id is not None and not re.fullmatch(PADRAO_ID_CURTO, audio_id):
        raise ValueError(
            f"'audio_id' deve casar {PADRAO_ID_CURTO} (sem espaços nem dados pessoais): {audio_id!r}"
        )
    if recorded_at is None:
        return None
    try:
        instante = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"'recorded_at' inválido: {recorded_at!r} (use ISO-8601 UTC)") from None
    if instante.tzinfo is None or instante.utcoffset() != timedelta(0):
        raise ValueError("'recorded_at' precisa estar em UTC (sufixo Z), ex.: 2026-10-01T13:00:00Z")
    return instante.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def formatar_transcricao(
    resposta: dict,
    *,
    nome_arquivo: str,
    sha256: str,
    tamanho_bytes: int,
    max_speakers: int,
    frases_medicas: bool,
    medico_falante: int | None = None,
    locale: str = "pt-BR",
    audio_id: str | None = None,
    patient_id: str | None = None,
    recorded_at: str | None = None,
) -> dict:
    """Monta o documento salvo em JSON a partir do retorno bruto da API.

    `medico_falante` é o número do falante (diarização) que é o médico. Quando
    informado, os demais falantes viram "paciente". Sem ele, `papel` fica nulo,
    porque a diarização separa falantes mas não diz quem é quem.

    `recorded_at` é o início da gravação; somado ao `offset_ms` de cada fala dá
    o instante UTC dela.
    """
    falas = [_formatar_fala(frase, medico_falante) for frase in resposta.get("phrases", [])]

    return {
        "schema_version": SCHEMA_VERSION,
        "audio_id": audio_id,
        "patient_id": patient_id,
        "recorded_at": recorded_at,
        "arquivo": {"nome": nome_arquivo, "sha256": sha256, "bytes": tamanho_bytes},
        "idioma": locale,
        "duracao_ms": resposta.get("durationMilliseconds"),
        "diarizacao": {"habilitada": True, "max_falantes": max_speakers},
        "frases_medicas": frases_medicas,
        "medico_falante": medico_falante,
        "criado_em": datetime.now(timezone.utc).isoformat(),
        "texto_completo": " ".join(fala["texto"] for fala in falas),
        "falas": falas,
    }


def _formatar_fala(frase: dict, medico_falante: int | None) -> dict:
    falante = frase.get("speaker")
    return {
        "texto": frase.get("text", ""),
        "falante": falante,
        "papel": _papel(falante, medico_falante),
        "offset_ms": frase.get("offsetMilliseconds"),
        "duracao_ms": frase.get("durationMilliseconds"),
        "confianca": frase.get("confidence"),
        "palavras": [
            {
                "texto": palavra.get("text", ""),
                "offset_ms": palavra.get("offsetMilliseconds"),
                "duracao_ms": palavra.get("durationMilliseconds"),
            }
            for palavra in frase.get("words", [])
        ],
    }


def _papel(falante: int | None, medico_falante: int | None) -> str | None:
    if falante is None or medico_falante is None:
        return None
    return "medico" if falante == medico_falante else "paciente"
