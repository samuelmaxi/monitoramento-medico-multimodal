"""Converte a resposta da API de transcrição rápida no JSON salvo pelo projeto (US11).

Os nomes de campo do JSON do projeto são em português. `confianca` existe por
frase: a API não devolve confiança por palavra, então `palavras` traz só
texto, offset e duração.
"""

from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"


def formatar_transcricao(
    resposta: dict,
    *,
    nome_arquivo: str,
    sha256: str,
    tamanho_bytes: int,
    max_speakers: int,
    frases_medicas: bool,
    medico_falante: int | None = None,
) -> dict:
    """Monta o documento salvo em JSON a partir do retorno bruto da API.

    `medico_falante` é o número do falante (diarização) que é o médico. Quando
    informado, os demais falantes viram "paciente". Sem ele, `papel` fica nulo,
    porque a diarização separa falantes mas não diz quem é quem.
    """
    falas = [_formatar_fala(frase, medico_falante) for frase in resposta.get("phrases", [])]

    return {
        "schema_version": SCHEMA_VERSION,
        "arquivo": {"nome": nome_arquivo, "sha256": sha256, "bytes": tamanho_bytes},
        "idioma": "pt-BR",
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
