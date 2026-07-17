# pipeline.py
"""
Orquestração síncrona e sem fila do Nimrod: 1 perfil, começo-meio-fim.

REGRA IMUTÁVEL: nenhuma etapa deste pipeline pode substituir uma falha real
por um valor fabricado que pareça um resultado legítimo. Toda falha é marcada
explicitamente (ERRO_*) e contabilizada — nunca disfarçada de "NEUTRO" ou
"sem achados". Ver core/integrity.py.

Etapas:
  1. Coleta  -> InstagramScraperV2.scrape_profile() (pula fixados nativamente,
                levanta ExtractionFailure se não conseguir extrair dado real —
                nunca retorna comentário fabricado)
  2. Classificação -> AIService.classify_text() (cascata PASA). Falha de
                todos os provedores é marcada como ERRO_CLASSIFICACAO, não NEUTRO.
  3. Linguística -> StanzaNLPEngine (lemas, n-gramas periciais)
  4. Comportamento -> BehaviorEngine.detect_coordinated_clusters() (bots/coordenação)
  5. Temas -> clustering.cluster_temas() (agrupamento semântico do que é hostil)
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from config import config
from core.instagram_scraper_v2 import InstagramScraperV2
from core.ai_service import ai_service
from core.behavior_engine import BehaviorEngine
from core.clustering import cluster_temas, extrair_ngrams
from core.integrity import assert_dados_reais, assert_classificacao_confiavel, DadosNaoReaisError

logger = logging.getLogger("nimrod.pipeline")

_AI_PROVIDER_ENV_VARS = [
    "MISTRAL_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
]


class PreflightError(Exception):
    """Erro de pré-checagem: o ambiente não tem o mínimo necessário para coletar/analisar dado real."""


def _preflight_checks() -> None:
    """
    Verifica ANTES de abrir o navegador se há o mínimo necessário para uma
    análise real. Falhar aqui é preferível a rodar parcialmente e produzir
    um relatório com lacunas silenciosas.
    """
    has_env_session = any(
        os.getenv(f"INSTAGRAM_SESSIONID_{i}") for i in range(1, 11)
    ) or os.getenv("INSTAGRAM_SESSIONID") or os.getenv("INSTAGRAM_COOKIE_FULL")
    
    import glob
    has_file_session = len(glob.glob(os.path.join(os.getcwd(), "cookies*.txt"))) > 0

    if not (has_env_session or has_file_session):
        raise PreflightError(
            "Nenhuma sessão do Instagram configurada (.env) nem arquivos cookies*.txt encontrados. "
            "Sem sessão real, o Nimrod não coleta nada — e não existe modo de coleta 'simulada'. Configure INSTAGRAM_SESSIONID."
        )

    has_ollama = bool(os.getenv("OLLAMA_BASE_URL"))
    has_cloud_provider = any(os.getenv(v) for v in _AI_PROVIDER_ENV_VARS)
    if not (has_ollama or has_cloud_provider):
        raise PreflightError(
            "Nenhum provedor de classificação de IA configurado (.env). Sem isso, todo "
            "comentário cairia em ERRO_CLASSIFICACAO. Configure ao menos 1 de: "
            + ", ".join(_AI_PROVIDER_ENV_VARS) + " ou OLLAMA_BASE_URL."
        )


async def _classify_all(comments: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Classifica todos os comentários pela cascata PASA, com concorrência limitada.
    Falha de classificação é marcada explicitamente — nunca vira NEUTRO por default.
    Retorna contagem de sucesso/falha para auditoria no relatório.
    """
    sem = asyncio.Semaphore(config.max_ai_concurrency)
    counters = {"sucesso": 0, "falha": 0}

    async def _classify_one(c: Dict[str, Any]) -> None:
        async with sem:
            texto = c.get("texto_bruto", "")
            if not texto:
                c["categoria_ia"] = "ERRO_CLASSIFICACAO"
                c["is_hate"] = None
                c["confianca_ia"] = None
                c["analise_pericial"] = "Comentário sem texto extraído — não classificado."
                counters["falha"] += 1
                return
            try:
                result = await ai_service.classify_text(texto, comment_id=c.get("id_externo", "N/A"))
                # "failover" é o próprio ai_service admitindo que todos os provedores falharam
                # — isso NÃO é um resultado de classificação real, é uma falha disfarçada de
                # NEUTRO pelo código original do Sentinela. O Nimrod não aceita essa máscara.
                if result.get("name") == "failover":
                    raise RuntimeError("Todos os provedores de IA falharam para este comentário.")
                c["categoria_ia"] = result.get("categoria_ia", "NEUTRO")
                c["is_hate"] = bool(result.get("is_hate", False))
                c["confianca_ia"] = float(result.get("confianca_ia", 0.0) or 0.0)
                c["analise_pericial"] = result.get("analise_pericial")
                c["classificador_usado"] = result.get("name")
                counters["sucesso"] += 1
            except Exception as e:
                logger.warning(f"[Pipeline] Falha real ao classificar comentário {c.get('id_externo')}: {e}")
                c["categoria_ia"] = "ERRO_CLASSIFICACAO"
                c["is_hate"] = None
                c["confianca_ia"] = None
                c["analise_pericial"] = f"Falha na cascata de IA: {e}"
                counters["falha"] += 1

    await asyncio.gather(*[_classify_one(c) for c in comments])
    return counters


