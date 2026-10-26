import json

import httpx
import pytest

from app import amostra_hf
from app.amostra_hf import baixar_amostra

TOTAL = 100


class _DatasetFalso:
    """Imita o datasets-server: /rows por offset e os áudios em /cached-assets."""

    def __init__(self):
        self.pedidos_audio: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rows":
            indice = int(request.url.params["offset"])
            url_audio = f"https://datasets-server.huggingface.co/cached-assets/x/{indice}.m4a"
            return httpx.Response(
                200,
                json={
                    "num_rows_total": TOTAL,
                    "rows": [
                        {
                            "row_idx": indice,
                            "row": {
                                "audio": [{"src": url_audio}],
                                "text": f"referencia {indice}",
                                "duration": 10.5,
                                "session_id": f"sessao-{indice}",
                                "file_name": f"/x/{indice}.m4a",
                                "audio_language": "en",
                                "text_language": "en",
                                "md5_audio": "informado",
                            },
                        }
                    ],
                },
            )
        if request.url.path.startswith("/cached-assets/"):
            self.pedidos_audio.append(request.url.path)
            return httpx.Response(200, content=f"m4a-{request.url.path}".encode())
        return httpx.Response(404)


@pytest.fixture
def conversao_falsa(monkeypatch):
    """Evita o ffmpeg: grava um arquivo qualquer no lugar do WAV."""
    chamadas = []

    def converter(origem, destino):
        chamadas.append((origem, destino))
        destino.write_bytes(b"RIFF-falso")
        return destino

    monkeypatch.setattr(amostra_hf, "converter_para_wav", converter)
    return chamadas


def test_amostra_grava_wav_referencia_origem_e_manifesto(tmp_path, conversao_falsa):
    dataset = _DatasetFalso()

    pares = baixar_amostra(
        3, destino=tmp_path, seed=7, transporte=httpx.MockTransport(dataset)
    )

    assert len(pares) == 3
    for wav, txt in pares:
        assert wav.suffix == ".wav" and wav.is_file()
        assert txt.read_text(encoding="utf-8").startswith("referencia ")
        assert (tmp_path / "origem" / f"{wav.stem}.m4a").is_file()

    manifesto = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifesto["seed"] == 7
    assert len(manifesto["amostras"]) == 3
    primeira = manifesto["amostras"][0]
    assert primeira["session_id"].startswith("sessao-")
    assert len(primeira["md5_origem"]) == 32
    assert primeira["md5_hf_informado"] == "informado"


def test_mesma_seed_sorteia_os_mesmos_indices(tmp_path, conversao_falsa):
    primeira = baixar_amostra(
        5, destino=tmp_path / "a", seed=42, transporte=httpx.MockTransport(_DatasetFalso())
    )
    segunda = baixar_amostra(
        5, destino=tmp_path / "b", seed=42, transporte=httpx.MockTransport(_DatasetFalso())
    )

    assert [p[0].name for p in primeira] == [p[0].name for p in segunda]


def test_seed_diferente_muda_a_amostra(tmp_path, conversao_falsa):
    a = baixar_amostra(5, destino=tmp_path / "a", seed=1, transporte=httpx.MockTransport(_DatasetFalso()))
    b = baixar_amostra(5, destino=tmp_path / "b", seed=2, transporte=httpx.MockTransport(_DatasetFalso()))

    assert [p[0].name for p in a] != [p[0].name for p in b]


def test_rodar_de_novo_nao_baixa_audio_existente(tmp_path, conversao_falsa):
    dataset = _DatasetFalso()
    transporte = httpx.MockTransport(dataset)
    baixar_amostra(3, destino=tmp_path, seed=3, transporte=transporte)
    baixados_na_primeira = len(dataset.pedidos_audio)

    baixar_amostra(3, destino=tmp_path, seed=3, transporte=transporte)

    assert baixados_na_primeira == 3
    assert len(dataset.pedidos_audio) == 3  # a segunda rodada não baixou nada
    assert len(conversao_falsa) == 3


def test_amostra_maior_que_o_dataset_falha(tmp_path, conversao_falsa):
    with pytest.raises(ValueError, match="100 linhas"):
        baixar_amostra(
            TOTAL + 1, destino=tmp_path, transporte=httpx.MockTransport(_DatasetFalso())
        )
