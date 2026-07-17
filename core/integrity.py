# core/integrity.py
"""
Trava de integridade do Nimrod — NÃO É OPCIONAL.

Regra imutável: nenhum relatório do Nimrod pode conter dado mockado, sintético,
de exemplo ou fabricado como se fosse real. Este módulo é a única fonte de
verdade sobre o que conta como "dado real" e é chamado como última linha de
defesa antes de qualquer PDF ser gerado.

Como funciona na prática:
  - Todo comentário genuinamente coletado do Instagram carrega a tag
    FONTE_REAL no campo "fonte_dado" (ver core/instagram_scraper_v2.py,
    único ponto do scraper que constrói o dicionário final de um comentário).
  - Toda etapa de análise (classificação de IA, linguística, comportamento,
    clustering) que falhar deve marcar o dado como ERRO explicitamente — nunca
    substituir silenciosamente por um valor neutro/default que pareça real.
  - `assert_dados_reais()` é chamado em dois pontos independentes: no fim do
    pipeline (pipeline.py) e de novo dentro do gerador de PDF
    (report/pdf_builder.py), como defesa em profundidade. Se qualquer
    comentário não tiver a tag de proveniência, a geração do relatório é
    ABORTADA com exceção — não existe modo de "gerar mesmo assim".
"""

FONTE_REAL = "coleta_real_instagram"

# Valores que sinalizam explicitamente uma falha de etapa — são permitidos
# porque são declarações honestas de erro, não dados fabricados.
MARCADORES_DE_ERRO_PERMITIDOS = {
    "ERRO_CLASSIFICACAO",
    "ERRO_LINGUISTICA",
}


class DadosNaoReaisError(Exception):
    """Levantado quando dado não proveniente de coleta real tenta entrar no relatório."""


def assert_dados_reais(comments: list) -> None:
    """
    Verifica que TODO comentário na lista tem proveniência de coleta real.
    Levanta DadosNaoReaisError imediatamente se encontrar qualquer item sem
    a tag — inclusive listas vazias são permitidas (relatório de perfil sem
    comentários), o que não é permitido é dado fabricado se passando por real.
    """
    for i, c in enumerate(comments):
        if not isinstance(c, dict):
            raise DadosNaoReaisError(
                f"Item {i} não é um dicionário de comentário válido: {type(c)}"
            )
        if c.get("fonte_dado") != FONTE_REAL:
            raise DadosNaoReaisError(
                f"Comentário {i} (id_externo={c.get('id_externo')!r}) não tem a tag de "
                f"proveniência '{FONTE_REAL}'. Isso indica dado sintético, mockado ou "
                f"construído manualmente tentando entrar no relatório — bloqueado por regra "
                f"imutável. Se este dado é real, corrija o ponto de coleta para incluir "
                f"'fonte_dado': '{FONTE_REAL}'."
            )


def assert_classificacao_confiavel(comments: list, max_failure_rate: float = 0.3) -> None:
    """
    Garante que a cascata de classificação de IA de fato rodou na maioria dos
    comentários. Se mais de `max_failure_rate` dos comentários não foram
    classificados com sucesso (todos os provedores de IA falharam), aborta —
    porque um relatório onde "tudo deu NEUTRO" pode significar duas coisas
    completamente diferentes (perfil pacífico vs. IA fora do ar), e o Nimrod
    nunca deve deixar essa ambiguidade se passar por resultado real.
    """
    if not comments:
        return
    falhas = sum(1 for c in comments if c.get("categoria_ia") == "ERRO_CLASSIFICACAO")
    taxa = falhas / len(comments)
    if taxa > max_failure_rate:
        raise DadosNaoReaisError(
            f"{falhas}/{len(comments)} comentários ({taxa:.0%}) falharam na classificação "
            f"de IA — acima do limite de {max_failure_rate:.0%}. Isso normalmente indica que "
            f"nenhum provedor de IA está configurado/acessível (.env). Geração de relatório "
            f"abortada para não produzir um dossiê com dados de classificação incompletos "
            f"disfarçados de resultado real. Configure ao menos 1 provedor (MISTRAL_API_KEY, "
            f"GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY ou OLLAMA_BASE_URL) e rode de novo."
        )
