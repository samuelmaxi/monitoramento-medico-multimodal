"""Gera ``postman/prescricoes.json``, a coleção Postman da US16.

    python scripts/gerar_postman_us16.py

Os corpos ficam aqui, e ``tests/prescricoes/test_api.py`` envia os mesmos
corpos da coleção gerada à API: se a regra mudar e um exemplo deixar de dar o
resultado que a coleção verifica, o teste quebra antes do Postman.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

DESTINO = Path(__file__).resolve().parents[1] / "postman" / "prescricoes.json"

def o(med, dose, un, via, freq, n24, ini, fim, **extra):
    d = {"medicamento": med, "dose": dose, "unidade": un, "via": via, "frequencia": freq,
         "inicio": ini, "fim": fim}
    if n24 is not None:
        d["doses_24h"] = n24
    d.update(extra)
    return d

ADM, ALTA = "2026-10-01T10:00:00Z", "2026-10-12T14:00:00Z"
base = lambda pid, ordens, **kw: {"patient_id": pid, "encounter_id": f"{pid}-int1", "admissao": ADM,
                                  "alta": ALTA, "is_synthetic": True, "prescricoes": ordens, **kw}

EXEMPLOS = {
  "dose": base("sim-paciente-16a", [
      o("Enoxaparin Sodium", "40", "mg", "SC", "QPM", 1, "2026-10-01T22:00:00Z", "2026-10-03T11:00:00Z",
        ordem_id="enox-1", status="Inactive (Due to a change order)"),
      o("Enoxaparin Sodium", "100", "mg", "SC", "Q12H", 2, "2026-10-03T12:00:00Z", "2026-10-07T20:00:00Z", ordem_id="enox-2"),
  ]),
  "duplicidade": base("sim-paciente-16b", [
      o("Oxycodone-Acetaminophen (5mg-325mg)", "1", "TAB", "PO/NG", "Q4H:PRN", None, "2026-10-02T18:00:00Z", "2026-10-04T21:00:00Z", ordem_id="percocet"),
      o("Acetaminophen", "1000", "mg", "PO/NG", "TID", 3, "2026-10-03T12:00:00Z", "2026-10-04T21:00:00Z", ordem_id="paracetamol"),
  ]),
  "suspensao": base("sim-paciente-16c", [
      o("Metoprolol Tartrate", "25", "mg", "PO", "TID", 3, "2026-10-02T08:00:00Z", "2026-10-06T08:00:00Z", ordem_id="metoprolol"),
  ]),
  "alta_vigilancia": base("sim-paciente-16d", [
      o("Pantoprazole", "40", "mg", "PO", "Q24H", 1, "2026-10-01T12:00:00Z", "2026-10-05T12:00:00Z", ordem_id="ibp"),
      o("Potassium Chloride", "40", "mEq", "IV", "ONCE", None, "2026-10-02T09:00:00Z", "2026-10-02T13:00:00Z",
        ordem_id="kcl", apresentacao="2mEq/mL-20mL"),
  ]),
  "sem_achados": base("sim-paciente-16e", [
      o("PredniSONE", "40", "mg", "PO", "DAILY", 1, "2026-10-01T12:00:00Z", "2026-10-04T12:00:00Z", ordem_id="pred-40"),
      o("PredniSONE", "30", "mg", "PO", "DAILY", 1, "2026-10-04T12:00:00Z", "2026-10-07T12:00:00Z", ordem_id="pred-30"),
      o("Furosemide", "40", "mg", "PO", "DAILY", 1, "2026-10-01T12:00:00Z", "2026-10-03T12:00:00Z", ordem_id="furo-40"),
      o("Furosemide", "50", "mg", "PO", "DAILY", 1, "2026-10-03T12:00:00Z", "2026-10-12T12:00:00Z", ordem_id="furo-50"),
  ]),
  "unidades": base("sim-paciente-16f", [
      o("Levothyroxine Sodium", "100", "mcg", "PO", "DAILY", 1, "2026-10-01T08:00:00Z", "2026-10-03T08:00:00Z", ordem_id="levo-mcg"),
      o("Levothyroxine Sodium", "0.025", "mg", "PO", "DAILY", 1, "2026-10-03T08:00:00Z", "2026-10-12T08:00:00Z", ordem_id="levo-mg"),
  ]),
  "erro_utc": base("sim-paciente-16g", [
      o("Pantoprazole", "40", "mg", "PO", "Q24H", 1, "2026-10-01T09:00:00-03:00", None),
  ]),
  "erro_fim": base("sim-paciente-16h", [
      o("Pantoprazole", "40", "mg", "PO", "Q24H", 1, "2026-10-03T09:00:00Z", "2026-10-02T09:00:00Z"),
  ]),
}

# --------------------------------------------------------------------------- #
# Testes JS (pm.test) de cada requisição
# --------------------------------------------------------------------------- #

CONTRATO = [
    'pm.test("200 OK", () => pm.response.to.have.status(200));',
    "const corpo = pm.response.json();",
    'pm.test("eventos no contrato: modality prescricoes e paciente pseudonimizado", () => {',
    "    pm.expect(corpo.patient_id).to.match(/^pt_[0-9a-f]{24}$/);",
    "    pm.expect(corpo.eventos.length).to.be.above(0);",
    "    corpo.eventos.forEach(e => {",
    '        pm.expect(e.modality).to.eql("prescricoes");',
    "        pm.expect(e.patient_id).to.eql(corpo.patient_id);",
    '        pm.expect(e.evidence.thresholds).to.have.property("versao_limiares");',
    "    });",
    "});",
]


def achado(tipo: str, *verificacoes: str) -> list[str]:
    return [
        *CONTRATO,
        f'const ev = corpo.eventos.find(e => e.event_type === "{tipo}");',
        f'pm.test("detecta {tipo}", () => pm.expect(ev, "evento {tipo}").to.exist);',
        *verificacoes,
    ]


TESTES = {
    "dose": achado(
        "mudanca_abrupta_dose",
        'pm.test("justificativa com antes × depois", () => {',
        '    pm.expect(ev.evidence.summary).to.include("subiu 400%");',
        "    pm.expect(ev.evidence.features.antes_dose_diaria).to.eql(40);",
        "    pm.expect(ev.evidence.features.depois_dose_diaria).to.eql(200);",
        "});",
        'pm.test("anticoagulante (ISMP) sobe a severidade para media", () => pm.expect(ev.severity).to.eql("media"));',
    ),
    "duplicidade": achado(
        "duplicidade_terapeutica",
        'pm.test("paracetamol dentro da combinação", () => {',
        '    pm.expect(ev.evidence.features.principio_ativo).to.eql("acetaminophen");',
        '    pm.expect(ev.evidence.features.antes_medicamento).to.include("Oxycodone-Acetaminophen");',
        '    pm.expect(ev.evidence.features.depois_medicamento).to.eql("Acetaminophen");',
        "    pm.expect(ev.evidence.features.sobreposicao_h).to.be.at.least(6);",
        "});",
    ),
    "suspensao": achado(
        "suspensao_abrupta",
        'pm.test("betabloqueador sem desmame, com o paciente internado", () => {',
        '    pm.expect(ev.evidence.features.classe_terapeutica).to.eql("betabloqueador");',
        '    pm.expect(ev.evidence.summary).to.include("nenhuma prescrição");',
        '    pm.expect(ev.timestamp).to.eql("2026-10-06T08:00:00.000Z");',
        "});",
    ),
    "alta_vigilancia": achado(
        "medicamento_alta_vigilancia",
        'pm.test("KCl concentrado, severidade alta; pantoprazol não entra", () => {',
        '    pm.expect(ev.evidence.features.classe_ismp).to.eql("potassio_concentrado");',
        '    pm.expect(ev.severity).to.eql("alta");',
        '    pm.expect(corpo.eventos.filter(e => e.event_type === "medicamento_alta_vigilancia")).to.have.length(1);',
        "});",
    ),
    "sem_achados": [
        *CONTRATO,
        'pm.test("desmame de 25% e ajuste de 25% não são anomalia", () => {',
        "    pm.expect(corpo.total_anomalias).to.eql(0);",
        '    pm.expect(corpo.eventos.map(e => e.event_type)).to.eql(["sem_achados"]);',
        '    pm.expect(corpo.eventos[0].severity).to.eql("info");',
        "});",
    ],
    "unidades": achado(
        "mudanca_abrupta_dose",
        'pm.test("mcg e mg normalizados antes da comparação", () => {',
        '    pm.expect(corpo.normalizadas.map(o => o.unidade)).to.eql(["mg", "mg"]);',
        "    pm.expect(corpo.normalizadas.map(o => o.dose)).to.eql([0.1, 0.025]);",
        '    pm.expect(ev.evidence.summary).to.include("caiu 75%");',
        "});",
    ),
    "erro_utc": [
        'pm.test("422", () => pm.response.to.have.status(422));',
        'pm.test("cita UTC", () => pm.expect(JSON.stringify(pm.response.json())).to.include("UTC"));',
    ],
    "erro_fim": [
        'pm.test("422", () => pm.response.to.have.status(422));',
        'pm.test("cita o fim antes do início", () => pm.expect(pm.response.json().detail).to.include("antes do início"));',
    ],
}

POSTS = [
    ("Mudança abrupta de dose (enoxaparina)", "dose", "Enoxaparina 40 mg/dia (profilaxia) trocada por 100 mg a cada 12 h: +400% em 1 h. Esperado: mudanca_abrupta_dose media com antes_dose_diaria=40 e depois_dose_diaria=200. Mesmo caso real documentado no MIMIC-IV Demo."),
    ("Duplicidade terapêutica (paracetamol)", "duplicidade", "Oxicodona+paracetamol PRN e paracetamol 1 g TID em paralelo por 33 h: o paracetamol está escondido na combinação. Esperado: duplicidade_terapeutica com principio_ativo=acetaminophen."),
    ("Suspensão abrupta (metoprolol)", "suspensao", "Metoprolol 25 mg TID por 4 dias, parado 6 dias antes da alta, sem redução nem substituto. Esperado: suspensao_abrupta no fim da última ordem."),
    ("Alta vigilância (KCl concentrado)", "alta_vigilancia", "Pantoprazol (fora da lista) e KCl 2 mEq/mL IV. Esperado: um único medicamento_alta_vigilancia, classe potassio_concentrado, severidade alta."),
    ("Sem achados (desmame e ajuste pequeno)", "sem_achados", "Prednisona 40 → 30 mg (−25%, desmame) e furosemida 40 → 50 mg (+25%). Esperado: só sem_achados (info)."),
    ("Unidades normalizadas (com detalhes)", "unidades", "Levotiroxina 100 mcg → 0,025 mg. Com ?detalhes=true, a resposta mostra as duas ordens em mg; a queda de 75% só aparece porque mcg virou mg antes de comparar."),
    ("Erro 422 - horário fora de UTC", "erro_utc", "inicio com -03:00: o contrato exige UTC. Esperado: 422."),
    ("Erro 422 - fim antes do início", "erro_fim", "Ordem que termina antes de começar. Esperado: 422."),
]


def _url(*caminho: str, query: list[tuple[str, str]] = ()) -> dict:
    raw = "{{baseUrl}}/" + "/".join(caminho)
    if query:
        raw += "?" + "&".join(f"{k}={v}" for k, v in query)
    url = {"raw": raw, "host": ["{{baseUrl}}"], "path": list(caminho)}
    if query:
        url["query"] = [{"key": k, "value": v} for k, v in query]
    return url


def _id(nome: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"us16/{nome}"))


def _req(nome: str, metodo: str, url: dict, descricao: str, testes: list[str], corpo: dict | None = None) -> dict:
    request = {"method": metodo, "header": [], "url": url, "description": descricao}
    if corpo is not None:
        request["header"] = [{"key": "Content-Type", "value": "application/json"}]
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(corpo, ensure_ascii=False, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    return {
        "id": _id(nome),
        "name": nome,
        "request": request,
        "response": [],
        "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": testes}}],
    }


def colecao() -> dict:
    analise = [
        _req(
            nome, "POST",
            _url("prescricoes", "analise", query=[("publicar", "{{publicar}}")] + ([("detalhes", "true")] if chave == "unidades" else [])),
            descricao, TESTES[chave], EXEMPLOS[chave],
        )
        for nome, chave, descricao in POSTS
    ]
    mimic = [
        _req(
            "Paciente real: enoxaparina 40 → 200 mg/dia", "GET",
            _url("prescricoes", "mimic", "{{subjectEnoxaparina}}", query=[("publicar", "{{publicar}}")]),
            "subject_id 10008454 do MIMIC-IV Demo, uma internação. Esperado: a mudança abrupta de enoxaparina (ordens 41490219 → 58986734) entre as anomalias.",
            [
                *CONTRATO,
                'const dose = corpo.eventos.filter(e => e.event_type === "mudanca_abrupta_dose");',
                'pm.test("mudança de enoxaparina +400%", () => {',
                '    const ev = dose.find(e => e.evidence.features.principio_ativo === "enoxaparin");',
                "    pm.expect(ev).to.exist;",
                "    pm.expect(ev.evidence.features.variacao_pct).to.eql(400);",
                '    pm.expect(ev.timestamp).to.eql("2110-12-06T12:00:00.000Z");',
                "});",
                'pm.test("resumo por internação", () => {',
                "    pm.expect(corpo.internacoes).to.have.length(1);",
                "    pm.expect(corpo.internacoes[0].encounter_id).to.match(/^enc_[0-9a-f]{24}$/);",
                "    pm.expect(corpo.internacoes[0].total_anomalias).to.eql(corpo.total_anomalias);",
                "});",
            ],
        ),
        _req(
            "Paciente real: paracetamol duplicado (uma internação)", "GET",
            _url("prescricoes", "mimic", "{{subjectParacetamol}}", query=[("hadm_id", "{{hadmParacetamol}}"), ("publicar", "{{publicar}}")]),
            "subject_id 10002428, hadm_id 28676446. Esperado: duplicidade_terapeutica de acetaminophen (oxicodona+paracetamol PRN com paracetamol 1 g TID).",
            [
                *CONTRATO,
                'pm.test("duplicidade de paracetamol", () => {',
                '    const dup = corpo.eventos.filter(e => e.event_type === "duplicidade_terapeutica" && e.evidence.features.principio_ativo === "acetaminophen");',
                "    pm.expect(dup.length).to.be.above(0);",
                "});",
                'pm.test("só a internação pedida", () => pm.expect(corpo.internacoes).to.have.length(1));',
            ],
        ),
        _req(
            "Erro 404 - paciente fora do demo", "GET",
            _url("prescricoes", "mimic", "99999999", query=[("publicar", "false")]),
            "subject_id que não existe no MIMIC-IV Demo. Esperado: 404.",
            ['pm.test("404", () => pm.response.to.have.status(404));'],
        ),
    ]
    return {
        "info": {
            "_postman_id": _id("colecao"),
            "name": "Monitoramento Médico - Prescrições (US16)",
            "description": (
                "Anomalias na evolução de prescrições (US16): mudança abrupta de dose, duplicidade "
                "terapêutica, suspensão abrupta e introdução de medicamento de alta vigilância (ISMP). "
                "Suba a API com `uv run uvicorn app.main:app --reload` (PSEUDONYM_KEY no .env) e ajuste "
                "`baseUrl` se não for http://127.0.0.1:8000. A pasta MIMIC-IV Demo precisa das tabelas "
                "baixadas com `uv run python -m prescricoes.run --baixar`. `publicar=false` não grava no "
                "barramento local; troque para true para os eventos seguirem para fusão e alertas. "
                "Gerada por scripts/gerar_postman_us16.py; instruções em postman/README.md."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "baseUrl", "value": "http://127.0.0.1:8000"},
            {"key": "publicar", "value": "false"},
            {"key": "subjectEnoxaparina", "value": "10008454"},
            {"key": "subjectParacetamol", "value": "10002428"},
            {"key": "hadmParacetamol", "value": "28676446"},
        ],
        "item": [
            _req(
                "Health", "GET", _url("health"), "Confere se a API está no ar.",
                ['pm.test("200 OK", () => pm.response.to.have.status(200));'],
            ),
            _req(
                "Limiares e lista ISMP em uso", "GET", _url("prescricoes", "config"),
                "Versão dos limiares, regras e as classes ISMP de config/us16_prescricoes.yaml.",
                [
                    'pm.test("200 OK", () => pm.response.to.have.status(200));',
                    "const cfg = pm.response.json();",
                    'pm.test("limiar de dose 50% em 24 h", () => {',
                    "    pm.expect(cfg.mudanca_abrupta_dose.variacao_min_pct).to.eql(50);",
                    "    pm.expect(cfg.mudanca_abrupta_dose.janela_h).to.eql(24);",
                    "});",
                    'pm.test("lista ISMP com insulina, anticoagulante, opioide e KCl concentrado", () => {',
                    '    pm.expect(cfg.alta_vigilancia.classes).to.include.keys("insulina", "anticoagulante", "opioide", "potassio_concentrado");',
                    "});",
                ],
            ),
            {
                "name": "US16 - Analisar prescrições",
                "description": (
                    "POST /prescricoes/analise recebe as ordens de uma internação (medicamento, dose, unidade, "
                    "via, frequência, início, fim) e devolve os eventos EventoAchado (modality=prescricoes) com a "
                    "justificativa em evidence.summary e os valores antes × depois em evidence.features. "
                    "Horários em UTC. Sem `alta`, a suspensão abrupta não é avaliada."
                ),
                "item": analise,
            },
            {
                "name": "US16 - Pacientes do MIMIC-IV Demo",
                "description": (
                    "GET /prescricoes/mimic/{subject_id} roda a análise nas prescrições reais de um paciente do "
                    "MIMIC-IV Demo (dados/mimic-iv-demo, ou PRESCRICOES_DADOS_DIR). ?hadm_id= limita a uma "
                    "internação. 503 se as tabelas não foram baixadas."
                ),
                "item": mimic,
            },
        ],
    }


def main() -> int:
    DESTINO.write_text(json.dumps(colecao(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(DESTINO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
