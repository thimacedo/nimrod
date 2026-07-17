# -*- coding: utf-8 -*-
"""
PASA v52.5 - AI Service: Motor de Inteligencia Resiliente (Unified Rotation Queue)
Correcoes v1.0: ContextClassifier para mitigacao de falsos positivos em contextos positivos.
Roteamento dinamico unificado com atraso rigido anti-429, cache I/O, e fallback integrado.
"""
import os
import json
import logging
import asyncio
import traceback
import re
import codecs
import time
import random
import httpx
from typing import Dict, Any, List, Optional
from openai import AsyncOpenAI, APIStatusError
from core.circuit_breaker import ai_circuit_breaker

logger = logging.getLogger("AIService")

CONFIDENCE_THRESHOLD = float(os.getenv('CONFIDENCE_THRESHOLD', '0.5'))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD_DATASET_PATH = os.path.join(BASE_DIR, "data", "classifier_gold_dataset.json")
MD_PATH = os.path.join(BASE_DIR, "docs", "PADRONIZACAO_LINGUISTICA_ANALITICA.md")
CUSTOM_RULES_PATH = os.path.join(BASE_DIR, "config_data", "custom_rules.json")
# Contexto linguistico forense centralizado (PASA v51.0)
CONTEXTO_CLASSIFICACAO_PATH = os.path.join(BASE_DIR, "CONTEXTO_CLASSIFICACAO.md")
_CONTEXTO_CACHE: str = ""  # Cache em memoria para evitar I/O em cada ciclo

# ============================================================================
# ContextClassifier v1.0: Detector de Contextos Positivos
# Mitigacao de falsos positivos em textos claramente benignos (aniversario, etc.)
# ============================================================================
POSITIVE_CONTEXT_PATTERNS = [
    # Aniversario / Celebracoes
    r'\bparab[eé]ns\b.*\b(?:anivers[áa]rio|amigo|irm[ãa]o|vida|felicidades?)\b',
    r'\bfeliz\s+(?:anivers[áa]rio|niver|dia|Natal|ano novo|P[áa]scoa)\b',
    r'\bmuitos\s+(?:anos\s+de\s+vida)\b',
    r'\btudo\s+de\s+bom\b',
    r'\b(?:Deus|Jesus)\s+aben[çc]oe\b',
    # Agradecimentos / Apoio
    r'\b(?:obrigad[ao]|agrade[çc]o)\b.*\b(?:Deus|aben[çc]oad[ao]|feliz)\b',
    r'\b(?:for[çc]a|apoio|estamos\s+juntos)\b.*\b(?:amigo|irm[ãa]o)\b',
    # Saudacoes positivas
    r'\b(?:bom\s+dia|boa\s+tarde|boa\s+noite)\b.*\b(?:aben[çc]oad[ao]|lindo|maravilhoso)\b',
    # Emojis de celebracao + texto positivo
    r'^[🎉🎂🎁🙌👏🥳🎊🎈✨🙏❤️💖💕]+.*\b(?:parab[eé]ns|feliz|bom|lindo)\b',
    # Ancoragem Positiva Inicial (Apoio Político/Defesa)
    r'^(?:\W*\w+\W*){0,5}\b(?:tem\s+raz[ãa]o|perfeito|estamos\s+combatendo|parab[eé]ns|concordo|excelente|apoiado)\b',
]

_compiled_positive_patterns = [re.compile(p, re.IGNORECASE) for p in POSITIVE_CONTEXT_PATTERNS]

POSITIVE_CONTEXT_KEYWORDS = {
    'aniversario', 'parabens', 'feliz_aniversario', 'niver',
    'felicidades', 'abençoe', 'realizações',
    'razão', 'perfeito', 'combatendo', 'excelente', 'apoiado', 'concordo',
}

# Palavras que, se presentes, invalidam a classificacao de contexto benigno
NEGATIVE_INDICATORS_IN_POSITIVE = {
    'bandido', 'ladrão', 'ladráo', 'corrupto', 'verme', 'lixo',
    'vagabundo', 'idiota', 'imbecil', 'burro', 'morte', 'matar',
    'assassino', 'golpista', 'ditadura', 'fraude', 'crime',
    'odio', 'ódio', 'racista', 'homofobia', 'xenofobia',
    'preconceito', 'discriminação', 'discriminacao',
    'vergonha', 'nojo', 'asqueroso', 'repugnante',
}


class ContextClassifier:
    """
    Detector deterministico de contextos pragmaticos positivos.
    Usado ANTES do LLM para evitar falsos positivos em textos claramente benignos
    (aniversario, parabens, celebracoes, agradecimentos).
    """

    @staticmethod
    def is_positive_context(text: str) -> bool:
        """
        Retorna True se o texto esta em um contexto claramente positivo
        e NAO contem indicadores negativos (ataques).
        """
        if not text or len(text.strip()) < 3:
            return False

        text_lower = text.lower()

        # 1. Verifica se ha indicadores negativos (ataque) no texto
        # Se houver, nao eh contexto benigno — deixa o LLM decidir
        for neg in NEGATIVE_INDICATORS_IN_POSITIVE:
            if neg in text_lower:
                return False

        # 2. Verifica padroes regex de contexto positivo
        positive_pattern_matches = 0
        for pattern in _compiled_positive_patterns:
            if pattern.search(text):
                positive_pattern_matches += 1

        # 3. Verifica presenca de keywords positivas
        words = set(re.findall(r'\b\w+\b', text_lower))
        keyword_matches = words & POSITIVE_CONTEXT_KEYWORDS

        # 4. Heuristica: 1+ padrao E 1+ keyword, OU 2+ padroes positivos
        if (positive_pattern_matches >= 1 and len(keyword_matches) >= 1) or positive_pattern_matches >= 2:
            return True

        # 5. Heuristica adicional: texto curto (ate 20 palavras) com "parabens" + "amigo"
        word_count = len(words)
        if word_count <= 20 and 'parabens' in words:
            friend_words = {'amigo', 'irmão', 'irmao', 'parceiro', 'querido', 'querida', 'brother'}
            if words & friend_words:
                return True

        return False


