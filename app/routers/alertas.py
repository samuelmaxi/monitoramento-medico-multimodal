"""Alertas da US19: consulta, detalhe e reconhecimento pela equipe.

O reconhecimento vale por dois caminhos: o link do e-mail (GET, com token HMAC) e a
API (POST, com o mesmo token). Sem token válido, responde 403.
"""

from __future__ import annotations

import html
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from alertas import Alerta, ServicoAlertas, StatusAlerta
from alertas.ack import verificar_token
from alertas.notificadores.formatacao import ROTULOS
from contratos import Severidade

router = APIRouter(prefix="/alerts", tags=["alertas (US19)"])


def _servico(request: Request) -> ServicoAlertas:
    servico = getattr(request.app.state, "alertas", None)
    if servico is None:
        raise HTTPException(status_code=503, detail="Motor de alertas não iniciado")
    return servico


def _verificar(servico: ServicoAlertas, alerta_id: uuid.UUID, token: str) -> None:
    if not verificar_token(alerta_id, token, servico.segredo_ack):
        raise HTTPException(status_code=403, detail="Token inválido para este alerta")


@router.get("", response_model=list[Alerta])
def listar_alertas(
    request: Request,
    status: StatusAlerta | None = Query(None),
    prioridade: Severidade | None = Query(None),
    paciente_id: str | None = Query(None, description="Pseudônimo pt_<24 hex>"),
    limite: int = Query(100, ge=1, le=500),
) -> list[Alerta]:
    return _servico(request).armazenamento.listar(
        status=status,
        prioridade=prioridade.value if prioridade else None,
        paciente_id=paciente_id,
        limite=limite,
    )


@router.get("/{alerta_id}")
def detalhar_alerta(alerta_id: uuid.UUID, request: Request) -> dict:
    armazenamento = _servico(request).armazenamento
    alerta = armazenamento.obter(alerta_id)
    if alerta is None:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    eventos = armazenamento.eventos(alerta_id)
    return {
        "alerta": alerta.model_dump(mode="json"),
        "auditoria": [e.model_dump(mode="json") for e in eventos],
    }


@router.get("/{alerta_id}/ack", response_class=HTMLResponse)
def reconhecer_pelo_link(
    alerta_id: uuid.UUID,
    request: Request,
    token: str = Query(..., description="Token do link enviado no e-mail"),
) -> HTMLResponse:
    servico = _servico(request)
    _verificar(servico, alerta_id, token)
    alerta = servico.motor.reconhecer(alerta_id, por="link de e-mail")
    if alerta is None:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    rotulo = html.escape(ROTULOS[alerta.priority])
    return HTMLResponse(
        "<html><body><h2>Alerta reconhecido</h2>"
        f"<p>Prioridade {rotulo}. Identificador: {html.escape(str(alerta.id))}.</p></body></html>"
    )


@router.post("/{alerta_id}/ack", response_model=Alerta)
def reconhecer_pela_api(
    alerta_id: uuid.UUID,
    request: Request,
    token: str = Query(..., description="Token do link enviado no e-mail"),
) -> Alerta:
    servico = _servico(request)
    _verificar(servico, alerta_id, token)
    alerta = servico.motor.reconhecer(alerta_id, por="api")
    if alerta is None:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return alerta
