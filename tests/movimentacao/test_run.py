import json

from contratos.validar import validar_arquivo
from movimentacao import run
from movimentacao.modelos import Posicao as P

from .conftest import obs


def test_run_mimic_grava_jsonl_e_relatorio(tmp_path, monkeypatch, chave):
    series = {
        ("1", "10", "100"): [obs("08:00", P.SUPINO), obs("09:20", P.SUPINO), obs("10:40", P.LATERAL_ESQ)],
        ("2", "20", "200"): [obs("08:00", P.SUPINO), obs("09:00", P.LATERAL_ESQ)],
    }
    monkeypatch.setattr(run, "carregar", lambda diretorio: series)
    assert run.main(["mimic", "--saida", str(tmp_path)]) == 0

    validos, erros = validar_arquivo(tmp_path / "mimic_eventos.jsonl")
    assert erros == []
    tipos = sorted(e.event_type for e in validos)
    assert tipos == ["imobilidade_prolongada", "sem_achados"]

    rel = json.loads((tmp_path / "mimic_relatorio.json").read_text())
    assert rel["estadias"] == 2
    assert rel["eventos"] == {"imobilidade_prolongada": 1, "sem_achados": 1}


def test_run_mimic_filtra_estadia(tmp_path, monkeypatch, chave):
    series = {("1", "10", "100"): [obs("08:00", P.SUPINO)], ("2", "20", "200"): [obs("08:00", P.SUPINO)]}
    monkeypatch.setattr(run, "carregar", lambda diretorio: series)
    assert run.main(["mimic", "--stay-id", "200", "--saida", str(tmp_path)]) == 0
    rel = json.loads((tmp_path / "mimic_200_relatorio.json").read_text())
    assert rel["estadias"] == 1


def test_run_mimic_reescreve_saida(tmp_path, monkeypatch, chave):
    series = {("1", "10", "100"): [obs("08:00", P.SUPINO), obs("09:00", P.SUPINO)]}
    monkeypatch.setattr(run, "carregar", lambda diretorio: series)
    run.main(["mimic", "--saida", str(tmp_path)])
    run.main(["mimic", "--saida", str(tmp_path)])
    assert len((tmp_path / "mimic_eventos.jsonl").read_text().splitlines()) == 1
