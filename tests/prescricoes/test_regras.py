from contratos import Severidade
from prescricoes.regras import (
    duplicidades,
    introducoes_alta_vigilancia,
    mudancas_de_dose,
    suspensoes,
)

from .conftest import ordem

# --------------------------------------------------------------------------- #
# Mudança abrupta de dose
# --------------------------------------------------------------------------- #


def test_aumento_de_dose_na_troca_de_prescricao(cfg, internacao):
    a = ordem("Metoprolol Tartrate", "12.5", "mg", de=0, ate=24, freq="BID", n24=2)
    b = ordem("Metoprolol Tartrate", "50", "mg", de=24, ate=72, freq="BID", n24=2)
    [anomalia] = mudancas_de_dose([a, b], cfg, internacao)
    assert anomalia.features["variacao_pct"] == 300
    assert anomalia.features["base_comparacao"] == "dose_diaria"
    assert anomalia.antes["dose_diaria"] == 25 and anomalia.depois["dose_diaria"] == 100
    assert "subiu 300%" in anomalia.resumo and "25 → 100 mg/dia" in anomalia.resumo
    assert anomalia.severidade is Severidade.BAIXA
    assert anomalia.ordens == (a.ordem_id, b.ordem_id)


def test_reducao_de_dose_e_unidades_normalizadas(cfg, internacao):
    a = ordem("Levothyroxine Sodium", "100", "mcg", de=0, ate=24)
    b = ordem("Levothyroxine Sodium", "0.025", "mg", de=24, ate=48)
    [anomalia] = mudancas_de_dose([a, b], cfg, internacao)
    assert anomalia.features["variacao_pct"] == -75
    assert "caiu 75%" in anomalia.resumo


def test_variacao_abaixo_do_limiar_nao_dispara(cfg, internacao):
    a = ordem("Furosemide", "40", "mg", de=0, ate=24)
    b = ordem("Furosemide", "1", "mg", de=24, ate=48)
    c = ordem("Furosemide", "1.4", "mg", de=48, ate=72)
    anomalias = mudancas_de_dose([a, b, c], cfg, internacao)
    assert [x.ordens[-1] for x in anomalias] == [b.ordem_id]  # 1 → 1,4 mg é +40%


def test_dose_fora_da_janela_de_24h_nao_dispara(cfg, internacao):
    a = ordem("Furosemide", "20", "mg", de=0, ate=24)
    b = ordem("Furosemide", "80", "mg", de=50, ate=72)
    assert mudancas_de_dose([a, b], cfg, internacao) == []


def test_dose_composta_conta_a_soma(cfg, internacao):
    # Varfarina 2,5 + 1 mg = 3,5 mg, depois 3,5 mg numa ordem só: sem mudança.
    a1 = ordem("Warfarin", "2.5", "mg", de=0, ate=24, freq="DAILY16")
    a2 = ordem("Warfarin", "1", "mg", de=0, ate=24, freq="DAILY16")
    b = ordem("Warfarin", "3.5", "mg", de=24, ate=48, freq="DAILY16")
    assert mudancas_de_dose([a1, a2, b], cfg, internacao) == []


def test_prn_e_titulacao_nao_tem_dose_para_comparar(cfg, internacao):
    a = ordem("Morphine Sulfate", "2", "mg", de=0, ate=24, via="IV", freq="Q4H:PRN", n24=None)
    b = ordem("Morphine Sulfate", "8", "mg", de=24, ate=48, via="IV", freq="Q4H:PRN", n24=None)
    c = ordem("Propofol", "200", "mg", de=0, ate=24, via="IV DRIP", freq="TITRATE TO", n24=None)
    d = ordem("Propofol", "1000", "mg", de=24, ate=48, via="IV DRIP", freq="TITRATE TO", n24=None)
    assert mudancas_de_dose([a, b, c, d], cfg, internacao) == []


def test_unidades_diferentes_nao_comparam(cfg, internacao):
    a = ordem("Docusate Sodium", "100", "mg", de=0, ate=24)
    b = ordem("Docusate Sodium", "10", "mL", de=24, ate=48)
    assert mudancas_de_dose([a, b], cfg, internacao) == []


