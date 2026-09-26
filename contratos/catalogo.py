"""Catálogo controlado de modalidades, severidades e tipos de evento.

Este arquivo é a fonte única de verdade sobre QUAIS achados o sistema conhece.
Para criar um novo ``event_type``, abra um PR alterando ``TIPOS_DE_EVENTO`` e
regenere o JSON Schema com ``python -m contratos.schema``.
"""

from __future__ import annotations

from enum import Enum


class Modalidade(str, Enum):
    """Origem do achado. Cada módulo do repositório emite em uma modalidade."""

    VIDEO = "video"                  # video/        
    AUDIO = "audio"                  # audio/        
    TEXTO = "texto"                  # audio/ (NLP)  
    SINAIS_VITAIS = "sinais_vitais"  # anomalias/   
    PRESCRICOES = "prescricoes"      # anomalias/    
    MOVIMENTACAO = "movimentacao"    # anomalias/   
    FUSAO = "fusao"                  # fusao_alertas/ 


class Severidade(str, Enum):
    """Prioridade do achado, alinhada à IEC 60601-1-8.

    A norma define três prioridades de alarme (alta, média, baixa) e o sinal
    de informação, que aqui é ``info`` e nunca vira alerta.
    """

    INFO = "info"
    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"

    @property
    def peso(self) -> int:
        """Ordem numérica para comparar severidades (info=0 … alta=3)."""
        return _PESO_SEVERIDADE[self]


_PESO_SEVERIDADE = {
    Severidade.INFO: 0,
    Severidade.BAIXA: 1,
    Severidade.MEDIA: 2,
    Severidade.ALTA: 3,
}

SEM_ACHADOS = "sem_achados"
"""Análise concluída sem anomalias. Permite à fusão distinguir
"modalidade presente e normal" de "modalidade ausente"."""

TIPOS_DE_EVENTO: dict[Modalidade, dict[str, str]] = {
    Modalidade.VIDEO: {
        "postura_fora_da_faixa": "Ângulo articular fora da faixa esperada para o procedimento",
        "assimetria_movimento": "Assimetria esquerda × direita acima do limiar",
        "mudanca_brusca_ou_queda": "Mudança brusca de posição ou queda detectada no vídeo",
        "entrada_area_critica": "Pessoa/objeto entrou em área crítica (ROI)",
        "saida_area_critica": "Pessoa/objeto saiu de área crítica (ROI)",
        "presenca_indevida_area_critica": "Pessoa/objeto indevido em área crítica",
        SEM_ACHADOS: "Vídeo analisado sem desvios (US09)",
    },
    Modalidade.AUDIO: {
        "fadiga_vocal": "Indicativo de fadiga pelos biomarcadores vocais",
        "dificuldade_respiratoria": "Indicativo de dificuldade respiratória",
        "possivel_disartria": "Indicativo de possível disartria",
        "evento_respiratorio": "Tosse, respiração ofegante ou arfar detectado por modelo AudioSet",
        "desvio_baseline_vocal": "Biomarcador vocal fora da baseline do próprio paciente",
        SEM_ACHADOS: "Áudio analisado sem alterações vocais",
    },
    Modalidade.TEXTO: {
        "termo_critico": "Termo crítico afirmado na transcrição, sem negação",
        "sentimento_negativo": "Sentimento negativo acima do limiar documentado",
        SEM_ACHADOS: "Transcrição analisada sem termos críticos",
    },
    Modalidade.SINAIS_VITAIS: {
        "news2_resposta_urgente": "NEWS2 total 5–6 ou 3 pontos em parâmetro único",
        "news2_emergencia": "NEWS2 total ≥ 7",
        "desvio_baseline": "Mudança súbita em relação à baseline do paciente, camada de ML",
        "tendencia_deterioracao": "Tendência sustentada de piora, camada de ML",
        SEM_ACHADOS: "Janela de sinais vitais sem anomalias",
    },
    Modalidade.PRESCRICOES: {
        "mudanca_abrupta_dose": "Mudança de dose acima do limiar configurado",
        "duplicidade_terapeutica": "Mesmo princípio ativo ativo em paralelo",
        "suspensao_abrupta": "Suspensão abrupta de medicamento",
        "medicamento_alta_vigilancia": "Introdução de medicamento da lista ISMP",
        SEM_ACHADOS: "Evolução de prescrições sem anomalias",
    },
    Modalidade.MOVIMENTACAO: {
        "imobilidade_prolongada": "Sem mudança de posição acima do limiar, referência > 2 h",
        "saida_do_leito": "Saída do leito, severidade alta no período noturno",
        "queda": "Queda durante a internação",
        "agitacao_subita": "Aumento súbito de movimentação versus baseline",
        "reducao_mobilidade": "Redução brusca de mobilidade versus baseline",
        SEM_ACHADOS: "Padrão de movimentação dentro da baseline",
    },
    Modalidade.FUSAO: {
        "risco_multimodal": "Score de risco por paciente combinando modalidades (US18)",
    },
}


def tipos_validos(modalidade: Modalidade) -> frozenset[str]:
    """Conjunto de ``event_type`` aceitos para a modalidade."""
    return frozenset(TIPOS_DE_EVENTO[modalidade])


def severidade_sugerida(score: float) -> Severidade:
    """Mapeamento padrão score → severidade.

    Use somente em módulos sem regra clínica própria. NEWS2, a lista ISMP e o
    limiar de imobilidade definem severidade pelas próprias regras, não por aqui.
    """
    if score < 0.25:
        return Severidade.INFO
    if score < 0.5:
        return Severidade.BAIXA
    if score < 0.75:
        return Severidade.MEDIA
    return Severidade.ALTA
