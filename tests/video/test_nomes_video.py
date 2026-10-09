"""Testes do parser de nomes de vídeo (``video.nomes_video``)."""

from __future__ import annotations

from video.nomes_video import parsear_nome


def test_parser_reconhece_cenario_bed():
    nome = parsear_nome("B_D_0001.mp4")
    assert nome.cenario == "cama"
    assert nome.cenario_token == "B"
    assert nome.identificador == "0001"
    assert nome.redimensionado is False


def test_parser_nao_inventa_condicao_para_token_n():
    nome = parsear_nome("B_N_100.mp4")
    assert nome.condicao is None
    assert "N" in nome.tokens_desconhecidos
    assert nome.requer_revisao is True


def test_parser_preserva_d_como_desconhecido():
    nome = parsear_nome("B_D_0002.mp4")
    assert nome.condicao is None
    assert "D" in nome.tokens_desconhecidos


def test_parser_detecta_resized_e_modalidade():
    resized = parsear_nome("B_N_87_resized.mp4")
    assert resized.redimensionado is True
    assert resized.condicao is None

    mask = parsear_nome("B_F_0003_mask.mp4")
    assert mask.modalidade == "mask"
    assert mask.condicao == "queda"
    raw = parsear_nome("B_NF_0004_raw.mp4")
    assert raw.modalidade == "raw"
    assert raw.condicao == "sem_queda"


def test_parser_chave_grupo_une_derivados():
    base = parsear_nome("B_N_87.mp4")
    resized = parsear_nome("B_N_87_resized.mp4")
    mask = parsear_nome("B_N_87_mask.mp4")
    assert base.chave_grupo == resized.chave_grupo == mask.chave_grupo


def test_parser_marca_ambiguidade_de_condicao():
    nome = parsear_nome("F_NF_0001.mp4")
    assert nome.condicao is None
    assert nome.requer_revisao is True
    assert any("condicao" in a for a in nome.ambiguidades)


def test_parser_nome_livre_sem_metadados():
    nome = parsear_nome("video_triste.mp4")
    assert nome.cenario is None
    assert nome.condicao is None
    assert nome.requer_revisao is True
    assert set(nome.tokens_desconhecidos) == {"video", "triste"}


def test_parser_todos_os_cenarios_confirmados():
    assert parsear_nome("S_F_1.mp4").cenario == "em_pe"
    assert parsear_nome("C_NF_1.mp4").cenario == "cadeira"
