"""CLI ``python -m audio.vocal.run``: códigos de saída, saídas em disco e lote com falha."""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest
import soundfile as sf

from audio.vocal.injecao import fala_normal, transcricao_sintetica
from audio.vocal.run import main

from .conftest import RAIZ

TAXA = 16000
GRAVADO_EM = "2026-10-01T13:00:00Z"


@pytest.fixture(autouse=True)
def _ambiente(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "eventos.jsonl"))
    monkeypatch.setenv("ALERTA_STORE", "memoria")
    monkeypatch.setenv("ALERTA_REGRAS_PATH", str(RAIZ / "config" / "alertas_regras.yaml"))
    monkeypatch.setenv("ALERTA_SAIDA_DIR", str(tmp_path / "alertas"))
    monkeypatch.delenv("AUDIO_SAIDA_DIR", raising=False)


def gravar(tmp_path, nome, sinal=None):
    sinal = fala_normal()[0] if sinal is None else sinal
    caminho = tmp_path / "gravacoes" / nome
    caminho.parent.mkdir(exist_ok=True)
    sf.write(str(caminho), sinal, TAXA, subtype="PCM_16")
    return caminho


def argumentos(*audios, extra=()):
    return [
        "--audio", *map(str, audios), "--patient-id", "10000032", "--recorded-at", GRAVADO_EM,
        "--config", str(RAIZ / "audio" / "vocal" / "config.yaml"),
        "--config-ingestao", str(RAIZ / "audio" / "ingestao" / "config.yaml"),
        "--modelo-yamnet", "nao/existe/yamnet.onnx", *extra,
    ]


def test_um_audio_gera_arquivos_eventos_e_publica_no_barramento(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    audio = gravar(tmp_path, "consulta.wav")

    assert main(argumentos(audio)) == 0

    pasta = tmp_path / "saida" / "audio" / "consulta"
    assert {p.name for p in pasta.iterdir()} == {
        "convertido.wav", "features.wav", "metadados.json", "eventos.jsonl", "relatorio.json"}
    relatorio = json.loads((pasta / "relatorio.json").read_text(encoding="utf-8"))
    assert relatorio["classe"] == "normal"
    barramento = (tmp_path / "eventos.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(barramento) == 1
    assert json.loads(barramento[0])["event_type"] == "sem_achados"
    assert "classe: normal" in caplog.text


def test_yamnet_ausente_so_avisa_e_a_analise_segue(tmp_path, caplog):
    audio = gravar(tmp_path, "consulta.wav")

    assert main(argumentos(audio)) == 0

    assert "YAMNet indisponível" in caplog.text
    relatorio = json.loads(
        (tmp_path / "saida" / "audio" / "consulta" / "relatorio.json").read_text(encoding="utf-8"))
    assert relatorio["respiratorio"]["disponivel"] is False


def test_audio_ruim_no_lote_da_saida_2_mas_os_outros_sao_processados(tmp_path, caplog):
    bom = gravar(tmp_path, "bom.wav")
    vazio = tmp_path / "gravacoes" / "vazio.wav"
    vazio.write_bytes(b"")
    outro_bom = gravar(tmp_path, "outro.wav")

    codigo = main(argumentos(bom, vazio, outro_bom))

    assert codigo == 2
    assert (tmp_path / "saida" / "audio" / "bom" / "relatorio.json").is_file()
    assert (tmp_path / "saida" / "audio" / "outro" / "relatorio.json").is_file()
    assert not (tmp_path / "saida" / "audio" / "vazio").exists()
    assert "[vazio.wav]" in caplog.text
    assert len((tmp_path / "eventos.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_audios_seguidos_do_mesmo_paciente_formam_a_baseline(tmp_path):
    audios = [gravar(tmp_path, f"a{i}.wav", fala_normal(semente=i)[0]) for i in range(3)]

    assert main(argumentos(*audios)) == 0

    arquivos = list((tmp_path / "saida" / "audio" / "baseline").glob("pt_*.json"))
    assert len(arquivos) == 1
    assert [a["audio_id"] for a in json.loads(arquivos[0].read_text(encoding="utf-8"))["audios"]
            ] == ["a0", "a1", "a2"]


def test_sem_atualizar_baseline_nao_cria_o_arquivo(tmp_path):
    audio = gravar(tmp_path, "consulta.wav")

    assert main(argumentos(audio, extra=["--sem-atualizar-baseline"])) == 0

    assert not (tmp_path / "saida" / "audio" / "baseline").exists()


def test_transcricao_pronta_do_azure_e_usada(tmp_path):
    sinal, falas = fala_normal()
    audio = gravar(tmp_path, "consulta.wav", sinal)
    json_us11 = tmp_path / "consulta_us11.json"
    json_us11.write_text(json.dumps(transcricao_sintetica(falas)), encoding="utf-8")

    assert main(argumentos(audio, extra=["--transcricao", str(json_us11)])) == 0

    relatorio = json.loads(
        (tmp_path / "saida" / "audio" / "consulta" / "relatorio.json").read_text(encoding="utf-8"))
    assert relatorio["fonte_segmentos"] == "transcricao"
    assert relatorio["biomarcadores"]["taxa_fala_pps"] is not None


def test_sem_alertas_nao_liga_o_motor_mas_publica_no_barramento(tmp_path):
    audio = gravar(tmp_path, "consulta.wav")

    assert main(argumentos(audio, extra=["--sem-alertas"])) == 0

    assert (tmp_path / "eventos.jsonl").is_file()
    assert not (tmp_path / "alertas").exists()


def test_sem_pseudonym_key_sai_com_1_e_nao_converte_nada(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    audio = gravar(tmp_path, "consulta.wav")

    assert main(argumentos(audio)) == 1

    assert "PSEUDONYM_KEY" in caplog.text
    assert not (tmp_path / "saida").exists()


def test_configuracao_inexistente_sai_com_1(tmp_path, caplog):
    audio = gravar(tmp_path, "consulta.wav")
    args = argumentos(audio)
    args[args.index("--config") + 1] = str(tmp_path / "nao_existe.yaml")

    assert main(args) == 1
    assert "não encontrado" in caplog.text


def test_transcricao_json_ilegivel_sai_com_1(tmp_path):
    audio = gravar(tmp_path, "consulta.wav")
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{ isto nao e json", encoding="utf-8")

    assert main(argumentos(audio, extra=["--transcricao", str(ruim)])) == 1


def test_audio_id_e_transcricao_so_valem_com_um_audio(tmp_path):
    a, b = gravar(tmp_path, "a.wav"), gravar(tmp_path, "b.wav")

    with pytest.raises(SystemExit) as saida:
        main(argumentos(a, b, extra=["--audio-id", "x"]))

    assert saida.value.code == 2


@pytest.mark.parametrize("instante", ["2026-10-01T13:00:00", "2026-10-01T13:00:00-03:00", "ontem"])
def test_recorded_at_precisa_ser_utc(tmp_path, instante):
    audio = gravar(tmp_path, "consulta.wav")
    args = argumentos(audio)
    args[args.index("--recorded-at") + 1] = instante

    with pytest.raises(SystemExit) as saida:
        main(args)

    assert saida.value.code == 2


def test_audio_so_de_ruido_e_erro_de_analise_e_sai_com_2(tmp_path, caplog):
    ruido = (0.0005 * np.random.default_rng(0).standard_normal(TAXA * 3)).astype(np.float32)
    audio = gravar(tmp_path, "ruido.wav", ruido)

    assert main(argumentos(audio)) == 2

    assert "nenhum segmento de fala" in caplog.text