def _load_contexto_classificacao() -> str:
    """Carrega o contexto linguistico forense centralizado uma unica vez."""
    global _CONTEXTO_CACHE
    if _CONTEXTO_CACHE:
        return _CONTEXTO_CACHE
    try:
        if os.path.exists(CONTEXTO_CLASSIFICACAO_PATH):
            with open(CONTEXTO_CLASSIFICACAO_PATH, "r", encoding="utf-8") as f:
                _CONTEXTO_CACHE = f.read()
                logger.info(f"[AI] CONTEXTO_CLASSIFICACAO carregado ({len(_CONTEXTO_CACHE)} chars).")
        else:
            logger.warning("[AI] CONTEXTO_CLASSIFICACAO.md nao encontrado. Classificacao sem contexto forense.")
    except Exception as e:
        logger.warning(f"[AI] Erro ao carregar CONTEXTO_CLASSIFICACAO.md: {e}")
    return _CONTEXTO_CACHE

# MCA v2.4 Protocol - Calibragem Analitica Critica Vichi-Sentinela (v95.1)
# v1.0 FIX: Adicionada secao de CONTEXTOS POSITIVOS para mitigar falsos positivos
SYSTEM_PROMPT = """Voce eh um analista especializado em Linguistica Analitica Digital baseado no Metodo Vichi-Sentinela para identificacao de ataques coordenados e hostilidade politica.
Sua missao eh classificar comentarios com realismo absoluto, seguindo a Metodologia de Classificacao de Ataques (MCA v2.4).

--- REGRAS DE OURO ---
1. REALISMO: Nao ignore ataques velados, ironias destrutivas ou acusacoes de corrupcao/crime.
2. FALSA EQUIVALENCIA E IDENTIDADE: Se o texto associar uma minoria (genero, sexualidade, raca) a palavra 'crime' ou 'aberracao' (ex: 'ser gay nao eh crime mas querer obrigar a aceitar crimes praticados por eles', 'eh uma aberracao'), a categoria DEVE ser ODIO_IDENTITARIO. Isso NAO E opiniao politica neutra.
3. OBFUSCACAO E LEETSPEAK: Textos que usam V5RM5, LIX0, V44G4BUND0 sao INSULTOS (Verme, Lixo, Vagabundo). Avalie o significado decodificado e classifique como INSULTO_AD_HOMINEM.
4. FALSAS ANALISES: O uso de jargao juridico para "teorizar" ou acusar o alvo de crimes (traicao, assassinato, corrupcao) eh um ataque direto e deve ser classificado como DANO_A_IMAGEM.
5. COMUNICACAO: Se detectar uma imputacao de ato ilicito, voce NAO DEVE usar a palavra "crime" na sua analise.
6. ATAQUES INSTITUCIONAIS DIRETOS: Associar tribunais superiores (ex: STF, TSE) ou o sistema democratico a faccoes, ditaduras ou crimes (ex: "tribunal do crime", "ditadura do STF") DEVE ser classificado incondicionalmente como ATAQUE_INSTITUCIONAL (is_hate: true).
7. IDIOMA: Sua resposta (incluindo a analise_pericial) deve ser 100% em Portugues Brasileiro (pt-BR).

--- CONTEXTOS POSITIVOS (NOVO v1.0) ---
Os textos abaixo NAO SAO discurso de odio. Classifique como NEUTRO (is_hate: false):
- Mensagens de aniversario: "Parabens", "Feliz aniversario", "Muitos anos de vida"
- Agradecimentos e bençãos: "Deus abençoe", "Obrigado amigo", "Tudo de bom"
- Saudacoes positivas: "Bom dia abençoado", "Boa tarde a todos"
- Expressoes de apoio nao agressivas: "Forca amigo", "Estamos juntos" (SEM insultos)
- Ancoragem Positiva de Defesa: Se o comentario COMECAR concordando ou elogiando (ex: "Tem razao!", "Perfeito!", "Parabens!", "Estamos combatendo"), ele eh NEUTRO/Apoio. Nao classifique como ataque mesmo que contenha palavras duras em seguida (ex: "destruindo", "ilegais", "fakeniws", "mata").
- IMPORTANTE: "Parabens" em contexto de aniversario ou celebracao pessoal eh SEMPRE NEUTRO.
  Nao confunda com sarcasmo politico — se o texto nao contiver ataque, insulto ou acusacao,
  e for claramente uma mensagem de carinho/celebracao, classifique como NEUTRO.

--- CATEGORIZACAO (MCA v2.4) ---
Se o comentario for hostil (is_hate: true), escolha obrigatoriamente UMA chave exata:
- ODIO_IDENTITARIO: Ataques ou falsa equivalencia moral contra raca, religiao, orientacao sexual (homofobia), misoginia ou regionalismo. Palavras como 'aberracao' voltadas a identidade se encaixam aqui.
- VIOLENCIA_GENERO: Ofensas focadas na condicao feminina.
- AMEACA: Incitacao a dano fisico, violencia fisica ou morte.
- INSULTO_AD_HOMINEM: Desumanizacao (verme, lixo), baixo calao, ataques a honra, aparencia ou competencia.
- ATAQUE_INSTITUCIONAL: Deslegitimacao de orgaos de Estado ou do sistema democratico.
- DANO_A_IMAGEM: Acusacoes de corrupcao, roubo ou infracoes graves contra o alvo.

Se o comentario NAO for hostil (is_hate: false), use:
- NEUTRO: Expressoes de engajamento, slogans ou criticas tecnicas, mensagens de carinho, aniversario, agradecimentos.

--- FORMATO DE RESPOSTA (JSON APENAS) ---
{
  "is_hate": boolean,
  "categoria_ia": "ODIO_IDENTITARIO|VIOLENCIA_GENERO|AMEACA|INSULTO_AD_HOMINEM|ATAQUE_INSTITUCIONAL|DANO_A_IMAGEM|NEUTRO",
  "confianca_ia": float,
  "analise_pericial": "Explicacao curta (sem usar a palavra crime)."
}
"""

