"""Testes da US09 (relatório automático de desvios), com pose, eventos e vídeo sintéticos.

Sem OpenPose nem YOLOv8: a US09 lê o que a US06 e a US07 gravam, como a US08.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from alertas import Despachante, MotorAlertas, ServicoAlertas, carregar_regras
from alertas.armazenamento import MemoriaStore
from alertas.notificadores import NotificadorFake
from contratos import (
    BarramentoLocal,
    EmissorJsonl,
    EventoAchado,
    Evidencia,
    Modalidade,
    Severidade,
    agora_utc,
)
from video import criar_video_teste
from video.desvios.modelos import Desvio, Repeticao, ResumoRepeticoes
from video.relatorio import (
    MODEL_VERSION,
    SCHEMA_VERSION,
    confianca,
    construcao,
    privacidade,
    tempo,
)
from video.relatorio.config import ErroDeConfiguracao, carregar_config, config_de_dict
from video.relatorio.html import renderizar
from video.relatorio.run import gerar, main, publicar

from .conftest import PACIENTE
from .test_desvios import CFG, CONFIG, INICIO, RAIZ, _corpo, _pose

CONFIG_RELATORIO = RAIZ / "config" / "us09_relatorio.yaml"
CFG_REL = carregar_config(CONFIG_RELATORIO)
POSE_CONFIG = RAIZ / "video" / "pose" / "config.yaml"
QUEDA = [200.0] * 10 + [230, 270, 310, 320] + [320.0] * 10  # 24 quadros a 10 fps = 2,4 s
PARADO = [200.0] * 24


@pytest.fixture(autouse=True)
def _dentro_de_tmp(tmp_path, monkeypatch):
    """Saídas relativas ao diretório de trabalho: os URIs de artefato do contrato são relativos."""
    monkeypatch.chdir(tmp_path)


def _preparar(tmp_path: Path, alturas: list[float], *, corpos=None) -> tuple[Path, Path]:
    video = criar_video_teste(tmp_path / "leito.avi", quadros=len(alturas), largura=640, altura=480)
    pose = _pose(corpos or [_corpo(y) for y in alturas], origem=str(video))
    pose.save_json(tmp_path / "leito.json")
    return tmp_path / "leito.json", video


def _gerar(tmp_path: Path, alturas: list[float] = QUEDA, **kw):
    pose_json, _ = _preparar(tmp_path, alturas, corpos=kw.pop("corpos", None))
    kw.setdefault("perfil_id", "internacao_leito")
    kw.setdefault("saida", Path("saida"))
    return gerar(
        config=CONFIG,
        config_relatorio=CONFIG_RELATORIO,
        pose_path=pose_json,
        patient_id=PACIENTE,
        inicio_utc="2026-10-05T19:00:00Z",
        pose_config=POSE_CONFIG,
        **kw,
    )


def _evento_area(
    tipo: str, t: float, *, score: float = 0.8, area: str = "borda_leito"
) -> EventoAchado:
    return EventoAchado.criar(
        patient_id=PACIENTE,
        modality=Modalidade.VIDEO,
        event_type=tipo,
        timestamp=INICIO + timedelta(seconds=t),
        score=score,
        severity=Severidade.BAIXA,
        evidence=Evidencia(
            summary=f"person {tipo}",
            features={
                "classe": "person",
                "area_critica_id": area,
                "area_critica_nome": area.replace("_", " ").capitalize(),
                "tempo_video_s": t,
                "frame_index": int(t * 10),
                "x1": 10.0,
                "y1": 10.0,
                "x2": 50.0,
                "y2": 90.0,
                "track_id": 3,
            },
            thresholds={"confianca_minima": 0.25, "iou_nms": 0.5, "criterio_roi": "fracao"},
            models={"yolov8": "yolov8n@8.4.0"},
        ),
        model_version="video-areas-criticas@0.1.0",
    )


def _eventos_areas(
    tmp_path: Path, entrada: float = 0.5, saida: float = 5.0, area: str = "borda_leito"
) -> Path:
    caminho = tmp_path / "areas.jsonl"
    EmissorJsonl(caminho).emitir_varios(
        [
            _evento_area("entrada_area_critica", entrada, area=area),
            _evento_area("saida_area_critica", saida, area=area),
        ]
    )
    return caminho


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #


def test_config_versionada_carrega_e_tem_versao():
    cfg = carregar_config(CONFIG_RELATORIO)
    assert cfg.versao == "1.0.0" and cfg.fonte == CONFIG_RELATORIO
    assert 0 < cfg.confianca.minima <= 1
    assert cfg.evidencia.largura_html_px >= 64


def _dados() -> dict:
    return yaml.safe_load(CONFIG_RELATORIO.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("alterar", "trecho"),
    [
        (lambda d: d["confianca"].update(minimo=0.5), "chaves desconhecidas em 'confianca'"),
        (lambda d: d["confianca"].update(minima=1.5), "confianca.minima"),
        (lambda d: d["confianca"].update(cobertura_minima_pct=-1), "cobertura_minima_pct"),
        (lambda d: d["evidencia"].update(largura_html_px=10), "largura_html_px"),
        (lambda d: d["evidencia"].update(qualidade_jpeg=80.5), "qualidade_jpeg"),
        (lambda d: d.pop("versao"), "versao"),
        (lambda d: d.update(extra=1), "chaves desconhecidas em 'raiz'"),
        (lambda d: d.update(versao="  "), "versao"),
    ],
)
def test_config_invalida_falha_na_carga(alterar, trecho):
    dados = copy.deepcopy(_dados())
    alterar(dados)
    with pytest.raises(ErroDeConfiguracao, match=trecho):
        config_de_dict(dados)


def test_config_inexistente_falha_com_o_caminho(tmp_path):
    with pytest.raises(ErroDeConfiguracao, match="não encontrada"):
        carregar_config(tmp_path / "nao_existe.yaml")


# --------------------------------------------------------------------------- #
# Tempo em desvio
# --------------------------------------------------------------------------- #


def test_trechos_sobrepostos_sao_unidos_e_nao_contam_duas_vezes():
    unidos = tempo.unir([(1.0, 3.0), (2.0, 4.0), (6.0, 7.0), (7.0, 8.0)])
    assert unidos == [(1.0, 4.0), (6.0, 8.0)]
    assert tempo.total(unidos) == 5.0


def test_trecho_alem_do_fim_do_video_e_cortado_e_o_percentual_nao_passa_de_100():
    unidos = tempo.unir([(8.0, 50.0), (-3.0, 1.0)], limite=10.0)
    assert unidos == [(0.0, 1.0), (8.0, 10.0)]
    assert tempo.percentual(tempo.total(unidos), 10.0) == 30.0
    assert tempo.percentual(99.0, 10.0) == 100.0


def test_percentual_sem_duracao_e_none_e_intervalo_vazio_some():
    assert tempo.percentual(5.0, None) is None
    assert tempo.percentual(5.0, 0.0) is None
    assert tempo.unir([(2.0, 2.0), (5.0, 1.0)]) == []


# --------------------------------------------------------------------------- #
# Confiança
# --------------------------------------------------------------------------- #


def _desvio(regra: str, medida: str, inicio: float = 0.5, fim: float = 1.5, score: float = 0.6):
    return Desvio(
        event_type="postura_fora_da_faixa",
        regra=regra,
        severidade=Severidade.BAIXA,
        score=score,
        inicio_s=inicio,
        fim_s=fim,
        pico_s=1.0,
        frame_idx=10,
        medida=medida,
        valor=50.0,
        limiar=45.0,
        unidade="graus",
        janela_s=1.0,
        resumo="x",
    )


def _pose_com_confianca(conf: float, *, sem_joelho_em: set[int] = frozenset()):
    corpos = []
    for i in range(30):
        kp = _corpo(200.0)
        for nome in kp:
            kp[nome] = [kp[nome][0], kp[nome][1], conf]
        if i in sem_joelho_em:
            kp["RKnee"] = None
        corpos.append(kp)
    return _pose(corpos)


def test_confianca_de_pose_e_a_media_dos_keypoints_da_medida_no_trecho():
    desvio = _desvio("faixa.joelho_direito", "joelho_direito")
    c = confianca.da_pose(desvio, _pose_com_confianca(0.8), CFG_REL.confianca)
    assert c.valor == pytest.approx(0.8)
    assert c.cobertura_pct == 100.0
    assert not c.baixa and c.origem == confianca.ORIGEM_POSE


def test_confianca_baixa_pela_media_dos_keypoints():
    desvio = _desvio("faixa.joelho_direito", "joelho_direito")
    c = confianca.da_pose(desvio, _pose_com_confianca(0.3), CFG_REL.confianca)
    assert c.valor == pytest.approx(0.3) and c.baixa


def test_confianca_baixa_pela_cobertura_de_quadros_com_a_medida_completa():
    desvio = _desvio("faixa.joelho_direito", "joelho_direito")  # quadros 5 a 15 (10 fps)
    sem = set(range(5, 15))  # o joelho some em quase todo o trecho
    c = confianca.da_pose(desvio, _pose_com_confianca(0.9, sem_joelho_em=sem), CFG_REL.confianca)
    assert c.valor == pytest.approx(0.9)  # o que foi visto tinha boa confiança...
    assert c.cobertura_pct < CFG_REL.confianca.cobertura_minima_pct
    assert c.baixa  # ...mas a medida saiu de poucos quadros


def test_confianca_sem_quadros_no_trecho_e_none_e_baixa():
    desvio = _desvio("faixa.joelho_direito", "joelho_direito", inicio=50.0, fim=51.0)
    c = confianca.da_pose(desvio, _pose_com_confianca(0.9), CFG_REL.confianca)
    assert c.valor is None and c.baixa and c.origem == confianca.ORIGEM_INDISPONIVEL


def test_confianca_de_area_e_a_da_deteccao_e_sem_pose_e_indisponivel():
    area = _desvio("area.borda_leito", "permanencia_na_area", score=0.92)
    c = confianca.avaliar(area, None, CFG_REL.confianca)
    assert c.valor == 0.92 and not c.baixa and c.origem == confianca.ORIGEM_DETECCAO
    fraca = confianca.avaliar(_desvio("area.x", "permanencia_na_area", score=0.3), None, CFG_REL.confianca)
    assert fraca.baixa
    sem_pose = confianca.avaliar(_desvio("faixa.joelho_direito", "joelho_direito"), None, CFG_REL.confianca)
    assert sem_pose.valor is None and sem_pose.baixa


def test_confianca_do_tronco_usa_pescoco_e_quadril_mesmo_sem_midhip():
    corpos = []
    for _ in range(30):
        kp = _corpo(200.0)
        kp["MidHip"] = None
        corpos.append(kp)
    desvio = _desvio("queda", "descida_quadril")
    c = confianca.da_pose(desvio, _pose(corpos), CFG_REL.confianca)
    assert c.valor == pytest.approx(0.9) and c.cobertura_pct == 100.0


# --------------------------------------------------------------------------- #
# O relatório (DoD)
# --------------------------------------------------------------------------- #


def test_relatorio_de_queda_tem_tudo_que_o_dod_pede(tmp_path):
    r = _gerar(tmp_path)
    rel = r.relatorio

    # identificação, duração e tipo de procedimento
    assert rel["video"]["id"] == "sintetico"
    assert rel["video"]["tipo_procedimento"] == "leito"
    assert rel["video"]["perfil"] == "internacao_leito"
    assert rel["video"]["duracao_s"] == pytest.approx(2.4)
    assert len(rel["video"]["sha256"]) == 64
    assert rel["schema_version"] == SCHEMA_VERSION and rel["model_version"] == MODEL_VERSION

    # lista de desvios: início/fim, tipo, severidade, confiança e frame de evidência
    (d,) = rel["desvios"]
    assert d["tipo"] == "mudanca_brusca_ou_queda" and d["severidade"] == "alta"
    assert 0 <= d["inicio_s"] < d["fim_s"] <= 2.4 + 1e-6
    assert d["confianca"] == pytest.approx(0.9)
    assert d["baixa_confianca"] is False
    assert d["evidencia"]["disponivel"] is True
    assert d["evidencia"]["frame_index"] is not None
    assert (tmp_path / d["evidencia"]["uri"]).is_file()
    assert d["event_id"] == str(r.execucao.eventos[0].event_id)

    # resumo: total por tipo e % do tempo em desvio
    resumo = rel["resumo"]
    assert resumo["status"] == "desvios_detectados" and resumo["total_desvios"] == 1
    assert resumo["por_tipo"]["mudanca_brusca_ou_queda"]["total"] == 1
    assert resumo["por_severidade"] == {"alta": 1, "media": 0, "baixa": 0}
    assert 0 < resumo["pct_tempo_em_desvio"] <= 100
    esperado = round(100 * (d["fim_s"] - d["inicio_s"]) / 2.4, 2)
    assert resumo["pct_tempo_em_desvio"] == pytest.approx(esperado, abs=0.01)

    # versões dos modelos e limiares
    assert rel["modelos"]["openpose"]["utilizado"] is True
    assert rel["modelos"]["openpose"]["modelo"] == "BODY_25"
    assert rel["modelos"]["openpose"]["versao_modulo_pose"] == "0.1.0"
    assert rel["modelos"]["yolov8"]["utilizado"] is False
    assert rel["modelos"]["regras_de_desvio"].startswith("video-desvios@")
    assert rel["limiares"]["versao"] == CFG.versao
    assert rel["limiares"]["perfil"]["queda"]["limiar"] == CFG.perfil("internacao_leito").queda.limiar
    assert rel["limiares"]["geral"]["janela_minima_s"] == CFG.geral.janela_minima_s
    assert rel["versao_config_relatorio"] == CFG_REL.versao


def test_json_gravado_e_o_mesmo_relatorio_devolvido(tmp_path):
    r = _gerar(tmp_path)
    assert r.caminho_json is not None and r.caminho_json.name == "sintetico_relatorio_desvios.json"
    assert json.loads(r.caminho_json.read_text(encoding="utf-8")) == r.relatorio
    assert r.caminho_html is not None and r.caminho_html.stat().st_size > 1000
    assert not list(Path("saida").glob("*.part"))  # gravação atômica não deixa resto


def test_video_sem_desvios_gera_relatorio_sem_desvios_detectados(tmp_path):
    r = _gerar(tmp_path, PARADO)

    rel = r.relatorio
    assert rel["resumo"]["status"] == "sem_desvios_detectados"
    assert rel["resumo"]["mensagem"] == "Sem desvios detectados."
    assert rel["resumo"]["total_desvios"] == 0 and rel["desvios"] == []
    assert rel["resumo"]["por_tipo"] == {}
    assert rel["resumo"]["pct_tempo_em_desvio"] == 0.0
    assert rel["analise"]["confiavel"] is True
    # não é um documento vazio: identifica o vídeo, o padrão, os modelos e os limiares
    assert rel["video"]["id"] == "sintetico" and rel["video"]["padrao_esperado"]
    assert rel["modelos"]["openpose"]["modelo"] == "BODY_25"
    assert rel["limiares"]["perfil"]["queda"]["limiar"] > 0
    assert r.caminho_json.stat().st_size > 500
    pagina = r.caminho_html.read_text(encoding="utf-8")
    assert "Sem desvios detectados" in pagina and "Identificação" in pagina
    assert "<svg" not in pagina  # sem desvios não há linha do tempo
    # e o evento sem_achados (info) continua indo para a fusão
    assert [e.event_type for e in r.execucao.eventos] == ["sem_achados"]


def test_sem_desvios_com_analise_pouco_confiavel_nao_afirma_que_esta_tudo_bem(tmp_path):
    so_dois = [{"Neck": [300.0, 100.0, 0.9], "MidHip": [300.0, 200.0, 0.9]} for _ in range(24)]
    r = _gerar(tmp_path, PARADO, corpos=so_dois)

    rel = r.relatorio
    assert rel["resumo"]["status"] == "sem_desvios_detectados"
    assert "pouco confiável" in rel["resumo"]["mensagem"]
    assert rel["analise"]["confiavel"] is False
    assert rel["analise"]["cobertura_pose_pct"] == 0.0
    assert any("não é conclusiva" in a for a in rel["avisos"])
    assert r.execucao.eventos == []  # a US08 não afirma "normal" sobre o que não viu
    assert "status-aviso" in r.caminho_html.read_text(encoding="utf-8")


def test_pessoa_nunca_vista_gera_relatorio_e_nao_erro(tmp_path):
    vazios = [{} for _ in range(24)]
    r = _gerar(tmp_path, PARADO, corpos=vazios)

    assert r.relatorio["resumo"]["status"] == "sem_desvios_detectados"
    assert r.relatorio["analise"]["confiavel"] is False
    assert r.caminho_json.is_file() and r.caminho_html.is_file()


def test_presenca_em_area_critica_usa_a_confianca_da_deteccao_e_registra_o_yolov8(tmp_path):
    areas = _eventos_areas(tmp_path)

    r = _gerar(tmp_path, PARADO, eventos_areas_path=areas)

    (d,) = r.relatorio["desvios"]
    assert d["tipo"] == "presenca_indevida_area_critica" and d["severidade"] == "media"
    assert d["confianca"] == pytest.approx(0.8)
    assert d["confianca_origem"].startswith("YOLOv8")
    assert d["cobertura_trecho_pct"] is None
    modelos = r.relatorio["modelos"]
    assert modelos["yolov8"]["utilizado"] is True and "yolov8n@8.4.0" in modelos["yolov8"]["descricao"]
    assert r.relatorio["limiares"]["us07"]["iou_nms"] == 0.5
    # a presença vai até 5 s, mas o vídeo tem 2,4 s: o % não passa de 100
    assert r.relatorio["resumo"]["pct_tempo_em_desvio"] <= 100


def test_sem_duracao_conhecida_o_percentual_e_indisponivel_mas_o_relatorio_sai(tmp_path):
    """Perfil só de áreas, sem pose e sem o arquivo do vídeo: não há como saber a duração."""
    areas = _eventos_areas(tmp_path, entrada=1.0, saida=4.0, area="area_restrita")

    r = gerar(
        perfil_id="cirurgia",
        config=CONFIG,
        config_relatorio=CONFIG_RELATORIO,
        eventos_areas_path=areas,
        source_id="sala-sintetica",
        patient_id=PACIENTE,
        saida=Path("saida"),
        pose_config=POSE_CONFIG,
    )

    rel = r.relatorio
    (d,) = rel["desvios"]
    assert d["tipo"] == "presenca_indevida_area_critica" and d["severidade"] == "alta"
    assert rel["video"]["duracao_s"] is None
    assert rel["resumo"]["pct_tempo_em_desvio"] is None
    assert rel["resumo"]["tempo_em_desvio_s"] == pytest.approx(3.0)
    assert any("Duração do vídeo desconhecida" in a for a in rel["avisos"])
    assert rel["modelos"]["openpose"]["utilizado"] is False
    assert "pose da US06 não informada" in rel["modelos"]["openpose"]["motivo"]
    pagina = r.caminho_html.read_text(encoding="utf-8")
    assert pagina.count("<rect") == 1 and "Não utilizado" in pagina


def test_sobreposicao_entre_tipos_nao_conta_o_tempo_duas_vezes(tmp_path):
    areas = _eventos_areas(tmp_path, entrada=0.0, saida=5.0)  # cobre o vídeo inteiro

    r = _gerar(tmp_path, QUEDA, eventos_areas_path=areas)

    resumo = r.relatorio["resumo"]
    assert resumo["total_desvios"] == 2
    assert resumo["pct_tempo_em_desvio"] == 100.0  # somar passaria de 100
    assert sum(t["tempo_em_desvio_s"] for t in resumo["por_tipo"].values()) > 2.4
    assert resumo["tempo_em_desvio_s"] == pytest.approx(2.4)


def test_desvio_sem_confianca_ou_com_pouca_vira_aviso(tmp_path):
    baixo = []
    for y in QUEDA:
        kp = _corpo(y)
        for nome in kp:
            kp[nome] = [kp[nome][0], kp[nome][1], 0.35]
        baixo.append(kp)

    r = _gerar(tmp_path, QUEDA, corpos=baixo)

    (d,) = r.relatorio["desvios"]
    assert d["baixa_confianca"] is True
    assert any("baixa confiança" in a for a in r.relatorio["avisos"])
    assert "conferir no quadro" in r.caminho_html.read_text(encoding="utf-8")


def test_sem_video_o_relatorio_sai_sem_quadro_de_evidencia_e_avisa(tmp_path):
    pose_json, video = _preparar(tmp_path, QUEDA)
    video.unlink()

    r = gerar(
        perfil_id="internacao_leito",
        config=CONFIG,
        config_relatorio=CONFIG_RELATORIO,
        pose_path=pose_json,
        patient_id=PACIENTE,
        saida=Path("saida"),
        pose_config=POSE_CONFIG,
    )

    (d,) = r.relatorio["desvios"]
    assert d["evidencia"]["disponivel"] is False and d["evidencia"]["uri"] is None
    assert d["evidencia"]["frame_index"] is not None  # o número do quadro continua no relatório
    assert any("sem quadro de evidência" in a for a in r.relatorio["avisos"])
    assert r.relatorio["video"]["sha256"] is None
    assert "indisponível" in r.caminho_html.read_text(encoding="utf-8")


def test_formato_json_ou_html_e_formato_desconhecido(tmp_path):
    so_json = _gerar(tmp_path, formatos=("json",))
    assert so_json.caminho_json.is_file() and so_json.caminho_html is None
    assert not Path("saida/sintetico_relatorio_desvios.html").exists()

    with pytest.raises(ValueError, match="formato"):
        _gerar(tmp_path, formatos=("pdf",))


def test_reexecutar_sobrescreve_sem_acumular(tmp_path):
    _gerar(tmp_path)
    _gerar(tmp_path)
    assert len(list(Path("saida").glob("*_relatorio_desvios.json"))) == 1


def test_perfil_inexistente_e_erro_de_entrada_nao_relatorio(tmp_path):
    with pytest.raises(ValueError, match="perfil 'nao_existe'"):
        _gerar(tmp_path, perfil_id="nao_existe")


# --------------------------------------------------------------------------- #
# Alertas (US19)
# --------------------------------------------------------------------------- #


def _servico_de_alertas(email: NotificadorFake):
    armazenamento = MemoriaStore()
    canais = {"email": email, "webhook": NotificadorFake("webhook")}
    despachante = Despachante(canais, armazenamento, relogio=agora_utc)
    motor = MotorAlertas(
        armazenamento,
        carregar_regras(RAIZ / "config" / "alertas_regras.yaml"),
        despachante.acordar,
        relogio=agora_utc,
    )
    return ServicoAlertas(motor, despachante, segredo_ack="segredo-de-teste"), armazenamento


def test_desvio_de_severidade_alta_chega_ao_motor_de_alertas(tmp_path):
    email = NotificadorFake("email")
    servico, armazenamento = _servico_de_alertas(email)
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
    servico.assinar(barramento)

    r = _gerar(tmp_path, emissor=barramento)
    asyncio.run(servico.processar_pendentes())

    (alerta,) = armazenamento.listar()
    assert alerta.event_type == "mudanca_brusca_ou_queda"
    assert alerta.priority is Severidade.ALTA
    assert alerta.modalities == ["video"]
    assert any(a.event_type == "mudanca_brusca_ou_queda" for a in email.enviados)
    assert barramento.falhas == []
    # e o relatório diz que o desvio foi ao motor
    alertas = r.relatorio["alertas"]
    assert alertas["publicacao_solicitada"] is True and alertas["falhas"] == []
    assert alertas["desvios_severidade_alta"] == 1
    assert [x["event_id"] for x in alertas["enviados_ao_motor"]] == [
        r.relatorio["desvios"][0]["event_id"]
    ]
    assert "enviado(s) ao motor de alertas" in r.caminho_html.read_text(encoding="utf-8")


def test_video_sem_desvios_nao_gera_alerta(tmp_path):
    email = NotificadorFake("email")
    servico, armazenamento = _servico_de_alertas(email)
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
    servico.assinar(barramento)

    r = _gerar(tmp_path, PARADO, emissor=barramento)
    asyncio.run(servico.processar_pendentes())

    assert armazenamento.listar() == [] and email.enviados == []  # sem_achados é info
    assert r.relatorio["alertas"]["enviados_ao_motor"] == []
    assert barramento.total_publicados == 1  # o sem_achados foi ao barramento, para a fusão


def test_sem_emissor_o_relatorio_diz_que_nada_foi_ao_motor(tmp_path):
    r = _gerar(tmp_path)

    alertas = r.relatorio["alertas"]
    assert alertas["publicacao_solicitada"] is False and alertas["enviados_ao_motor"] == []
    assert alertas["desvios_severidade_alta"] == 1
    assert "não solicitada" in alertas["observacao"]


class _EmissorQuebrado:
    def emitir(self, evento):
        raise OSError("disco cheio em C:\\Users\\fulano\\saida\\eventos.jsonl")

    def emitir_varios(self, eventos):
        raise OSError("disco cheio em C:\\Users\\fulano\\saida\\eventos.jsonl")


def test_falha_na_publicacao_nao_impede_o_relatorio_e_fica_registrada(tmp_path):
    r = _gerar(tmp_path, emissor=_EmissorQuebrado())

    assert r.caminho_json.is_file() and r.caminho_html.is_file()
    alertas = r.relatorio["alertas"]
    assert alertas["falhas"] and "OSError" in alertas["falhas"][0]
    assert alertas["enviados_ao_motor"] == []  # não afirma o que não sabe que aconteceu
    assert "pode não ter chegado" in alertas["observacao"] or "falhou" in alertas["observacao"]
    assert "fulano" not in r.caminho_json.read_text(encoding="utf-8")  # caminho removido


def test_falha_de_assinante_do_barramento_entra_no_relatorio(tmp_path):
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")

    def quebrado(_evento):
        raise RuntimeError("assinante com defeito")

    barramento.assinar("defeituoso", quebrado)

    r = _gerar(tmp_path, emissor=barramento)

    falhas = r.relatorio["alertas"]["falhas"]
    assert falhas and "defeituoso" in falhas[0]
    assert r.publicacao.falhas == tuple(falhas)


def test_publicar_sem_emissor_nao_faz_nada():
    assert publicar([], None).solicitada is False


# --------------------------------------------------------------------------- #
# Privacidade
# --------------------------------------------------------------------------- #


def test_relatorio_nao_traz_caminho_paciente_nem_nome_de_arquivo(tmp_path):
    areas = _eventos_areas(tmp_path)
    r = _gerar(tmp_path, QUEDA, eventos_areas_path=areas, bed_id="UTI-07", encounter_id="999")

    texto = r.caminho_json.read_text(encoding="utf-8") + r.caminho_html.read_text(encoding="utf-8")
    assert str(tmp_path) not in texto and tmp_path.name not in texto
    assert PACIENTE not in texto  # nem o pseudônimo: o vídeo é identificado só pelo id e o hash
    assert "UTI-07" not in texto and "leito.avi" not in texto and "leito.json" not in texto
    assert r.relatorio["privacidade"]["redacoes"] == 0
    for caminho in r.relatorio["arquivos"].values():
        assert caminho is None or not Path(caminho).is_absolute()


def test_limpeza_troca_cpf_email_e_caminhos_de_qualquer_nivel():
    dados = {
        "a": "CPF 123.456.789-09 e e-mail maria@exemplo.com.br",
        "b": ["veja C:\\Users\\maria\\video.mp4", {"c": "em /home/maria/video.mp4 ou /Users/maria/x"}],
        "d": 7,
        "e": ("sem nada",),
    }
    limpo, trocas = privacidade.limpar(dados)
    texto = json.dumps(limpo, ensure_ascii=False)
    for proibido in ("123.456.789-09", "maria@", "C:\\\\Users", "/home/maria", "/Users/maria"):
        assert proibido not in texto
    assert trocas == 5 and limpo["d"] == 7 and limpo["e"] == ["sem nada"]


def test_limpeza_nao_mexe_em_hash_nem_em_uuid():
    sha = "a" * 31 + "12345678901" + "b" * 22
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    limpo, trocas = privacidade.limpar({"sha": sha, "id": uuid})
    assert limpo == {"sha": sha, "id": uuid} and trocas == 0


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #


def test_html_e_arquivo_unico_com_linha_do_tempo_tabela_e_evidencia_embutida(tmp_path):
    areas = _eventos_areas(tmp_path, entrada=0.5, saida=5.0)
    r = _gerar(tmp_path, QUEDA, eventos_areas_path=areas)

    pagina = r.caminho_html.read_text(encoding="utf-8")
    assert pagina.startswith("<!DOCTYPE html>") and 'lang="pt-BR"' in pagina
    assert "<script" not in pagina and "http://" not in pagina and "https://" not in pagina
    assert "<link" not in pagina and "@import" not in pagina  # nada externo
    assert pagina.count("<rect") == 2  # uma barra por desvio
    assert "m-alta" in pagina and "m-media" in pagina
    for severidade in ("baixa", "média", "alta"):
        assert f"Severidade {severidade}" in pagina  # legenda: a cor não carrega o sentido sozinha
    assert 'role="img"' in pagina and "<title" in pagina
    assert "data:image/jpeg;base64," in pagina  # o quadro de evidência vai dentro do arquivo
    assert "Mudança brusca ou queda" in pagina and "Presença indevida em área crítica" in pagina
    assert "Modelos e limiares usados" in pagina and "BODY_25" in pagina
    # os limiares aparecem com nome e unidade, não com o nome do campo do código
    assert "Persistência mínima (s)" in pagina and "Confiança mínima da detecção" in pagina
    assert "janela_s" not in pagina and "tempo_max_s" not in pagina


def test_html_escapa_todo_texto_dinamico(tmp_path):
    r = _gerar(tmp_path)
    rel = copy.deepcopy(r.relatorio)
    veneno = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    rel["desvios"][0]["resumo"] = veneno
    rel["video"]["id"] = veneno
    rel["avisos"] = [veneno]
    rel["alertas"]["falhas"] = [veneno]

    pagina = renderizar(rel)

    assert "<script>" not in pagina and "<img src=x" not in pagina
    assert "&lt;script&gt;" in pagina


def test_repeticoes_entram_no_json_e_no_html(tmp_path):
    """A série de repetições da fisioterapia (US08) vai inteira para o relatório."""
    r = _gerar(tmp_path, PARADO)
    repeticoes = tuple(
        Repeticao(
            numero=i,
            inicio_s=i * 2.0,
            fim_s=i * 2.0 + 1.5,
            pico_s=i * 2.0 + 0.7,
            pico=130.0 + i,
            atingiu_alvo=None,
            frame_pico=i * 20,
            referencia=i <= 3,
        )
        for i in range(1, 7)
    )
    resumo = ResumoRepeticoes(
        angulo="ombro_direito",
        alvo=None,
        repeticoes=repeticoes,
        esperadas=(5, 10),
        referencia={"ombro_direito": 131.0},
    )
    execucao = replace(r.execucao, resultado=replace(r.execucao.resultado, repeticoes=resumo))

    rel = construcao.construir(execucao, CFG_REL, gerado_em=agora_utc())

    assert rel["repeticoes"]["contadas"] == 6 and rel["repeticoes"]["dentro_do_esperado"] is True
    pagina = renderizar(rel)
    assert "6 repetições contadas (esperadas de 5 a 10)" in pagina
    assert pagina.count("<td>referência</td>") == 3  # as 3 primeiras só servem de padrão


def test_html_sem_imagens_ainda_renderiza_a_tabela(tmp_path):
    r = _gerar(tmp_path)

    pagina = renderizar(r.relatorio, imagens={})

    assert "data:image" not in pagina and "Desvios detectados" in pagina
    assert "indisponível" in pagina


def test_cada_perfil_do_projeto_gera_relatorio_sem_erro(tmp_path):
    """Todo perfil da US08 precisa produzir relatório (a renderização lê a config inteira)."""
    for perfil in CFG.perfis:
        pasta = tmp_path / perfil
        pasta.mkdir()
        pose_json, _ = _preparar(pasta, QUEDA)
        r = gerar(
            perfil_id=perfil,
            config=CONFIG,
            config_relatorio=CONFIG_RELATORIO,
            pose_path=pose_json if CFG.perfil(perfil).usa_pose else None,
            eventos_areas_path=None if CFG.perfil(perfil).usa_pose else _eventos_areas(pasta),
            source_id=perfil,
            patient_id=PACIENTE,
            saida=Path("saida") / perfil,
            pose_config=POSE_CONFIG,
        )
        assert r.caminho_html.is_file() and r.relatorio["limiares"]["perfil"]["id"] == perfil


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _argv(tmp_path: Path, pose_json: Path, *extra: str) -> list[str]:
    return [
        "--perfil", "internacao_leito",
        "--config", str(CONFIG),
        "--config-relatorio", str(CONFIG_RELATORIO),
        "--pose", str(pose_json),
        "--patient-id", PACIENTE,
        "--inicio-utc", "2026-10-05T19:00:00Z",
        "--pose-config", str(POSE_CONFIG),
        "--saida", "saida",
        *extra,
    ]  # fmt: skip


def test_cli_gera_relatorio_e_publica_no_barramento(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "barramento.jsonl"))
    pose_json, _ = _preparar(tmp_path, QUEDA)

    codigo = main(_argv(tmp_path, pose_json, "--sem-alertas"))

    saida = capsys.readouterr().out
    assert codigo == 0
    assert "1 desvio detectado" in saida and "1 de severidade alta" in saida
    assert Path("saida/sintetico_relatorio_desvios.json").is_file()
    assert Path("saida/sintetico_relatorio_desvios.html").is_file()
    publicados = (tmp_path / "barramento.jsonl").read_text(encoding="utf-8").splitlines()
    assert [EventoAchado.de_json(x).event_type for x in publicados] == ["mudanca_brusca_ou_queda"]


def test_cli_so_json_e_sem_desvios(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "barramento.jsonl"))
    pose_json, _ = _preparar(tmp_path, PARADO)

    codigo = main(_argv(tmp_path, pose_json, "--sem-alertas", "--formato", "json"))

    assert codigo == 0 and "Sem desvios detectados." in capsys.readouterr().out
    assert Path("saida/sintetico_relatorio_desvios.json").is_file()
    assert not Path("saida/sintetico_relatorio_desvios.html").exists()


def test_cli_sai_com_2_quando_a_publicacao_falha_mas_gera_o_relatorio(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(BarramentoLocal, "do_ambiente", classmethod(lambda cls: _EmissorQuebrado()))
    pose_json, _ = _preparar(tmp_path, QUEDA)

    codigo = main(_argv(tmp_path, pose_json, "--sem-alertas"))

    assert codigo == 2
    assert "falha na publicação" in capsys.readouterr().out
    assert Path("saida/sintetico_relatorio_desvios.html").is_file()


def test_cli_sem_o_motor_de_alertas_gera_o_relatorio_e_sai_com_2(tmp_path, monkeypatch, capsys):
    """Regras de alerta ausentes não podem impedir o relatório, mas precisam ser ditas."""
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "barramento.jsonl"))

    def sem_regras(_config):
        raise FileNotFoundError("config/alertas_regras.yaml")

    monkeypatch.setattr("alertas.construir", sem_regras)
    pose_json, _ = _preparar(tmp_path, QUEDA)

    codigo = main(_argv(tmp_path, pose_json))  # sem --sem-alertas: tenta ligar o motor

    assert codigo == 2
    assert "motor de alertas indisponível" in capsys.readouterr().out
    rel = json.loads(Path("saida/sintetico_relatorio_desvios.json").read_text(encoding="utf-8"))
    assert rel["alertas"]["desvios_severidade_alta"] == 1
    assert rel["alertas"]["enviados_ao_motor"] == []  # a queda não chegou a nenhum alerta
    assert "motor de alertas indisponível" in rel["alertas"]["falhas"][0]
    assert Path("saida/sintetico_relatorio_desvios.html").is_file()


def test_cli_sai_com_1_para_entrada_ou_configuracao_invalida(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "barramento.jsonl"))
    pose_json, _ = _preparar(tmp_path, QUEDA)

    assert main(_argv(tmp_path, pose_json, "--sem-alertas", "--config-relatorio", "nao_existe.yaml")) == 1
    ruim = _argv(tmp_path, pose_json, "--sem-alertas")
    ruim[ruim.index("--perfil") + 1] = "nao_existe"
    assert main(ruim) == 1


def test_cli_exige_pose_ou_eventos_de_area(tmp_path):
    with pytest.raises(SystemExit):
        main(["--perfil", "internacao_leito"])
