"""US12/US14 ponta a ponta: do arquivo de áudio aos eventos do contrato e ao motor de alertas."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from alertas import Despachante, MotorAlertas, ServicoAlertas, carregar_regras
from alertas.armazenamento import MemoriaStore
from alertas.notificadores import NotificadorFake
from audio.ingestao.erros import AudioVazio
from audio.ingestao.lote import ingerir
from audio.vocal.analise import EntradaAnalise, analisar
from audio.vocal.baseline import RepositorioBaseline
from audio.vocal.erros import ErroDeAnalise, SemFalaDoPaciente
from audio.vocal.injecao import (
    Frase,
    ParametrosVoz,
    fala_normal,
    montar_fala,
    sintetizar_voz,
    transcricao_sintetica,
)
from audio.vocal.respiratorio import DetectorRespiratorio
from audio.vocal.run import processar
from contratos import BarramentoLocal, ChavePseudonimizacaoAusente, Severidade, agora_utc
from contratos.testing import assert_eventos_validos

from .conftest import RAIZ
from .test_vocal_respiratorio import NOMES, sessao_com

TAXA = 16000
GRAVADO_EM = datetime(2026, 10, 1, 13, 0, 0, tzinfo=UTC)
PACIENTE = "10000032"


@pytest.fixture(autouse=True)
def _dentro_de_tmp(tmp_path, monkeypatch):
    """Saídas relativas ao diretório de trabalho: os URIs de artefato do contrato são relativos."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def gravar(tmp_path):
    def _gravar(nome, sinal):
        caminho = tmp_path / "entrada" / nome
        caminho.parent.mkdir(exist_ok=True)
        sf.write(str(caminho), sinal, TAXA, subtype="PCM_16")
        return caminho

    return _gravar


def rodar(origem, cfg_ingestao, cfg_vocal, falas=None, **kw):
    """Roda a ingestão + análise. ``falas`` (do gerador) vira o JSON da US11."""
    kw.setdefault("patient_id", PACIENTE)
    kw.setdefault("recorded_at", GRAVADO_EM)
    kw.setdefault("saida", Path("saida/audio"))
    if falas is not None and "transcricao" not in kw:
        kw["transcricao"] = transcricao_sintetica(falas)
    return processar(origem, cfg_ingestao=cfg_ingestao, cfg=cfg_vocal, **kw)


def detector_com(cfg_vocal, **por_classe):
    return DetectorRespiratorio(sessao_com(**por_classe), NOMES, cfg_vocal.respiratorio)


def servico_de_alertas(email, webhook=None):
    """Motor de alertas completo, com as regras do projeto e canais falsos (alta usa os dois)."""
    armazenamento = MemoriaStore()
    canais = {"email": email, "webhook": webhook or NotificadorFake("webhook")}
    despachante = Despachante(canais, armazenamento, relogio=agora_utc)
    motor = MotorAlertas(
        armazenamento, carregar_regras(RAIZ / "config" / "alertas_regras.yaml"),
        despachante.acordar, relogio=agora_utc)
    return ServicoAlertas(motor, despachante, segredo_ack="segredo-de-teste"), armazenamento


# --- voz normal ----------------------------------------------------------------------------- #