LOCAL_SYSTEM_PROMPT = """Voce eh um classificador binario de hostilidade politica baseado no Metodo Vichi-Sentinela.
Atencao redobrada a:
1. Obfuscacao (ex: V5RM5 = Verme -> SUSPEITO).
2. Associacao de minorias a crimes ou a termo 'aberracao' (Homofobia velada -> SUSPEITO).
3. Ataques a instituicoes democraticas, como associar STF/TSE a faccoes (ex: 'tribunal do crime') ou ditadura -> SUSPEITO.
Analise se o texto contem: insultos reais, ameacas, acusacoes de corrupcao ou deslegitimacao.

--- CONTEXTOS POSITIVOS (NOVO v1.0) ---
NAO marque como SUSPEITO textos que sao claramente:
- Mensagens de aniversario: "Parabens amigo", "Feliz aniversario"
- Agradecimentos: "Deus abençoe", "Obrigado", "Tudo de bom"
- Saudacoes positivas: "Bom dia abençoado"
- Expressoes de carinho/apoio SEM insultos
- Ancoragem Positiva de Defesa: Se o texto iniciar com "Tem razao", "Perfeito", "Estamos combatendo" (Apoio), marque NEUTRO ignorando palavras de risco subsequentes (destruir, matar, ilegal).

Responda APENAS com JSON:
{
  "is_hate": boolean,
  "categoria_ia": "NEUTRO|LIXO|SUSPEITO",
  "confianca_ia": float,
  "analise_pericial": "Motivo rapido (sem usar a palavra crime)"
}
IMPORTANTE: Se houver QUALQUER sinal de ataque, obfuscacao ou hostilidade identitaria, marque como "SUSPEITO" para analise posterior. Mas se for claramente uma mensagem de aniversario, parabens ou agradecimento, marque como NEUTRO.
"""

def safe_decode_unicode(s: str) -> str:
    try:
        def decode_escapes(match):
            try:
                return codecs.decode(match.group(0), 'unicode-escape')
            except Exception:
                return match.group(0)
        pattern = r'\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][cdefCDEF][0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}'
        decoded = re.sub(pattern, decode_escapes, s)
        return decoded.encode('utf-8', errors='replace').decode('utf-8')
    except Exception:
        return s

def clean_null_chars(data: Any) -> Any:
    if isinstance(data, str):
        return data.replace("\u0000", "").replace("\x00", "")
    elif isinstance(data, list):
        return [clean_null_chars(item) for item in data]
    elif isinstance(data, dict):
        return {key: clean_null_chars(value) for key, value in data.items()}
    return data


