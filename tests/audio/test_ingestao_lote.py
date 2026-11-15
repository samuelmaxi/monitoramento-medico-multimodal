"""US10: o lote registra cada falha em log e em metadados e continua com os demais arquivos."""

from __future__ import annotations

import csv
import json
import logging

import numpy as np
import pytest

from audio.ingestao import leitura
from audio.ingestao import lote as modulo_lote
from audio.ingestao.config import CAMINHO_PADRAO
from audio.ingestao.lote import Entrada, gerar_audio_id, ingerir, ingerir_lote
from audio.ingestao.metadados import COLUNAS
from audio.ingestao.run import main

from .conftest import RAIZ, tom


@pytest.fixture(autouse=True)
def _sem_ffmpeg(monkeypatch):
    monkeypatch.setattr(leitura.shutil, "which", lambda _nome: None)


@pytest.fixture
def pasta_mista(tmp_path, gravar_audio):
    """Três áudios bons e três ruins (vazio, corrompido, extensão desconhecida)."""
    entrada = tmp_path / "entrada"
    gravar_audio("bom_1.wav", tom(1.5), 16000, subtype="PCM_16")
    gravar_audio("bom_2.wav", tom(2.0, 44100, canais=2), 44100, subtype="FLOAT")
    gravar_audio("bom_3.flac", tom(1.0), 16000, format="FLAC", subtype="PCM_16")
    (entrada / "vazio.wav").write_bytes(b"")
    (entrada / "corrompido.wav").write_bytes(np.random.default_rng(1).bytes(2048))
    (entrada / "notas.txt").write_text("não é áudio")
    return entrada


def _arquivos(pasta):
    return sorted(p for p in pasta.iterdir() if p.is_file())


def test_um_arquivo_ruim_nao_interrompe_o_lote(pasta_mista, tmp_path, cfg_ingestao, caplog):
    caplog.set_level(logging.INFO, logger="audio.ingestao")

    resultados = ingerir_lote(_arquivos(pasta_mista), tmp_path / "saida", cfg_ingestao)

    por_id = {r.metadados.audio_id: r for r in resultados}
    assert len(resultados) == 6
    assert {i for i, r in por_id.items() if r.ok} == {"bom_1", "bom_2", "bom_3"}
    assert por_id["vazio"].metadados.erro_tipo == "audio_vazio"
    assert por_id["corrompido"].metadados.erro_tipo == "audio_corrompido"
    assert por_id["notas"].metadados.erro_tipo == "formato_nao_suportado"
    for r in resultados:
        if r.ok:
            assert r.convertido.is_file()
            assert r.features.is_file()


def test_cada_erro_e_logado_com_id_e_codigo(pasta_mista, tmp_path, cfg_ingestao, caplog):
    caplog.set_level(logging.INFO, logger="audio.ingestao")

    ingerir_lote(_arquivos(pasta_mista), tmp_path / "saida", cfg_ingestao)

    erros = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(erros) == 3
    assert any("[vazio] audio_vazio" in m for m in erros)
    assert any("[corrompido] audio_corrompido" in m for m in erros)
    assert any("[notas] formato_nao_suportado" in m for m in erros)
    assert all(str(tmp_path) not in m for m in erros)  # só o nome do arquivo, sem caminho absoluto