def test_mudanca_de_alta_vigilancia_sobe_severidade(cfg, internacao):
    a = ordem("Enoxaparin Sodium", "40", "mg", de=0, ate=24, via="SC", freq="DAILY")
    b = ordem("Enoxaparin Sodium", "100", "mg", de=24, ate=72, via="SC", freq="Q12H", n24=2)
    [anomalia] = mudancas_de_dose([a, b], cfg, internacao)
    assert anomalia.severidade is Severidade.MEDIA
    assert anomalia.features["classe_ismp"] == "anticoagulante"


def test_ordem_substituida_nao_soma_com_a_nova(cfg, internacao):
    # MIMIC: a antiga fica "Inactive (Due to a change order)" com o stoptime original.
    a = ordem("Vancomycin", "1000", "mg", de=0, ate=96, via="IV", freq="Q12H", n24=2,
              status="Inactive (Due to a change order)")
    b = ordem("Vancomycin", "1250", "mg", de=48, ate=96, via="IV", freq="Q12H", n24=2)
    assert mudancas_de_dose([a, b], cfg, internacao) == []
    assert duplicidades([a, b], cfg, internacao) == []


# --------------------------------------------------------------------------- #
# Duplicidade terapêutica
# --------------------------------------------------------------------------- #


def test_combinacao_com_isolado_e_duplicidade(cfg, internacao):
    a = ordem("Acetaminophen", "650", "mg", de=0, ate=72, freq="Q6H:PRN", n24=None)
    b = ordem("Oxycodone-Acetaminophen (5mg-325mg)", "1", "TAB", de=10, ate=60, freq="Q4H:PRN", n24=None)
    [anomalia] = duplicidades([a, b], cfg, internacao)
    assert anomalia.principio == "acetaminophen"
    assert anomalia.features["sobreposicao_h"] == 50
    assert "Oxycodone-Acetaminophen" in anomalia.resumo and "Acetaminophen" in anomalia.resumo
    assert anomalia.antes["medicamento"] == "Acetaminophen"


def test_mesmo_produto_em_duas_vias_agendadas(cfg, internacao):
    a = ordem("LORazepam", "0.5", "mg", de=0, ate=72, freq="Q8H", n24=3)
    b = ordem("LORazepam", "0.5", "mg", de=24, ate=72, via="IV", freq="Q8H", n24=3)
    [anomalia] = duplicidades([a, b], cfg, internacao)
    assert anomalia.severidade is Severidade.ALTA  # benzodiazepínico IV é ISMP, as duas agendadas


def test_sobreposicao_curta_e_troca_nao_duplicidade(cfg, internacao):
    a = ordem("Amlodipine", "10", "mg", de=0, ate=30)
    b = ordem("Amlodipine", "10", "mg", de=26, ate=72)  # 4 h < 6 h
    assert duplicidades([a, b], cfg, internacao) == []


def test_esquema_fracionado_e_resgate_nao_sao_duplicidade(cfg, internacao):
    noite = ordem("QUEtiapine Fumarate", "50", "mg", de=0, ate=72, freq="QHS")
    manha = ordem("QUEtiapine Fumarate", "25", "mg", de=0, ate=72, freq="QAM")
    resgate = ordem("QUEtiapine Fumarate", "25", "mg", de=2, ate=72, freq="DAILY:PRN", n24=None)
    assert duplicidades([noite, manha, resgate], cfg, internacao) == []


def test_principios_permitidos_e_topicos_ficam_de_fora(cfg, internacao):
    k1 = ordem("Potassium Chloride", "40", "mEq", de=0, ate=24)
    k2 = ordem("Potassium Chloride", "20", "mEq", de=2, ate=24, via="IV", freq="ONCE")
    l1 = ordem("Lidocaine 5% Patch", "1", "PTCH", de=0, ate=48, via="TP")
    l2 = ordem("Lidocaine Jelly 2%", "1", "Appl", de=0, ate=48, via="TP", freq="PRN", n24=None)
    assert duplicidades([k1, k2, l1, l2], cfg, internacao) == []


# --------------------------------------------------------------------------- #
# Suspensão abrupta
# --------------------------------------------------------------------------- #


