# report/pdf_builder.py
"""
Gerador de relatório PDF do Nimrod (evolução do processing/report_generator.py do Sentinela).

Diferenças em relação ao original:
  - Gráficos reais (matplotlib) em vez de retângulos desenhados manualmente
  - Tabelas: top autores, top n-gramas, resumo de temas
  - Infográfico de comportamento coordenado (bots/clusters)
  - Relatório completo do perfil (não só evidências de ódio)
"""
import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from fpdf import FPDF

from core.integrity import assert_dados_reais, DadosNaoReaisError
from report.charts import (
    chart_category_donut,
    chart_volume_por_post,
    chart_timeline,
    chart_risk_gauge,
    chart_top_autores,
    chart_temas,
)


class NimrodReport(FPDF):
    def __init__(self):
        super().__init__()
        self.primary_color = (37, 99, 235)
        self.danger_color = (220, 38, 38)
        self.success_color = (16, 185, 129)
        self.bg_color = (248, 250, 252)
        self.set_auto_page_break(auto=True, margin=15)

    # ---------------------------------------------------------------- utils
    def clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = (
            str(text)
            .replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2013", "-").replace("\u2014", "-")
        )
        return text.encode("latin-1", "replace").decode("latin-1")

    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(148, 163, 184)
            self.cell(0, 10, "NIMROD | RELATORIO DE PERFIL", align="L")
            self.set_x(0)
            self.cell(0, 10, f'{datetime.now().strftime("%d/%m/%Y")}', align="R")
            self.ln(12)
            self.set_draw_color(226, 232, 240)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 10, f"Pagina {self.page_no()} | Gerado automaticamente pelo Nimrod", align="C")

    def section_title(self, title: str):
        if self.get_y() > 250:
            self.add_page()
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*self.primary_color)
        self.cell(0, 10, title, ln=True)
        self.set_draw_color(*self.primary_color)
        self.line(10, self.get_y(), 55, self.get_y())
        self.ln(6)

    def kpi_row(self, kpis: List[tuple]):
        """Renderiza uma fileira de cartões KPI (label, valor)."""
        n = len(kpis)
        card_w = 190 / n
        y0 = self.get_y()
        for i, (label, value) in enumerate(kpis):
            x = 10 + i * card_w
            self.set_xy(x, y0)
            self.set_fill_color(*self.bg_color)
            self.set_draw_color(226, 232, 240)
            self.rect(x, y0, card_w - 3, 22, "DF")
            self.set_xy(x + 2, y0 + 3)
            self.set_font("Helvetica", "B", 16)
            self.set_text_color(*self.primary_color)
            self.cell(card_w - 6, 8, str(value), align="C")
            self.set_xy(x + 2, y0 + 13)
            self.set_font("Helvetica", "", 7.5)
            self.set_text_color(100, 116, 139)
            self.cell(card_w - 6, 5, self.clean_text(label), align="C")
        self.set_y(y0 + 27)

    def add_image_full(self, path: str, w: int = 190):
        if self.get_y() > 220:
            self.add_page()
        self.image(path, x=10, w=w)
        self.ln(4)

    def add_image_pair(self, path_left: str, path_right: str, w: int = 92):
        if self.get_y() > 220:
            self.add_page()
        y = self.get_y()
        self.image(path_left, x=10, y=y, w=w)
        self.image(path_right, x=108, y=y, w=w)
        self.ln(max(70, 5))

    def table(self, headers: List[str], rows: List[List[str]], col_widths: List[int]):
        self.set_font("Helvetica", "B", 8.5)
        self.set_fill_color(*self.primary_color)
        self.set_text_color(255, 255, 255)
        for h, w in zip(headers, col_widths):
            self.cell(w, 7, self.clean_text(h), border=1, align="C", fill=True)
        self.ln()
        self.set_font("Helvetica", "", 8)
        self.set_text_color(30, 41, 59)
        fill = False
        for row in rows:
            if self.get_y() > 265:
                self.add_page()
                self.set_font("Helvetica", "B", 8.5)
                self.set_fill_color(*self.primary_color)
                self.set_text_color(255, 255, 255)
                for h, w in zip(headers, col_widths):
                    self.cell(w, 7, self.clean_text(h), border=1, align="C", fill=True)
                self.ln()
                self.set_font("Helvetica", "", 8)
                self.set_text_color(30, 41, 59)
            self.set_fill_color(248, 250, 252) if fill else self.set_fill_color(255, 255, 255)
            for val, w in zip(row, col_widths):
                self.cell(w, 6.5, self.clean_text(str(val)), border=1, fill=True)
            self.ln()
            fill = not fill
        self.ln(4)

    def render_integrity_note(self, data: Dict[str, Any]):
        """Seção obrigatória: mostra abertamente o que funcionou, o que falhou, e nunca esconde erro."""
        counters = data.get("classification_counters") or {"sucesso": 0, "falha": 0}
        stage_status = data.get("stage_status") or {}

        self.add_page()
        self.section_title("NOTA DE INTEGRIDADE DOS DADOS")
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 5.5, self.clean_text(
            "Todos os comentarios deste relatorio vem de coleta real do perfil analisado "
            "(nenhum dado sintetico, mockado ou de exemplo e aceito pelo gerador de relatorio "
            "- trava tecnica em core/integrity.py). Falhas de qualquer etapa sao declaradas "
            "abaixo em vez de omitidas ou disfarcadas de resultado positivo."
        ))
        self.ln(4)

        rows = [
            ["Classificacao de IA - sucesso", str(counters.get("sucesso", 0))],
            ["Classificacao de IA - falha (ERRO_CLASSIFICACAO)", str(counters.get("falha", 0))],
        ]
        for etapa, status in stage_status.items():
            rows.append([f"Etapa: {etapa}", status])
        self.table(["Item", "Resultado"], rows, [130, 60])

        if counters.get("falha", 0) > 0:
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*self.danger_color)
            self.multi_cell(0, 5, self.clean_text(
                f"Atencao: {counters['falha']} comentario(s) nao puderam ser classificados "
                f"(falha da cascata de IA) e aparecem marcados como ERRO_CLASSIFICACAO no "
                f"grafico de categorias, EXCLUIDOS das contagens de discurso hostil. Eles nao "
                f"foram contados como 'neutros' nem como 'hostis' - permanecem como incerteza "
                f"declarada."
            ))

    # ------------------------------------------------------------- páginas
    def render_cover(self, username: str, total: int, total_hate: int, scrape_stats: Dict[str, Any]):
        self.add_page()
        self.set_fill_color(*self.primary_color)
        self.rect(0, 0, 210, 60, "F")

        self.set_y(20)
        self.set_font("Helvetica", "B", 30)
        self.set_text_color(255, 255, 255)
        self.cell(0, 15, "RELATORIO NIMROD", ln=True, align="C")
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 10, "ANALISE COMPLETA DE PERFIL - INSTAGRAM", ln=True, align="C")

        self.set_y(80)
        self.set_text_color(30, 41, 59)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 10, "PERFIL ANALISADO", ln=True, align="C")
        self.set_font("Helvetica", "B", 24)
        self.set_text_color(*self.primary_color)
        self.cell(0, 15, f"@{username}", ln=True, align="C")

        risk_pct = (total_hate / total * 100) if total else 0.0
        gauge_path = chart_risk_gauge(risk_pct)
        self.image(gauge_path, x=55, y=self.get_y() + 5, w=100)
        self.set_y(self.get_y() + 60)

        self.set_font("Helvetica", "", 10.5)
        self.set_text_color(71, 85, 105)
        self.multi_cell(0, 6, (
            f"Este relatorio consolida a analise de {total} comentarios coletados dos posts mais recentes "
            f"do perfil (posts fixados foram ignorados), com classificacao de discurso de odio, analise "
            f"linguistica, deteccao de comportamento coordenado e agrupamento tematico."
        ), align="C")

        self.ln(8)
        self.set_font("Helvetica", "I", 8.5)
        self.set_text_color(148, 163, 184)
        self.cell(0, 5, f"Posts analisados: {scrape_stats.get('posts_scraped', 0)} | Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}", ln=True, align="C")

    def render_executive_summary(self, data: Dict[str, Any]):
        comments = data["comments"]
        total = len(comments)
        total_hate = len([c for c in comments if c.get("is_hate")])
        autores_unicos = len(set(c.get("autor_username") for c in comments))
        coord = data.get("coordinated_clusters", 0)

        self.add_page()
        self.section_title("RESUMO EXECUTIVO")
        self.kpi_row([
            ("Comentarios analisados", total),
            ("Hostis detectados", total_hate),
            ("Autores unicos", autores_unicos),
            ("Clusters coordenados", coord),
        ])

        donut_path = chart_category_donut(comments)
        self.add_image_full(donut_path, w=130)

    def render_volume_e_tempo(self, comments: List[Dict[str, Any]]):
        self.add_page()
        self.section_title("VOLUME E EVOLUCAO TEMPORAL")
        vol_path = chart_volume_por_post(comments)
        self.add_image_full(vol_path)
        if any(c.get("is_hate") for c in comments):
            timeline_path = chart_timeline(comments)
            self.add_image_full(timeline_path)

    def render_autores(self, comments: List[Dict[str, Any]]):
        self.add_page()
        self.section_title("AUTORES MAIS HOSTIS")
        chart_path = chart_top_autores(comments)
        self.add_image_full(chart_path, w=160)

        counts = Counter(c.get("autor_username", "anon") for c in comments if c.get("is_hate"))
        rows = [[f"@{a}", str(n)] for a, n in counts.most_common(15)]
        if rows:
            self.table(["Autor", "Comentarios hostis"], rows, [140, 50])

    def render_linguistica(self, data: Dict[str, Any]):
        bigrams = data.get("ngrams_bigrams") or []
        trigrams = data.get("ngrams_trigrams") or []
        if not bigrams and not trigrams:
            return
        self.add_page()
        self.section_title("ANALISE LINGUISTICA (N-GRAMAS PERICIAIS)")
        if bigrams:
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(30, 41, 59)
            self.cell(0, 8, "Bigramas mais frequentes", ln=True)
            rows = [[" ".join(g), str(n)] for g, n in bigrams[:15]]
            self.table(["Expressao", "Ocorrencias"], rows, [140, 50])
        if trigrams:
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(30, 41, 59)
            self.cell(0, 8, "Trigramas mais frequentes", ln=True)
            rows = [[" ".join(g), str(n)] for g, n in trigrams[:10]]
            self.table(["Expressao", "Ocorrencias"], rows, [140, 50])

    def render_temas(self, comments: List[Dict[str, Any]]):
        cluster_counts = Counter(c["cluster_id"] for c in comments if c.get("cluster_id") is not None)
        if not cluster_counts:
            return
        self.add_page()
        self.section_title("TEMAS IDENTIFICADOS")
        chart_path = chart_temas(cluster_counts)
        self.add_image_full(chart_path, w=150)

    def render_comportamento_coordenado(self, comments: List[Dict[str, Any]]):
        bots = [c for c in comments if c.get("is_bot")]
        if not bots:
            return
        self.add_page()
        self.section_title("COMPORTAMENTO COORDENADO / BOTS")
        self.set_fill_color(254, 242, 242)
        self.set_draw_color(252, 165, 165)
        y0 = self.get_y()
        self.rect(10, y0, 190, 20, "DF")
        self.set_xy(15, y0 + 4)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*self.danger_color)
        clusters = set(c.get("cluster_id") for c in bots if c.get("cluster_id"))
        self.multi_cell(180, 6, self.clean_text(
            f"Detectados {len(bots)} comentarios com padrao de coordenacao semantica, "
            f"agrupados em {len(clusters)} cluster(s) distintos."
        ))
        self.ln(6)

        rows = []
        for c in bots[:20]:
            rows.append([
                f"@{c.get('autor_username', 'anon')}",
                str(c.get("cluster_id", "-")),
                self.clean_text((c.get("texto_bruto") or "")[:60]),
            ])
        self.table(["Autor", "Cluster", "Trecho do comentario"], rows, [40, 20, 130])

    def render_evidence_item(self, item: Dict[str, Any]):
        if self.get_y() > 235:
            self.add_page()
        current_y = self.get_y()
        self.set_fill_color(*self.bg_color)
        self.rect(10, current_y, 190, 38, "F")
        self.set_draw_color(226, 232, 240)
        self.rect(10, current_y, 190, 38, "D")

        self.set_xy(15, current_y + 4)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(30, 41, 59)
        autor = self.clean_text(item.get("autor_username") or "Oculto")
        self.cell(0, 5, f"AUTOR: @{autor} | PLATAFORMA: {item.get('plataforma', 'IG').upper()}")

        cat = (item.get("categoria_ia") or "NEUTRO").upper()
        self.set_xy(140, current_y + 4)
        self.set_text_color(*self.danger_color)
        self.cell(55, 5, f"[ {cat} ]", align="R")

        self.set_xy(15, current_y + 11)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(71, 85, 105)
        texto = self.clean_text(item.get("texto_bruto", ""))
        self.multi_cell(180, 4, f"TEXTO: {texto[:350]}")

        if item.get("analise_pericial"):
            self.set_x(15)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(100, 116, 139)
            self.multi_cell(180, 3.5, f"ANALISE: {self.clean_text(item['analise_pericial'])[:250]}")

        self.set_y(current_y + 42)

    def render_evidencias(self, comments: List[Dict[str, Any]]):
        hate_items = sorted(
            [c for c in comments if c.get("is_hate")],
            key=lambda c: c.get("confianca_ia", 0), reverse=True,
        )[:40]
        if not hate_items:
            return
        self.add_page()
        self.section_title("EVIDENCIAS DETALHADAS")
        for item in hate_items:
            self.render_evidence_item(item)

    def render_integrity_seal(self, data: Dict[str, Any]):
        self.add_page()
        self.set_y(100)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*self.primary_color)
        self.cell(0, 10, "CERTIFICACAO DE INTEGRIDADE", ln=True, align="C")

        digest_source = json.dumps(
            {"username": data["username"], "n": len(data["comments"])}, sort_keys=True, default=str
        )
        data_hash = hashlib.sha256(digest_source.encode()).hexdigest()

        self.ln(5)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 6, (
            "Este relatorio foi gerado eletronicamente pelo Nimrod a partir de dados publicos "
            "coletados do perfil analisado."
        ), align="C")

        self.ln(10)
        self.set_font("Courier", "B", 9)
        self.set_fill_color(241, 245, 249)
        self.cell(0, 12, f"HASH SHA-256: {data_hash}", border=1, ln=True, align="C", fill=True)

        self.set_y(-40)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(148, 163, 184)
        self.multi_cell(0, 5, (
            "AVISO: A classificacao e realizada por modelos de IA e pode conter margens de erro. "
            "Use este relatorio como insumo analitico, nao como prova juridica isolada."
        ), align="C")

    # ------------------------------------------------------------- build
    def build(self, data: Dict[str, Any], output_path: str) -> str:
        # Defesa em profundidade: mesmo que o pipeline já tenha checado,
        # o gerador de PDF se recusa a rodar sobre dado sem proveniência real.
        assert_dados_reais(data["comments"])

        comments = data["comments"]
        total = len(comments)
        total_hate = len([c for c in comments if c.get("is_hate")])

        self.render_cover(data["username"], total, total_hate, data.get("scrape_stats", {}))
        self.render_integrity_note(data)
        self.render_executive_summary(data)
        self.render_volume_e_tempo(comments)
        self.render_autores(comments)
        self.render_linguistica(data)
        self.render_temas(comments)
        self.render_comportamento_coordenado(comments)
        self.render_evidencias(comments)
        self.render_integrity_seal(data)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self.output(output_path)
        return output_path


def generate_report(data: Dict[str, Any], output_dir: str = "output") -> str:
    username = data["username"]
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    output_path = os.path.join(output_dir, f"nimrod_{username}_{date_str}.pdf")
    report = NimrodReport()
    return report.build(data, output_path)
