import json

import pytest

from app import transcription_service
from app.transcription_client import TranscricaoError
from app.transcription_format import formatar_transcricao
from app.transcription_service import carregar_frases_medicas, transcrever_e_salvar

RESPOSTA_API = {
    "durationMilliseconds": 4200,
    "combinedPhrases": [{"text": "Bom dia. Estou com falta de ar."}],
    "phrases": [
        {
            "speaker": 1,
            "text": "Bom dia.",
            "offsetMilliseconds": 0,
            "durationMilliseconds": 900,
            "confidence": 0.95,
            "locale": "pt-BR",
            "words": [{"text": "Bom", "offsetMilliseconds": 0, "durationMilliseconds": 300}],
        },
        {
            "speaker": 2,
            "text": "Estou com falta de ar.",
            "offsetMilliseconds": 1000,
            "durationMilliseconds": 3000,
            "confidence": 0.9,
            "locale": "pt-BR",
            "words": [],
        },
    ],
}


@pytest.fixture
def audio(tmp_path):
    caminho = tmp_path / "entrada" / "consulta_01.wav"
    caminho.parent.mkdir()
    caminho.write_bytes(b"RIFF-consulta")
    return caminho


class _ApiFalsa:
    def __init__(self, resposta=RESPOSTA_API, erro=None):
        self.resposta = resposta
        self.erro = erro
        self.chamadas: list[dict] = []

    def __call__(self, audio_path, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro is not None:
            raise self.erro
        return self.resposta


@pytest.fixture
def api_falsa(monkeypatch):
    api = _ApiFalsa()
    monkeypatch.setattr(transcription_service, "transcrever_arquivo", api)
    return api


def test_formatacao_traz_texto_falante_offset_duracao_e_confianca():
    documento = formatar_transcricao(
        RESPOSTA_API,
        nome_arquivo="consulta_01.wav",
        sha256="abc",
        tamanho_bytes=10,
        max_speakers=2,
        frases_medicas=False,
    )

    primeira = documento["falas"][0]
    assert primeira["texto"] == "Bom dia."
    assert primeira["falante"] == 1
    assert primeira["offset_ms"] == 0
    assert primeira["duracao_ms"] == 900
    assert primeira["confianca"] == 0.95
    assert primeira["palavras"] == [{"texto": "Bom", "offset_ms": 0, "duracao_ms": 300}]
    assert documento["texto_completo"] == "Bom dia. Estou com falta de ar."
    assert documento["idioma"] == "pt-BR"
    assert documento["duracao_ms"] == 4200


def test_papel_so_e_definido_quando_se_informa_o_medico():
    sem_medico = formatar_transcricao(
        RESPOSTA_API, nome_arquivo="a.wav", sha256="x", tamanho_bytes=1,
        max_speakers=2, frases_medicas=False,
    )
    com_medico = formatar_transcricao(
        RESPOSTA_API, nome_arquivo="a.wav", sha256="x", tamanho_bytes=1,
        max_speakers=2, frases_medicas=False, medico_falante=1,
    )

    assert [f["papel"] for f in sem_medico["falas"]] == [None, None]
    assert [f["papel"] for f in com_medico["falas"]] == ["medico", "paciente"]


def test_grava_json_e_reaproveita_sem_chamar_a_api_de_novo(audio, tmp_path, api_falsa):
    saida = tmp_path / "saida"

    primeira = transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k")
    segunda = transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k")

    assert len(api_falsa.chamadas) == 1
    assert segunda == primeira
    gravado = json.loads((saida / "consulta_01.json").read_text(encoding="utf-8"))
    assert gravado["arquivo"]["nome"] == "consulta_01.wav"
    assert len(gravado["arquivo"]["sha256"]) == 64
    assert audio.read_bytes() == b"RIFF-consulta"


def test_mudar_opcoes_invalida_o_cache(audio, tmp_path, api_falsa):
    saida = tmp_path / "saida"

    transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k", usar_frases=False)
    transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k", usar_frases=True)

    assert len(api_falsa.chamadas) == 2
    assert api_falsa.chamadas[1]["frases"] == carregar_frases_medicas()


def test_falha_da_api_preserva_audio_e_nao_grava_json(audio, tmp_path, monkeypatch):
    saida = tmp_path / "saida"
    monkeypatch.setattr(
        transcription_service,
        "transcrever_arquivo",
        _ApiFalsa(erro=TranscricaoError("timeout", None, 3)),
    )

    with pytest.raises(TranscricaoError):
        transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k")

    assert audio.read_bytes() == b"RIFF-consulta"
    assert not (saida / "consulta_01.json").exists()


def test_frases_medicas_ignora_linhas_vazias_e_comentarios():
    frases = carregar_frases_medicas()

    assert "dispneia" in frases
    assert all(frase and not frase.startswith("#") for frase in frases)


def test_mudar_o_locale_invalida_o_cache(audio, tmp_path, api_falsa):
    saida = tmp_path / "saida"

    transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k", locale="pt-BR")
    resultado = transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k", locale="en-IN")

    assert len(api_falsa.chamadas) == 2
    assert api_falsa.chamadas[1]["locale"] == "en-IN"
    assert resultado["idioma"] == "en-IN"
def test_metadados_da_consulta_vao_para_o_json(audio, tmp_path, api_falsa):
    documento = transcrever_e_salvar(
        audio, tmp_path / "saida", endpoint="https://x", chave="k",
        patient_id="10000032", recorded_at="2026-10-01T13:00:00+00:00",
    )

    assert documento["audio_id"] == "consulta_01"
    assert documento["patient_id"] == "10000032"
    assert documento["recorded_at"] == "2026-10-01T13:00:00Z"
    assert documento["schema_version"] == "1.1"


def test_nome_de_arquivo_que_nao_serve_de_id_deixa_audio_id_nulo(tmp_path, api_falsa):
    audio = tmp_path / "consulta com espaço.wav"
    audio.write_bytes(b"RIFF")

    documento = transcrever_e_salvar(audio, tmp_path / "saida", endpoint="https://x", chave="k")

    assert documento["audio_id"] is None


@pytest.mark.parametrize(
    ("opcoes", "mensagem"),
    [
        ({"recorded_at": "2026-10-01T10:00:00-03:00"}, "UTC"),
        ({"recorded_at": "ontem"}, "inválido"),
        ({"audio_id": "consulta 01"}, "audio_id"),
    ],
)
def test_metadados_invalidos_falham_antes_da_api(audio, tmp_path, api_falsa, opcoes, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        transcrever_e_salvar(audio, tmp_path / "saida", endpoint="https://x", chave="k", **opcoes)

    assert api_falsa.chamadas == []


def test_reaproveitamento_atualiza_metadados_sem_chamar_a_api(audio, tmp_path, api_falsa):
    saida = tmp_path / "saida"
    transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k", patient_id="p-1")

    segunda = transcrever_e_salvar(
        audio, saida, endpoint="https://x", chave="k", recorded_at="2026-10-01T13:00:00Z"
    )

    assert len(api_falsa.chamadas) == 1
    assert segunda["patient_id"] == "p-1"
    assert segunda["recorded_at"] == "2026-10-01T13:00:00Z"
    gravado = json.loads((saida / "consulta_01.json").read_text(encoding="utf-8"))
    assert gravado == segunda


def test_json_anterior_ao_schema_1_1_ganha_os_campos_de_metadados(audio, tmp_path, api_falsa):
    saida = tmp_path / "saida"
    primeira = transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k")
    antigo = {k: v for k, v in primeira.items() if k not in ("audio_id", "patient_id", "recorded_at")}
    (saida / "consulta_01.json").write_text(json.dumps({**antigo, "schema_version": "1.0"}), encoding="utf-8")

    reaproveitado = transcrever_e_salvar(audio, saida, endpoint="https://x", chave="k")

    assert len(api_falsa.chamadas) == 1
    assert reaproveitado["schema_version"] == "1.1"
    assert reaproveitado["audio_id"] == "consulta_01"
    assert reaproveitado["patient_id"] is None