class AIService:
    def __init__(self):
        self.ollama_client = None
        self.mistral_client = None
        self.providers = []
        self.consecutive_failures: Dict[str, int] = {}
        self.fallback_llm = None
        self.current_provider_idx = 0  # v96.0: Pointer para revezamento

        # Cache de I/O em memoria
        self._prompt_cache = {"enriched_local": None, "enriched_cloud": None}
        self.refresh_prompt_cache()

    def _get_next_provider(self):
        """Retorna o proximo provedor saudavel baseado em Round-Robin."""
        now = time.time()
        # Filtra apenas os que nao estao em cooldown
        healthy = [p for p in self.providers if p["cooldown_until"] <= now]
        if not healthy:
            return None

        # Rotacao Round-Robin
        self.current_provider_idx = (self.current_provider_idx + 1) % len(healthy)
        return healthy[self.current_provider_idx]

    def _ensure_clients(self):
        """Inicializa os clientes de IA se ainda nao existirem (Lazy Loading v92.9)."""
        if self.ollama_client is None:
            self.ollama_client = AsyncOpenAI(
                api_key="ollama",
                base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
                max_retries=2,
                http_client=httpx.AsyncClient(timeout=httpx.Timeout(timeout=300.0, connect=60.0))
            )

        if self.mistral_client is None:
            self.mistral_client = AsyncOpenAI(
                api_key=os.getenv("MISTRAL_API_KEY") or "dummy-mistral-key",
                base_url="https://api.mistral.ai/v1",
                max_retries=0
            )

        if not self.providers:
            finetuned_model = os.getenv('FINETUNED_MODEL_NAME', "open-mistral-nemo")
            self.providers = [
                {"name": "mistral", "client": self.mistral_client, "model": finetuned_model, "timeout": 30.0, "cooldown_until": 0.0, "is_async_openai": True},
            ]

            # Alibaba (DashScope) - PASA v52.7
            alibaba_key = os.getenv("ALIBABA_API_KEY") or ""
            if alibaba_key:
                self.providers.append({
                    "name": "alibaba",
                    "client": AsyncOpenAI(api_key=alibaba_key, base_url="https://ws-718h73opsywfpzbv.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"),
                    "model": "qwen-max",
                    "timeout": 45.0,
                    "cooldown_until": 0.0,
                    "is_async_openai": True
                })

            # Ollama (Local - Super Leve)
            self.providers.append({
                "name": "ollama",
                "client": self.ollama_client,
                "model": "phi3",
                "timeout": 30.0,
                "cooldown_until": 0.0,
                "is_async_openai": True
            })

            # Adicionar Google Gemini apenas se a chave estiver configurada
            if os.getenv("GEMINI_API_KEY"):
                self.providers.append({"name": "gemini-2.5-flash", "client": None, "model": "gemini-2.5-flash", "timeout": 45.0, "cooldown_until": 0.0, "is_async_openai": False})

            # Adicionar Claude 3.5 Sonnet (para Fallback de Visao ou Texto via Patch)
            if os.getenv("ANTHROPIC_API_KEY"):
                self.providers.append({"name": "claude-3-5-sonnet", "api_key": os.getenv("ANTHROPIC_API_KEY"), "client": None, "model": "claude-3-5-sonnet", "timeout": 45.0, "cooldown_until": 0.0, "is_async_openai": False})

            try:
                from core.sentinela_ai_config import FALLBACK_PROVIDERS
                for prov in FALLBACK_PROVIDERS:
                    if not any(p["name"] == prov["name"] for p in self.providers):
                        # Pular provedores de fallback se a chave de API obrigatoria estiver ausente
                        api_key_env = prov.get("api_key_env")
                        if api_key_env and not os.getenv(api_key_env):
                            continue

                        self.providers.append({
                            "name": prov["name"],
                            "model": prov.get("model", ""),
                            "timeout": 45.0,
                            "cooldown_until": 0.0,
                            "is_async_openai": False,
                        })
            except Exception as e:
                logger.warning(f"[AI] Falha ao injetar FALLBACK_PROVIDERS: {e}")

    def refresh_prompt_cache(self) -> None:
        """Recarrega arquivos pesados (MD/JSON) do disco e popula o cache de prompts enriquecidos."""
        self._prompt_cache["enriched_local"] = self._build_enrichment(is_local=True)
        self._prompt_cache["enriched_cloud"] = self._build_enrichment(is_local=False)
        logger.debug("[AI] Cache de prompts enriquecidos recarregado com sucesso.")

    def _build_enrichment(self, is_local: bool) -> str:
        """Gera o prompt do zero combinando SYSTEM_PROMPT, PADRONIZACAO e dataset ouro."""
        base_prompt = LOCAL_SYSTEM_PROMPT if is_local else SYSTEM_PROMPT

        # Para modelos locais (Ollama), injetamos uma versao compacta do contexto forense
        if is_local:
            enrichment = "\n\n--- DIRETRIZES ESSENCIAIS (TRIAGEM LOCAL) ---\n"
            enrichment += "- Foque em detectar INSULTOS, AMEACAS e ACUSACOES GRAVES.\n"
            enrichment += "- Se houver hostilidade clara, marque como SUSPEITO.\n"
            enrichment += "- Criticas normais e slogans sao NEUTRO.\n"
            enrichment += "- XENOFOBIA: termos como 'nordestino ingrato/analfabeto/burro' = SUSPEITO.\n"
            enrichment += "- IRONIA: 'Que genio, so faliu 3 empresas!' eh insulto velado = SUSPEITO.\n"
            enrichment += "- HYPE POSITIVO: 'Matou no debate! Bomba de boa!' = NEUTRO.\n"
            enrichment += "- ANIVERSARIO/CELEBRACAO: 'Parabens amigo', 'Feliz aniversario' = NEUTRO.\n"
            enrichment += "- AGRADECIMENTO: 'Deus abençoe', 'Obrigado', 'Tudo de bom' = NEUTRO.\n"
            return base_prompt + enrichment

        # Contexto forense centralizado (PASA v51.0) — Substitui PADRONIZACAO_LINGUISTICA_ANALITICA.md
        contexto = _load_contexto_classificacao()
        if contexto:
            enrichment = "\n\n--- CONTEXTO LINGUISTICO FORENSE (PASA v51.0) ---\n" + contexto + "\n"
        else:
            # Fallback: tenta o arquivo legado
            enrichment = "\n\n--- PADRONIZACAO LINGUISTICA ANALITICA (MD) ---\n"
            if os.path.exists(MD_PATH):
                try:
                    with open(MD_PATH, "r", encoding="utf-8") as f:
                        enrichment += f.read() + "\n"
                except Exception as e:
                    logger.warning(f"[AI] Erro ao ler {MD_PATH}: {e}")
            else:
                enrichment += "(Arquivo de Padronizacao nao encontrado)\n"

        if os.path.exists(CUSTOM_RULES_PATH):
            try:
                with open(CUSTOM_RULES_PATH, "r", encoding="utf-8") as f:
                    rules = json.load(f)
                enrichment += "\n--- DIRETRIZES ADICIONAIS DE PESQUISA (PASA EXTRA) ---\n"
                if "additional_rules" in rules and rules["additional_rules"]:
                    enrichment += "Regras Adicionais de Classificacao:\n" + "\n".join(f"- {r}" for r in rules["additional_rules"]) + "\n"
                if "mitigate_false_positives" in rules and rules["mitigate_false_positives"]:
                    enrichment += "Blindagem Extra contra Falsos Positivos:\n" + "\n".join(f"- {r}" for r in rules["mitigate_false_positives"]) + "\n"
                if "custom_keywords" in rules and rules["custom_keywords"]:
                    enrichment += "Dicionario Lexico Customizado por Categoria:\n" + "\n".join(f"- Categoria {cat}: {', '.join(kw)}" for cat, kw in rules["custom_keywords"].items()) + "\n"
            except Exception as e:
                logger.warning(f"[AI] Erro ao carregar {CUSTOM_RULES_PATH}: {e}")

        if not is_local and os.path.exists(GOLD_DATASET_PATH):
            try:
                with open(GOLD_DATASET_PATH, "r", encoding="utf-8") as f:
                    gold_data = json.load(f)
                if isinstance(gold_data, list) and gold_data:
                    examples = [f"- Texto: \"{str(i.get('text', ''))[:240]}\" -> Categoria: {str(i.get('label', '')).upper()}" for i in gold_data[-10:] if i.get("text") and i.get("label")]
                    if examples:
                        enrichment += "\n\n--- PADRAO OURO AUDITADO ---\nUse estes exemplos como calibracao:\n" + "\n".join(examples) + "\n"
            except Exception as e:
                logger.warning(f"[AI] Erro ao carregar {GOLD_DATASET_PATH}: {e}")

        if "--- FORMATO DE RESPOSTA (JSON APENAS) ---" in base_prompt:
            parts = base_prompt.split("--- FORMATO DE RESPOSTA (JSON APENAS) ---")
            return parts[0] + enrichment + "\n--- FORMATO DE RESPOSTA (JSON APENAS) ---" + parts[1]

        return base_prompt + enrichment

    def _get_system_prompt(self, is_local: bool) -> str:
        cache_key = "enriched_local" if is_local else "enriched_cloud"
        if not self._prompt_cache.get(cache_key):
            self.refresh_prompt_cache()
        return self._prompt_cache[cache_key]

    def _rotate_provider(self, name: str, reason: str = "") -> None:
        """Gira a fila unificada transferindo o provider selecionado para o final."""
        prov = next((p for p in self.providers if p["name"] == name), None)
        if prov:
            self.providers.remove(prov)
            self.providers.append(prov)
            logger.debug(f"[AI] Provedor '{name}' rotacionado. Motivo: {reason}")

    def _remove_provider(self, name: str, reason: str = "") -> None:
        """Remove permanentemente o provedor da fila unificada."""
        prov = next((p for p in self.providers if p["name"] == name), None)
        if prov:
            self.providers.remove(prov)
            logger.warning(f"[AI] Provedor '{name}' REMOVIDO permanentemente. {reason}")

    def _handle_provider_error(self, provider: Dict[str, Any], exception: Exception) -> bool:
        """
        Processa excecoes de APIs e aplica penalidades na fila de providers.
        Retorna `True` se o provider foi removido, `False` se foi rotacionado com cooldown.
        """
        import httpx
        name = provider["name"]

        status_code = getattr(exception, "status_code", None)
        if hasattr(exception, "response") and hasattr(exception.response, "status_code"):
            status_code = exception.response.status_code

        if name == "ollama" and isinstance(exception, (httpx.ConnectError, httpx.ConnectTimeout)):
            status_code = 503

        self.consecutive_failures[name] = self.consecutive_failures.get(name, 0) + 1
        ai_circuit_breaker.record_failure(name, status_code if status_code else 500)

        if name == "ollama" and not ai_circuit_breaker.can_execute(name):
            # Nimrod local: sem watchdog.
            logger.warning("[AI] Ollama local falhou criticamente (Circuit Breaker Aberto).")

        if status_code in [400, 401, 402, 403, 404]:
            # Protecao especial para ollama: nao remover permanentemente em 404, apenas cooldown
            if name == "ollama":
                logger.warning(f"[AI] Ollama retornou 404. Aplicando cooldown em vez de remocao permanente.")
                provider["cooldown_until"] = time.time() + 300.0
                return False
            else:
                self._remove_provider(name, f"Erro Critico de Acesso/Cota/Bad Request ({status_code})")
                return True

        if status_code == 429:
            provider["cooldown_until"] = time.time() + 300.0
            penalty_desc = "300s (Rate Limit 429)"
        else:
            provider["cooldown_until"] = time.time() + 30.0
            penalty_desc = f"30s (Erro {status_code or 'desconhecido'})"

        self._rotate_provider(name, f"Falha temporaria - {penalty_desc} - {str(exception)[:100]}")
        return False

    async def _execute_provider_call(self, provider: Dict[str, Any], final_system_prompt: str, user_content: str, response_format: str, comment_id: str = None, candidato_id: str = None) -> str:
        """Encapsula o dispatch do cliente (AsyncOpenAI vs FallbackLLM)."""
        self._ensure_clients()
        name = provider["name"]

        if name == "ollama":
            from core.health_check import ensure_ollama_running
            ensure_ollama_running()

        if provider.get("is_async_openai", False):
            kwargs = {
                "model": provider["model"],
                "messages": [{"role": "system", "content": final_system_prompt}, {"role": "user", "content": user_content}],
                "temperature": 0.0,
                "timeout": provider.get("timeout", 15.0)
            }
            if response_format and response_format != "text":
                kwargs["response_format"] = {"type": response_format}

            response = await provider["client"].chat.completions.create(**kwargs)
            return response.choices[0].message.content
        else:
            if self.fallback_llm is None:
                from core.fallback_llm import FallbackLLM
                self.fallback_llm = FallbackLLM()

            fallback_text = f"{final_system_prompt}\n\nUser: \"{user_content}\"\n\nResponda estritamente no formato exigido."
            return await asyncio.to_thread(self.fallback_llm.classify, fallback_text, name, comment_id, candidato_id)

    async def classify(self, text: str, comment_id: str = "N/A") -> Dict[str, Any]:
        return await self.classify_text(text, comment_id)

    async def chat_completion(self, prompt: str, system_prompt: str = "Voce eh um assistente tecnico...", response_format: str = "json_object") -> Optional[Dict[str, Any]]:
        active_providers = [p for p in self.providers if p["name"] != "ollama"]
        if not active_providers:
            active_providers = list(self.providers)

        max_attempts = len(active_providers)

        for _ in range(max_attempts):
            provider = active_providers[0]
            name = provider["name"]

            if not ai_circuit_breaker.can_execute(name):
                active_providers.remove(provider)
                active_providers.append(provider)
                self._rotate_provider(name, "Circuito Aberto")
                continue

            now = time.time()
            if now < provider.get("cooldown_until", 0.0):
                await asyncio.sleep(provider["cooldown_until"] - now)

            try:
                base_sys = self._get_system_prompt(is_local=False)
                final_system_prompt = f"{system_prompt}\n\n{base_sys}" if system_prompt not in base_sys else base_sys

                content = await self._execute_provider_call(provider, final_system_prompt, prompt, response_format)

                provider["cooldown_until"] = time.time() + 1.0
                self.consecutive_failures[name] = 0
                ai_circuit_breaker.record_success(name)

                self._rotate_provider(name, "Sucesso (cooldown 1s)")
                active_providers.remove(provider)
                active_providers.append(provider)

                return json.loads(content) if response_format == "json_object" else {"content": content}

            except Exception as e:
                logger.warning(f"[AI] Falha no provider '{name}' em chat_completion: {e}")
                was_removed = self._handle_provider_error(provider, e)
                active_providers.remove(provider)
                if not was_removed:
                    active_providers.append(provider)
                continue

        return None

    async def classify_text(self, text: str, comment_id: str = "N/A", trace_id: str = None, force_cloud: bool = False, force_local: bool = False, candidato_id: str = None) -> Dict[str, Any]:
        self._ensure_clients()

        if not isinstance(text, str):
            text = str(text or "")
        text = text.strip()
        if not text:
            return {"is_hate": False, "categoria_ia": "NEUTRO", "confianca_ia": 1.0, "analise_pericial": "Texto vazio.", "name": "guard"}
        if len(text) > 8000:
            text = text[:8000]

        # =====================================================================
        # FIX v1.0: ContextClassifier - Detector de contextos positivos
        # Verifica se o texto eh claramente benigno ANTES de gastar tokens de LLM
        # =====================================================================
        if ContextClassifier.is_positive_context(text):
            logger.info(f"[AI:ContextClassifier] Contexto positivo detectado para ID {comment_id}. Pulando LLM.")
            return {
                "is_hate": False,
                "categoria_ia": "NEUTRO",
                "confianca_ia": 0.99,
                "analise_pericial": "Contexto benigno detectado: celebracao/agradecimento/parabens. [ContextClassifier v1.0]",
                "name": "context_classifier"
            }
        # =====================================================================

        # v95.0: Leetspeak / Obfuscacao Decoder (V5RM5 -> VERME)
        def decode_leetspeak(t: str) -> str:
            replacements = {
                '5': 'E', '4': 'A', '3': 'E', '1': 'I', '0': 'O', 'I': 'I',
                '@': 'A', '$': 'S', '!': 'I', '7': 'T', '8': 'B'
            }
            # So substitui se houver uma mistura de letras e numeros na palavra para evitar falsos positivos em numeros reais
            words = t.split()
            decoded_words = []
            for w in words:
                if any(c.isdigit() for c in w) and any(c.isalpha() for c in w):
                    for k, v in replacements.items():
                        w = w.replace(k, v).replace(k.lower(), v.lower())
                elif any(c in 'I@$!' for c in w):
                    for k, v in replacements.items():
                        w = w.replace(k, v)
                decoded_words.append(w)
            return " ".join(decoded_words)

        decoded_text = decode_leetspeak(text)

        from core.lexical_filter import lexical_filter
        if lexical_filter.is_junk(decoded_text):
            return {"is_hate": False, "categoria_ia": "NEUTRO", "confianca_ia": 1.0, "analise_pericial": "Filtro lexico.", "name": "lexical"}

        # Roteamento via DSPy se ativado (PASA v98.6)
        if os.getenv("USE_DSPY", "false").lower() == "true":
            try:
                from core.dspy_integration import DSpyClassifierEngine
                engine = DSpyClassifierEngine(self)
                res_dspy = await engine.classificar(decoded_text, force_local=force_local, force_cloud=force_cloud)
                if res_dspy and res_dspy.get("success", False):
                    res_dspy["name"] = "dspy-mesh"
                    return res_dspy
            except Exception as e_dspy:
                logger.warning(f"[AI] Falha no roteamento DSPy: {e_dspy}. Prosseguindo com fallback classico...")

        # Roteamento Inteligente (Nuvem vs Local)
        allowed_providers = self.providers
        if force_cloud:
            allowed_providers = [p for p in self.providers if p["name"] != "ollama"]
        elif force_local:
            allowed_providers = [p for p in self.providers if p["name"] == "ollama"]

        if not allowed_providers:
            allowed_providers = self.providers

        res = None
        for _ in range(len(allowed_providers)):
            now = time.time()
            healthy = [p for p in allowed_providers if p["cooldown_until"] <= now]

            if not healthy:
                await asyncio.sleep(5)
                provider = allowed_providers[0]
            else:
                provider = healthy[self.current_provider_idx % len(healthy)]
                self.current_provider_idx += 1

            name = provider["name"]

            try:
                is_local = "ollama" in name
                final_system_prompt = self._get_system_prompt(is_local)
                user_content = f"Texto: \"{decoded_text}\""

                content = await self._execute_provider_call(provider, final_system_prompt, user_content, "json_object", comment_id, candidato_id)
                res = self._parse_json_response(content)
                res["name"] = name

                # Se obteve sucesso, retorna
                if res and res.get("categoria_ia") != "ERRO":
                    return res

            except Exception as e:
                # Loga falha e aplica o cooldown via _handle_provider_error
                self._handle_provider_error(provider, e)
                continue

        # Se chegar aqui, todos falharam
        return {"is_hate": False, "categoria_ia": "NEUTRO", "confianca_ia": 0.0, "analise_pericial": "Falha geral nos provedores de IA.", "name": "failover"}

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        allowed_categories = {"ODIO_IDENTITARIO", "VIOLENCIA_GENERO", "AMEACA", "INSULTO_AD_HOMINEM", "ATAQUE_INSTITUCIONAL", "DANO_A_IMAGEM", "NEUTRO", "LIXO", "SUSPEITO", "ERRO"}
        fallback = {"is_hate": False, "categoria_ia": "NEUTRO", "confianca_ia": 0.5, "analise_pericial": "Erro parser."}

        parsed = None
        try:
            parsed = json.loads(content)
        except Exception:
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception as e:
                    logger.warning(f"[AI] Falha no fallback de Regex JSON parser: {e}. Payload: {content[:200]}")

        if not isinstance(parsed, dict):
            return fallback

        category = str(parsed.get("categoria_ia", "")).upper().strip()
        if category not in allowed_categories:
            category = "ERRO"

        confidence = parsed.get("confianca_ia", 0.5)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.5
        confidence = max(0.0, min(confidence, 1.0))

        # =====================================================================
        # FIX v1.0: SUSPEITO nao eh discurso de odio — eh apenas triagem
        # Forca is_hate=False para SUSPEITO, pois eh apenas um flag de revisao
        # =====================================================================
        is_hate = bool(parsed.get("is_hate", False))
        if category in {"NEUTRO", "LIXO", "SUSPEITO"}:
            is_hate = False
        # =====================================================================

        analise = str(parsed.get("analise_pericial", "")).strip() or "Sem analise."
        return {"is_hate": is_hate, "categoria_ia": category, "confianca_ia": confidence, "analise_pericial": analise}

    async def run_batch_classification(self, limit: int = 50) -> int:
        # Busca comentarios nao processados no banco e executa a classificacao.
        # Otimizado (v90.0): Implementacao Hibrida. Processamento individual com concorrencia
        # limitada para provedores locais e cloud.
        try:
            from core.db import db_client
            res = await asyncio.to_thread(
                db_client.client.table('comentarios').select('id, texto_bruto, trace_id, candidato_id').eq('processado_ia', False).limit(limit).execute
            )
            items = res.data or []
            if not items:
                return 0

            # -- FAST-DROP TRIAGE (v92.0) --
            try:
                from core.voyant_service import voyant_service
                texts = [item["texto_bruto"] for item in items]
                triage = await voyant_service.triage_batch(texts)
            except Exception as _voyant_exc:
                logger.warning("[AI:Batch] VoyantService falhou inesperadamente: %s. Fallback ao LLM.", _voyant_exc)
                triage = None  # Garante o fallback silencioso

            force_local_batch = False
            force_cloud_batch = False

            if triage is not None:
                if triage["drop"]:
                    logger.info("[AI:Voyant] Lote NEUTRO detectado. Redirecionando exclusivo para Ollama local.")
                    force_local_batch = True
                else:
                    logger.info("[AI:Voyant] Vocabulario hostil detectado: ratio=%.2f%% -- delegando ao LLM. Termos: %s",
                                triage["hostile_ratio"] * 100,
                                ", ".join(sorted(triage.get("hostile_terms", []))[:10]))
                    force_cloud_batch = True
            # -- FIM DO FAST-DROP --

            count = 0

            # v90.0: Paralelismo controlado (Concurrency)
            semaphore = asyncio.Semaphore(5) # Limita a 5 requests paralelos simultaneos para evitar 429/OOM

            async def _process_single(item):
                async with semaphore:
                    try:
                        res_ia = await self.classify_text(
                            item["texto_bruto"],
                            item["id"],
                            trace_id=item.get("trace_id"),
                            force_cloud=force_cloud_batch,
                            force_local=force_local_batch,
                            candidato_id=item.get("candidato_id")
                        )
                        if res_ia and res_ia.get("categoria_ia") != "ERRO":
                            engine_name = res_ia.get("name", "unknown").upper()
                            analise = f"[{engine_name}] {res_ia.get('analise_pericial', '')}"

                            # Analise linguistica via Stanza (PASA v98.6)
                            analise_ling = {}
                            try:
                                from core.stanza_nlp import stanza_nlp
                                com_hostil = res_ia.get("is_hate", False) or res_ia.get("categoria_ia") == "SUSPEITO"
                                analise_ling = stanza_nlp.processar_texto(item["texto_bruto"], include_dependencies=com_hostil)
                            except Exception as e_nlp:
                                logger.warning(f"[AI:Stanza] Falha no processamento do Stanza: {e_nlp}")
                                analise_ling = {"error": str(e_nlp)}

                            # Atualiza no banco
                            await asyncio.to_thread(
                                db_client.client.table('comentarios').update({
                                    "categoria_ia": res_ia["categoria_ia"],
                                    "confianca_ia": res_ia["confianca_ia"],
                                    "is_hate": res_ia["is_hate"],
                                    "analise_pericial": analise,
                                    "processado_ia": True,
                                    "analise_linguistica": analise_ling
                                }).eq("id", item["id"]).execute
                            )

                            # Se for SUSPEITO, sinaliza o subagente de revisao online imediatamente (Pipeline Reativo)
                            if res_ia.get("categoria_ia") == "SUSPEITO":
                                from core.event_bus import local_bus
                                local_bus.signal_new_suspects()

                            # --- TELEMETRIA DE ROTEAMENTO (PASA v98.5) ---
                            try:
                                from core.supabase_service import get_supabase_client
                                db = get_supabase_client()

                                route_decision = "unrouted_llm"
                                if triage is not None:
                                    route_decision = "voyant_local" if force_local_batch else "voyant_cloud"

                                db.table("system_events").insert({
                                    "event_type": "classification_resolved",
                                    "source_module": "ai_service",
                                    "provider_name": res_ia.get("name", "unknown").lower(),
                                    "status": "success",
                                    "metadata": {
                                        "route": route_decision,
                                        "category": res_ia["categoria_ia"],
                                        "confidence": res_ia["confianca_ia"]
                                    }
                                }).execute()
                            except Exception as e:
                                logger.error(f"[AI:Batch] Falha ao registrar telemetria: {e}")

                            return True
                    except Exception as e:
                        if "Colapso" in str(e):
                            raise e
                        logger.debug(f"[AI:Batch] Erro pontual no ID {item['id']}: {e}")
                    return False

            results = await asyncio.gather(*[_process_single(item) for item in items], return_exceptions=True)

            for r in results:
                if isinstance(r, Exception) and "Colapso" in str(r):
                    logger.error("[AI] Colapso detectado nas APIs. Abortando lote.")
                    raise r
                if r is True:
                    count += 1

            return count
        except Exception as e:
            raise e

    async def run_batch_reanalysis(self, limit: int = 15, confidence_threshold: float = 0.6) -> int:
        """
        PASA v94.2 - Majority Vote Reanalysis (IA Mesh):
        Busca registros com baixa confianca e realiza uma nova pericia com 2 provedores Cloud
        distintos para desempate.
        """
        try:
            from core.db import db_client
            # Reduzimos o limite para 15 para evitar overhead massivo de tokens Cloud
            res = await asyncio.to_thread(
                db_client.client.table('comentarios')
                .select('id, texto_bruto, trace_id, candidato_id, analise_pericial, categoria_ia')
                .eq('processado_ia', True)
                .lt('confianca_ia', confidence_threshold)
                .not_.eq('categoria_ia', 'ERRO')
                .order('data_coleta', desc=True)
                .limit(limit)
                .execute
            )
            items = res.data or []
            count = 0

            # Filtra provedores cloud disponiveis
            cloud_providers = [p for p in self.providers if p["name"] != "ollama"]
            if len(cloud_providers) < 2:
                logger.warning("[AI:Mesh] Menos de 2 provedores Cloud ativos. Abortando re-analise profunda de desempate.")
                return 0

            for item in items:
                analise_antiga = item.get("analise_pericial") or ""
                if "[RE-ANALISE:" in analise_antiga:
                    continue

                try:
                    # [V2.4] Integracao Voyant em Re-analise (PASA v95.5)
                    voyant_insight = ""
                    try:
                        from core.voyant_service import voyant_service
                        # Triagem rapida antes de gastar tokens
                        triage = await voyant_service.triage_batch([item['texto_bruto']])
                        if triage and item['texto_bruto'] in triage:
                            ratio = triage[item['texto_bruto']].get('hostile_ratio', 0)
                            voyant_insight = f"[Voyant-Pericial: {ratio:.1%}] "
                    except Exception as e_v:
                        logger.debug("[Voyant:Mesh] Falha na triagem durante re-analise: %s", e_v)

                    # Executa 2 chamadas paralelas com provedores diferentes
                    p1, p2 = random.sample(cloud_providers, 2)

                    logger.info(f"[AI:Mesh] Iniciando desempate {voyant_insight}para ID {item['id']} ({p1['name']} vs {p2['name']})")

                    tasks = [
                        self._execute_provider_call(p1, self._get_system_prompt(False), f"Texto: \"{item['texto_bruto']}\"", "json_object", item['id'], item['candidato_id']),
                        self._execute_provider_call(p2, self._get_system_prompt(False), f"Texto: \"{item['texto_bruto']}\"", "json_object", item['id'], item['candidato_id'])
                    ]

                    responses = await asyncio.gather(*tasks, return_exceptions=True)
                    valid_res = []
                    for i, r in enumerate(responses):
                        if isinstance(r, Exception): continue
                        parsed = self._parse_json_response(r)
                        parsed["provider"] = [p1, p2][i]["name"]
                        valid_res.append(parsed)

                    if not valid_res: continue

                    # Logica de Desempate (Majority Vote / Highest Confidence)
                    # 1. Se houver consenso de categoria, usa essa categoria com media de confianca.
                    # 2. Se houver divergencia, usa a de maior confianca.

                    final_category = valid_res[0]["categoria_ia"]
                    final_confidence = valid_res[0]["confianca_ia"]
                    final_is_hate = valid_res[0]["is_hate"]
                    engine_tag = valid_res[0]["provider"]

                    if len(valid_res) == 2:
                        if valid_res[0]["categoria_ia"] == valid_res[1]["categoria_ia"]:
                            final_confidence = (valid_res[0]["confianca_ia"] + valid_res[1]["confianca_ia"]) / 2
                            engine_tag = f"CONSENSUS:{valid_res[0]['provider']}+{valid_res[1]['provider']}"
                        else:
                            # Divergencia: pega o de maior confianca
                            winner = valid_res[0] if valid_res[0]["confianca_ia"] >= valid_res[1]["confianca_ia"] else valid_res[1]
                            final_category = winner["categoria_ia"]
                            final_confidence = winner["confianca_ia"]
                            final_is_hate = winner["is_hate"]
                            engine_tag = f"SPLIT:WINNER={winner['provider']}"

                    tag_status = "FINALIZADA" if final_confidence < confidence_threshold else "CONCLUIDA"
                    analise = f"[RE-ANALISE:{tag_status}:{engine_tag.upper()}] {valid_res[0].get('analise_pericial', '')}"

                    await asyncio.to_thread(
                        db_client.client.table('comentarios').update({
                            "categoria_ia": final_category,
                            "confianca_ia": final_confidence,
                            "is_hate": final_is_hate,
                            "analise_pericial": analise
                        }).eq("id", item["id"]).execute
                    )
                    count += 1

                    await asyncio.sleep(2.0) # Backoff entre itens de re-analise

                except Exception as e:
                    logger.error(f"[AI:Mesh] Erro no desempate do ID {item['id']}: {e}")
                    if "Colapso" in str(e): break

            return count
        except Exception as e:
            raise e

    async def push_custom_rules_to_providers(self) -> None:
        pass

    async def run_batch_online_review(self, limit: int = 50) -> int:
        # Busca comentarios marcados como SUSPEITO no banco e executa a reclassificacao online (Cloud).
        try:
            from core.db import db_client
            res = await asyncio.to_thread(
                db_client.client.table('comentarios').select('id, texto_bruto, trace_id, candidato_id').eq('categoria_ia', 'SUSPEITO').limit(limit).execute
            )
            items = res.data or []
            count = 0
            for item in items:
                try:
                    res_ia = await self.classify_text(item["texto_bruto"], item["id"], trace_id=item.get("trace_id"), force_cloud=True, candidato_id=item.get("candidato_id"))
                    if res_ia and res_ia.get("categoria_ia") not in ["ERRO", "SUSPEITO"]:
                        engine_name = res_ia.get("name", "unknown").upper()
                        analise = f"[REVISAO:{engine_name}] {res_ia.get('analise_pericial', '')}"
                        await asyncio.to_thread(
                            db_client.client.table('comentarios').update({
                                "categoria_ia": res_ia["categoria_ia"],
                                "confianca_ia": res_ia["confianca_ia"],
                                "is_hate": res_ia["is_hate"],
                                "analise_pericial": analise,
                                "processado_ia": True
                            }).eq("id", item["id"]).execute
                        )
                        count += 1

                    await asyncio.sleep(2.0)
                except Exception as e:
                    logger.error(f"[AI] Erro ao processar revisao do ID {item['id']}: {e}")
                    if "Colapso" in str(e):
                        logger.error("[AI] Colapso detectado nas APIs Cloud. Abortando lote de revisao.")
                        raise e
            return count
        except Exception as e:
            raise e

    async def vision_completion(self, image_b64: str, prompt: str,
                                cache_key: str | None = None,
                                mime_type: str = "image/png") -> dict:
        """
        Chamada multimodal (imagem + texto) para modelos de visao.
        Roteia exclusivamente para provedores com suporte a visao (Gemini Flash).
        NAO usa Maritaca/Ollama/Mistral.
        """
        from core.ai_service_vision_patch import vision_completion as _vision_impl
        return await _vision_impl(self, image_b64, prompt, cache_key, mime_type)

ai_service = AIService()