def test_voz_normal_gera_so_sem_achados_e_a_classe_normal(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()

    execucao = rodar(gravar("normal.wav", sinal), cfg_ingestao, cfg_vocal, falas)

    r = execucao.resultado
    assert r.classe == "normal"
    assert [e.event_type for e in r.eventos] == ["sem_achados"]
    assert r.eventos[0].severity is Severidade.INFO
    assert r.eventos[0].score == 0.0
    assert r.relatorio["classes"] == ["normal"]
    assert r.relatorio["respiratorio"]["disponivel"] is False
    assert r.relatorio["alertaveis"] == 0


def test_arquivos_gerados_e_eventos_validos_no_contrato(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()

    execucao = rodar(gravar("normal.wav", sinal), cfg_ingestao, cfg_vocal, falas)

    pasta = execucao.caminho_eventos.parent
    for nome in ("convertido.wav", "features.wav", "metadados.json", "eventos.jsonl",
                 "relatorio.json"):
        assert (pasta / nome).is_file(), nome
    eventos = assert_eventos_validos(execucao.caminho_eventos, modalidade="audio")
    assert [e.event_id for e in eventos] == [e.event_id for e in execucao.resultado.eventos]
    relatorio = json.loads(execucao.caminho_relatorio.read_text(encoding="utf-8"))
    assert relatorio["aviso"] == "Indicativo para avaliação clínica; não é diagnóstico."
    assert relatorio["model_version"] == "audio-achados@0.1.0"


def test_o_audio_original_nao_e_alterado(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()
    original = gravar("normal.wav", sinal)
    antes = hashlib.sha256(original.read_bytes()).hexdigest()

    execucao = rodar(original, cfg_ingestao, cfg_vocal, falas)

    assert hashlib.sha256(original.read_bytes()).hexdigest() == antes
    assert execucao.ingestao.metadados.original_preservado is True


def test_paciente_nunca_aparece_com_o_id_de_origem(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()

    execucao = rodar(gravar("normal.wav", sinal), cfg_ingestao, cfg_vocal, falas)

    pasta = execucao.caminho_eventos.parent
    for arquivo in pasta.iterdir():
        if arquivo.suffix in {".json", ".jsonl"}:
            assert PACIENTE not in arquivo.read_text(encoding="utf-8"), arquivo.name
    assert re.fullmatch(r"pt_[0-9a-f]{24}", execucao.resultado.patient_id)


# --- dificuldade respiratória → alerta ------------------------------------------------------ #


def audio_com_dificuldade_respiratoria():
    """Frases curtas e lentas separadas por pausas longas."""
    return montar_fala([Frase(1.5, 3, 1.8) for _ in range(4)], ParametrosVoz())


def test_dificuldade_respiratoria_alta_explica_a_decisao_e_diz_que_nao_e_diagnostico(
    gravar, cfg_ingestao, cfg_vocal
):
    sinal, falas = audio_com_dificuldade_respiratoria()
    detector = detector_com(cfg_vocal, Cough={2: 0.8, 3: 0.7}, Breathing={9: 0.5, 10: 0.6})

    execucao = rodar(gravar("dispneia.wav", sinal), cfg_ingestao, cfg_vocal, falas,
                     detector=detector)

    por_tipo = {e.event_type: e for e in execucao.resultado.eventos}
    achado = por_tipo["dificuldade_respiratoria"]
    assert achado.severity is Severidade.ALTA
    assert achado.alertavel
    ev = achado.evidence
    assert ev.summary.startswith("Indicativo para avaliação clínica, não é diagnóstico")
    assert ev.features["aviso"] == "Indicativo para avaliação clínica; não é diagnóstico."
    for criterio in ("eventos_respiratorios", "pausas_altas", "fala_lenta"):
        assert criterio in ev.features["regras_acionadas"]
    assert ev.features["criterios_atendidos"] == 3
    assert ev.features["proporcao_pausas"] > cfg_vocal.limiares.proporcao_pausas_max
    assert ev.thresholds["versao_limiares"] == cfg_vocal.versao
    assert ev.thresholds["proporcao_pausas_max"] == cfg_vocal.limiares.proporcao_pausas_max
    assert ev.thresholds["hnr_db_min"] == 20.0
    assert {"praat", "parselmouth", "yamnet", "azure-speech"} <= set(ev.models)
    assert execucao.resultado.classe == "dificuldade_respiratoria"
    assert "sem_achados" not in por_tipo


def test_eventos_respiratorios_saem_com_score_e_trecho(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = audio_com_dificuldade_respiratoria()
    detector = detector_com(cfg_vocal, Cough={2: 0.8, 3: 0.7})

    execucao = rodar(gravar("tosse.wav", sinal), cfg_ingestao, cfg_vocal, falas, detector=detector)

    (tosse,) = [e for e in execucao.resultado.eventos if e.event_type == "evento_respiratorio"]
    assert tosse.score == pytest.approx(0.8, abs=1e-4)
    assert tosse.evidence.features["tipo"] == "tosse"
    assert tosse.evidence.features["classe_audioset"] == "Cough"
    assert tosse.timestamp == GRAVADO_EM.replace(microsecond=0) + (tosse.timestamp - GRAVADO_EM
                                                                    ).__class__(seconds=0) \
        or tosse.timestamp > GRAVADO_EM
    assert tosse.window.start <= tosse.timestamp <= tosse.window.end
    (segmento,) = [a for a in tosse.evidence.artifacts if a.kind == "audio_segment"]
    assert segmento.start_ms < segmento.end_ms


def test_achado_alto_publicado_no_barramento_vira_alerta_com_o_aviso(
    gravar, cfg_ingestao, cfg_vocal, tmp_path
):
    sinal, falas = audio_com_dificuldade_respiratoria()
    detector = detector_com(cfg_vocal, Cough={2: 0.8, 3: 0.7}, Breathing={9: 0.5, 10: 0.6})
    email = NotificadorFake("email")
    servico, armazenamento = servico_de_alertas(email)
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
    servico.assinar(barramento)

    rodar(gravar("dispneia.wav", sinal), cfg_ingestao, cfg_vocal, falas, detector=detector,
          emissor=barramento)
    asyncio.run(servico.processar_pendentes())

    alertas = {a.event_type: a for a in armazenamento.listar()}
    alerta = alertas["dificuldade_respiratoria"]
    assert alerta.priority is Severidade.ALTA
    assert alerta.modalities == ["audio"]
    assert "Indicativo para avaliação clínica" in alerta.summary
    assert alerta.evidence["features"]["aviso"].startswith("Indicativo")
    assert any(a.event_type == "dificuldade_respiratoria" for a in email.enviados)
    assert barramento.falhas == []
    assert barramento.total_publicados == len(
        (tmp_path / "eventos.jsonl").read_text(encoding="utf-8").splitlines())


def test_voz_normal_nao_gera_alerta(gravar, cfg_ingestao, cfg_vocal, tmp_path):
    sinal, falas = fala_normal()
    email = NotificadorFake("email")
    servico, armazenamento = servico_de_alertas(email)
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
    servico.assinar(barramento)

    rodar(gravar("normal.wav", sinal), cfg_ingestao, cfg_vocal, falas, emissor=barramento)
    asyncio.run(servico.processar_pendentes())

    assert armazenamento.listar() == []  # sem_achados é info e nunca vira alerta
    assert email.enviados == []


def test_sem_o_modelo_yamnet_a_analise_segue_e_o_relatorio_diz_isso(
    gravar, cfg_ingestao, cfg_vocal
):
    sinal, falas = audio_com_dificuldade_respiratoria()

    execucao = rodar(gravar("dispneia.wav", sinal), cfg_ingestao, cfg_vocal, falas, detector=None)

    r = execucao.resultado
    assert r.relatorio["respiratorio"] == {"disponivel": False, "eventos": []}
    achado = next(e for e in r.eventos if e.event_type == "dificuldade_respiratoria")
    assert achado.severity is Severidade.MEDIA  # 2 critérios; o respiratório não pôde ser contado
    assert "eventos_respiratorios" in achado.evidence.features["criterios_nao_avaliados"]
    assert "yamnet" not in achado.evidence.models


# --- voz do médico, transcrição e papel ----------------------------------------------------- #


def test_voz_e_eventos_do_medico_nao_entram_nos_achados_do_paciente(
    gravar, cfg_ingestao, cfg_vocal
):
    paciente = sintetizar_voz(2.5, ParametrosVoz(f0_hz=140.0, variacao_f0_st=0.0), semente=1)
    medico = sintetizar_voz(2.5, ParametrosVoz(f0_hz=220.0, variacao_f0_st=0.0), semente=2)
    pausa = np.zeros(TAXA // 2, np.float32)
    sinal = np.concatenate([pausa, paciente, pausa, medico, pausa])
    transcricao = {"medico_falante": 2, "falas": [
        {"texto": "a b c", "falante": 1, "offset_ms": 500, "duracao_ms": 2500, "palavras": []},
        {"texto": "d e f", "falante": 2, "offset_ms": 3500, "duracao_ms": 2500, "palavras": []},
    ]}
    # o quadro 8 (centro ~4,3 s) cai na fala do médico: tosse dele não é do paciente
    detector = detector_com(cfg_vocal, Cough={8: 0.95})

    execucao = rodar(gravar("consulta.wav", sinal), cfg_ingestao, cfg_vocal,
                     transcricao=transcricao, medico_falante=2, detector=detector)

    bio = execucao.resultado.biomarcadores
    assert bio.f0_media_hz == pytest.approx(140.0, abs=4.0)
    assert execucao.resultado.respiratorio.eventos == ()
    assert execucao.resultado.segmentos.papel_assumido is False


def test_dois_falantes_sem_dizer_quem_e_o_medico_e_recusado(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()
    transcricao = transcricao_sintetica(falas)
    transcricao["falas"][1]["falante"] = 2  # uma segunda voz, sem dizer qual é a do médico

    with pytest.raises(ErroDeAnalise, match="medico_falante"):
        rodar(gravar("a.wav", sinal), cfg_ingestao, cfg_vocal, transcricao=transcricao)


def test_transcrever_usa_o_servico_com_o_paciente_pseudonimizado(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()
    chamadas = []

    def transcritor(convertido, **kw):
        chamadas.append((convertido, kw))
        return transcricao_sintetica(falas)

    execucao = rodar(gravar("a.wav", sinal), cfg_ingestao, cfg_vocal, transcrever=True,
                     transcritor=transcritor, medico_falante=None)

    (convertido, kw), = chamadas
    assert convertido.name == "convertido.wav"  # o WAV PCM 16 kHz mono vai ao Azure Speech
    assert kw["patient_id"].startswith("pt_")  # o ID de origem nunca vai para o JSON em disco
    assert PACIENTE not in kw["patient_id"]
    assert kw["recorded_at"] == "2026-10-01T13:00:00Z"
    assert kw["audio_id"] == "a"
    assert execucao.resultado.segmentos.fonte == "transcricao"
    assert execucao.caminho_transcricao.name == "convertido.json"


def test_sem_transcricao_usa_vad_e_nao_ha_taxa_de_fala(gravar, cfg_ingestao, cfg_vocal):
    sinal, _ = fala_normal()

    execucao = rodar(gravar("a.wav", sinal), cfg_ingestao, cfg_vocal)

    r = execucao.resultado
    assert r.segmentos.fonte == "vad"
    assert r.biomarcadores.taxa_fala_pps is None
    assert r.relatorio["fonte_segmentos"] == "vad"
    assert any("sem transcrição" in n for n in r.relatorio["notas"])


# --- baseline do paciente ------------------------------------------------------------------- #


def test_apos_alguns_audios_um_desvio_gera_desvio_baseline_vocal(gravar, cfg_ingestao, cfg_vocal):
    repo = RepositorioBaseline("saida/audio/baseline")
    for i in range(cfg_vocal.baseline.audios_minimos + 1):
        sinal, falas = fala_normal(semente=i)
        rodar(gravar(f"n{i}.wav", sinal), cfg_ingestao, cfg_vocal, falas, repositorio=repo)

    alterado, falas = montar_fala(
        [Frase(2.4, 8, 0.5) for _ in range(4)],
        ParametrosVoz(jitter_pct=2.0, shimmer_pct=8.0, snr_db=10.0, f0_hz=210.0), semente=99)
    execucao = rodar(gravar("alterado.wav", alterado), cfg_ingestao, cfg_vocal, falas,
                     repositorio=repo)

    tipos = {e.event_type: e for e in execucao.resultado.eventos}
    desvio = tipos["desvio_baseline_vocal"]
    assert desvio.severity in (Severidade.BAIXA, Severidade.MEDIA)
    assert desvio.evidence.features["n_metricas_fora"] >= 3
    assert "f0_media_hz" in desvio.evidence.features["metricas_fora_da_baseline"]
    assert desvio.evidence.features["baseline_audios"] == cfg_vocal.baseline.audios_minimos + 1
    assert desvio.evidence.thresholds["z_minimo"] == cfg_vocal.baseline.z_minimo
    assert "sem_achados" not in tipos


def test_audio_normal_entra_na_baseline_e_o_alterado_por_classe_nao(
    gravar, cfg_ingestao, cfg_vocal
):
    repo = RepositorioBaseline("saida/audio/baseline")
    sinal, falas = fala_normal()
    execucao = rodar(gravar("n.wav", sinal), cfg_ingestao, cfg_vocal, falas, repositorio=repo)
    paciente = execucao.resultado.patient_id
    assert [r.audio_id for r in repo.carregar(paciente)] == ["n"]

    ruim, falas_ruins = audio_com_dificuldade_respiratoria()
    rodar(gravar("ruim.wav", ruim), cfg_ingestao, cfg_vocal, falas_ruins, repositorio=repo,
          detector=detector_com(cfg_vocal, Cough={2: 0.8, 3: 0.7}))

    assert [r.audio_id for r in repo.carregar(paciente)] == ["n"]  # o achado de classe ficou de fora


def test_rodar_o_mesmo_audio_de_novo_nao_duplica_na_baseline(gravar, cfg_ingestao, cfg_vocal):
    repo = RepositorioBaseline("saida/audio/baseline")
    sinal, falas = fala_normal()
    origem = gravar("n.wav", sinal)

    primeira = rodar(origem, cfg_ingestao, cfg_vocal, falas, repositorio=repo)
    segunda = rodar(origem, cfg_ingestao, cfg_vocal, falas, repositorio=repo)

    assert len(repo.carregar(primeira.resultado.patient_id)) == 1
    assert primeira.resultado.eventos[0].event_id != segunda.resultado.eventos[0].event_id


def test_sem_atualizar_baseline_nao_grava(gravar, cfg_ingestao, cfg_vocal):
    repo = RepositorioBaseline("saida/audio/baseline")
    sinal, falas = fala_normal()

    execucao = rodar(gravar("n.wav", sinal), cfg_ingestao, cfg_vocal, falas, repositorio=repo,
                     atualizar_baseline=False)

    assert repo.carregar(execucao.resultado.patient_id) == []


# --- qualidade do áudio de origem ------------------------------------------------------------ #


def test_audio_comprimido_leva_ressalva_no_achado_e_no_relatorio(
    tmp_path, cfg_ingestao, cfg_vocal
):
    """Opus/Vorbis (ex.: áudio do WhatsApp) distorce jitter, shimmer e HNR: o achado avisa."""
    sinal, falas = fala_normal()
    comprimido = tmp_path / "entrada" / "voz.ogg"
    comprimido.parent.mkdir()
    sf.write(str(comprimido), sinal, TAXA, format="OGG", subtype="VORBIS")

    execucao = rodar(comprimido, cfg_ingestao, cfg_vocal, falas)

    features = execucao.resultado.eventos[0].evidence.features
    assert features["audio_comprimido"] is True
    assert features["formato_origem"] == "ogg"
    notas = execucao.resultado.relatorio["notas"]
    assert any("comprimido com perdas (ogg)" in n and "superestimados" in n for n in notas)


def test_audio_sem_perdas_nao_leva_a_ressalva(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()

    execucao = rodar(gravar("voz.wav", sinal), cfg_ingestao, cfg_vocal, falas)

    features = execucao.resultado.eventos[0].evidence.features
    assert features["audio_comprimido"] is False
    assert features["formato_origem"] == "wav"
    assert not any("comprimido" in n for n in execucao.resultado.relatorio["notas"])


# --- tempo, contexto e erros ---------------------------------------------------------------- #


def test_timestamp_e_janela_vem_da_gravacao_mais_a_posicao_na_fala(gravar, cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()

    execucao = rodar(gravar("a.wav", sinal), cfg_ingestao, cfg_vocal, falas)

    evento = execucao.resultado.eventos[0]
    bio = execucao.resultado.biomarcadores
    assert (evento.timestamp - GRAVADO_EM).total_seconds() == pytest.approx(bio.inicio_s, abs=1e-3)
    assert (evento.window.end - GRAVADO_EM).total_seconds() == pytest.approx(bio.fim_s, abs=1e-3)
    assert bio.inicio_s == pytest.approx(0.3, abs=0.1)  # a fala começa depois de 0,3 s de silêncio
    (segmento,) = [a for a in evento.evidence.artifacts if a.kind == "audio_segment"]
    assert segmento.uri == "saida/audio/a/convertido.wav"
    assert segmento.start_ms == pytest.approx(bio.inicio_s * 1000, abs=2)
    assert any(a.kind == "relatorio" and a.uri == "saida/audio/a/relatorio.json"
               for a in evento.evidence.artifacts)


def test_leito_e_internacao_vao_para_o_contexto_com_a_internacao_pseudonimizada(
    gravar, cfg_ingestao, cfg_vocal
):
    sinal, falas = fala_normal()

    execucao = rodar(gravar("a.wav", sinal), cfg_ingestao, cfg_vocal, falas, bed_id="leito_07",
                     encounter_id="20000099")

    contexto = execucao.resultado.eventos[0].context
    assert contexto.source_id == "a"
    assert contexto.bed_id == "leito_07"
    assert re.fullmatch(r"enc_[0-9a-f]{24}", contexto.encounter_id)


def test_sem_pseudonym_key_falha_antes_de_converter_qualquer_coisa(
    gravar, cfg_ingestao, cfg_vocal, monkeypatch
):
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    sinal, falas = fala_normal()

    with pytest.raises(ChavePseudonimizacaoAusente):
        rodar(gravar("a.wav", sinal), cfg_ingestao, cfg_vocal, falas)

    assert not Path("saida").exists()


def test_audio_so_de_silencio_e_recusado_na_ingestao(gravar, cfg_ingestao, cfg_vocal):
    with pytest.raises(AudioVazio):
        rodar(gravar("mudo.wav", np.zeros(TAXA * 3, np.float32)), cfg_ingestao, cfg_vocal)


def test_so_ruido_de_ambiente_nao_tem_fala_do_paciente(gravar, cfg_ingestao, cfg_vocal):
    ruido = (0.0005 * np.random.default_rng(0).standard_normal(TAXA * 3)).astype(np.float32)

    with pytest.raises(SemFalaDoPaciente):
        rodar(gravar("ruido.wav", ruido), cfg_ingestao, cfg_vocal)


def test_recorded_at_sem_fuso_e_recusado(gravar, cfg_ingestao, cfg_vocal):
    sinal, _ = fala_normal()
    caminho = gravar("a.wav", sinal)
    ingestao = ingerir(caminho, "saida/audio", cfg_ingestao)

    with pytest.raises(ErroDeAnalise, match="UTC"):
        analisar(ingestao.convertido,
                 EntradaAnalise("a", PACIENTE, datetime(2026, 10, 1, 13, 0, 0)),
                 cfg_ingestao, cfg_vocal)


def test_wav_que_nao_e_16khz_mono_e_recusado_pela_analise(tmp_path, cfg_ingestao, cfg_vocal):
    estereo = tmp_path / "estereo.wav"
    sf.write(str(estereo), np.zeros((TAXA, 2), np.float32) + 0.1, 44100)

    with pytest.raises(ErroDeAnalise, match="mono"):
        analisar(estereo, EntradaAnalise("a", PACIENTE, GRAVADO_EM), cfg_ingestao, cfg_vocal)
