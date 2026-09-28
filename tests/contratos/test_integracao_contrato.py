"""Exemplos por modalidade, pseudonimização, emissores e CLI de validação."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from contratos import (
    TIPOS_DE_EVENTO,
    BarramentoLocal,
    ChavePseudonimizacaoAusente,
    EmissorJsonl,
    EmissorMemoria,
    EventoAchado,
    Modalidade,
    e_pseudonimo_de_paciente,
    filtro,
    pseudonimizar_internacao,
    pseudonimizar_paciente,
)
from contratos.testing import assert_eventos_validos
from contratos.validar import main as validar_cli

from .constantes import CHAVE_TESTE, DIR_EXEMPLOS


class TestExemplos:
    def test_ha_exemplo_para_toda_modalidade(self, exemplos):
        modalidades = {EventoAchado.model_validate(d).modality for d in exemplos.values()}
        assert modalidades == set(Modalidade)

    def test_exemplos_validam_no_pydantic(self, exemplos):
        for nome, dados in exemplos.items():
            EventoAchado.model_validate(dados)

    def test_fusao_referencia_eventos_existentes_do_mesmo_paciente(self, exemplos):
        eventos = {str(e.event_id): e for e in map(EventoAchado.model_validate, exemplos.values())}
        fusao = next(e for e in eventos.values() if e.modality is Modalidade.FUSAO)
        for relacionado in fusao.related_event_ids:
            origem = eventos[str(relacionado)]
            assert origem.patient_id == fusao.patient_id
            assert origem.modality is not Modalidade.FUSAO

    def test_todo_event_type_do_catalogo_tem_descricao(self):
        for modalidade, tipos in TIPOS_DE_EVENTO.items():
            assert tipos, modalidade
            assert all(descricao.strip() for descricao in tipos.values())


class TestPseudonimizacao:
    def test_deterministico_e_no_formato(self):
        a = pseudonimizar_paciente(10000032, chave=CHAVE_TESTE)
        assert a == pseudonimizar_paciente("10000032", chave=CHAVE_TESTE)
        assert e_pseudonimo_de_paciente(a)

    def test_chave_diferente_gera_pseudonimo_diferente(self):
        outra = "outra-chave-de-teste-com-mais-de-32-bytes-0"
        assert pseudonimizar_paciente(1, chave=CHAVE_TESTE) != pseudonimizar_paciente(1, chave=outra)

    def test_paciente_e_internacao_nao_colidem(self):
        assert pseudonimizar_paciente(7, chave=CHAVE_TESTE)[3:] != pseudonimizar_internacao(7, chave=CHAVE_TESTE)[4:]

    def test_le_chave_do_ambiente(self):
        assert pseudonimizar_paciente(1) == pseudonimizar_paciente(1, chave=CHAVE_TESTE)

    def test_sem_chave_falha(self, monkeypatch):
        monkeypatch.delenv("PSEUDONYM_KEY")
        with pytest.raises(ChavePseudonimizacaoAusente):
            pseudonimizar_paciente(1)

    def test_chave_curta_falha(self):
        with pytest.raises(ChavePseudonimizacaoAusente):
            pseudonimizar_paciente(1, chave="curta")

    def test_identificador_vazio_falha(self):
        with pytest.raises(ValueError):
            pseudonimizar_paciente("  ", chave=CHAVE_TESTE)


class TestEmissores:
    def test_memoria_valida_dict(self, campos_validos):
        emissor = EmissorMemoria()
        emissor.emitir(campos_validos)
        assert isinstance(emissor.eventos[0], EventoAchado)

    def test_emissor_recusa_evento_fora_do_contrato(self, campos_validos):
        with pytest.raises(Exception):
            EmissorMemoria().emitir({**campos_validos, "score": 2})

    def test_jsonl_grava_uma_linha_por_evento(self, tmp_path, evento):
        caminho = tmp_path / "saida" / "eventos.jsonl"
        emissor = EmissorJsonl(caminho)
        emissor.emitir_varios([evento, evento])
        linhas = caminho.read_text(encoding="utf-8").splitlines()
        assert len(linhas) == 2
        assert EventoAchado.de_json(linhas[0]) == evento
        assert_eventos_validos(caminho, modalidade="sinais_vitais", minimo=2)


def _variar(evento: EventoAchado, **campos) -> EventoAchado:
    dados = evento.model_dump()
    dados.update(event_id=uuid.uuid4(), **campos)
    return EventoAchado.model_validate(dados)


class TestBarramentoLocal:
    def test_grava_jsonl_e_entrega_na_ordem(self, tmp_path, evento):
        barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
        recebidos = []
        barramento.assinar("coletor", recebidos.append)
        eventos = [_variar(evento) for _ in range(3)]
        barramento.emitir_varios(eventos)
        assert [e.event_id for e in recebidos] == [e.event_id for e in eventos]
        assert len(barramento.caminho.read_text(encoding="utf-8").splitlines()) == 3
        assert barramento.total_publicados == 3

    def test_filtros_da_fusao_e_dos_alertas(self, evento):
        barramento = BarramentoLocal()
        fusao, alertas = [], []
        barramento.assinar("fusao", fusao.append, filtro(exceto_modalidades=["fusao"]))
        barramento.assinar("alertas", alertas.append, filtro(severidade_minima="media"))
        baixo = _variar(evento, severity="baixa")
        alto = _variar(evento, severity="alta")
        risco = _variar(evento, modality="fusao", event_type="risco_multimodal",
                        severity="alta", related_event_ids=[alto.event_id])
        barramento.emitir_varios([baixo, alto, risco])
        assert [e.event_id for e in fusao] == [baixo.event_id, alto.event_id]
        assert [e.event_id for e in alertas] == [alto.event_id, risco.event_id]

    def test_filtro_por_modalidade(self, evento):
        so_sinais = filtro(modalidades=[Modalidade.SINAIS_VITAIS])
        assert so_sinais(evento)
        assert not filtro(modalidades=["video"])(evento)

    def test_assinante_pode_publicar_sem_recursao(self, evento):
        """A fusão devolve risco_multimodal ao barramento durante a entrega."""
        barramento = BarramentoLocal()
        ordem = []

        def fusao(e: EventoAchado) -> None:
            ordem.append(("fusao", e.modality.value))
            barramento.emitir(_variar(e, modality="fusao", event_type="risco_multimodal",
                                      related_event_ids=[e.event_id]))

        barramento.assinar("fusao", fusao, filtro(exceto_modalidades=["fusao"]))
        barramento.assinar("log", lambda e: ordem.append(("log", e.modality.value)))
        barramento.emitir(evento)
        assert ordem == [("fusao", "sinais_vitais"), ("log", "sinais_vitais"), ("log", "fusao")]

    def test_falha_de_um_assinante_nao_para_os_outros(self, evento):
        barramento = BarramentoLocal()
        recebidos = []

        def quebrado(_e):
            raise RuntimeError("bug no módulo")

        barramento.assinar("quebrado", quebrado)
        barramento.assinar("alertas", recebidos.append)
        barramento.emitir(evento)
        assert recebidos == [evento]
        assert barramento.falhas[0].assinante == "quebrado"
        assert "bug no módulo" in barramento.falhas[0].erro

    def test_recusa_evento_fora_do_contrato_antes_de_gravar(self, tmp_path, campos_validos):
        barramento = BarramentoLocal(tmp_path / "e.jsonl")
        with pytest.raises(Exception):
            barramento.emitir({**campos_validos, "patient_id": "10000032"})
        assert not (tmp_path / "e.jsonl").exists() or not (tmp_path / "e.jsonl").read_text()

    def test_caminho_pelo_ambiente(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "x.jsonl"))
        assert BarramentoLocal.do_ambiente().caminho == tmp_path / "x.jsonl"


class TestCliValidar:
    def test_exemplos_passam(self, capsys):
        assert validar_cli([str(p) for p in DIR_EXEMPLOS.glob("*.json")]) == 0
        assert "0 inválido(s)" in capsys.readouterr().out

    def test_arquivo_com_evento_invalido_falha(self, tmp_path, campos_validos, capsys):
        caminho = tmp_path / "saida.jsonl"
        ruim = {**campos_validos, "timestamp": "2026-09-26T14:02:00-03:00"}
        caminho.write_text(json.dumps(campos_validos) + "\n" + json.dumps(ruim) + "\n", encoding="utf-8")
        assert validar_cli([str(caminho)]) == 1
        saida = capsys.readouterr()
        assert "saida.jsonl:2" in saida.err
        assert "1 evento(s) válido(s), 1 inválido(s)" in saida.out

    def test_assert_eventos_validos_aponta_modalidade_errada(self, evento):
        with pytest.raises(AssertionError, match="modalidade diferente"):
            assert_eventos_validos([evento], modalidade="video")


def test_datetime_utc_de_outro_tz_objeto_e_normalizado(campos_validos):
    instante = datetime(2026, 9, 26, 17, 2, tzinfo=timezone.utc)
    evento = EventoAchado.model_validate({**campos_validos, "timestamp": instante})
    assert evento.para_dict()["timestamp"] == "2026-09-26T17:02:00.000Z"
