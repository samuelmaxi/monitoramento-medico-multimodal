"""Relatório de desvios do vídeo (US09) dentro do pipeline ponta a ponta (US20).

O YOLO é falso (``yolo_falso`` em conftest.py): o que se testa é o encadeamento US07 → US08 →
US09 → barramento da execução, não a detecção.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pipeline import ConfigPipeline, OrquestradorPipeline
from pipeline.etapas import relatorio_video as etapa_relatorio


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")
    for var in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ENDPOINT", "AZURE_LANGUAGE_KEY",
                "AZURE_LANGUAGE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)


def _executar(tmp_path, **extra):
    config = ConfigPipeline.from_env(
        fator_velocidade=0.0, saida_dir=tmp_path / "saida", sinais_fonte="sintetico", **extra
    )
    resumo = OrquestradorPipeline(config).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    etapa = next(e for e in relatorio["etapas"] if e["etapa"] == "relatorio_video")
    return resumo, etapa


def _eventos(resumo) -> list[dict]:
    linhas = resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()
    return [json.loads(linha) for linha in linhas if linha.strip()]


def test_modo_simulado_nao_gera_relatorio(tmp_path):
    resumo, etapa = _executar(tmp_path, modo="simulado")

    assert etapa["status"] == "sucesso"
    assert etapa["eventos_emitidos"] == 0
    assert etapa["detalhes"]["relatorio_gerado"] is False
    assert "simulado" in etapa["detalhes"]["motivo"]
    assert resumo.caminho_relatorio_video_json is None
    assert resumo.caminho_relatorio_video_html is None
    assert not (tmp_path / "saida" / "video").exists()


def test_video_real_gera_relatorio_e_publica_o_desvio(tmp_path, video_sintetico, yolo_falso):
    video = video_sintetico()
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito"),
                ("saida_area_critica", 5.0, "borda_leito")])

    resumo, etapa = _executar(tmp_path, modo="real", video_path=video)

    assert etapa["status"] == "sucesso"
    assert resumo.caminho_relatorio_video_json.is_file()
    assert resumo.caminho_relatorio_video_html.is_file()
    rel = json.loads(resumo.caminho_relatorio_video_json.read_text(encoding="utf-8"))
    assert rel["video"]["perfil"] == "internacao_leito"
    assert rel["resumo"]["total_desvios"] == 1
    assert rel["desvios"][0]["tipo"] == "presenca_indevida_area_critica"
    assert rel["desvios"][0]["severidade"] == "media"
    assert any("Sem pose" in aviso for aviso in rel["avisos"])  # limitação dita no relatório

    detalhes = etapa["detalhes"]
    assert detalhes["relatorio_gerado"] is True
    assert detalhes["total_desvios"] == 1
    assert detalhes["por_severidade"]["media"] == 1
    assert detalhes["arquivos"] == [
        "video_leito_relatorio_desvios.json", "video_leito_relatorio_desvios.html"
    ]
    assert etapa["eventos_emitidos"] == 1

    # O desvio entrou no barramento da execução, com o paciente do pipeline: chega à fusão.
    desvios = [e for e in _eventos(resumo) if e["event_type"] == "presenca_indevida_area_critica"]
    assert len(desvios) == 1
    assert desvios[0]["patient_id"] == resumo.paciente_id
    assert "presenca_indevida_area_critica" in {e.event_type for e in resumo.risco.eventos}


def test_relatorio_nao_traz_o_nome_do_arquivo_nem_caminho(tmp_path, video_sintetico, yolo_falso):
    video = video_sintetico("paciente_joao_silva.avi")
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito"),
                ("saida_area_critica", 5.0, "borda_leito")])

    resumo, etapa = _executar(tmp_path, modo="real", video_path=video)

    rel = resumo.caminho_relatorio_video_json.read_text(encoding="utf-8")
    html = resumo.caminho_relatorio_video_html.read_text(encoding="utf-8")
    detalhes = json.dumps(etapa["detalhes"], ensure_ascii=False)
    for texto in (rel, html, detalhes):
        assert "joao" not in texto.lower()
        assert str(tmp_path) not in texto
    assert json.loads(rel)["video"]["id"] == "video_leito"


def test_presenca_aberta_fecha_no_fim_do_trecho_lido_pelo_yolo(
    tmp_path, video_sintetico, yolo_falso
):
    # O vídeo tem 15 s, mas o YOLO só leu 50 quadros (5 s): a presença sem "saida" termina em
    # 5 s, não nos 15 s do arquivo, e o % do tempo é sobre os 5 s analisados.
    video = video_sintetico(quadros=150)
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito")], quadros_lidos=50)

    resumo, _ = _executar(tmp_path, modo="real", video_path=video)

    rel = json.loads(resumo.caminho_relatorio_video_json.read_text(encoding="utf-8"))
    assert rel["video"]["duracao_s"] == pytest.approx(5.0)
    assert rel["desvios"][0]["fim_s"] == pytest.approx(5.0)
    assert rel["resumo"]["pct_tempo_em_desvio"] == pytest.approx(90.0)


def test_video_sem_transicoes_gera_relatorio_sem_desvios(tmp_path, video_sintetico, yolo_falso):
    video = video_sintetico()
    yolo_falso([])

    resumo, etapa = _executar(tmp_path, modo="real", video_path=video)

    assert etapa["status"] == "sucesso"
    rel = json.loads(resumo.caminho_relatorio_video_json.read_text(encoding="utf-8"))
    assert rel["resumo"]["total_desvios"] == 0
    # Sem pose, a análise não é conclusiva: o relatório diz isso em vez de "tudo certo".
    assert "não é conclusiva" in rel["resumo"]["mensagem"]


def test_falha_da_analise_vira_falha_da_etapa_e_o_fluxo_segue(
    tmp_path, video_sintetico, yolo_falso, monkeypatch
):
    video = video_sintetico()
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito")])
    monkeypatch.setattr(etapa_relatorio, "PERFIL_US08", "perfil_que_nao_existe")

    resumo, etapa = _executar(tmp_path, modo="real", video_path=video)

    assert etapa["status"] == "falha"
    assert resumo.etapas_com_falha == ("relatorio_video",)
    assert resumo.caminho_relatorio_video_html is None
    assert resumo.nivel_risco_final != "indisponivel"  # fusão e alertas rodaram


def test_publicacao_com_falha_marca_a_etapa_mas_mantem_o_relatorio(
    tmp_path, video_sintetico, yolo_falso, monkeypatch
):
    video = video_sintetico()
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito"),
                ("saida_area_critica", 5.0, "borda_leito")])
    gerar_real = etapa_relatorio.gerar

    def gerar_com_falha(**kw):
        resultado = gerar_real(**kw)
        return replace(
            resultado, publicacao=replace(resultado.publicacao, falhas=("fusao: indisponível",))
        )

    monkeypatch.setattr(etapa_relatorio, "gerar", gerar_com_falha)

    resumo, etapa = _executar(tmp_path, modo="real", video_path=video)

    assert etapa["status"] == "falha"
    assert etapa["detalhes"]["falhas_publicacao"] == ["fusao: indisponível"]
    assert resumo.caminho_relatorio_video_html.is_file()
