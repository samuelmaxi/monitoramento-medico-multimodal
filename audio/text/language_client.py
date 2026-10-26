"""Cliente do Azure AI Language para a US13: Text Analytics for health e sentimento.

- Endpoint e chave só por variável de ambiente (``AZURE_LANGUAGE_ENDPOINT`` e
  ``AZURE_LANGUAGE_KEY``).
- Retry exponencial em HTTP 429 e 5xx transitório, respeitando ``Retry-After``.
  O retry interno do SDK fica desligado (``retry_total=0``) para haver uma
  política só, visível no log.
- 401 e 403 viram :class:`LanguageAuthError` com a causa provável.
- Cada texto é guardado em cache (``azure.cache_dir``), com o hash de operação,
  idioma, versão do modelo e texto como chave. Rodar de novo não repete chamadas.
- O resultado volta como dataclasses simples, não como objetos do SDK, para o
  cache e os testes não dependerem do SDK.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TypeVar

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError

from .config import AzureConfig
from .segmentation import batched

log = logging.getLogger(__name__)

T = TypeVar("T")

API_VERSION = "2023-04-01"  # padrão do azure-ai-textanalytics 5.3.0
_STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}

OP_HEALTH = "health"
OP_SENTIMENT = "sentiment"


class LanguageServiceError(RuntimeError):
    """Falha do Azure AI Language que não se resolve com retry."""


class LanguageAuthError(LanguageServiceError):
    """Credencial inválida (401) ou sem permissão (403)."""


class _HealthTimeout(Exception):
    """O job assíncrono do health não terminou em ``azure.health_timeout_s``."""


@dataclass(frozen=True)
class HealthEntity:
    text: str
    category: str
    subcategory: Optional[str]
    confidence: float
    offset: int
    length: int
    normalized_text: Optional[str]
    certainty: Optional[str]
    conditionality: Optional[str]
    association: Optional[str]
    temporality: Optional[str]


@dataclass(frozen=True)
class SentenceSentiment:
    text: str
    sentiment: str
    positive: float
    neutral: float
    negative: float
    offset: int
    length: int


def snake(valor: Any) -> Optional[str]:
    """``"negativePossible"`` / ``EntityCertainty.NEGATIVE_POSSIBLE`` → ``"negative_possible"``."""
    if valor is None:
        return None
    texto = str(getattr(valor, "value", valor))
    saida = []
    for i, c in enumerate(texto):
        if c.isupper() and i and texto[i - 1] not in "_-":
            saida.append("_")
        saida.append(c.lower())
    return "".join(saida).replace("-", "_")


def require_env(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        raise LanguageAuthError(
            f"A variável {nome} não foi definida. Copie .env.sample para .env e preencha "
            "AZURE_LANGUAGE_ENDPOINT e AZURE_LANGUAGE_KEY (portal Azure → recurso Language → "
            "Chaves e ponto de extremidade)."
        )
    return valor


class _Cache:
    def __init__(self, pasta: Optional[Path]) -> None:
        self.pasta = pasta

    @staticmethod
    def chave(operacao: str, idioma: str, modelo: str, texto: str) -> str:
        return hashlib.sha256(f"{operacao}\0{idioma}\0{modelo}\0{texto}".encode("utf-8")).hexdigest()

    def _caminho(self, operacao: str, chave: str) -> Optional[Path]:
        return self.pasta / operacao / f"{chave}.json" if self.pasta else None

    def ler(self, operacao: str, chave: str) -> Optional[dict[str, Any]]:
        caminho = self._caminho(operacao, chave)
        if caminho is None or not caminho.is_file():
            return None
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("cache corrompido, ignorando: %s", caminho)
            return None

    def gravar(self, operacao: str, chave: str, dados: dict[str, Any]) -> None:
        caminho = self._caminho(operacao, chave)
        if caminho is None:
            return
        caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = caminho.with_suffix(".tmp")
        temporario.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        temporario.replace(caminho)


class LanguageClient:
    """Envolve um ``TextAnalyticsClient`` (ou um falso, nos testes)."""

    def __init__(self, sdk_client: Any, config: AzureConfig, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sdk = sdk_client
        self._config = config
        self._sleep = sleep
        self._cache = _Cache(config.cache_dir)
        self.model_versions: dict[str, str] = {}
        self.requests_sent: dict[str, int] = {OP_HEALTH: 0, OP_SENTIMENT: 0}

    @classmethod
    def from_env(cls, config: AzureConfig) -> "LanguageClient":
        from azure.ai.textanalytics import TextAnalyticsClient
        from azure.core.credentials import AzureKeyCredential

        sdk = TextAnalyticsClient(
            endpoint=require_env("AZURE_LANGUAGE_ENDPOINT"),
            credential=AzureKeyCredential(require_env("AZURE_LANGUAGE_KEY")),
            retry_total=0,
        )
        return cls(sdk, config)

    # ------------------------------------------------------------------ #
    # Retry e erros
    # ------------------------------------------------------------------ #

    def _espera(self, tentativa: int, exc: Exception) -> float:
        resposta = getattr(exc, "response", None)
        cabecalhos = getattr(resposta, "headers", None) or {}
        retry_after = cabecalhos.get("Retry-After") or cabecalhos.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), self._config.retry.max_delay_s)
            except ValueError:
                pass
        r = self._config.retry
        return min(r.initial_delay_s * 2 ** (tentativa - 1), r.max_delay_s)

    def _chamar(self, operacao: str, chamada: Callable[[], T]) -> T:
        maximo = self._config.retry.max_attempts
        for tentativa in range(1, maximo + 1):
            try:
                self.requests_sent[operacao] += 1
                return chamada()
            except ClientAuthenticationError as exc:
                raise LanguageAuthError(
                    "Azure AI Language recusou a credencial (HTTP 401). Confira AZURE_LANGUAGE_KEY "
                    "e se AZURE_LANGUAGE_ENDPOINT é do mesmo recurso (a chave de um recurso não vale em outro)."
                ) from exc
            except HttpResponseError as exc:
                status = exc.status_code
                if status == 401:
                    raise LanguageAuthError(
                        "Azure AI Language recusou a credencial (HTTP 401). Confira AZURE_LANGUAGE_KEY "
                        "e AZURE_LANGUAGE_ENDPOINT."
                    ) from exc
                if status == 403:
                    raise LanguageAuthError(
                        "Azure AI Language negou o acesso (HTTP 403). Causas comuns: cota do nível "
                        "gratuito (F0) esgotada no mês, rede do recurso restrita (firewall/VNet) ou "
                        "identidade sem a função Cognitive Services Language Reader. "
                        f"Detalhe do serviço: {exc.message}"
                    ) from exc
                if status in _STATUS_TRANSITORIOS and tentativa < maximo:
                    espera = self._espera(tentativa, exc)
                    log.warning("%s: HTTP %s (tentativa %d/%d); nova tentativa em %.1fs",
                                operacao, status, tentativa, maximo, espera)
                    self._sleep(espera)
                    continue
                if status == 429:
                    raise LanguageServiceError(
                        f"Azure AI Language continuou limitando as requisições (HTTP 429) após {maximo} "
                        "tentativas. Aguarde alguns minutos ou aumente azure.retry no config."
                    ) from exc
                raise LanguageServiceError(f"Azure AI Language falhou em {operacao} (HTTP {status}): {exc.message}") from exc
            except _HealthTimeout as exc:
                if tentativa < maximo:
                    espera = self._espera(tentativa, exc)
                    log.warning("%s: %s (tentativa %d/%d); nova tentativa em %.1fs",
                                operacao, exc, tentativa, maximo, espera)
                    self._sleep(espera)
                    continue
                raise LanguageServiceError(
                    f"Azure AI Language não concluiu {operacao} após {maximo} tentativas: {exc}. "
                    "O serviço pode estar sobrecarregado; tente de novo mais tarde ou aumente azure.health_timeout_s."
                ) from exc
            except ServiceRequestError as exc:
                if tentativa < maximo:
                    espera = self._espera(tentativa, exc)
                    log.warning("%s: erro de rede (%s); nova tentativa em %.1fs", operacao, exc, espera)
                    self._sleep(espera)
                    continue
                raise LanguageServiceError(
                    f"Não foi possível conectar ao Azure AI Language ({exc}). Confira AZURE_LANGUAGE_ENDPOINT."
                ) from exc
        raise AssertionError("inalcançável")  # pragma: no cover

    # ------------------------------------------------------------------ #
    # Cache + lotes
    # ------------------------------------------------------------------ #

    def _com_cache(
        self,
        operacao: str,
        idioma: str,
        textos: Sequence[str],
        tamanho_lote: int,
        enviar: Callable[[list[str]], tuple[list[list[dict[str, Any]]], Optional[str]]],
    ) -> list[list[dict[str, Any]]]:
        limite = self._config.max_chars_per_document
        for i, texto in enumerate(textos):
            if not texto.strip():
                raise ValueError(f"documento {i} vazio: o serviço recusa texto vazio")
            if len(texto) > limite:
                raise ValueError(
                    f"documento {i} com {len(texto)} caracteres excede {limite}; divida com segmentation.chunk_text"
                )

        modelo = self._config.model_version
        chaves = [self._cache.chave(operacao, idioma, modelo, t) for t in textos]
        resultados: list[Optional[list[dict[str, Any]]]] = [None] * len(textos)
        pendentes: list[int] = []
        for i, chave in enumerate(chaves):
            em_cache = self._cache.ler(operacao, chave)
            if em_cache is None:
                pendentes.append(i)
                continue
            resultados[i] = em_cache["result"]
            if em_cache.get("model_version"):
                self.model_versions.setdefault(operacao, em_cache["model_version"])

        if len(textos):
            log.debug("%s: %d documento(s), %d do cache", operacao, len(textos), len(textos) - len(pendentes))

        for lote in batched(pendentes, tamanho_lote):
            saidas, versao = enviar([textos[i] for i in lote])
            if versao:
                self.model_versions[operacao] = versao
            for i, saida in zip(lote, saidas):
                resultados[i] = saida
                self._cache.gravar(operacao, chaves[i], {"model_version": versao, "result": saida})
        return [r if r is not None else [] for r in resultados]

    # ------------------------------------------------------------------ #
    # Text Analytics for health
    # ------------------------------------------------------------------ #

    def analyze_health(self, textos: Sequence[str]) -> list[list[HealthEntity]]:
        """Entidades clínicas de cada texto, com as asserções do serviço."""
        idioma = self._config.health_language

        def enviar(lote: list[str]) -> tuple[list[list[dict[str, Any]]], Optional[str]]:
            documentos = [{"id": str(i), "text": t} for i, t in enumerate(lote)]
            bruto: dict[str, Any] = {}

            def gancho(resposta: Any) -> None:
                if getattr(resposta, "raw_response", None):
                    bruto["json"] = resposta.raw_response

            def chamada() -> list[Any]:
                bruto.clear()
                poller = self._sdk.begin_analyze_healthcare_entities(
                    documentos, language=idioma, model_version=self._config.model_version,
                    raw_response_hook=gancho,
                )
                limite = self._config.health_timeout_s
                poller.wait(timeout=limite)
                if not poller.done():
                    raise _HealthTimeout(f"job do health sem resposta em {limite:.0f}s")
                return list(poller.result())

            docs = self._chamar(OP_HEALTH, chamada)
            versao, extras = _ler_json_health(bruto.get("json"))
            saidas: list[list[dict[str, Any]]] = []
            for doc in docs:
                if getattr(doc, "is_error", False):
                    erro = getattr(doc, "error", None)
                    raise LanguageServiceError(
                        f"health recusou o documento {doc.id}: {getattr(erro, 'code', '?')} {getattr(erro, 'message', '')}"
                    )
                entidades = []
                for e in doc.entities:
                    assercao = getattr(e, "assertion", None)
                    extra = extras.get((str(doc.id), e.offset, e.length), {})
                    entidades.append(asdict(HealthEntity(
                        text=e.text,
                        category=str(getattr(e.category, "value", e.category)),
                        subcategory=e.subcategory,
                        confidence=float(e.confidence_score),
                        offset=int(e.offset),
                        length=int(e.length),
                        normalized_text=e.normalized_text,
                        certainty=snake(getattr(assercao, "certainty", None)),
                        conditionality=snake(getattr(assercao, "conditionality", None)),
                        association=snake(getattr(assercao, "association", None)),
                        temporality=snake(getattr(assercao, "temporality", None) or extra.get("temporality")),
                    )))
                saidas.append(entidades)
            return saidas, versao

        brutos = self._com_cache(OP_HEALTH, idioma, textos, self._config.health_batch_size, enviar)
        return [[HealthEntity(**e) for e in doc] for doc in brutos]

    # ------------------------------------------------------------------ #
    # Sentimento
    # ------------------------------------------------------------------ #

    def analyze_sentiment(self, textos: Sequence[str]) -> list[list[SentenceSentiment]]:
        """Sentenças de cada texto com os três scores do serviço."""
        idioma = self._config.sentiment_language

        def enviar(lote: list[str]) -> tuple[list[list[dict[str, Any]]], Optional[str]]:
            documentos = [{"id": str(i), "text": t} for i, t in enumerate(lote)]
            versao: dict[str, Optional[str]] = {}

            def gancho(resposta: Any) -> None:
                versao["model_version"] = getattr(resposta, "model_version", None)

            docs = self._chamar(OP_SENTIMENT, lambda: self._sdk.analyze_sentiment(
                documentos, language=idioma, model_version=self._config.model_version,
                raw_response_hook=gancho,
            ))
            saidas: list[list[dict[str, Any]]] = []
            for doc in docs:
                if getattr(doc, "is_error", False):
                    erro = getattr(doc, "error", None)
                    raise LanguageServiceError(
                        f"sentimento recusou o documento {doc.id}: {getattr(erro, 'code', '?')} {getattr(erro, 'message', '')}"
                    )
                saidas.append([
                    asdict(SentenceSentiment(
                        text=s.text,
                        sentiment=str(s.sentiment),
                        positive=float(s.confidence_scores.positive),
                        neutral=float(s.confidence_scores.neutral),
                        negative=float(s.confidence_scores.negative),
                        offset=int(s.offset),
                        length=int(s.length),
                    ))
                    for s in doc.sentences
                ])
            return saidas, versao.get("model_version")

        brutos = self._com_cache(OP_SENTIMENT, idioma, textos, self._config.sentiment_batch_size, enviar)
        return [[SentenceSentiment(**s) for s in doc] for doc in brutos]


def _ler_json_health(dados: Any) -> tuple[Optional[str], dict[tuple[str, int, int], dict[str, Any]]]:
    """``modelVersion`` e asserções que o SDK não expõe (ex.: ``temporality``) do JSON cru.

    O SDK 5.3.0 só mapeia certeza, condicionalidade e associação. Se o serviço
    devolver temporalidade, ela é lida daqui, por documento e posição da entidade.
    """
    if not isinstance(dados, dict):
        return None, {}
    resultados = dados.get("results")
    if resultados is None:
        itens = (dados.get("tasks") or {}).get("items") or []
        resultados = itens[0].get("results") if itens else None
    if not isinstance(resultados, dict):
        return None, {}
    extras: dict[tuple[str, int, int], dict[str, Any]] = {}
    for doc in resultados.get("documents") or []:
        for e in doc.get("entities") or []:
            assercao = e.get("assertion") or {}
            temporalidade = assercao.get("temporality") or assercao.get("temporal")
            if temporalidade:
                extras[(str(doc.get("id")), e.get("offset"), e.get("length"))] = {"temporality": temporalidade}
    return resultados.get("modelVersion"), extras
