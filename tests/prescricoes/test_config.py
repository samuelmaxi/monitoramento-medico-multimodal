import copy

import pytest
import yaml

from prescricoes.config import CAMINHO_PADRAO, ErroDeConfiguracao, config_de_dict


@pytest.fixture(scope="module")
def bruto():
    return yaml.safe_load(CAMINHO_PADRAO.read_text(encoding="utf-8"))


def test_config_versionada_carrega(cfg):
    assert cfg.versao
    assert cfg.dose.variacao_min_pct == 50 and cfg.dose.janela_h == 24
    assert "ISMP" in cfg.alta_vigilancia.lista


@pytest.mark.parametrize(
    ("principios", "via", "nome", "apresentacao", "classe"),
    [
        (("insulin glargine",), "SC", "Insulin Glargine", "", "insulina"),
        (("heparin",), "SC", "Heparin", "", "anticoagulante"),
        (("morphine",), "IV", "Morphine Sulfate", "", "opioide"),
        (("potassium chloride",), "IV", "Potassium Chloride", "2mEq/mL-20mL", "potassio_concentrado"),
        (("sodium chloride",), "IV", "Sodium Chloride 3% (Hypertonic)", "", "cloreto_de_sodio_hipertonico"),
    ],
)
def test_lista_ismp(cfg, principios, via, nome, apresentacao, classe):
    assert cfg.alta_vigilancia.classe(principios, via, nome, apresentacao).id == classe


@pytest.mark.parametrize(
    ("principios", "via", "nome", "apresentacao"),
    [
        (("potassium chloride",), "IV", "Potassium Chloride", "20mEq/50mL Premix"),
        (("potassium chloride",), "enteral", "Potassium Chloride", "10mEq ER Tablet"),
        (("sodium chloride",), "IV", "Sodium Chloride 0.9%", ""),
        (("lidocaine",), "topica", "Lidocaine 5% Patch", ""),
        (("pantoprazole",), "IV", "Pantoprazole", ""),
    ],
)
def test_fora_da_lista_ismp(cfg, principios, via, nome, apresentacao):
    assert cfg.alta_vigilancia.classe(principios, via, nome, apresentacao) is None


def test_chave_desconhecida_recusada(bruto):
    dados = copy.deepcopy(bruto)
    dados["mudanca_abrupta_dose"]["variacao_pct"] = 50
    with pytest.raises(ErroDeConfiguracao, match="variacao_pct"):
        config_de_dict(dados)


def test_frequencia_invalida_recusada(bruto):
    dados = copy.deepcopy(bruto)
    dados["mudanca_abrupta_dose"]["frequencias_comparaveis"] = ["diaria"]
    with pytest.raises(ErroDeConfiguracao, match="tipos de frequência"):
        config_de_dict(dados)


def test_classe_de_suspensao_precisa_existir(bruto):
    dados = copy.deepcopy(bruto)
    dados["suspensao_abrupta"]["classes"].append("estatina")
    with pytest.raises(ErroDeConfiguracao, match="estatina"):
        config_de_dict(dados)


def test_severidade_invalida_recusada(bruto):
    dados = copy.deepcopy(bruto)
    dados["alta_vigilancia"]["classes"]["insulina"]["severidade"] = "critica"
    with pytest.raises(ErroDeConfiguracao, match="severidade"):
        config_de_dict(dados)
