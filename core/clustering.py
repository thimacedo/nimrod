# core/clustering.py
"""
Clusterização temática standalone (adaptado de processing/data_miner.py do Sentinela).
Sem fila, sem Supabase — opera inteiramente sobre uma lista de comentários em memória.
"""
import os
import logging
from collections import Counter
from typing import List, Dict, Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

from core.stanza_nlp import stanza_nlp

logger = logging.getLogger("core.clustering")


def _load_glove_embeddings() -> Dict[str, np.ndarray]:
    """Carrega embeddings GloVe locais se disponíveis; senão, cai para TF-IDF."""
    glove_path = os.getenv("GLOVE_PATH") or "data/glove_s50.txt"
    embeddings: Dict[str, np.ndarray] = {}
    if not os.path.exists(glove_path):
        logger.debug(f"[GloVe] Arquivo não encontrado em '{glove_path}'. Usando TF-IDF lematizado.")
        return embeddings
    try:
        with open(glove_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if not parts or len(parts) < 2:
                    continue
                embeddings[parts[0]] = np.array(parts[1:], dtype=np.float32)
        logger.info(f"[GloVe] {len(embeddings)} palavras carregadas.")
    except Exception as e:
        logger.warning(f"[GloVe] Falha ao carregar embeddings: {e}")
    return embeddings


def _lemmatize(comments: List[Dict[str, Any]]) -> List[str]:
    """Lematiza cada comentário com Stanza (POS/lemas), com fallback pro texto bruto."""
    texts = []
    for c in comments:
        texto = str(c.get("texto_bruto") or c.get("texto_limpo") or "").strip()
        lemmas = []
        try:
            res = stanza_nlp.processar_texto(texto)
            lemmas = res.get("lemmas", [])
        except Exception as e:
            logger.debug(f"[Clustering] Falha ao lematizar comentário: {e}")
        texts.append(" ".join(lemmas) if lemmas else texto)
    return texts


def cluster_temas(comments: List[Dict[str, Any]], min_items: int = 5) -> List[Dict[str, Any]]:
    """
    Agrupa comentários hostis (is_hate=True) por tema semântico.
    Retorna a mesma lista de comentários com o campo `cluster_id` preenchido nos itens
    que entraram em algum grupo (None para os demais).
    """
    hate_items = [c for c in comments if c.get("is_hate")]
    if len(hate_items) < min_items:
        logger.info("[Clustering] Amostra insuficiente para clusterização temática real.")
        return comments

    lemmatized = _lemmatize(hate_items)
    if not any(lemmatized):
        return comments

    glove = _load_glove_embeddings()
    if glove:
        dim = next(iter(glove.values())).shape[0]
        X_list = []
        for text in lemmatized:
            vectors = [glove[w] for w in text.split() if w in glove]
            X_list.append(np.mean(vectors, axis=0) if vectors else np.zeros(dim, dtype=np.float32))
        X = np.array(X_list)
    else:
        vectorizer = TfidfVectorizer(max_features=100, stop_words=None)
        X = vectorizer.fit_transform(lemmatized)

    n_clusters = min(5, max(2, len(hate_items) // 3))
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    cluster_words = {}
    for text, label in zip(lemmatized, labels):
        cluster_words.setdefault(label, []).extend(text.split())
    
    cluster_names = {}
    for label, words in cluster_words.items():
        valid_words = [w for w in words if len(w) > 3]
        if valid_words:
            from collections import Counter as Cnt
            top_words = [w for w, c in Cnt(valid_words).most_common(2)]
            cluster_names[label] = " / ".join(top_words).title()
        else:
            cluster_names[label] = f"Tema {label}"

    for item, label in zip(hate_items, labels):
        item["cluster_id"] = int(label)
        item["cluster_name"] = cluster_names[label]

    logger.info(f"[Clustering] {len(hate_items)} comentários hostis agrupados em {n_clusters} temas.")
    return comments


def extrair_ngrams(comments: List[Dict[str, Any]], n: int = 2, top_k: int = 20) -> List[Any]:
    """Extrai os n-gramas (bigramas/trigramas) periciais mais frequentes via Stanza."""
    all_sentences = []
    for c in comments:
        texto = str(c.get("texto_bruto") or "").strip()
        if not texto:
            continue
        try:
            res = stanza_nlp.processar_texto(texto)
            for sent in res.get("sentences", []):
                if sent:
                    all_sentences.append(sent)
        except Exception:
            continue
    if not all_sentences:
        return []
    try:
        return stanza_nlp.extrair_ngrams(all_sentences, n)[:top_k]
    except Exception as e:
        logger.debug(f"[Clustering] Falha ao extrair n-gramas via Stanza: {e}")
        return []


def top_temas_labels(comments: List[Dict[str, Any]]) -> Counter:
    """Conta quantos comentários caíram em cada cluster_id (para gráfico/tabela)."""
    return Counter(c.get("cluster_name") or f"Tema {c.get('cluster_id')}" for c in comments if c.get("cluster_id") is not None)