async def run_profile_analysis(username: str) -> Dict[str, Any]:
    """
    Executa o pipeline completo para 1 perfil e retorna um dicionário consolidado
    pronto para ser passado ao gerador de relatório. Levanta PreflightError ou
    DadosNaoReaisError se, em qualquer ponto, não for possível garantir que o
    resultado é 100% dado real.
    """
    _preflight_checks()

    username = username.lstrip("@").strip()
    started_at = datetime.now(timezone.utc)
    logger.info(f"🎯 [Nimrod] Iniciando análise de @{username}")

    # 1. Coleta — o scraper levanta ExtractionFailure em vez de retornar dado
    #    fabricado quando a extração real falha (comportamento nativo, não alterado).
    scraper = InstagramScraperV2(headless=config.headless)
    scrape_result = await scraper.scrape_profile(
        username=username,
        candidato_id=username,
        max_posts=config.max_posts,
        max_comments_per_post=config.max_comments_per_post,
        max_age_days=config.max_age_days,
    )
    comments = scrape_result.get("comments", [])
    scrape_stats = dict(scraper.stats)
    logger.info(f"📥 [Nimrod] {len(comments)} comentários coletados de {scrape_stats.get('posts_scraped', 0)} posts.")

    # Trava de integridade #1: todo comentário tem que carregar a tag de coleta real.
    assert_dados_reais(comments)

    stage_status: Dict[str, str] = {}

    if not comments:
        return {
            "username": username,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc),
            "scrape_stats": scrape_stats,
            "comments": [],
            "ngrams_bigrams": [],
            "ngrams_trigrams": [],
            "coordinated_clusters": 0,
            "classification_counters": {"sucesso": 0, "falha": 0},
            "stage_status": stage_status,
        }

    # 2. Classificação (cascata PASA de IA) — obrigatória, sem fallback silencioso.
    classification_counters = await _classify_all(comments)

    # Trava de integridade #2: se a maioria falhou, aborta em vez de entregar
    # um relatório com lacunas disfarçadas de resultado real.
    assert_classificacao_confiavel(comments)

    # 3. Linguística (n-gramas periciais) — opcional, mas falha é registrada, não escondida.
    bigrams, trigrams = [], []
    if config.enable_linguistics:
        try:
            bigrams = extrair_ngrams(comments, n=2, top_k=20)
            trigrams = extrair_ngrams(comments, n=3, top_k=15)
            stage_status["linguistics"] = "ok"
        except Exception as e:
            logger.warning(f"[Pipeline] Falha na análise linguística: {e}")
            stage_status["linguistics"] = f"erro: {e}"
    else:
        stage_status["linguistics"] = "desativado (.env)"

    # 4. Comportamento coordenado / bots — opcional, falha registrada.
    if config.enable_behavior:
        try:
            behavior = BehaviorEngine()
            comments = await behavior.detect_coordinated_clusters(comments)
            stage_status["behavior"] = "ok"
        except Exception as e:
            logger.warning(f"[Pipeline] Falha na detecção de comportamento coordenado: {e}")
            stage_status["behavior"] = f"erro: {e}"
    else:
        stage_status["behavior"] = "desativado (.env)"

    # 5. Clusterização temática dos comentários hostis — opcional, falha registrada.
    if config.enable_clustering:
        try:
            comments = cluster_temas(comments)
            stage_status["clustering"] = "ok"
        except Exception as e:
            logger.warning(f"[Pipeline] Falha na clusterização temática: {e}")
            stage_status["clustering"] = f"erro: {e}"
    else:
        stage_status["clustering"] = "desativado (.env)"

    coordinated_clusters = len({c["cluster_id"] for c in comments if c.get("is_bot") and c.get("cluster_id")})

    return {
        "username": username,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc),
        "scrape_stats": scrape_stats,
        "comments": comments,
        "ngrams_bigrams": bigrams,
        "ngrams_trigrams": trigrams,
        "coordinated_clusters": coordinated_clusters,
        "classification_counters": classification_counters,
        "stage_status": stage_status,
    }
