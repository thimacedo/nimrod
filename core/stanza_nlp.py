# -*- coding: utf-8 -*-
"""
core/stanza_nlp.py - Motor de NLP Baseado no Stanford Stanza (PASA v98.6)
═══════════════════════════════════════════════════════════════════════
Implementação robusta da Metodologia Vichi-Sentinela de análise linguística:
- Lematização e POS Tagging neurais de alta precisão para pt-BR.
- Execução resiliente offline e CPU-only (sem GPU).
- Processadores seletivos para otimizar velocidade (tokenize, mwt, pos, lemma).
- Parser de dependência sintática (depparse) disponível sob demanda.
"""

import os
import logging
import stanza
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter

logger = logging.getLogger("core.stanza_nlp")

class StanzaNLPEngine:
    def __init__(self):
        self.nlp = None
        self.nlp_with_dep = None
        self._initialized = False
        self._initialized_dep = False
        
        # Garante o diretório de recursos
        self.resources_dir = os.getenv("STANZA_RESOURCES_DIR", os.path.expanduser("~/stanza_resources"))
        os.makedirs(self.resources_dir, exist_ok=True)

    def _ensure_model_downloaded(self, lang: str = "pt"):
        """Garante que os recursos do modelo em português estejam baixados localmente."""
        lang_dir = os.path.join(self.resources_dir, lang)
        if not os.path.exists(lang_dir) or not os.listdir(lang_dir):
            logger.info(f"[Stanza] Modelo em português não localizado em '{self.resources_dir}'. Iniciando download...")
            stanza.download(lang=lang, model_dir=self.resources_dir)
            logger.info("[Stanza] Download do modelo concluído.")
        else:
            logger.debug("[Stanza] Recursos do modelo localizados em cache.")

    def _ensure_pipeline(self):
        """Inicializa o pipeline básico (tokenize, mwt, pos, lemma) em CPU."""
        if self._initialized:
            return

        try:
            self._ensure_model_downloaded("pt")
            logger.info("[Stanza] Inicializando pipeline básico (CPU-only)...")
            self.nlp = stanza.Pipeline(
                lang="pt",
                model_dir=self.resources_dir,
                processors="tokenize,mwt,pos,lemma",
                use_gpu=False,  # Força execução em CPU para portabilidade e resiliência
                logging_level="WARN"
            )
            self._initialized = True
            logger.info("[Stanza] Pipeline básico carregado com sucesso.")
        except Exception as e:
            logger.error(f"[Stanza] Falha crítica ao inicializar pipeline básico: {e}")
            self._initialized = False
            raise e

    def _ensure_dep_pipeline(self):
        """Inicializa o pipeline completo com dependências sintáticas (depparse) sob demanda."""
        if self._initialized_dep:
            return

        try:
            self._ensure_model_downloaded("pt")
            logger.info("[Stanza] Inicializando pipeline completo com dependências (depparse) em CPU...")
            self.nlp_with_dep = stanza.Pipeline(
                lang="pt",
                model_dir=self.resources_dir,
                processors="tokenize,mwt,pos,lemma,depparse",
                use_gpu=False,
                logging_level="WARN"
            )
            self._initialized_dep = True
            logger.info("[Stanza] Pipeline com dependências carregado com sucesso.")
        except Exception as e:
            logger.error(f"[Stanza] Falha crítica ao inicializar pipeline com dependências: {e}")
            self._initialized_dep = False
            raise e

    def processar_texto(self, text: str, include_dependencies: bool = False) -> Dict[str, Any]:
        """
        Executa a normalização Vichi-Sentinela em um texto bruto:
        - Lematização precisa.
        - POS Tags (etiquetas morfossintáticas).
        - Divisão de sentenças por limite de frase da rede neural.
        - Opcional: Relações de dependências sintáticas (Sujeito -> Verbo -> Objeto/Adjetivo).
        """
        if not text or not text.strip():
            return {
                "lemmas": [],
                "pos_tags": [],
                "sentences": [],
                "dependencies": [],
                "error": "Texto vazio"
            }

        try:
            # Seleciona o pipeline correto
            if include_dependencies:
                self._ensure_dep_pipeline()
                doc = self.nlp_with_dep(text)
            else:
                self._ensure_pipeline()
                doc = self.nlp(text)

            lemmas_list = []
            pos_tags = []
            sentences_list = []
            dependencies = []

            for sent in doc.sentences:
                sent_lemmas = []
                for word in sent.words:
                    # Coleta POS Tags e Lemmas relevantes (foco em VERB, NOUN, ADJ, PROPN)
                    pos_clean = word.upos or "UNKNOWN"
                    lemma_clean = (word.lemma or word.text or "").strip().lower()
                    
                    if pos_clean in {"VERB", "NOUN", "ADJ", "PROPN"}:
                        if len(lemma_clean) > 1 and lemma_clean not in {"-pron-", "—"}:
                            lemmas_list.append(lemma_clean)
                            pos_tags.append({
                                "text": word.text,
                                "lemma": lemma_clean,
                                "pos": pos_clean
                            })
                    
                    # Salva lemmas de todos os tokens válidos da sentença para n-gramas
                    if pos_clean not in {"PUNCT", "SYM", "SPACE"}:
                        sent_lemmas.append(lemma_clean)

                    # Se o parser de dependência for usado
                    if include_dependencies and hasattr(word, "deprel"):
                        dependencies.append({
                            "word": word.text,
                            "pos": pos_clean,
                            "lemma": lemma_clean,
                            "deprel": word.deprel,
                            "head_text": sent.words[word.head - 1].text if word.head > 0 else "ROOT",
                            "head_pos": sent.words[word.head - 1].upos if word.head > 0 else "ROOT"
                        })

                if sent_lemmas:
                    sentences_list.append(sent_lemmas)

            return {
                "lemmas": lemmas_list,
                "pos_tags": pos_tags,
                "sentences": sentences_list,
                "dependencies": dependencies,
                "success": True
            }

        except Exception as e:
            logger.error(f"[Stanza] Falha ao processar texto: {e}")
            # Fallback resiliente básico em caso de colapso do Stanza
            tokens = [w.strip().lower() for w in text.split() if len(w.strip()) > 1]
            return {
                "lemmas": tokens,
                "pos_tags": [{"text": t, "lemma": t, "pos": "UNKNOWN"} for t in tokens],
                "sentences": [tokens],
                "dependencies": [],
                "success": False,
                "error": str(e)
            }

    def extrair_ngrams(self, sentences: List[List[str]], n: int) -> List[Tuple[str, int]]:
        """
        Extrai n-gramas de lemmas respeitando estritamente a fronteira de sentenças.
        Garante que a análise de n-gramas nunca cruze o final de uma frase.
        """
        counter = Counter()
        for sent in sentences:
            if len(sent) < n:
                continue
            for i in range(len(sent) - n + 1):
                gram = tuple(sent[i:i+n])
                counter[gram] += 1
        return counter.most_common()

# Instancia singleton
stanza_nlp = StanzaNLPEngine()
