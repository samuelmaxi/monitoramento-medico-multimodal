"""Gera ``postman/fusao.json``, a coleção Postman da US18.

    python scripts/gerar_postman_us18.py

Os corpos vêm dos cenários de ``fusao/cenarios.py`` (os mesmos da demo e dos
testes), com ``event_id`` e ``patient_id`` fixos para a coleção não mudar a cada
geração. ``tests/fusao/test_postman.py`` envia os corpos da coleção gerada à API
e confere os mesmos resultados que os ``pm.test``: se a fusão mudar e um exemplo
deixar de dar o resultado esperado, o teste quebra antes do Postman.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from contratos import Modalidade  # noqa: E402
from fusao.cenarios import cenario_dispneia, cenario_sem_video  # noqa: E402

DESTINO = RAIZ / "postman" / "fusao.json"
T0 = datetime(2026, 9, 26, 16, 10, tzinfo=UTC)
# Pseudônimos fixos (formato pt_<24 hex> do contrato), sem depender de PSEUDONYM_KEY.
PACIENTE_DEMO = "pt_00000000000000000000a007"
PACIENTE_SEM_VIDEO = "pt_00000000000000000000a003"
PACIENTE_NORMAL = "pt_00000000000000000000a001"


def _id(nome: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"us18/{nome}"))


def _fixos(eventos, prefixo: str) -> list[dict]:
    """Eventos no JSON do contrato, com event_id determinístico."""
    saida = []
    for i, evento in enumerate(eventos):
        dados = evento.para_dict()
        dados["event_id"] = _id(f"{prefixo}/{i}")
        saida.append(dados)
    return saida


DEMO = _fixos(cenario_dispneia(PACIENTE_DEMO, T0), "demo")
SEM_VIDEO = _fixos(cenario_sem_video(PACIENTE_SEM_VIDEO, T0), "sem-video")
NORMAIS = [
    {**e, "patient_id": PACIENTE_NORMAL, "event_id": _id(f"normal/{i}")}
    for i, e in enumerate(d for d in DEMO if d["event_type"] == "sem_achados")
]


def _so(modalidade: Modalidade, eventos: list[dict]) -> list[dict]:
    return [e for e in eventos if e["modality"] == modalidade.value]


EXEMPLOS = {
    "demo": {"eventos": DEMO},
    "so_sinais": {"eventos": _so(Modalidade.SINAIS_VITAIS, DEMO)},
    "so_audio": {"eventos": _so(Modalidade.AUDIO, DEMO)},
    "sem_video": {"eventos": SEM_VIDEO},
    "janela_curta": {"eventos": DEMO, "janela_min": 5},
    "normal": {"eventos": NORMAIS},
    "dois_pacientes": {"eventos": DEMO + SEM_VIDEO},
    "erro_paciente": {"eventos": [{**DEMO[2], "patient_id": "10000032"}]},
    "erro_janela": {"eventos": DEMO, "janela_min": 0},
}

# --------------------------------------------------------------------------- #
# Testes JS (pm.test) de cada requisição
# --------------------------------------------------------------------------- #

UM_RISCO = [
    'pm.test("200 OK", () => pm.response.to.have.status(200));',
    "const riscos = pm.response.json();",
    'pm.test("um risco por paciente", () => pm.expect(riscos).to.have.length(1));',
    "const r = riscos[0];",
    'pm.test("contribuições somam o score (explicabilidade)", () => {',
    "    const soma = Object.values(r.contribuicoes).reduce((a, b) => a + b, 0);",
    "    pm.expect(soma).to.be.closeTo(r.score, 0.002);",
    "});",
    'pm.test("risco_multimodal no contrato US04", () => {',
    '    pm.expect(r.evento.modality).to.eql("fusao");',
    '    pm.expect(r.evento.event_type).to.eql("risco_multimodal");',
    "    pm.expect(r.evento.related_event_ids.length).to.be.above(0);",
    "    pm.expect(r.evento.score).to.be.closeTo(r.score, 0.0001);",
    "});",
]


def nivel(nivel: str, severidade: str) -> list[str]:
    return [
        f'pm.test("nível {nivel}, severidade {severidade}", () => {{',
        f'    pm.expect(r.nivel).to.eql("{nivel}");',
        f'    pm.expect(r.severidade).to.eql("{severidade}");',
        "});",
    ]


def so_contribui(modalidade: str) -> list[str]:
    return [
        f'pm.test("só {modalidade} contribui", () => {{',
        "    Object.entries(r.contribuicoes).forEach(([m, c]) => {",
        f'        if (m === "{modalidade}") pm.expect(c).to.be.above(0);',
        "        else pm.expect(c).to.eql(0);",
        "    });",
        "});",
    ]


TESTES = {
    "demo": [
        *UM_RISCO,
        *nivel("alto", "alta"),
        'pm.test("combinação supera cada sinal isolado", () => {',
        "    pm.expect(r.score).to.be.above(r.score_max_isolado);",
        "    pm.expect(r.ganho_fusao).to.be.above(0);",
        '    ["sinais_vitais", "audio", "texto"].forEach(m => pm.expect(r.forca_isolada[m]).to.be.below(0.7));',
        "});",
        'pm.test("concordância no eixo respiratório", () => {',
        '    pm.expect(r.concordancia.eixo).to.eql("respiratorio");',
        '    pm.expect(r.concordancia.modalidades).to.have.members(["sinais_vitais", "audio", "texto"]);',
        "});",
        'pm.test("vídeo e movimentação ausentes, sem erro", () => {',
        '    pm.expect(r.modalidades_ausentes).to.eql(["video", "movimentacao"]);',
        "    pm.expect(r.contribuicoes.video).to.eql(0);",
        "});",
        'pm.collectionVariables.set("scoreDemo", r.score);',
    ],
    "so_sinais": [*UM_RISCO, *nivel("medio", "info"), *so_contribui("sinais_vitais")],
    "so_audio": [*UM_RISCO, *nivel("baixo", "info"), *so_contribui("audio")],
    "sem_video": [
        *UM_RISCO,
        *nivel("alto", "info"),
        'pm.test("quatro modalidades ausentes, sem erro", () => {',
        '    pm.expect(r.modalidades_ausentes).to.eql(["video", "audio", "texto", "movimentacao"]);',
        "});",
        *so_contribui("sinais_vitais"),
    ],
    "janela_curta": [
        *UM_RISCO,
        'pm.test("janela de 5 min deixa o NEWS2 de fora", () => {',
        "    pm.expect(r.contribuicoes.sinais_vitais).to.eql(0);",
        '    pm.expect(r.modalidades_ausentes).to.include("sinais_vitais");',
        "});",
        'pm.test("score menor que o do caso com janela de 60 min", () => {',
        '    const demo = Number(pm.collectionVariables.get("scoreDemo"));',
        "    if (!isNaN(demo)) pm.expect(r.score).to.be.below(demo);",
        "});",
    ],
    "normal": [
        *UM_RISCO,
        *nivel("baixo", "info"),
        'pm.test("só sem_achados: score 0 com modalidades presentes", () => {',
        "    pm.expect(r.score).to.eql(0);",
        '    pm.expect(r.modalidades_presentes).to.have.members(["sinais_vitais", "prescricoes"]);',
        "});",
    ],
    "dois_pacientes": [
        'pm.test("200 OK", () => pm.response.to.have.status(200));',
        "const riscos = pm.response.json();",
        'pm.test("um risco por paciente, maior primeiro", () => {',
        "    pm.expect(riscos).to.have.length(2);",
        f'    pm.expect(riscos[0].patient_id).to.eql("{PACIENTE_DEMO}");',
        "    pm.expect(riscos[0].score).to.be.at.least(riscos[1].score);",
        "});",
    ],
    "erro_paciente": [
        'pm.test("422", () => pm.response.to.have.status(422));',
        'pm.test("cita o patient_id", () => pm.expect(JSON.stringify(pm.response.json())).to.include("patient_id"));',
    ],
    "erro_janela": ['pm.test("422", () => pm.response.to.have.status(422));'],
}

POSTS = [
    ("Caso de demonstração: SpO2 em queda + dispneia", "demo",
     "Leito UTI-07: NEWS2 = 5 (SpO2 92%), fala com dispneia (áudio) e relato de falta de ar (texto), sem vídeo nem movimentação. Esperado: risco alto (≈ 0,82), acima de cada sinal isolado, com concordância no eixo respiratório."),
    ("Sinal isolado: só sinais vitais", "so_sinais",
     "Só o NEWS2 de resposta urgente do caso. Esperado: risco médio, severidade info (uma modalidade só não gera alerta de novo)."),
    ("Sinal isolado: só áudio", "so_audio",
     "Só a dificuldade respiratória na fala. Esperado: risco baixo."),
    ("Modalidade ausente: paciente sem vídeo", "sem_video",
     "Leito UTI-03: só sinais vitais (NEWS2 = 8) e prescrições. Esperado: risco alto sem falhar pelas quatro modalidades ausentes, severidade info."),
    ("Janela configurável: 5 min", "janela_curta",
     "O caso de demonstração com janela_min = 5. Esperado: o NEWS2 (16:50) sai da janela e o score cai."),
    ("Só sem_achados", "normal",
     "Sinais vitais e prescrições analisados, sem anomalias. Esperado: score 0, modalidades presentes, nível baixo."),
    ("Dois pacientes no mesmo lote", "dois_pacientes",
     "Achados dos leitos UTI-07 e UTI-03 juntos. Esperado: um risco por patient_id, ordenados do maior para o menor."),
    ("Erro 422 - patient_id sem pseudônimo", "erro_paciente",
     "Achado com o ID cru do paciente: o contrato exige pt_<24 hex>. Esperado: 422."),
    ("Erro 422 - janela_min = 0", "erro_janela", "Janela precisa ser maior que zero. Esperado: 422."),
]


def _url(*caminho: str, query: list[tuple[str, str]] = ()) -> dict:
    raw = "{{baseUrl}}/" + "/".join(caminho)
    if query:
        raw += "?" + "&".join(f"{k}={v}" for k, v in query)
    url = {"raw": raw, "host": ["{{baseUrl}}"], "path": list(caminho)}
    if query:
        url["query"] = [{"key": k, "value": v} for k, v in query]
    return url


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
    calculo = [
        _req(nome, "POST", _url("fusao", "risco", query=[("publicar", "false")]), descricao,
             TESTES[chave], EXEMPLOS[chave])
        for nome, chave, descricao in POSTS
    ]
    ao_vivo = [
        _req(
            "1. Publicar o caso no barramento", "POST",
            _url("fusao", "risco", query=[("publicar", "true")]),
            "Os achados do caso de demonstração entram no barramento local como se viessem dos detectores. A fusão ao vivo recalcula o risco a cada achado e publica risco_multimodal; o motor de alertas (US19) recebe tudo.",
            [*UM_RISCO, *nivel("alto", "alta")],
            EXEMPLOS["demo"],
        ),
        _req(
            "2. Risco atual do paciente", "GET",
            _url("fusao", "pacientes", "{{pacienteDemo}}"),
            "Estado da fusão ao vivo depois da publicação. Esperado: o mesmo risco alto do cálculo.",
            [
                'pm.test("200 OK", () => pm.response.to.have.status(200));',
                "const r = pm.response.json();",
                'pm.test("risco alto calculado pelo serviço", () => {',
                '    pm.expect(r.nivel).to.eql("alto");',
                '    pm.expect(r.concordancia.eixo).to.eql("respiratorio");',
                "});",
            ],
        ),
        _req(
            "3. Alerta alta gerado pela fusão", "GET",
            _url("alerts", query=[("paciente_id", "{{pacienteDemo}}")]),
            "Alertas do paciente no motor da US19. Esperado: cada achado isolado gerou alerta media, e o risco_multimodal gerou o alerta alta com a contribuição de cada modalidade no resumo.",
            [
                'pm.test("200 OK", () => pm.response.to.have.status(200));',
                "const alertas = pm.response.json();",
                'const fusao = alertas.find(a => a.modalities.includes("fusao"));',
                'pm.test("alerta da fusão com prioridade alta", () => {',
                "    pm.expect(fusao, \"alerta de fusao\").to.exist;",
                '    pm.expect(fusao.priority).to.eql("alta");',
                '    pm.expect(fusao.summary).to.include("Risco alto");',
                "    pm.expect(fusao.evidence.features.contrib_sinais_vitais).to.be.above(0);",
                "});",
                'pm.test("achados isolados só geraram alerta media", () => {',
                '    alertas.filter(a => !a.modalities.includes("fusao"))',
                '        .forEach(a => pm.expect(a.priority).to.eql("media"));',
                "});",
            ],
        ),
        _req(
            "4. Todos os pacientes", "GET", _url("fusao", "pacientes"),
            "Último risco de cada paciente visto pelo barramento, do maior para o menor.",
            [
                'pm.test("200 OK", () => pm.response.to.have.status(200));',
                'pm.test("inclui o paciente do caso", () => {',
                '    const ids = pm.response.json().map(r => r.patient_id);',
                '    pm.expect(ids).to.include(pm.collectionVariables.get("pacienteDemo"));',
                "});",
            ],
        ),
        _req(
            "Erro 404 - paciente sem achados", "GET",
            _url("fusao", "pacientes", "pt_ffffffffffffffffffffffff"),
            "Paciente que a fusão nunca viu. Esperado: 404.",
            ['pm.test("404", () => pm.response.to.have.status(404));'],
        ),
    ]
    return {
        "info": {
            "_postman_id": _id("colecao"),
            "name": "Monitoramento Médico - Fusão multimodal (US18)",
            "description": (
                "Score de risco multimodal por paciente (US18): late fusion ponderada dos achados de "
                "vídeo, áudio, texto, sinais vitais, prescrições e movimentação, com a contribuição de "
                "cada modalidade. Suba a API com `uv run uvicorn app.main:app --reload` e ajuste "
                "`baseUrl` se não for http://127.0.0.1:8000. A pasta \"Cálculo\" não altera o estado "
                "(publicar=false). A pasta \"Fluxo ao vivo\" publica o caso de demonstração no "
                "barramento e confere o risco e o alerta resultantes; rode-a em ordem. Os achados são "
                "sintéticos (is_synthetic=true). Gerada por scripts/gerar_postman_us18.py; instruções "
                "em postman/README.md."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "baseUrl", "value": "http://127.0.0.1:8000"},
            {"key": "pacienteDemo", "value": PACIENTE_DEMO},
            {"key": "scoreDemo", "value": ""},
        ],
        "item": [
            _req("Health", "GET", _url("health"), "Confere se a API está no ar.",
                 ['pm.test("200 OK", () => pm.response.to.have.status(200));']),
            {"name": "US18 - Cálculo do risco", "item": calculo},
            {"name": "US18 - Fluxo ao vivo (barramento → fusão → alerta)", "item": ao_vivo},
        ],
    }


def main() -> None:
    DESTINO.write_text(json.dumps(colecao(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Coleção gravada em {DESTINO.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
