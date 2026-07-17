# report/charts.py
"""
Geração de gráficos reais (matplotlib) para embutir no PDF do Nimrod.
Cada função retorna o caminho do PNG gerado em um diretório temporário.
"""
import os
import tempfile
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Paleta consistente com a identidade visual do dossiê (tons usados no report_generator original)
COLOR_PRIMARY = "#2563EB"
COLOR_DANGER = "#DC2626"
COLOR_SUCCESS = "#10B981"
COLOR_MUTED = "#94A3B8"
CATEGORY_COLORS = {
    "NEUTRO": "#94A3B8",
    "AMEACA": "#F97316",
    "ODIO_IDENTITARIO": "#DC2626",
    "VIOLENCIA_GENERO": "#DB2777",
    "RIGOR_CRIMINAL": "#06B6D4",
    "INSULTO_AD_HOMINEM": "#8B5CF6",
    "ATAQUE_INSTITUCIONAL": "#7C3AED",
    "ERRO_CLASSIFICACAO": "#1E293B",
}

_tmp_dir = tempfile.mkdtemp(prefix="nimrod_charts_")


def _save(fig, name: str) -> str:
    path = os.path.join(_tmp_dir, name)
    fig.savefig(path, dpi=180, bbox_inches="tight", transparent=False)
    plt.close(fig)
    return path


def chart_category_donut(comments: List[Dict[str, Any]]) -> str:
    """Donut chart: distribuição de comentários por categoria PASA."""
    counts = Counter(c.get("categoria_ia", "NEUTRO") for c in comments)
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    colors = [CATEGORY_COLORS.get(l, COLOR_MUTED) for l in labels]

    fig, ax = plt.subplots(figsize=(5, 5))
    wedges, _ = ax.pie(values, colors=colors, startangle=90, wedgeprops=dict(width=0.42, edgecolor="white"))
    total = sum(values)
    ax.text(0, 0.05, f"{total}", ha="center", va="center", fontsize=26, fontweight="bold", color="#1E293B")
    ax.text(0, -0.15, "comentários", ha="center", va="center", fontsize=10, color="#64748B")
    ax.legend(
        wedges, [f"{l} ({counts[l]})" for l in labels],
        loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=9,
    )
    ax.set_title("Distribuição por Categoria (PASA)", fontsize=12, fontweight="bold", color="#1E293B", pad=15)
    return _save(fig, "categoria_donut.png")


def chart_volume_por_post(comments: List[Dict[str, Any]]) -> str:
    """Barras: volume total e volume hostil de comentários por post."""
    by_post: Dict[str, Dict[str, Any]] = {}
    for c in comments:
        pid = str(c.get("post_shortcode") or c.get("post_id") or "?")
        if pid not in by_post:
            dt_str = str(c.get("data_publicacao") or c.get("data_coleta") or "")
            date_label = ""
            if len(dt_str) >= 10:
                # Format to DD/MM
                date_label = f"({dt_str[8:10]}/{dt_str[5:7]})"
            by_post[pid] = {"total": 0, "hate": 0, "date": date_label}
            
        by_post[pid]["total"] += 1
        if c.get("is_hate"):
            by_post[pid]["hate"] += 1

    posts = list(by_post.keys())
    totals = [by_post[p]["total"] for p in posts]
    hates = [by_post[p]["hate"] for p in posts]
    
    # Human readable labels
    labels = [f"Post {i+1} {by_post[p]['date']}".strip() for i, p in enumerate(posts)]

    fig, ax = plt.subplots(figsize=(8, 4))
    x = range(len(posts))
    ax.bar(x, totals, color=COLOR_MUTED, label="Total")
    ax.bar(x, hates, color=COLOR_DANGER, label="Hostis")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Comentários")
    ax.set_title("Volume de Comentários por Post", fontsize=12, fontweight="bold", color="#1E293B")
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return _save(fig, "volume_por_post.png")


def chart_timeline(comments: List[Dict[str, Any]]) -> str:
    """Linha: comentários hostis ao longo do tempo (por dia)."""
    by_day: Dict[str, int] = {}
    for c in comments:
        if not c.get("is_hate"):
            continue
        raw = c.get("data_publicacao") or c.get("data_coleta") or ""
        day = str(raw)[:10] or "N/A"
        by_day[day] = by_day.get(day, 0) + 1

    days = sorted(by_day.keys())
    values = [by_day[d] for d in days]

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(days, values, color=COLOR_DANGER, marker="o", linewidth=2)
    ax.fill_between(days, values, color=COLOR_DANGER, alpha=0.12)
    ax.set_xticks(range(len(days)))
    ax.set_xticklabels(days, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Comentários hostis")
    ax.set_title("Evolução de Hostilidade no Tempo", fontsize=12, fontweight="bold", color="#1E293B")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return _save(fig, "timeline.png")


def chart_risk_gauge(risk_pct: float) -> str:
    """Gauge semicircular de severidade (infográfico)."""
    fig, ax = plt.subplots(figsize=(5, 3), subplot_kw={"aspect": "equal"})
    color = COLOR_DANGER if risk_pct > 20 else ("#F59E0B" if risk_pct > 10 else COLOR_SUCCESS)

    ax.pie(
        [risk_pct, 100 - risk_pct] if risk_pct <= 100 else [100, 0],
        colors=[color, "#E2E8F0"],
        startangle=180,
        counterclock=False,
        wedgeprops=dict(width=0.35, edgecolor="white"),
        radius=1.0,
    )
    ax.text(0, -0.1, f"{risk_pct:.1f}%", ha="center", va="center", fontsize=24, fontweight="bold", color=color)
    ax.set_ylim(-0.2, 1.1)
    ax.set_title("Índice de Severidade Analítica", fontsize=12, fontweight="bold", color="#1E293B")
    return _save(fig, "risk_gauge.png")


def chart_top_autores(comments: List[Dict[str, Any]], top_n: int = 10) -> str:
    """Barras horizontais: autores com mais comentários hostis."""
    counts = Counter(c.get("autor_username", "anon") for c in comments if c.get("is_hate"))
    top = counts.most_common(top_n)
    if not top:
        top = [("(nenhum autor hostil identificado)", 0)]
    names, values = zip(*reversed(top))

    fig, ax = plt.subplots(figsize=(7, max(2.5, 0.4 * len(names))))
    ax.barh(names, values, color=COLOR_DANGER)
    ax.set_xlabel("Comentários hostis")
    ax.set_title("Top Autores Mais Hostis", fontsize=12, fontweight="bold", color="#1E293B")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return _save(fig, "top_autores.png")


def chart_temas(cluster_counts: Counter) -> str:
    """Barras: tamanho dos clusters temáticos identificados no discurso hostil."""
    if not cluster_counts:
        cluster_counts = Counter({"Sem clusters": 0})
    labels = list(cluster_counts.keys())
    values = list(cluster_counts.values())

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.bar(labels, values, color=COLOR_PRIMARY)
    ax.set_ylabel("Comentários")
    ax.set_title("Temas Identificados no Discurso Hostil", fontsize=12, fontweight="bold", color="#1E293B")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    
    # Ensure long text doesn't overlap
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=9)
    
    fig.tight_layout()
    return _save(fig, "temas.png")
