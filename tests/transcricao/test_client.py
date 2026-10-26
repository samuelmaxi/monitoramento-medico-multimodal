import httpx
import pytest

from app.transcription_client import API_VERSION, TranscricaoError, transcrever_arquivo

ENDPOINT = "https://eastus.api.cognitive.microsoft.com"
RESPOSTA_OK = {"durationMilliseconds": 1000, "phrases": []}


@pytest.fixture
def audio(tmp_path):
    caminho = tmp_path / "consulta.wav"
    caminho.write_bytes(b"RIFF-audio-de-teste")
    return caminho


class _Transporte:
    """Devolve as respostas da lista em ordem e guarda as requisições recebidas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.requisicoes: list[httpx.Request] = []
        self.transporte = httpx.MockTransport(self._responder)

    def _responder(self, request):
        self.requisicoes.append(request)
        proxima = self.respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima


def _sem_espera():
    esperas: list[float] = []
    return esperas, esperas.append


def test_envia_definicao_pt_br_com_diarizacao(audio):
    transporte = _Transporte([httpx.Response(200, json=RESPOSTA_OK)])

    resultado = transcrever_arquivo(
        audio, endpoint=ENDPOINT, chave="chave-teste", transporte=transporte.transporte
    )

    assert resultado == RESPOSTA_OK
    requisicao = transporte.requisicoes[0]
    assert requisicao.url.path == "/speechtotext/transcriptions:transcribe"
    assert requisicao.url.params["api-version"] == API_VERSION
    assert requisicao.headers["Ocp-Apim-Subscription-Key"] == "chave-teste"
    corpo = requisicao.read()
    assert b'"locales": ["pt-BR"]' in corpo
    assert b'"maxSpeakers": 2' in corpo
    assert b"phraseList" not in corpo


def test_envia_phrase_list_quando_informada(audio):
    transporte = _Transporte([httpx.Response(200, json=RESPOSTA_OK)])

    transcrever_arquivo(
        audio,
        endpoint=ENDPOINT,
        chave="k",
        frases=["dispneia", "dipirona"],
        transporte=transporte.transporte,
    )

    corpo = transporte.requisicoes[0].read()
    assert b'"phraseList"' in corpo
    assert "dispneia".encode() in corpo


def test_retenta_429_respeitando_retry_after(audio):
    esperas, dormir = _sem_espera()
    transporte = _Transporte(
        [
            httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {"message": "lento"}}),
            httpx.Response(503),
            httpx.Response(200, json=RESPOSTA_OK),
        ]
    )

    resultado = transcrever_arquivo(
        audio, endpoint=ENDPOINT, chave="k", transporte=transporte.transporte, dormir=dormir
    )

    assert resultado == RESPOSTA_OK
    assert len(transporte.requisicoes) == 3
    assert esperas == [3.0, 4.0]  # Retry-After; depois backoff exponencial (2 * 2)


def test_retenta_timeout_e_depois_sucesso(audio):
    esperas, dormir = _sem_espera()
    transporte = _Transporte(
        [httpx.ReadTimeout("demorou"), httpx.Response(200, json=RESPOSTA_OK)]
    )

    resultado = transcrever_arquivo(
        audio, endpoint=ENDPOINT, chave="k", transporte=transporte.transporte, dormir=dormir
    )

    assert resultado == RESPOSTA_OK
    assert esperas == [2.0]


def test_esgota_tentativas_e_levanta_erro_sem_dormir_no_fim(audio):
    esperas, dormir = _sem_espera()
    transporte = _Transporte([httpx.Response(503), httpx.Response(503), httpx.Response(503)])

    with pytest.raises(TranscricaoError) as info:
        transcrever_arquivo(
            audio,
            endpoint=ENDPOINT,
            chave="k",
            max_tentativas=3,
            transporte=transporte.transporte,
            dormir=dormir,
        )

    assert info.value.status_code == 503
    assert info.value.tentativas == 3
    assert len(transporte.requisicoes) == 3
    assert len(esperas) == 2


def test_timeout_persistente_vira_erro_sem_status(audio):
    transporte = _Transporte([httpx.ConnectTimeout("x")] * 2)

    with pytest.raises(TranscricaoError) as info:
        transcrever_arquivo(
            audio,
            endpoint=ENDPOINT,
            chave="k",
            max_tentativas=2,
            transporte=transporte.transporte,
            dormir=lambda _: None,
        )

    assert info.value.status_code is None
    assert "ConnectTimeout" in str(info.value)


def test_erro_4xx_nao_e_retentado_e_mostra_mensagem_do_azure(audio):
    transporte = _Transporte(
        [httpx.Response(401, json={"error": {"message": "Chave inválida"}})]
    )

    with pytest.raises(TranscricaoError) as info:
        transcrever_arquivo(
            audio, endpoint=ENDPOINT, chave="errada", transporte=transporte.transporte
        )

    assert info.value.status_code == 401
    assert "Chave inválida" in str(info.value)
    assert len(transporte.requisicoes) == 1


def test_arquivo_de_origem_nao_e_alterado_em_falha(audio):
    conteudo_original = audio.read_bytes()
    transporte = _Transporte([httpx.Response(400, text="formato inválido")])

    with pytest.raises(TranscricaoError):
        transcrever_arquivo(audio, endpoint=ENDPOINT, chave="k", transporte=transporte.transporte)

    assert audio.read_bytes() == conteudo_original
