#!/usr/bin/env python3
# nimrod.py
"""
Nimrod — análise completa de 1 perfil do Instagram por vez.

Uso:
    python nimrod.py @perfil
    python nimrod.py perfil --max-posts 20 --max-age-days 90
"""
import argparse
import asyncio
import logging
import sys
import time

from config import config
from pipeline import run_profile_analysis, PreflightError
from core.integrity import DadosNaoReaisError
from report.pdf_builder import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nimrod")


def _notify(title: str, message: str, priority: str = "default"):
    """Notificação opcional via ntfy (mesmo padrão usado nos outros projetos)."""
    if not config.ntfy_topic:
        return
    try:
        import httpx
        httpx.post(
            f"{config.ntfy_server}/{config.ntfy_topic}",
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": priority},
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"[ntfy] Falha ao notificar: {e}")


async def main_async(args: argparse.Namespace) -> int:
    username = args.username.lstrip("@")

    # Overrides de CLI sobre o config padrão do .env
    if args.max_posts:
        config.max_posts = args.max_posts
    if args.max_comments:
        config.max_comments_per_post = args.max_comments
    if args.max_age_days:
        config.max_age_days = args.max_age_days
    if args.no_headless:
        config.headless = False

    t0 = time.time()
    logger.info(f"🚀 Nimrod iniciando análise de @{username}")
    logger.info(
        f"   Config: max_posts={config.max_posts} | "
        f"max_comments_per_post={config.max_comments_per_post} | "
        f"max_age_days={config.max_age_days}"
    )

    try:
        data = await run_profile_analysis(username)
    except PreflightError as e:
        logger.error(f"🛑 Pré-checagem falhou — nada foi coletado, nenhum relatório será gerado.\n   {e}")
        return 1
    except DadosNaoReaisError as e:
        logger.error(
            f"🛑 TRAVA DE INTEGRIDADE ACIONADA — geração de relatório abortada.\n"
            f"   Isso significa que dado sem proveniência de coleta real (ou classificação "
            f"insuficientemente confiável) tentou entrar no relatório e foi bloqueado.\n   {e}"
        )
        _notify(f"Nimrod: trava de integridade em @{username}", str(e), priority="high")
        return 1
    except Exception as e:
        logger.error(f"❌ Falha na análise de @{username}: {e}", exc_info=True)
        _notify(f"Nimrod: falha em @{username}", str(e), priority="high")
        return 1

    if not data["comments"]:
        logger.warning("⚠️ Nenhum comentário coletado. Verifique sessão/credenciais do Instagram no .env.")
        _notify(f"Nimrod: @{username} sem dados", "Nenhum comentário coletado.", priority="default")
        return 1

    logger.info("📄 Gerando relatório PDF...")
    output_path = generate_report(data, output_dir=config.output_dir)

    elapsed = time.time() - t0
    total = len(data["comments"])
    total_hate = len([c for c in data["comments"] if c.get("is_hate")])
    logger.info(f"✅ Concluído em {elapsed:.1f}s | {total} comentários | {total_hate} hostis")
    import os
    out_dir_uri = "file:///" + os.path.dirname(os.path.abspath(output_path)).replace('\\', '/')
    logger.info(f"📁 Relatório salvo em: {output_path}")
    logger.info(f"🔗 Acesse a pasta clicando aqui: {out_dir_uri}")

    _notify(
        f"Nimrod: @{username} concluído",
        f"{total} comentários analisados, {total_hate} hostis. Relatório: {output_path}",
        priority="default",
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description="Nimrod — análise completa de 1 perfil do Instagram.")
    parser.add_argument("username", help="@ do perfil a analisar (com ou sem @)")
    parser.add_argument("--max-posts", type=int, default=None, help="Máximo de posts recentes a varrer (padrão: .env)")
    parser.add_argument("--max-comments", type=int, default=None, help="Máximo de comentários por post")
    parser.add_argument("--max-age-days", type=int, default=None, help="Ignorar posts mais antigos que N dias")
    parser.add_argument("--no-headless", action="store_true", help="Roda o navegador visível (debug)")
    args = parser.parse_args()

    exit_code = asyncio.run(main_async(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
