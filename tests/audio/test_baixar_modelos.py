"""O download do YAMNet confere o SHA-256 e nunca deixa arquivo pela metade (sem rede)."""

from __future__ import annotations

import hashlib
import io
import urllib.error
from dataclasses import replace

import pytest

from scripts.baixar_modelos_audio import ARQUIVOS, Arquivo, ErroDeDownload, baixar, main

CONTEUDO = b"conteudo-de-um-modelo" * 1000
ARQUIVO = Arquivo("modelo.onnx", "https://exemplo.invalid/modelo.onnx",
                  hashlib.sha256(CONTEUDO).hexdigest(), len(CONTEUDO))


class Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def abrir_com(conteudo: bytes):
    chamadas = []

    def abrir(url, timeout):
        chamadas.append((url, timeout))
        return Resposta(conteudo)

    abrir.chamadas = chamadas
    return abrir


def test_baixa_confere_o_hash_e_grava(tmp_path):
    abrir = abrir_com(CONTEUDO)

    assert baixar(ARQUIVO, tmp_path, abrir=abrir) == "baixado"

    assert (tmp_path / "modelo.onnx").read_bytes() == CONTEUDO
    assert not list(tmp_path.glob("*.parte"))
    assert abrir.chamadas == [(ARQUIVO.url, 60)]


def test_arquivo_ja_valido_nao_e_baixado_de_novo(tmp_path):
    (tmp_path / "modelo.onnx").write_bytes(CONTEUDO)
    abrir = abrir_com(b"outra coisa")

    assert baixar(ARQUIVO, tmp_path, abrir=abrir) == "ja_existia"

    assert abrir.chamadas == []


def test_conteudo_diferente_do_esperado_e_recusado_e_nao_fica_salvo(tmp_path):
    with pytest.raises(ErroDeDownload, match="SHA-256"):
        baixar(ARQUIVO, tmp_path, abrir=abrir_com(b"adulterado"))

    assert not (tmp_path / "modelo.onnx").exists()
    assert not list(tmp_path.glob("*.parte"))


def test_arquivo_local_corrompido_pede_para_apagar_ou_forcar(tmp_path):
    (tmp_path / "modelo.onnx").write_bytes(b"corrompido")

    with pytest.raises(ErroDeDownload, match="--forcar"):
        baixar(ARQUIVO, tmp_path, abrir=abrir_com(CONTEUDO))

    assert baixar(ARQUIVO, tmp_path, forcar=True, abrir=abrir_com(CONTEUDO)) == "baixado"
    assert (tmp_path / "modelo.onnx").read_bytes() == CONTEUDO


def test_falha_de_rede_vira_erro_claro_sem_deixar_resto(tmp_path):
    def sem_rede(url, timeout):
        raise urllib.error.URLError("sem conexão")

    with pytest.raises(ErroDeDownload, match="falha ao baixar"):
        baixar(ARQUIVO, tmp_path, abrir=sem_rede)

    assert list(tmp_path.iterdir()) == []


def test_os_dois_arquivos_do_yamnet_tem_hash_fixado():
    nomes = {a.nome: a for a in ARQUIVOS}

    assert set(nomes) == {"yamnet.onnx", "yamnet_class_map.csv"}
    for arquivo in ARQUIVOS:
        assert len(arquivo.sha256) == 64
        assert arquivo.url.startswith("https://")


def test_main_devolve_1_quando_o_download_falha(tmp_path, monkeypatch, capsys):
    import scripts.baixar_modelos_audio as modulo

    monkeypatch.setattr(modulo, "ARQUIVOS", (replace(ARQUIVO),))
    monkeypatch.setattr(modulo, "baixar", lambda *a, **k: (_ for _ in ()).throw(
        ErroDeDownload("sem rede")))

    assert main(["--destino", str(tmp_path)]) == 1
    assert "[ERRO] sem rede" in capsys.readouterr().err