def test_suspensao_abrupta_de_betabloqueador(cfg, internacao):
    a = ordem("Metoprolol Tartrate", "50", "mg", de=0, ate=120, freq="BID", n24=2)
    [anomalia] = suspensoes([a], cfg, internacao)
    assert anomalia.regra == "suspensao.betabloqueador"
    assert anomalia.depois is None and anomalia.antes["dose_diaria"] == 100
    assert anomalia.instante == a.fim
    assert "nenhuma prescrição" in anomalia.resumo


def test_suspensao_com_desmame_nao_dispara(cfg, internacao):
    a = ordem("PredniSONE", "40", "mg", de=0, ate=72)
    b = ordem("PredniSONE", "10", "mg", de=72, ate=120)
    assert suspensoes([a, b], cfg, internacao) == []


def test_suspensao_perto_da_alta_ou_curta_nao_dispara(cfg, internacao):
    alta = (internacao.alta - internacao.admissao).total_seconds() / 3600
    perto_da_alta = ordem("LeVETiracetam", "500", "mg", de=alta - 120, ate=alta - 10, freq="BID", n24=2)
    curta = ordem("CloniDINE", "0.1", "mg", de=0, ate=48, freq="TID", n24=3)
    assert suspensoes([perto_da_alta, curta], cfg, internacao) == []


def test_troca_na_classe_ou_prn_depois_nao_e_suspensao(cfg, internacao):
    metoprolol = ordem("Metoprolol Tartrate", "25", "mg", de=0, ate=96, freq="BID", n24=2)
    carvedilol = ordem("Carvedilol", "6.25", "mg", de=100, ate=None, freq="BID", n24=2)  # até a alta
    oxi = ordem("OxyCODONE SR (OxyconTIN)", "10", "mg", de=0, ate=96, freq="Q12H", n24=2)
    oxi_prn = ordem("OxyCODONE (Immediate Release)", "5", "mg", de=90, ate=150, freq="Q4H:PRN", n24=None)
    assert suspensoes([metoprolol, carvedilol, oxi, oxi_prn], cfg, internacao) == []


def test_suspensao_sem_alta_conhecida_nao_dispara(cfg, internacao):
    from dataclasses import replace

    a = ordem("Metoprolol Tartrate", "50", "mg", de=0, ate=120, freq="BID", n24=2)
    assert suspensoes([a], cfg, replace(internacao, alta=None)) == []


# --------------------------------------------------------------------------- #
# Alta vigilância
# --------------------------------------------------------------------------- #


def test_primeira_ordem_ismp_por_internacao(cfg, internacao):
    h1 = ordem("Heparin", "5000", "UNIT", de=2, ate=48, via="SC", freq="Q8H", n24=3)
    h2 = ordem("Heparin", "5000", "UNIT", de=50, ate=96, via="SC", freq="Q8H", n24=3)
    kcl = ordem("Potassium Chloride", "20", "mEq", de=5, ate=6, via="IV", freq="ONCE",
                apresentacao="2mEq/mL-20mL")
    comum = ordem("Pantoprazole", "40", "mg", de=1, ate=48)
    anomalias = introducoes_alta_vigilancia([h1, h2, kcl, comum], cfg, internacao)
    assert [(a.regra, a.ordens) for a in anomalias] == [
        ("ismp.anticoagulante", (h1.ordem_id,)),
        ("ismp.potassio_concentrado", (kcl.ordem_id,)),
    ]
    assert anomalias[1].severidade is Severidade.ALTA
    assert anomalias[0].antes is None and anomalias[0].depois["dose"] == 5000
    assert anomalias[0].features["horas_desde_admissao"] == 2


def test_flush_e_ordem_cancelada_nao_contam(cfg, internacao):
    flush = ordem("Heparin Flush (10 units/ml)", "2", "mL", de=0, ate=48, via="IV", freq="PRN", n24=None)
    cancelada = ordem("Morphine Sulfate", "2", "mg", de=3, ate=3, via="IV", freq="Q4H:PRN", n24=None)
    assert introducoes_alta_vigilancia([flush, cancelada], cfg, internacao) == []


def test_justificativa_nao_arredonda_dose_pequena(cfg, internacao):
    a = ordem("Levothyroxine Sodium", "100", "mcg", de=0, ate=24)
    b = ordem("Levothyroxine Sodium", "0.025", "mg", de=24, ate=48)
    [anomalia] = mudancas_de_dose([a, b], cfg, internacao)
    assert "0,1 → 0,025 mg/dia" in anomalia.resumo
