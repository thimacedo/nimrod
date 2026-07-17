# config.py
"""
Configuração central do Nimrod. Tudo via .env — sem Supabase, sem fila remota.
"""
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class NimrodConfig:
    # Coleta
    max_posts: int = int(os.getenv("NIMROD_MAX_POSTS", "12"))
    max_comments_per_post: int = int(os.getenv("NIMROD_MAX_COMMENTS_PER_POST", "150"))
    max_age_days: int = int(os.getenv("NIMROD_MAX_AGE_DAYS", "60"))
    headless: bool = os.getenv("NIMROD_HEADLESS", "true").lower() == "true"

    # Análise
    enable_linguistics: bool = os.getenv("NIMROD_ENABLE_LINGUISTICS", "true").lower() == "true"
    enable_behavior: bool = os.getenv("NIMROD_ENABLE_BEHAVIOR", "true").lower() == "true"
    enable_clustering: bool = os.getenv("NIMROD_ENABLE_CLUSTERING", "true").lower() == "true"
    max_ai_concurrency: int = int(os.getenv("NIMROD_AI_CONCURRENCY", "5"))

    # Saída
    output_dir: str = os.getenv("NIMROD_OUTPUT_DIR", "output")

    # Notificação (reaproveita o padrão ntfy que Thiago já usa em outros projetos)
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "")
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")


config = NimrodConfig()