def test_csv_do_lote_tem_uma_linha_por_arquivo_inclusive_os_com_erro(
    pasta_mista, tmp_path, cfg_ingestao
):
    saida = tmp_path / "saida"

    ingerir_lote(_arquivos(pasta_mista), saida, cfg_ingestao)

    with (saida / "metadados.csv").open(encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    assert tuple(linhas[0]) == COLUNAS
    assert {linha["audio_id"]: linha["status"] for linha in linhas} == {
        "bom_1": "ok", "bom_2": "ok", "bom_3": "ok",
        "corrompido": "erro", "notas": "erro", "vazio": "erro",
    }


def test_erro_inesperado_em_um_arquivo_tambem_nao_derruba_o_lote(
    pasta_mista, tmp_path, cfg_ingestao, monkeypatch, caplog
):
    original = modulo_lote.ler_audio

    def ler_com_bug(caminho, cfg):
        if caminho.name == "bom_2.wav":
            raise RuntimeError("bug inesperado")
        return original(caminho, cfg)

    monkeypatch.setattr(modulo_lote, "ler_audio", ler_com_bug)

    resultados = ingerir_lote(_arquivos(pasta_mista), tmp_path / "saida", cfg_ingestao)

    por_id = {r.metadados.audio_id: r for r in resultados}
    assert por_id["bom_1"].ok
    assert por_id["bom_3"].ok
    assert not por_id["bom_2"].ok
    assert por_id["bom_2"].metadados.erro_tipo == "RuntimeError"
    assert any("erro inesperado" in r.getMessage() for r in caplog.records)


def test_falha_nao_deixa_pasta_pela_metade(pasta_mista, tmp_path, cfg_ingestao):
    saida = tmp_path / "saida"

    ingerir_lote(_arquivos(pasta_mista), saida, cfg_ingestao)

    assert not (saida / "vazio").exists()
    assert not (saida / "corrompido").exists()
    assert (saida / "bom_1" / "convertido.wav").is_file()


def test_mesmo_nome_em_pastas_diferentes_gera_ids_distintos(tmp_path, gravar_audio, cfg_ingestao):
    a = gravar_audio("consulta.wav", tom(1.0), 16000, subtype="PCM_16", pasta=tmp_path / "a")
    b = gravar_audio("consulta.wav", tom(1.5), 16000, subtype="PCM_16", pasta=tmp_path / "b")

    resultados = ingerir_lote([Entrada(a, tmp_path / "a"), Entrada(b, tmp_path / "b")],
                              tmp_path / "saida", cfg_ingestao)

    ids = [r.metadados.audio_id for r in resultados]
    assert len(set(ids)) == 2
    assert ids[0] == "consulta"
    assert ids[1].startswith("consulta-")
    assert all(r.ok for r in resultados)


def test_audio_id_segue_o_padrao_do_contrato(tmp_path):
    arquivo = tmp_path / "WhatsApp Ptt 2026-11-02 at 23.20.41.ogg"
    arquivo.write_bytes(b"x")

    audio_id = gerar_audio_id(arquivo)

    assert audio_id == "WhatsApp_Ptt_2026-11-02_at_23.20.41"
    import re

    from contratos.evento import PADRAO_ID_CURTO

    assert re.fullmatch(PADRAO_ID_CURTO, audio_id)


def test_nome_registrado_e_relativo_a_pasta_de_entrada(tmp_path, gravar_audio, cfg_ingestao):
    entrada = tmp_path / "gravacoes"
    arquivo = gravar_audio("sub/nome.wav", tom(1.0), 16000, subtype="PCM_16", pasta=entrada)

    resultado = ingerir(arquivo, tmp_path / "saida", cfg_ingestao, raiz=entrada)

    assert resultado.metadados.arquivo == "sub/nome.wav"


def test_cli_sai_com_2_quando_algum_falha_e_com_0_quando_todos_passam(
    pasta_mista, tmp_path, gravar_audio, caplog
):
    saida = tmp_path / "saida"
    config = RAIZ / CAMINHO_PADRAO

    assert main(["--input", str(pasta_mista), "--saida", str(saida), "--config", str(config)]) == 2
    assert (saida / "metadados.csv").is_file()

    boa = tmp_path / "so_boas"
    gravar_audio("x.wav", tom(1.0), 16000, subtype="PCM_16", pasta=boa)
    assert main(["--input", str(boa), "--saida", str(tmp_path / "s2"), "--config", str(config)]) == 0


def test_cli_sai_com_1_sem_arquivos_ou_com_config_invalida(tmp_path):
    config = RAIZ / CAMINHO_PADRAO
    vazia = tmp_path / "vazia"
    vazia.mkdir()

    assert main(["--input", str(vazia), "--saida", str(tmp_path / "s"), "--config", str(config)]) == 1
    assert main(["--input", str(vazia), "--config", str(tmp_path / "nao_existe.yaml")]) == 1


def test_metadados_json_fica_ao_lado_do_audio_convertido(tmp_path, gravar_audio, cfg_ingestao):
    origem = gravar_audio("a.wav", tom(1.0), 16000, subtype="PCM_16")

    ingerir(origem, tmp_path / "saida", cfg_ingestao)

    dados = json.loads((tmp_path / "saida" / "a" / "metadados.json").read_text(encoding="utf-8"))
    assert dados["status"] == "ok"
    assert dados["convertido_uri"] == "a/convertido.wav"
