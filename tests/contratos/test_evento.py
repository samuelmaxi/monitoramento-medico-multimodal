"""Regras do contrato EventoAchado."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from contratos import SCHEMA_VERSION, TAMANHO_MAX_BYTES, EventoAchado, Modalidade, Severidade


def _com(campos: dict, **alteracoes) -> dict:
    return {**campos, **alteracoes}


def _erro(campos: dict) -> str:
    with pytest.raises(ValidationError) as exc:
        EventoAchado.model_validate(campos)
    return str(exc.value)


class TestCamposObrigatoriosDaDoD:
    @pytest.mark.parametrize(
        "campo",
        ["patient_id", "modality", "timestamp", "event_type", "score", "severity", "evidence", "model_version"],
    )
    def test_campo_da_dod_e_obrigatorio(self, campos_validos, campo):
        campos_validos.pop(campo)
        assert campo in _erro(campos_validos)

    def test_evento_valido_preenche_versao_do_schema(self, evento):
        assert evento.schema_version == SCHEMA_VERSION
        assert evento.modality is Modalidade.SINAIS_VITAIS
        assert evento.severity is Severidade.ALTA

    def test_campo_desconhecido_e_rejeitado(self, campos_validos):
        assert "extra" in _erro(_com(campos_validos, nome_paciente="Fulano")).lower()


class TestPatientIdPseudonimizado:
    @pytest.mark.parametrize("valor", ["10000032", "pt_123", "PT_" + "a" * 24, "pt_" + "g" * 24, "João da Silva"])
    def test_id_nao_pseudonimizado_e_rejeitado(self, campos_validos, valor):
        assert "patient_id" in _erro(_com(campos_validos, patient_id=valor))


class TestTimestampUtc:
    def test_serializa_em_iso8601_com_z_e_milissegundos(self, evento):
        dados = evento.para_dict()
        assert dados["timestamp"] == "2026-09-26T17:02:00.000Z"
        assert dados["detected_at"].endswith("Z")

    def test_aceita_mais_zero_zero(self, campos_validos):
        evento = EventoAchado.model_validate(_com(campos_validos, timestamp="2026-09-26T17:02:00+00:00"))
        assert evento.timestamp.tzinfo == timezone.utc

    def test_rejeita_horario_sem_fuso(self, campos_validos):
        assert "timestamp" in _erro(_com(campos_validos, timestamp="2026-09-26T17:02:00"))

    def test_rejeita_offset_diferente_de_utc(self, campos_validos):
        assert "UTC" in _erro(_com(campos_validos, timestamp="2026-09-26T14:02:00-03:00"))

    def test_rejeita_datetime_python_ingenuo(self, campos_validos):
        assert "timestamp" in _erro(_com(campos_validos, timestamp=datetime(2026, 9, 26, 17, 2)))


class TestScoreESeveridade:
    @pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
    def test_score_nos_limites(self, campos_validos, score):
        assert EventoAchado.model_validate(_com(campos_validos, score=score)).score == score

    @pytest.mark.parametrize("score", [-0.01, 1.01, float("nan"), float("inf")])
    def test_score_fora_de_0_a_1(self, campos_validos, score):
        assert "score" in _erro(_com(campos_validos, score=score))

    def test_severidade_fora_do_enum(self, campos_validos):
        assert "severity" in _erro(_com(campos_validos, severity="critica"))

    def test_sem_achados_exige_info(self, campos_validos):
        assert "info" in _erro(_com(campos_validos, event_type="sem_achados", severity="baixa"))

    def test_alertavel_a_partir_de_media(self, campos_validos):
        assert EventoAchado.model_validate(_com(campos_validos, severity="media")).alertavel
        assert not EventoAchado.model_validate(_com(campos_validos, severity="baixa")).alertavel


class TestCatalogo:
    def test_event_type_desconhecido(self, campos_validos):
        assert "catálogo" in _erro(_com(campos_validos, event_type="qualquer_coisa"))

    def test_event_type_de_outra_modalidade(self, campos_validos):
        erro = _erro(_com(campos_validos, event_type="termo_critico"))
        assert "não pertence à modalidade 'sinais_vitais'" in erro

    def test_modalidade_invalida(self, campos_validos):
        assert "modality" in _erro(_com(campos_validos, modality="imagem"))


class TestFusao:
    def test_fusao_exige_eventos_relacionados(self, campos_validos):
        campos = _com(campos_validos, modality="fusao", event_type="risco_multimodal")
        assert "related_event_ids" in _erro(campos)

    def test_fusao_valida_com_relacionados(self, campos_validos):
        campos = _com(campos_validos, modality="fusao", event_type="risco_multimodal",
                      related_event_ids=[str(uuid.uuid4()), str(uuid.uuid4())])
        assert len(EventoAchado.model_validate(campos).related_event_ids) == 2

    def test_evento_nao_referencia_a_si_mesmo(self, campos_validos):
        campos = _com(campos_validos, related_event_ids=[campos_validos["event_id"]])
        assert "próprio event_id" in _erro(campos)


class TestJanela:
    def test_timestamp_dentro_da_janela(self, campos_validos):
        campos = _com(campos_validos, window={"start": "2026-09-26T16:57:00Z", "end": "2026-09-26T17:02:00Z"})
        assert EventoAchado.model_validate(campos).window is not None

    def test_timestamp_fora_da_janela(self, campos_validos):
        campos = _com(campos_validos, window={"start": "2026-09-26T16:00:00Z", "end": "2026-09-26T16:30:00Z"})
        assert "dentro de window" in _erro(campos)

    def test_janela_invertida(self, campos_validos):
        campos = _com(campos_validos, window={"start": "2026-09-26T17:05:00Z", "end": "2026-09-26T17:00:00Z"})
        assert "window.end" in _erro(campos)


class TestEvidencia:
    def test_summary_obrigatorio(self, campos_validos):
        assert "summary" in _erro(_com(campos_validos, evidence={"features": {}}))

    @pytest.mark.parametrize("texto", ["CPF 123.456.789-09 internado", "contato: fulano@exemplo.com"])
    def test_bloqueia_pii_no_summary(self, campos_validos, texto):
        assert "dados pessoais" in _erro(_com(campos_validos, evidence={"summary": texto}))

    def test_bloqueia_pii_nas_features(self, campos_validos):
        evidencia = {"summary": "ok ok", "features": {"cpf": "12345678909"}}
        assert "dados pessoais" in _erro(_com(campos_validos, evidence=evidencia))

    def test_feature_numerica_nao_vira_texto_nem_vice_versa(self, campos_validos):
        evidencia = {"summary": "ok ok", "features": {"a": "42", "b": 42, "c": 4.2, "d": True}}
        features = EventoAchado.model_validate(_com(campos_validos, evidence=evidencia)).evidence.features
        assert features == {"a": "42", "b": 42, "c": 4.2, "d": True}
        assert type(features["a"]) is str and type(features["d"]) is bool

    @pytest.mark.parametrize("uri", ["saida/video/frame.jpg", "s3://bucket/a.wav", "https://x.org/r.pdf"])
    def test_artefato_aceita_caminho_relativo_ou_uri(self, campos_validos, uri):
        evidencia = {"summary": "ok ok", "artifacts": [{"kind": "frame", "uri": uri}]}
        assert EventoAchado.model_validate(_com(campos_validos, evidence=evidencia)).evidence.artifacts[0].uri == uri

    @pytest.mark.parametrize("uri", ["/tmp/frame.jpg", "C:\\Users\\x\\frame.jpg", "../fora/frame.jpg", ""])
    def test_artefato_recusa_caminho_absoluto_ou_fora_do_repo(self, campos_validos, uri):
        evidencia = {"summary": "ok ok", "artifacts": [{"kind": "frame", "uri": uri}]}
        assert "uri" in _erro(_com(campos_validos, evidence=evidencia))

    def test_artefato_com_intervalo_invertido(self, campos_validos):
        artefato = {"kind": "clip", "uri": "s3://b/c.mp4", "start_ms": 5000, "end_ms": 1000}
        assert "end_ms" in _erro(_com(campos_validos, evidence={"summary": "ok ok", "artifacts": [artefato]}))

    def test_evento_acima_de_256kb_e_rejeitado(self, campos_validos):
        features = {f"f{i}": "x" * 200 for i in range(50)}
        artefatos = [{"kind": "outro", "uri": "s3://b/" + "k" * 14_000} for _ in range(20)]
        evidencia = {"summary": "grande", "features": features, "artifacts": artefatos}
        assert str(TAMANHO_MAX_BYTES) in _erro(_com(campos_validos, evidence=evidencia))


class TestModelVersion:
    @pytest.mark.parametrize("valor", ["anomalias-sinais@0.1.0", "fusao-late-rules@1.2.0+exp.3", "yolov8n@8.3.40"])
    def test_formatos_aceitos(self, campos_validos, valor):
        assert EventoAchado.model_validate(_com(campos_validos, model_version=valor)).model_version == valor

    @pytest.mark.parametrize("valor", ["0.1.0", "Anomalias@1", "modulo@", "modulo 1.0"])
    def test_formatos_rejeitados(self, campos_validos, valor):
        assert "model_version" in _erro(_com(campos_validos, model_version=valor))


class TestApiDeConveniencia:
    def test_criar_preenche_id_e_detected_at(self, campos_validos):
        campos_validos.pop("event_id")
        campos_validos.pop("detected_at")
        antes = datetime.now(timezone.utc)
        evento = EventoAchado.criar(**campos_validos)
        assert isinstance(evento.event_id, uuid.UUID)
        assert antes - timedelta(seconds=1) <= evento.detected_at <= datetime.now(timezone.utc)

    def test_ida_e_volta_json(self, evento):
        assert EventoAchado.de_json(evento.para_json()) == evento

    def test_evento_e_imutavel(self, evento):
        with pytest.raises(ValidationError):
            evento.score = 0.1
