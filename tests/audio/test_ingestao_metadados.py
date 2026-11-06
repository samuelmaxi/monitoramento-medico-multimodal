"""US10: metadados por áudio e validação da configuração; nenhum áudio versionado no Git."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess

import pytest
import yaml

from audio.configuracao import ErroDeConfiguracao
from audio.ingestao.config import carregar_config, config_de_dict
from audio.ingestao.lote import ingerir

from .conftest import RAIZ, tom

CONFIG = RAIZ / "audio" / "ingestao" / "config.yaml"


def test_metadados_registram_duracao_taxa_canais_origem_e_licenca(tmp_path, gravar_audio, cfg_ingestao):
    origem = gravar_audio("consulta.wav", tom(2.5, 44100, canais=2), 44100, subtype="FLOAT")

    resultado = ingerir(origem, tmp_path / "saida", cfg_ingestao, origem="consulta simulada",
                        licenca="CC0 1.0")

    m = resultado.metadados
    assert m.duracao_s == pytest.approx(2.5, abs=0.01)
    assert m.taxa_amostragem_hz == 44100
    assert m.canais == 2
    assert m.origem == "consulta simulada"
    assert m.licenca == "CC0 1.0"
    assert (m.convertido_taxa_hz, m.convertido_canais, m.convertido_codificacao) == (
        16000, 1, "PCM_16")
    assert m.sha256 and len(m.sha256) == 64
    assert m.versao_config == cfg_ingestao.versao


def test_origem_e_licenca_tem_padrao_que_pede_confirmar_consentimento(
    tmp_path, gravar_audio, cfg_ingestao
):
    origem = gravar_audio("a.wav", tom(1.0), 16000, subtype="PCM_16")

    m = ingerir(origem, tmp_path / "saida", cfg_ingestao).metadados

    assert m.origem == cfg_ingestao.proveniencia.origem_padrao
    assert "consentimento" in m.licenca


def test_registro_nao_vaza_caminho_absoluto_do_disco(tmp_path, gravar_audio, cfg_ingestao):
    origem = gravar_audio("a.wav", tom(1.0), 16000, subtype="PCM_16")

    ingerir(origem, tmp_path / "saida", cfg_ingestao)

    texto = (tmp_path / "saida" / "a" / "metadados.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in texto
    assert str(tmp_path).replace("\\", "/") not in texto
    dados = json.loads(texto)
    assert dados["arquivo"] == "a.wav"
    assert not re.match(r"^([A-Za-z]:|/)", dados["convertido_uri"])


def test_config_padrao_carrega_e_traz_o_formato_exigido():
    cfg = carregar_config(CONFIG)

    assert cfg.taxa_amostragem_hz == 16000
    assert cfg.validacao.duracao_minima_s == 0.5
    assert cfg.validacao.decodificador_nativo <= cfg.validacao.extensoes


def _yaml() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_config_recusa_chave_ausente_e_desconhecida():
    sem_versao = _yaml()
    del sem_versao["versao"]
    with pytest.raises(ErroDeConfiguracao, match="versao"):
        config_de_dict(sem_versao)

    extra = _yaml()
    extra["copia_features"]["silencio"]["inventado"] = 1
    with pytest.raises(ErroDeConfiguracao, match="inventado"):
        config_de_dict(extra)


@pytest.mark.parametrize(
    ("caminho", "valor", "trecho"),
    [
        (("saida", "taxa_amostragem_hz"), 100, "taxa_amostragem_hz"),
        (("validacao", "duracao_minima_s"), -1, "duracao_minima_s"),
        (("validacao", "duracao_maxima_s"), 0.1, "duracao_maxima_s"),
        (("copia_features", "pico_alvo"), 1.5, "pico_alvo"),
        (("copia_features", "ganho_maximo"), 0.5, "ganho_maximo"),
        (("copia_features", "silencio", "manter_s"), 5, "manter_s"),
        (("copia_features", "silencio", "limiar_relativo_db"), 10, "limiar_relativo_db"),
        (("validacao", "decodificador_nativo"), ["wav", "xyz"], "decodificador_nativo"),
    ],
)
def test_config_recusa_valores_fora_do_dominio(caminho, valor, trecho):
    dados = copy.deepcopy(_yaml())
    alvo = dados
    for chave in caminho[:-1]:
        alvo = alvo[chave]
    alvo[caminho[-1]] = valor

    with pytest.raises(ErroDeConfiguracao, match=trecho):
        config_de_dict(dados)


def test_config_ausente_tem_mensagem_clara(tmp_path):
    with pytest.raises(ErroDeConfiguracao, match="não encontrado"):
        carregar_config(tmp_path / "nao_existe.yaml")


def test_nenhum_audio_esta_versionado_no_repositorio():
    """US10: nenhum áudio (dado pessoal potencial) pode estar no Git."""
    if shutil.which("git") is None or not (RAIZ / ".git").exists():
        pytest.skip("sem git ou fora de um clone")
    arquivos = subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    audios = [a for a in arquivos if re.search(r"\.(wav|ogg|oga|opus|m4a|mp3|flac|aac|wma|amr)$", a, re.I)]

    assert audios == []


def test_gitignore_cobre_audio_e_a_pasta_de_prototipo():
    ignorado = (RAIZ / ".gitignore").read_text(encoding="utf-8")

    for padrao in ("tratamento_audio/", "*.wav", "*.ogg", "*.m4a", "*.mp3", "*.flac"):
        assert padrao in ignorado.splitlines()
