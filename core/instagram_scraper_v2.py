from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from playwright.async_api import Page, BrowserContext, async_playwright, Browser, TimeoutError as PlaywrightTimeoutError

from core.ai_service import ai_service
from core.exceptions import DOMHealerRestartSignal, ExtractionFailure
from core.exceptions import ExtractionFailure

logger = logging.getLogger("instagram_scraper_v2")

# ── Constantes da API Interna do Instagram Web ────────────────────────────────
_IG_APP_ID = "936619743392459"   # App ID público do cliente web do Instagram
_IG_API_BASE = "https://i.instagram.com"
_IG_COMMENTS_PATH = "/api/v1/media/{pk}/comments/"
_IG_API_TIMEOUT = 15            # segundos por request HTTP

@dataclass
class Session:
    label: str
    session_id: str
    blocked_until: Optional[datetime] = None
    profile: Optional[Dict[str, Any]] = None
    error_count: int = 0
    last_used: Optional[datetime] = None

    @property
    def is_available(self) -> bool:
        if not self.blocked_until: return True
        return datetime.now(timezone.utc) > self.blocked_until

class InstagramScraperV2:
    """
    Motor de raspagem do Instagram independente (PASA v85.10).
    Focado em Playwright puro, sem Zyte.
    Implementa rotação de sessões, backoff exponencial e Stealth Mode Avançado.
    """

    def __init__(self, headless: bool = True, max_retries: int = 3, db_client: Optional[Any] = None, shutdown_event: Optional[asyncio.Event] = None):
        self.headless = headless
        self.max_retries = max_retries
        self.db = db_client
        self.shutdown_event = shutdown_event
        self.sessions: List[Session] = self._load_sessions()
        self.current_session_idx = 0
        self.captured_data: List[Dict[str, Any]] = []
        # Cache de pk (ID numérico) resolvido por shortcode — evita re-resolução
        self._pk_cache: Dict[str, str] = {}
        # Credenciais HTTP capturadas pelo interceptador de rede (Fase 1/2)
        self._csrf_token: Optional[str] = None
        self._session_id_active: Optional[str] = None
        self.stats = {
            "posts_found": 0,
            "posts_scraped": 0,
            "comments_extracted": 0,
            "api_calls": 0,
            "api_comments_calls": 0,   # chamadas à API interna de comentários
            "browser_renders": 0,
            "session_rotations": 0,
            "junk_detected": 0,
            "errors": 0
        }

    def _load_sessions(self) -> List[Session]:
        """
        Carrega sessões exclusivamente do .env (Nimrod não usa Supabase/fila remota).
        Suporta múltiplas sessões para reduzir chance de bloqueio, mas roda tudo local.
        """
        sessions = []

        for i in range(1, 11):
            sid = os.getenv(f"INSTAGRAM_SESSIONID_{i}") or (os.getenv("INSTAGRAM_SESSIONID") if i == 1 else None)
            if sid:
                sessions.append(Session(label=f"SESSION_{i}", session_id=sid))

        sid_val = os.getenv("INSTAGRAM_SESSIONID_VAL")
        if sid_val:
            sessions.append(Session(label="SESSION_VAL", session_id=sid_val))

        cookie_full = os.getenv("INSTAGRAM_COOKIE_FULL")
        if cookie_full and "sessionid=" in cookie_full:
            sid = re.search(r'sessionid=([^;]+)', cookie_full)
            if sid:
                sessions.append(Session(label="COOKIE_FULL", session_id=sid.group(1)))

        # Fallback: Load sessionid from provided cookie files in project root
        fallback_cookie_files = ["cookies.txt", "cookies_tempareiapodcast.txt", "cookies_monitoramento.discurso.txt"]
        for cookie_file in fallback_cookie_files:
            cookie_path = os.path.join(os.getcwd(), cookie_file)
            if os.path.isfile(cookie_path):
                try:
                    with open(cookie_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    match = re.search(r"sessionid[=\s]+([^;\s]+)", content)
                    if match:
                        sessions.append(Session(label=f"FILE_{cookie_file}", session_id=match.group(1)))
                        logger.info(f"🔑 [Nimrod] Loaded session from {cookie_file}")
                except Exception as e:
                    logger.debug(f"[Nimrod] Failed to load session from {cookie_file}: {e}")

        logger.info(f"🔑 [Nimrod] {len(sessions)} sessão(ões) carregada(s) do .env ou arquivos de cookie.")
        return sessions

    def _get_next_session(self) -> Session:
        """Rotaciona para a próxima sessão disponível (incluindo cooldown)."""
        available = [s for s in self.sessions if s.is_available]
        if not available:
            logger.error("❌ [V2] Todas as sessões estão bloqueadas (cooldown ativo)!")
            raise RuntimeError("all_sessions_blocked")

        session = available[self.current_session_idx % len(available)]
        self.current_session_idx += 1

        # ── Sticky Proxy Binding (PASA v98.0) ──────────────────────────────────
        # Deriva um ID de proxy determinístico do label da sessão.
        # Garante que SESSION_1 → sempre IP A, SESSION_2 → sempre IP B.
        # Troca de sessão IG = troca de IP residencial, sem fragmentação mid-session.
        if not hasattr(session, "sticky_proxy_id"):
            session.sticky_proxy_id = hashlib.sha256(session.label.encode()).hexdigest()[:10]
        return session

    async def _handle_response(self, response):
        """Interceptador de rede para capturar JSONs de interesse e credenciais HTTP."""
        url = response.url

        # ── Fase 1: Captura proativa de CSRF e App-ID dos headers de qualquer request IG ──
        if "instagram.com" in url:
            try:
                req_headers = response.request.headers
                # Extrai o csrf do cookie da requisição saindo
                raw_cookie = req_headers.get("cookie", "")
                csrf_match = re.search(r'csrftoken=([^;]+)', raw_cookie)
                if csrf_match and not self._csrf_token:
                    self._csrf_token = csrf_match.group(1)
                    logger.debug("[V2] CSRF token capturado via interceptador: %s...", self._csrf_token[:8])
                # Captura sessionid ativo
                sid_match = re.search(r'sessionid=([^;]+)', raw_cookie)
                if sid_match and not self._session_id_active:
                    self._session_id_active = sid_match.group(1)
            except Exception:
                pass

        # ── Captura de pk (media_id) a partir de respostas de comentários ──
        if "comments" in url or "graphql" in url or "web_profile_info" in url:
            try:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    data = await response.json()
                    self.captured_data.append({"url": url, "data": data})
                    self.stats["api_calls"] += 1
                    # Tenta extrair pk de respostas de media info
                    self._try_extract_pk_from_data(data)
            except Exception as e:
                logger.debug("[V2] Falha ao processar response JSON (%s): %s", url, e)

    def _try_extract_pk_from_data(self, data: Any) -> None:
        """Tenta extrair pares shortcode→pk de respostas JSON capturadas."""
        if not isinstance(data, dict):
            return
        # Padrão xdt_api / GraphQL
        for key in ("xdt_api__v1__media__shortcode__web_info", "shortcode_media", "media"):
            if key in data:
                item = data[key]
                items_list = item.get("items", [item]) if isinstance(item, dict) else []
                for it in items_list:
                    if isinstance(it, dict):
                        pk = str(it.get("pk", "") or it.get("id", ""))
                        sc = it.get("code", "") or it.get("shortcode", "")
                        if pk and sc:
                            self._pk_cache[sc] = pk
                            logger.debug("[V2] pk resolvido via XHR: %s → %s", sc, pk)
        # Recursão leve (1 nível)
        for v in data.values():
            if isinstance(v, dict):
                self._try_extract_pk_from_data(v)

    def _generate_stealth_profile(self) -> Dict[str, Any]:
        """
        Gera perfis de dispositivos e cabeçalhos HTTP realistas (PASA v98.0).
        Fase 3: inclui User-Agents do app Android do Instagram para reduzir detecção de bot.
        """
        chrome_major = random.choice([122, 123, 124, 125])
        chrome_build = random.randint(5000, 6400)
        chrome_patch = random.randint(100, 200)
        chrome_ver = f"{chrome_major}.0.{chrome_build}.{chrome_patch}"
        safari_ver = f"17.{random.choice([3, 4, 5])}"

        # ── Fase 3: Pool de User-Agents — Web Desktop + Android IG App ────────
        # Os UAs do app Android são mais confiáveis porque o IG os reconhece como
        # clientes legítimos e não aplica o mesmo nível de bot detection do Web.
        android_devices = [
            ("samsung", "SM-G991B", "o1s", "exynos2100", "33", "420dpi", "1080x2400"),
            ("samsung", "SM-S918B", "dm3q", "snapdragon8gen2", "33", "480dpi", "1080x2340"),
            ("google", "Pixel 8", "shiba", "tensor3", "34", "420dpi", "1080x2400"),
            ("xiaomi", "23049RAD8G", "gold", "snapdragon8gen2", "33", "440dpi", "1080x2400"),
        ]
        ig_version = random.choice(["275.0.0.27.98", "278.0.0.15.105", "281.0.0.23.110"])
        ig_version_code = random.choice(["453759104", "459228834", "463123456"])
        dev = random.choice(android_devices)
        android_ig_ua = (
            f"Instagram {ig_version} Android ({dev[4]}/{dev[4]}; {dev[5]}; "
            f"{dev[6]}; {dev[0]}; {dev[1]}; {dev[2]}; {dev[3]}; pt_BR; {ig_version_code})"
        )

        os_templates = [
            # Windows Chrome (Desktop Web)
            {
                "ua": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36",
                "w": random.choice([1920, 1366, 1536, 1440, 1600]),
                "h": random.choice([1080, 768, 864, 900, 1200]),
                "platform": "Win32",
                "vendor": "Google Inc.",
                "mobile": False
            },
            # Edge no Windows
            {
                "ua": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36 Edg/{chrome_major}.0.0.0",
                "w": 1920,
                "h": 1080,
                "platform": "Win32",
                "vendor": "Google Inc.",
                "mobile": False
            },
            # macOS Chrome
            {
                "ua": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36",
                "w": random.choice([1440, 1680, 2560, 2880]),
                "h": random.choice([900, 1050, 1600, 1800]),
                "platform": "MacIntel",
                "vendor": "Google Inc.",
                "mobile": False
            },
            # macOS Safari
            {
                "ua": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{safari_ver} Safari/605.1.15",
                "w": 1440,
                "h": 900,
                "platform": "MacIntel",
                "vendor": "Apple Computer, Inc.",
                "mobile": False
            },
            # Fase 3: Android Instagram App (simula acesso mobile real)
            {
                "ua": android_ig_ua,
                "w": random.choice([360, 390, 412, 414]),
                "h": random.choice([640, 800, 820, 896]),
                "platform": "Linux armv8l",
                "vendor": "",
                "mobile": True
            },
        ]

        # Peso: 60% Web, 40% Android (evita suspeita por uso excessivo do UA mobile)
        weights = [1, 1, 1, 1, 1.5]  # Android com peso ligeiramente maior
        profile = random.choices(os_templates, weights=weights, k=1)[0]
        is_mobile = profile.get("mobile", False)

        headers = {
            "Accept-Language": random.choice([
                "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "pt-BR,pt;q=0.9,en-US;q=0.9",
                "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7"
            ]),
            "Sec-Ch-Ua-Mobile": "?1" if is_mobile else "?0",
            "Upgrade-Insecure-Requests": "1"
        }
        if not is_mobile:
            headers["Sec-Ch-Ua"] = f'"{chrome_major}";v="{chrome_major}", "Not(A:Brand";v="24", "Chromium";v="{chrome_major}"'
            headers["Sec-Ch-Ua-Platform"] = f'"{profile["platform"]}"'

        return {
            "ua": profile["ua"],
            "w": profile["w"],
            "h": profile["h"],
            "platform": profile["platform"],
            "vendor": profile["vendor"],
            "mobile": is_mobile,
            "headers": headers
        }

    async def scrape_profile(
        self,
        username: str,
        candidato_id: str,
        max_posts: int = 3,
        max_comments_per_post: int = 50,
        max_age_days: int = 7,
        resume_after_shortcode: str = None,
        on_post_scraped: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extrai comentários de um perfil com retry e rotação.

        Parâmetro `resume_after_shortcode` (PASA v88.0 - Fase 8.5):
            Se fornecido, o scraper pula todos os posts cujo shortcode é anterior
            ao checkpoint, evitando reprocessamento após crash.
        """
        # Reset de stats para metricas por-ciclo precisas
        self.stats = {
            "posts_found": 0,
            "posts_scraped": 0,
            "comments_extracted": 0,
            "api_calls": 0,
            "api_comments_calls": 0,
            "browser_renders": 0,
            "session_rotations": 0,
            "junk_detected": 0,
            "errors": 0
        }
        all_comments = []
        retry_count = 0
        blocked_attempts = 0
        _resume_done = resume_after_shortcode is None  # True se sem checkpoint

        while retry_count < self.max_retries:
            session = self._get_next_session()
            if not session:
                break

            try:
                async with async_playwright() as pw:
                    browseract_key = os.getenv("BROWSERACT_API_KEY")
                    
                    # Temporariamente desabilitado o CDP do BrowserAct devido a erro 401 na API. 
                    # O BrowserAct será usado via ferramentas MCP configuradas no settings.json do agente.
                    use_browseract_cdp = False 
                    
                    if browseract_key and use_browseract_cdp:
                        logger.info(f"🌐 [V2] Conectando via BrowserAct (Cloud CDP) para máxima evasão antibot...")
                        ws_url = f"wss://api.browseract.com/connect?apiKey={browseract_key}&keep_alive=300000"
                        browser = await pw.chromium.connect_over_cdp(ws_url)
                    else:
                        browser = await pw.chromium.launch(
                            headless=self.headless,
                            args=[
                                "--disable-blink-features=AutomationControlled", 
                                "--no-sandbox",
                                "--disable-infobars",
                                "--window-position=0,0",
                                "--ignore-certificate-errors",
                                "--disable-extensions",
                                "--disable-notifications"
                            ]
                        )
                    
                    # 🎭 STEALTH PROFILE (Fixo por sessão para evitar suspeitas)
                    if not session.profile:
                        session.profile = self._generate_stealth_profile()
                    profile = session.profile

                    # 🛰️ PROXY BINDING (PASA v98.0 — Sticky Session)
                    # Prioridade: PROXY_URL_TEMPLATE (sticky) > PROXY_LIST (roundrobin) > PROXY_URL (fixo)
                    # Para constância: use PROXY_URL_TEMPLATE com residencial Webshare/IPRoyal.
                    # Exemplo: http://user-res-session-{SESSION_ID}:pass@proxy.webshare.io:10000
                    proxy_template = os.getenv("PROXY_URL_TEMPLATE", "")
                    proxy_list_env = os.getenv("PROXY_LIST", "")
                    proxies = [p.strip() for p in proxy_list_env.split(",") if p.strip()]
                    proxy_url = os.getenv("PROXY_URL")

                    sticky_id = getattr(session, "sticky_proxy_id", "") or \
                        hashlib.sha256(session.label.encode()).hexdigest()[:10]

                    if proxy_template and "{SESSION_ID}" in proxy_template:
                        # Modo Sticky: SESSION_1 sempre → mesmo IP residencial durante todo o scrape
                        proxy_url = proxy_template.replace("{SESSION_ID}", sticky_id)
                        logger.info(
                            "📍 [V2] Sticky proxy ativo para %s → session_id=%s",
                            session.label, sticky_id
                        )
                    elif proxies:
                        # Modo roundrobin legado (mantido para compatibilidade com PROXY_LIST)
                        proxy_url = random.choice(proxies)

                    context_kwargs = {
                        "viewport": {"width": profile["w"], "height": profile["h"]},
                        "user_agent": profile["ua"]
                    }
                    
                    if proxy_url:
                        # Suporte robusto a proxies com autenticação embutida na URL
                        if "@" in proxy_url:
                            auth_part, server_part = proxy_url.rsplit("@", 1)
                            auth_part = auth_part.replace("http://", "").replace("https://", "")
                            username_pwd = auth_part.split(":", 1)
                            username = username_pwd[0]
                            password = username_pwd[1] if len(username_pwd) > 1 else ""
                            protocol = "http://" if "http://" in proxy_url else "https://"
                            context_kwargs["proxy"] = {
                                "server": f"{protocol}{server_part}",
                                "username": username,
                                "password": password
                            }
                        else:
                            context_kwargs["proxy"] = {"server": proxy_url}
                        
                    context = await browser.new_context(**context_kwargs)
                    
                    await context.add_cookies([{
                        'name': 'sessionid', 
                        'value': session.session_id, 
                        'domain': '.instagram.com', 
                        'path': '/'
                    }])

                    page = await context.new_page()
                    page.on("response", self._handle_response)
                    
                    proxy_log = "com Proxy" if proxy_url else "sem Proxy"
                    logger.info(f"🎯 [V2] Scrape @{username} usando {session.label} | Profile: {profile['platform']} | Res: {profile['w']}x{profile['h']} ({proxy_log})")
                    
                    if not await self._verify_session(page, session):
                        logger.warning(f"⚠️ [V2] Sessão {session.label} expirada ou inválida. Cooldown 30min...")
                        session.blocked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
                        blocked_attempts += 1
                        retry_count += 1
                        await browser.close()
                        continue

                    response = await page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded", timeout=60000)
                    
                    if response and response.status == 429:
                        logger.warning(f"⚠️ [V2] Erro 429 detectado. Rotacionando IP/Sessão...")
                        await context.clear_cookies()
                        await browser.close()
                        retry_count += 1
                        continue

                    # Detecção antecipada de bloqueios / Scraping Warnings / Captchas
                    current_url = page.url
                    if "login" in current_url or "scraping_warning" in current_url or "challenge" in current_url:
                        logger.warning(f"⚠️ [V2] Login wall ou Scraping Warning detectado antecipadamente para {session.label} na URL: {current_url}")
                        session.blocked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
                        blocked_attempts += 1
                        retry_count += 1
                        await browser.close()
                        continue

                    try:
                        error_header = await page.query_selector("h2")
                        if error_header:
                            header_text = await error_header.inner_text()
                            if "Página não disponível" in header_text or "Sorry, this page" in header_text:
                                logger.error(f"❌ [V2] Alvo @{username} inexistente (404).")
                                await browser.close()
                                raise ValueError(f"invalid_target: 404_not_found")
                    except ValueError as ve: raise ve
                    except Exception as e_header:
                        logger.debug("[V2] Falha ao validar header de erro para @%s: %s", username, e_header)

                    try:
                        await page.wait_for_selector("main, header", timeout=20000)
                    except Exception as e_wait:
                        logger.warning(f"⚠️ [V2] Timeout aguardando elementos principais do perfil: {e_wait}")

                    await asyncio.sleep(random.uniform(3, 6))

                    post_metas = await self._extract_shortcodes(page, max_posts)
                    self.stats["posts_found"] = len(post_metas)
                    
                    if len(post_metas) == 0:
                        logger.warning(f"⚠️ [V2] Nenhum post encontrado para @{username}. Salvando diagnóstico...")
                        try:
                            os.makedirs("scratch", exist_ok=True)
                            await page.screenshot(path=f"scratch/scrape_empty_{username}.png")
                            html_content = await page.content()
                            with open(f"scratch/scrape_empty_{username}.html", "w", encoding="utf-8") as f:
                                f.write(html_content)
                            logger.info(f"💾 Diagnóstico salvo em scratch/scrape_empty_{username}.png e .html")
                        except Exception as e_diag:
                            logger.error(f"Falha ao salvar diagnóstico: {e_diag}")

                        # Detecção de login wall silencioso (PASA v98.9 - autocura avançada)
                        has_login_fields = await page.query_selector('input[name="username"], input[name="password"]')
                        has_login_buttons = await page.query_selector('button:has-text("Entrar"), a[href*="login"]')
                        if has_login_fields or has_login_buttons:
                            logger.warning(f"⚠️ [V2] Login wall silencioso detectado no perfil para {session.label}. Invalidando sessão...")
                            session.blocked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
                            blocked_attempts += 1
                            retry_count += 1
                            await browser.close()
                            continue
                    
                    scraped_count = 0
                    consecutive_old_posts = 0
                    consecutive_zero_comments = 0
                    
                    for meta in post_metas:
                        if self.shutdown_event and self.shutdown_event.is_set():
                            logger.warning(f"🛑 [V2] Interrupção detectada! Abortando...")
                            break

                        if scraped_count >= max_posts:
                            break
                            
                        shortcode = meta["shortcode"]
                        if page.is_closed(): break

                        # 💾 CHECKPOINT RESUME (PASA v88.0 - Fase 8.5)
                        # Pula posts anteriores ao checkpoint sem processar.
                        if not _resume_done:
                            if shortcode == resume_after_shortcode:
                                _resume_done = True  # Este post já foi salvo; próximo será processado
                                logger.info(
                                    "⏩ [V2] Checkpoint atingido (%s). Retomando a partir do próximo post.",
                                    shortcode
                                )
                            else:
                                logger.debug("[V2] Pulando post %s (antes do checkpoint).", shortcode)
                            continue

                        is_pinned = meta["is_pinned"]
                        post_timestamp = meta.get("timestamp")
                        
                        if is_pinned:
                            logger.info(f"⏭️ [V2] Post {shortcode} FAST-SKIP (Fixado).")
                            continue

                        if post_timestamp:
                            try:
                                post_dt = datetime.fromisoformat(post_timestamp.replace('Z', '+00:00'))
                                age_days = (datetime.now(timezone.utc) - post_dt).days
                                if age_days > max_age_days:
                                    consecutive_old_posts += 1
                                    logger.info(f"⏳ [V2] Post {shortcode} é velho ({age_days}d). [{consecutive_old_posts}/3]")
                                    if consecutive_old_posts >= 3: break
                                    continue
                                else:
                                    consecutive_old_posts = 0
                            except Exception as e_post_dt:
                                logger.debug("[V2] Falha ao interpretar data do post %s: %s", shortcode, e_post_dt)

                        logger.info(f"📄 [V2] Verificando post {shortcode}...")
                        post_comments, post_timestamp = await self._scrape_post(page, shortcode, username, candidato_id, max_comments_per_post, max_age_days)
                        
                        if post_timestamp:
                            meta["timestamp"] = post_timestamp

                        if post_comments:
                            all_comments.extend(post_comments)
                            scraped_count += 1
                            self.stats["posts_scraped"] += 1
                            consecutive_zero_comments = 0
                            
                            # Callback assíncrona para persistência incremental
                            if on_post_scraped:
                                try:
                                    await on_post_scraped(shortcode, post_comments)
                                except Exception as e_cb:
                                    logger.error(f"⚠️ [V2] Falha na callback on_post_scraped para post {shortcode}: {e_cb}")
                            
                            await asyncio.sleep(random.uniform(6, 18))
                        else:
                            logger.info(f"⏭️ [V2] Post {shortcode} ignorado.")
                            consecutive_zero_comments += 1
                            if consecutive_zero_comments >= 3:
                                logger.warning(f"🚨 [V2] 3 posts vazios consecutivos! Ativando auto-recuperação do ScrapeAgent no post {shortcode}...")
                                try:
                                    from core.agent_scraper.dom_healing import DOMHealer
                                    from core.ai_service import ai_service
                                    healer = DOMHealer(ai_service=ai_service)
                                    
                                    logger.info("[V2] Capturando screenshot e fragmento DOM da página...")
                                    screenshot_b64 = await healer._capture_screenshot(page)
                                    html_snippet = await healer._extract_html_snippet(page)
                                    
                                    heal_res = await healer.heal_selectors(
                                        page=page,
                                        selector_name="comment_container",
                                        screenshot_b64=screenshot_b64,
                                        html_snippet=html_snippet,
                                        cache_key=f"heal_{username}_{shortcode}"
                                    )
                                    if heal_res.get("success"):
                                        logger.info(f"✅ [V2] DOM curado com sucesso via IA de visão: {heal_res.get('selector')}")
                                        try:
                                            await browser.close()
                                        except: pass
                                        raise DOMHealerRestartSignal(
                                            reason="vision_healing_success",
                                            username=username,
                                            shortcode=shortcode
                                        )
                                    else:
                                        logger.warning(f"⚠️ [V2] DOM Healing de visão não obteve sucesso: {heal_res.get('error')}. Iniciando fallback HITL...")
                                except Exception as e_heal:
                                    if isinstance(e_heal, DOMHealerRestartSignal):
                                        raise e_heal
                                    if "hitl_intervention_completed_restarting" in str(e_heal):
                                        raise DOMHealerRestartSignal(reason="legacy_hitl", username=username, shortcode=shortcode)
                                    logger.error(f"❌ [V2] Falha interna no DOM Healing de visão: {e_heal}. Iniciando fallback HITL...")

                                try:
                                    await browser.close()
                                except: pass
                                learned = await self._request_human_intervention(session, shortcode)
                                if learned:
                                    logger.info(f"✅ Seletor aprendido e salvo com sucesso: {learned}")
                                    raise DOMHealerRestartSignal(
                                        reason="hitl_fallback_completed",
                                        username=username,
                                        shortcode=shortcode
                                    )
                                else:
                                    logger.warning(f"⚠️ [V2] HITL indisponível em Headless. Registrando post {shortcode} na Dead Letter Queue e finalizando coleta do perfil atual.")
                                    try:
                                        from core.skills.dead_letter_queue import dead_letter_queue
                                        await dead_letter_queue.add_failed_target(
                                            target_username=username,
                                            error_type="DOM_HEALING_FAILED",
                                            error_message=f"Falha de extração no post {shortcode} e HITL indisponível em modo Headless.",
                                            original_target_id=candidato_id
                                        )
                                    except Exception as e_dlq:
                                        logger.error(f"❌ [V2] Falha ao registrar post na DLQ: {e_dlq}")
                                        
                                    return {
                                        "comments": all_comments,
                                        "post_metas": post_metas,
                                        "success": True,
                                        "comments_collected": len(all_comments),
                                        "posts_processed": scraped_count
                                    }

                    await browser.close()
                    logger.info(f"✅ [V2] @{username} finalizado. {len(all_comments)} comentários extraídos.")
                    return {
                        "comments": all_comments,
                        "post_metas": post_metas,
                        "success": True,
                        "comments_collected": len(all_comments),
                        "posts_processed": scraped_count
                    }

            except Exception as e:
                if isinstance(e, DOMHealerRestartSignal) or "hitl_intervention_completed_restarting" in str(e):
                    logger.info("🔄 [V2] Reiniciando coleta de perfil após autocura (DOM Healing/HITL) com novos seletores...")
                    await asyncio.sleep(random.uniform(2, 4))
                    continue

                logger.error(f"💥 [V2] Erro na tentativa {retry_count+1}: {e}")
                if "all_sessions_blocked" in str(e):
                    raise e
                self.stats["errors"] += 1
                retry_count += 1
                wait_seconds = min((2 ** retry_count) + random.uniform(4, 12), 120)
                logger.warning("[V2] Aplicando backoff de %.1fs antes da próxima tentativa.", wait_seconds)
                await asyncio.sleep(wait_seconds)

        if not all_comments and blocked_attempts > 0 and blocked_attempts >= retry_count:
            logger.error("❌ [V2] Todas as tentativas de scraping resultaram em bloqueio, redirecionamento ou sessão inválida.")
            raise RuntimeError("all_sessions_blocked")

        return {
            "comments": all_comments,
            "post_metas": [],
            "success": len(all_comments) > 0,
            "comments_collected": len(all_comments),
            "posts_processed": 0
        }

    async def open_post_modal(self, page: Page, shortcode: str) -> bool:
        if page.is_closed(): return False
        selector = f'a[href*="/{shortcode}/"]'
        try:
            post_element = await page.query_selector(selector)
            if post_element:
                # scroll into view para garantir clique
                await post_element.scroll_into_view_if_needed()
                await post_element.click(timeout=15000, force=True)
                await asyncio.sleep(random.uniform(4, 7))
                # v95.1: Não depende apenas do <article>, procura também main[role="main"] ou divs com role="presentation"
                if await page.query_selector('article, main[role="main"] header, div[role="dialog"]'): return True
        except Exception as e_click:
            logger.debug("[V2] Falha ao abrir modal por clique (%s): %s", shortcode, e_click)
        try:
            logger.info(f"🔄 [V2] Fallback URL para {shortcode}...")
            await page.goto(f"https://www.instagram.com/p/{shortcode}/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(5, 8))
            
            # v95.1: Se cair na tela de login, abortar extração deste post imediatamente (Soft Block)
            login_indicators = await page.query_selector_all('input[name="username"], button[type="submit"]')
            if len(login_indicators) >= 2:
                 logger.error(f"🛑 [V2] Login Wall detectado na URL direta do post {shortcode}!")
                 return False

            if await page.query_selector('article, main[role="main"] header') or len(await page.query_selector_all("section")) > 0:
                return True
        except Exception as e_fallback:
            logger.debug("[V2] Falha no fallback URL (%s): %s", shortcode, e_fallback)
        return False

    async def scroll_comment_column(self, page: Page, scroll_amount: int = 800) -> None:
        # Carrega o seletor aprendido via Human-in-the-Loop, se existir
        learned_selector = ""
        learned_path = os.path.join("configs", "learned_selectors.json")
        if os.path.exists(learned_path):
            try:
                with open(learned_path, "r") as f:
                    learned_selector = json.load(f).get("comment_container", "")
                
                # Validação rápida para evitar envenenamento por frases descritivas
                if learned_selector:
                    import re
                    is_invalid = (
                        not re.match(r"^[a-zA-Z0-9.\#\[\]:>\+,\~\*\s\-\_\(\)\'\=\^\$\|\"]+$", learned_selector)
                        or len(learned_selector.split()) > 5
                    )
                    if is_invalid:
                        logger.error(f"🗑️ [V2] Detectado seletor aprendido corrompido no cache: '{learned_selector}'. Limpando cache...")
                        try:
                            os.remove(learned_path)
                        except: pass
                        learned_selector = ""
            except: pass

        # Tenta rolar usando Javascript direto no DOM
        scrolled = await page.evaluate("""(learned) => {
            if (learned) {
                try {
                    const el = document.querySelector(learned);
                    if (el && el.scrollHeight > el.clientHeight) {
                        el.scrollTop = el.scrollHeight;
                        return true;
                    }
                } catch (err) {
                    console.error("Erro no querySelector aprendido:", err);
                }
            }
            
            // Fallback: Abordagem genérica baseada em detecção de scroll
            const allElements = document.querySelectorAll('*');
            for (let i = 0; i < allElements.length; i++) {
                const el = allElements[i];
                if ((el.tagName === 'UL' || el.tagName === 'DIV') && el.scrollHeight > el.clientHeight + 10) {
                    const style = window.getComputedStyle(el);
                    if (style.overflowY === 'auto' || style.overflowY === 'scroll' || style.overflow === 'hidden') {
                        el.scrollTop = el.scrollHeight;
                        return true;
                    }
                }
            }
            return false;
        }""", learned_selector)
        
        if scrolled:
            logger.debug("📜 [V2] Scroll via JS executado no container de comentários.")
        else:
            logger.debug("🖱️ [V2] Nenhum container com scroll ativo encontrado. Aplicando fallback de mouse wheel.")
            await page.mouse.move(random.randint(800, 1200), random.randint(300, 600))
            await page.mouse.wheel(0, scroll_amount + random.randint(-100, 100))
            
        await asyncio.sleep(random.uniform(2, 4))

    async def close_post_modal(self, page: Page) -> None:
        if page.is_closed(): return
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(2)
            if await page.query_selector("article"):
                await page.go_back(wait_until="domcontentloaded", timeout=10000)
        except Exception as e_close:
            logger.debug("[V2] Falha ao fechar modal: %s", e_close)

    async def _scrape_post(self, page: Page, shortcode: str, username: str, candidato_id: str, max_comments: int, max_age_days: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        self.captured_data = []
        if page.is_closed(): return [], None
        if not await self.open_post_modal(page, shortcode): return [], None

        post_date_iso = None

        # ── Fase 1: wait_for_selector antes de extrair data ──────────────────
        # Aguarda o elemento de tempo aparecer (substitui sleep fixo por espera inteligente)
        try:
            await page.wait_for_selector(
                'article time, main time, div[role="dialog"] time',
                timeout=12000
            )
        except Exception:
            logger.debug("[V2] Timeout aguardando timestamp do post %s", shortcode)

        for _ in range(5):
            if page.is_closed(): break
            post_date_iso = await page.evaluate("""() => {
                let el = document.querySelector('article a[href*="/p/"] time, article a[href*="/reel/"] time, article a time');
                if (!el) {
                    el = document.querySelector('article time, div[role="dialog"] time');
                }
                return el ? el.getAttribute('datetime') : null;
            }""")
            if post_date_iso:
                break
            await asyncio.sleep(0.8)

        if post_date_iso:
            post_dt = datetime.fromisoformat(post_date_iso.replace('Z', '+00:00'))
            if (datetime.now(timezone.utc) - post_dt).days > max_age_days:
                await self.close_post_modal(page)
                return [], post_date_iso

        # ── Fase 1: wait_for_response — aguarda XHR de comentários antes de extrair ──
        # Isso elimina a race condition onde o DOM era lido antes dos comentários carregarem.
        try:
            async def _is_comments_response(resp):
                return (
                    ("comments" in resp.url or "graphql" in resp.url)
                    and resp.status == 200
                    and "json" in resp.headers.get("content-type", "")
                )
            await page.wait_for_response(_is_comments_response, timeout=8000)
            logger.debug("[V2] XHR de comentários detectado para post %s", shortcode)
        except Exception:
            # Fallback: scroll manual para forçar carregamento
            logger.debug("[V2] Sem XHR de comentários em 8s para %s — aplicando scroll manual.", shortcode)

        # Scrolls para carregar mais comentários no DOM
        for _ in range(random.randint(2, 4)):
            await self.scroll_comment_column(page, scroll_amount=random.randint(1000, 1500))

        # ── Fase 2: Tenta extrair via API interna primeiro (mais completo) ──
        pk = self._pk_cache.get(shortcode)
        if not pk:
            # Resolve pk direto do DOM se ainda não capturado pelo interceptador
            pk = await self._resolve_pk_from_dom(page, shortcode)

        comments: List[Dict[str, Any]] = []
        if pk and (self._csrf_token or self._session_id_active):
            logger.info("[V2] 🚀 Tentando extração via API interna (pk=%s) para post %s", pk, shortcode)
            comments = await self._fetch_comments_via_api(pk, shortcode, max_comments)
            if comments:
                logger.info("[V2] ✅ API interna retornou %d comentários para %s", len(comments), shortcode)
            else:
                logger.info("[V2] ⚠️ API interna retornou 0. Usando fallback DOM/XHR.")

        # Fallbacks clássicos se a API não retornou nada
        if not comments:
            comments = self._parse_captured_json(shortcode)
        if not comments:
            comments = await self._extract_from_scripts(page, shortcode)
        if not comments:
            comments = await self._extract_from_dom(page, shortcode)

        await self.close_post_modal(page)

        # Se todos os métodos falharam, registra falha estrutural (PASA v98.9)
        if not comments:
            self.stats["errors"] += 1
            logger.error(
                "❌ [V2] Falha estrutural de extração no post %s: "
                "API interna, captured JSON, scripts inline e DOM falharam.",
                shortcode
            )
            raise ExtractionFailure(
                f"All extraction methods failed for post {shortcode}. "
                f"API_Active={self._session_id_active is not None}, "
                f"CSRF={self._csrf_token is not None}"
            )
        
        now = datetime.now(timezone.utc).isoformat()
        normalized = []
        # Padrões de lixo/UI para descarte exato
        exact_junk = {
            'também da meta', 'instagram lite', 'localizações', 'campanha 2201',
            'view replies', 'ver respostas', 'ver tradução', 'see translation', 'see original',
            'responder', 'reply', 'ver thread', 'view thread',
            'pinned', 'fixado', 'pinned by', 'fixado por',
            'original audio', 'áudio original', 'original sound', 'som original',
            'use template', 'usar modelo', 'remix', 'collaboration', 'colaboração'
        }
        
        # Padrões de curtidas para descarte por prefixo
        prefix_junk = [
            'liked by', 'curtido por', 'others like this', 
            'pessoas curtiram', 'curtiram isto'
        ]
        
        for c in comments[:max_comments]:
            texto = (c.get("texto_bruto") or c.get("texto", "")).replace("\u0000", "").strip()
            if len(texto) < 2 or len(texto) > 2000: continue
            
            texto_lower = texto.lower()
            if texto_lower in exact_junk:
                continue
            if any(texto_lower.startswith(p) for p in prefix_junk):
                continue
            if ' and others' in texto_lower or ' e outras ' in texto_lower or ' e outros ' in texto_lower:
                continue
                
            # REGRA IMUTÁVEL DE SEGURANÇA (PASA v50.1 / SRE): Bloqueio de vazamentos de metadados, curtidas e posts do próprio candidato
            texto_strip = texto.strip()
            autor_clean = (c.get("autor_username") or c.get("autor") or "").replace(".rn", "").lower()
            candidato_clean = username.replace(".rn", "").lower() if username else ""
            
            # A. Descarta se for string literais de interface ou marcação de tempo isoladas
            if texto_strip in ["Meta", "1d", "2d", "3d", "4d", "5d", "6d", "7d", "1w", "2w", "3w", "4w"]:
                continue
            import re
            if re.match(r'^\d+\s*(day|hour|min|second)s?\s*ago$', texto_strip, re.IGNORECASE):
                continue
            if re.match(r'^[a-z0-9_.]+\.{3,}$', texto_strip, re.IGNORECASE):
                continue
                
            # B. Descarta se for menção cruzada de engajamento do DOM (ex: autor '167razoesrn', texto 'allysonbezerra.rn')
            if re.match(r'^[a-z0-9_.]+$', texto_strip, re.IGNORECASE):
                if texto_lower == candidato_clean or texto_lower == (username or "").lower() or autor_clean == candidato_clean:
                    continue
                    
            # C. Descarta se for post/legenda original do próprio candidato vazada na lista de comentários
            if autor_clean == candidato_clean and (candidato_clean in texto_lower or "\n" in texto):
                continue
                
            # D. Descarta se o texto for composto unicamente do nome do autor (erro de parsing do DOM)
            if texto_lower == autor_clean:
                continue
            
            # v99.2: Geração de ID determinístico unificado com base em atributos imutáveis para evitar duplicatas
            data_pub = c.get("data_publicacao") or c.get("timestamp") or post_date_iso or now
            if hasattr(data_pub, "isoformat"):
                data_pub_str = data_pub.isoformat()
            else:
                data_pub_str = str(data_pub)
                
            author = c.get("autor_username") or c.get("autor") or "anon"
            
            import hashlib
            hash_input = f"{shortcode}_{author}_{data_pub_str}"
            id_real = f"v2_hash_{hashlib.sha256(hash_input.encode()).hexdigest()[:16]}"

            normalized.append({
                "id_externo": id_real,
                "texto_bruto": texto,
                "autor_username": author,
                "data_publicacao": data_pub,
                "data_coleta": now,
                "candidato_id": username,
                "post_shortcode": shortcode,
                "plataforma": "INSTAGRAM",
                "processado_ia": False,
                "tier_used": 2,
                "fonte_dado": "coleta_real_instagram",  # trava de integridade — ver core/integrity.py
            })
        
        return normalized, post_date_iso

    async def _extract_shortcodes(self, page: Page, limit: int) -> List[Dict[str, Any]]:
        return await page.evaluate(f"""
            () => {{
                const getShortcode = (url) => {{
                    const m = url.match(/\\/(p|reel)\\/([^/\\?#]+)/);
                    return m ? m[2] : null;
                }};

                const results = [];
                const links = Array.from(document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]'));
                
                links.forEach(link => {{
                    const shortcode = getShortcode(link.href);
                    if (!shortcode) return;
                    if (results.some(r => r.shortcode === shortcode)) return;
                    
                    let container = link.parentElement;
                    let is_pinned = false;
                    let timestamp = null;
                    
                    for (let i = 0; i < 5 && container; i++) {{
                        const otherLinks = Array.from(container.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]'));
                        const uniqueShortcodes = new Set();
                        otherLinks.forEach(l => {{
                            const sc = getShortcode(l.href);
                            if (sc) uniqueShortcodes.add(sc);
                        }});
                        if (uniqueShortcodes.size > 1) {{
                            break;
                        }}

                        const pin_icon = container.querySelector('svg[aria-label*="Pinned"], svg[aria-label*="Fixado"], svg[aria-label*="pinned"], svg[aria-label*="fixado"]');
                        if (pin_icon) {{
                            is_pinned = true;
                        }}
                        const time_el = container.querySelector('time');
                        if (time_el) {{
                            timestamp = time_el.getAttribute('datetime');
                        }}
                        container = container.parentElement;
                    }}
                    
                    results.push({{ 
                        shortcode, 
                        is_pinned,
                        timestamp 
                    }});
                }});
                return results.slice(0, {limit + 3});
            }}
        """)

    async def _resolve_pk_from_dom(self, page: Page, shortcode: str) -> Optional[str]:
        """Extrai o ID numérico (pk) do post diretamente do DOM ou de scripts inline."""
        if shortcode in self._pk_cache:
            return self._pk_cache[shortcode]
        try:
            pk = await page.evaluate("""
                (sc) => {
                    // Tenta via __additionalDataLoaded / window._sharedData
                    if (window.__additionalDataLoaded) {
                        for (const [k, v] of Object.entries(window.__additionalDataLoaded)) {
                            const m = v?.graphql?.shortcode_media || v?.media;
                            if (m && (m.shortcode === sc || m.code === sc)) return String(m.id || m.pk);
                        }
                    }
                    // Tenta via scripts JSON inline
                    const scripts = Array.from(document.querySelectorAll('script[type="application/json"]'));
                    for (const s of scripts) {
                        try {
                            const d = JSON.parse(s.innerText);
                            const search = (obj) => {
                                if (!obj || typeof obj !== 'object') return null;
                                if ((obj.shortcode === sc || obj.code === sc) && (obj.id || obj.pk)) {
                                    return String(obj.id || obj.pk);
                                }
                                for (const v of Object.values(obj)) {
                                    const r = search(v);
                                    if (r) return r;
                                }
                                return null;
                            };
                            const found = search(d);
                            if (found) return found;
                        } catch(e) {}
                    }
                    return null;
                }
            """, shortcode)
            if pk:
                self._pk_cache[shortcode] = pk
                logger.debug("[V2] pk resolvido via DOM: %s → %s", shortcode, pk)
            return pk
        except Exception as e:
            logger.debug("[V2] Falha ao resolver pk via DOM para %s: %s", shortcode, e)
            return None

    async def _fetch_comments_via_api(
        self,
        pk: str,
        shortcode: str,
        max_comments: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fase 2: Extrai comentários via API interna do Instagram (i.instagram.com).
        Técnica identificada no benchmark (MRISOON/no-cookie-scraper).
        Usa paginação nativa por next_max_id — sem limite de 1 tela.
        """
        url = f"{_IG_API_BASE}{_IG_COMMENTS_PATH.format(pk=pk)}"
        session_id = self._session_id_active or ""
        csrf = self._csrf_token or ""

        if not session_id:
            logger.debug("[V2] Sem session_id capturado; pulando API interna para %s", shortcode)
            return []

        # Headers que imitam o cliente web do Instagram (x-ig-app-id é público)
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36 Instagram/281.0",
            "x-ig-app-id": _IG_APP_ID,
            "x-asbd-id": "198387",
            "x-ig-www-claim": "0",
            "Accept": "*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
            "Referer": f"https://www.instagram.com/p/{shortcode}/",
            "Origin": "https://www.instagram.com",
            "Cookie": f"sessionid={session_id}; csrftoken={csrf}",
        }
        if csrf:
            headers["x-csrftoken"] = csrf

        all_comments: List[Dict[str, Any]] = []
        next_max_id: Optional[str] = None
        page_num = 0

        async with httpx.AsyncClient(timeout=_IG_API_TIMEOUT, follow_redirects=True) as client:
            while len(all_comments) < max_comments:
                params: Dict[str, str] = {"can_support_threading": "true", "permalink_enabled": "false"}
                if next_max_id:
                    params["min_id"] = next_max_id

                try:
                    resp = await client.get(url, headers=headers, params=params)
                    self.stats["api_comments_calls"] += 1

                    if resp.status_code == 429:
                        logger.warning("[V2] Rate limit (429) na API interna para pk=%s. Parando paginação.", pk)
                        break
                    if resp.status_code in (401, 403):
                        logger.warning("[V2] Acesso negado (%d) na API interna. Sessão pode estar expirada.", resp.status_code)
                        break
                    if resp.status_code != 200:
                        logger.debug("[V2] API interna retornou %d para pk=%s", resp.status_code, pk)
                        break

                    data = resp.json()
                    page_num += 1

                    raw_comments = data.get("comments", [])
                    if not raw_comments:
                        break

                    now_iso = datetime.now(timezone.utc).isoformat()
                    for c in raw_comments:
                        user = c.get("user", {}) or {}
                        texto = (c.get("text") or "").strip()
                        if not texto or len(texto) < 2:
                            continue
                        all_comments.append({
                            "id_externo": f"ig_{c.get('pk') or c.get('id', '')}",
                            "texto": texto,
                            "autor": user.get("username", "unknown"),
                            "timestamp": datetime.fromtimestamp(
                                c.get("created_at", 0), timezone.utc
                            ).isoformat() if c.get("created_at") else now_iso,
                        })

                    logger.debug(
                        "[V2] API interna pág %d: +%d comentários (total %d)",
                        page_num, len(raw_comments), len(all_comments)
                    )

                    # Paginação por cursor (next_max_id)
                    next_max_id = data.get("next_max_id") or data.get("next_min_id")
                    if not next_max_id:
                        break  # Sem mais páginas

                    # Backoff leve entre páginas para evitar rate limit
                    await asyncio.sleep(random.uniform(0.8, 2.0))

                except (httpx.TimeoutException, httpx.NetworkError) as e_net:
                    logger.warning("[V2] Erro de rede na API interna (pk=%s): %s", pk, e_net)
                    break
                except Exception as e_api:
                    logger.error("[V2] Erro inesperado na API interna (pk=%s): %s", pk, e_api)
                    break

        if all_comments:
            logger.info(
                "[V2] 📊 API interna: %d comentários extraídos em %d páginas para %s",
                len(all_comments), page_num, shortcode
            )
        return all_comments

    def _parse_captured_json(self, shortcode: str) -> List[Dict[str, Any]]:
        comments = []
        for item in self.captured_data:
            extracted = self._recursive_find_comments(item["data"])
            if extracted: comments.extend(extracted)
        return comments

    async def _extract_from_scripts(self, page: Page, shortcode: str) -> List[Dict[str, Any]]:
        script_contents = await page.evaluate("""
            () => Array.from(document.querySelectorAll('script[type="application/json"]'))
                .map(s => s.innerText).filter(txt => txt.includes('comment'))
        """)
        comments = []
        for content in script_contents:
            try:
                extracted = self._recursive_find_comments(json.loads(content))
                comments.extend(extracted)
            except Exception as e_script:
                logger.debug("[V2] Falha ao parsear script JSON de comentários: %s", e_script)
                continue
        return comments

    async def _extract_from_dom(self, page: Page, shortcode: str) -> List[Dict[str, Any]]:
        """
        Extrai comentários diretamente do DOM renderizado.
        v98.1: Adiciona filtro de timestamps relativos do Instagram
        (ex: '20h', '1d · Edited', '5w') que compartilham o atributo dir=auto
        com textos de comentários reais, causando falsos positivos.
        """
        return await page.evaluate("""
            () => {
                const results = [];

                // Regex para detectar timestamps relativos do Instagram
                // Cobre: "20h", "1d", "5w", "3m", "1d · Edited", "Just now", "Agora", "edited"
                const TS_RE = /^(\\d+[smhdw]|just\\s*now|\\d+\\s*(hour|day|week|min|second)s?|\\d+[smhdw]\\s*[·•·].*|agora|edited)$/i;

                const links = Array.from(document.querySelectorAll('a[href*="/"]'));
                const seen_pairs = new Set();

                links.forEach(link => {
                    try {
                        const url = new URL(link.href);
                        const path = url.pathname.replace(/\\//g, '');
                        if (!path || path.length < 3) return;

                        const text = link.innerText.trim();
                        // O texto do link deve ser idêntico ao path (= username do perfil)
                        if (
                            text.toLowerCase() === path.toLowerCase() &&
                            !['explore', 'reels', 'direct', 'emails', 'stories', 'accounts'].includes(path)
                        ) {
                            const username = text;

                            // Validação de segurança de SRE contra contêiner de curtidas do post principal
                            let isLikeContainer = false;
                            let checkNode = link;
                            for (let j = 0; j < 5; j++) {
                                if (!checkNode.parentElement) break;
                                checkNode = checkNode.parentElement;
                                const checkText = (checkNode.innerText || "").toLowerCase();
                                if (
                                    checkText.includes('liked by') || 
                                    checkText.includes('curtido por') || 
                                    checkText.includes('others like this') ||
                                    checkText.includes('pessoas curtiram') ||
                                    checkText.includes('curtiram isto')
                                ) {
                                    isLikeContainer = true;
                                    break;
                                }
                            }
                            if (isLikeContainer) return;

                            // Navega até 5 níveis acima para encontrar o container do comentário
                            let node = link;
                            let commentText = "";
                            for (let i = 0; i < 5; i++) {
                                if (!node.parentElement) break;
                                node = node.parentElement;

                                const spans = Array.from(node.querySelectorAll('span[dir="auto"]'));
                                for (let span of spans) {
                                    // Exclui spans dentro de elementos <time> (timestamps)
                                    if (span.closest('time')) continue;

                                    const txt = span.innerText.trim();

                                    if (!txt) continue;
                                    if (txt === username) continue;
                                    if (txt.length < 3) continue;          // mínimo 3 chars
                                    if (TS_RE.test(txt)) continue;         // timestamp relativo

                                    // Filtra metadados de curtidas e botões de interface comuns no DOM
                                    const txtLower = txt.toLowerCase();
                                    if (
                                        txtLower.startsWith('liked by') ||
                                        txtLower.startsWith('curtido por') ||
                                        txtLower.includes(' e outras ') ||
                                        txtLower.includes(' and others') ||
                                        ['ver respostas', 'view replies', 'responder', 'reply'].includes(txtLower)
                                    ) {
                                        continue;
                                    }

                                    commentText = txt;
                                    break;
                                }
                                if (commentText) break;
                            }

                            if (username && commentText) {
                                const pair_key = `${username}:${commentText}`;
                                if (!seen_pairs.has(pair_key)) {
                                    seen_pairs.add(pair_key);
                                    results.push({ autor: username, texto: commentText });
                                }
                            }
                        }
                    } catch(e) {}
                });
                return results;
            }
        """)

    def _recursive_find_comments(self, data: Any) -> List[Dict[str, Any]]:
        comments = []
        if isinstance(data, dict):
            if "edge_media_to_parent_comment" in data:
                for edge in data["edge_media_to_parent_comment"].get("edges", []):
                    node = edge.get("node", {})
                    comments.append({
                        "id_externo": f"ig_{node.get('id')}",
                        "texto": node.get("text"),
                        "autor": node.get("owner", {}).get("username"),
                        "timestamp": datetime.fromtimestamp(node.get("created_at", 0), timezone.utc).isoformat()
                    })
            elif "xdt_api__v1__media__shortcode__web_info" in data:
                for item in data["xdt_api__v1__media__shortcode__web_info"].get("items", []):
                    for c in item.get("preview_comments", []):
                        comments.append({
                            "id_externo": f"ig_{c.get('pk')}", "texto": c.get("text"),
                            "autor": c.get("user", {}).get("username"),
                            "timestamp": datetime.fromtimestamp(c.get("created_at", 0), timezone.utc).isoformat()
                        })
            for v in data.values(): comments.extend(self._recursive_find_comments(v))
        elif isinstance(data, list):
            for item in data: comments.extend(self._recursive_find_comments(item))
        return comments

    async def _validate_target_identity(self, page: Page, username: str) -> Dict[str, Any]:
        """Extrai metadados biográficos para validar se o alvo é de interesse."""
        try:
            header_selector = "header section, main header"
            header = await page.query_selector(header_selector)
            if not header:
                return {"valid": False, "reason": "header_not_found"}

            is_private = await page.query_selector("svg[aria-label*='Privada'], svg[aria-label*='Private']")
            if is_private:
                return {"valid": False, "reason": "account_private"}

            display_name = await page.evaluate("() => document.querySelector('header h2')?.innerText")
            biography = await page.evaluate("() => document.querySelector('header div._ap30')?.innerText || document.querySelector('main header section div:last-child')?.innerText")
            followers = await page.evaluate("() => Array.from(document.querySelectorAll('header span')).find(s => s.innerText.includes('seguidores') || s.innerText.includes('followers'))?.innerText")

            return {
                "valid": True,
                "username": username,
                "display_name": display_name,
                "biography": biography,
                "followers": followers
            }
        except Exception as e:
            return {"valid": False, "reason": f"exception: {str(e)[:50]}"}

    async def _verify_session(self, page: Page, session: Session) -> bool:
        try:
            # Tenta carregar a home
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(random.uniform(2, 4))
            
            current_url = page.url
            if "accounts/login" in current_url or "scraping_warning" in current_url or "challenge" in current_url:
                logger.warning(f"⚠️ [V2] Redirecionamento de login, scraping_warning ou challenge detectado para {session.label} na URL: {current_url}")
                return False
                
            # Verifica a presença explícita do formulário de login no DOM
            login_field = await page.query_selector('input[name="username"]')
            if login_field:
                logger.warning(f"⚠️ [V2] Campos de credenciais visíveis no DOM para {session.label}.")
                return False
                
            # Se não há redirect de login nem inputs, a sessão é válida
            return True
        except (PlaywrightTimeoutError, Exception) as e:
            # Erros de rede, timeouts ou oscilação do proxy não invalidam o cookie!
            logger.error(f"⚠️ [V2] Erro temporário de rede ao verificar sessão {session.label}: {e}")
            # Propaga o erro para que a tentativa sofra retry sem banir a sessão do pool
            raise RuntimeError(f"session_network_error: {e}")

    def _is_night_shift(self) -> bool:
        """Verifica se está no horário noturno (23h às 05h)."""
        from zoneinfo import ZoneInfo
        hour = datetime.now(ZoneInfo("America/Fortaleza")).hour
        return hour >= 23 or hour < 5

    async def _request_human_intervention(self, session: Session, shortcode: str) -> str:
        """
        Inicia uma sessão Chromium visível (headless=False) para o humano clicar na coluna de comentários.
        Retorna um seletor CSS genérico para ser usado no headless mode.
        Ignora automaticamente se estiver no Modo Noturno (23h-05h).
        """
        # [PATCH] Ignora intervenção humana completamente em YOLO mode/Background
        logger.warning(f"🚨 [V2] Intervenção Humana ignorada: Processo operando em Background/Headless. Abortando silenciosamente para o post {shortcode}.")
        return ""

        logger.error(f"🚨 [HITL] Iniciando acesso monitorado para o post {shortcode}")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=False)
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                await context.add_cookies([{
                    'name': 'sessionid', 'value': session.session_id,
                    'domain': '.instagram.com', 'path': '/'
                }])
                page = await context.new_page()
                await page.goto(f"https://www.instagram.com/p/{shortcode}/", wait_until="domcontentloaded", timeout=45000)
                
                # Injeta a UI overlay e aguarda o clique do usuário
                selector = await page.evaluate("""() => {
                    return new Promise((resolve) => {
                        const overlay = document.createElement('div');
                        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;background:rgba(255,0,0,0.95);color:white;z-index:2147483647;text-align:center;padding:20px;font-size:24px;font-family:sans-serif;pointer-events:none;box-shadow: 0 4px 6px rgba(0,0,0,0.5);';
                        overlay.innerHTML = '<b style="font-size:32px;">🤖 MODO APRENDIZADO SENTINELA</b><br/>O robô travou na extração.<br/><span style="color:#FFFF00;"><b>POR FAVOR, CLIQUE NA ÁREA DA COLUNA DE COMENTÁRIOS</b></span> para ensinar o novo caminho ao sistema.';
                        document.body.appendChild(overlay);
                        
                        const clickHandler = (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            let el = e.target;
                            
                            // Procura o contêiner rolável mais próximo
                            while (el && el !== document.body) {
                                const style = window.getComputedStyle(el);
                                if (el.scrollHeight > el.clientHeight && (style.overflowY === 'auto' || style.overflowY === 'scroll')) {
                                    break;
                                }
                                el = el.parentElement;
                            }
                            if (!el || el === document.body) el = e.target; // Fallback para o clicado diretamente
                            
                            // Gera um seletor CSS aproximado
                            let classes = Array.from(el.classList).filter(c => c.length > 2).slice(0, 2).join('.');
                            let selector = el.tagName.toLowerCase() + (classes ? '.' + classes : '');
                            
                            overlay.innerHTML = '<b style="font-size:32px;">✅ APRENDIDO COM SUCESSO!</b><br/>Você pode fechar esta janela agora. O Sentinela retomará o modo invisível.';
                            overlay.style.background = 'rgba(0,128,0,0.95)';
                            
                            setTimeout(() => resolve(selector), 3000);
                        };
                        document.addEventListener('click', clickHandler, {capture: true, once: true});
                    });
                }""")
                
                await browser.close()
                
                if selector:
                    os.makedirs("configs", exist_ok=True)
                    learned_path = os.path.join("configs", "learned_selectors.json")
                    with open(learned_path, "w") as f:
                        json.dump({"comment_container": selector}, f)
                    return selector
                return ""
        except Exception as e:
            logger.error(f"❌ [HITL] Falha no acesso monitorado: {e}")
            return ""

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas acumuladas do scraper."""
        return self.stats

    async def _take_screenshot(self, page: Page, name: str) -> None:
        try:
            if page.is_closed(): return
            folder = os.path.join("logs", "evidence")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{datetime.now().strftime('%H%M%S')}_{name}.png")
            await page.screenshot(path=path, full_page=True)
        except Exception as e_screenshot:
            logger.debug("[V2] Falha ao capturar screenshot '%s': %s", name, e_screenshot)

async def scrape_instagram(username: str, candidato_id: str, max_posts: int = 3, max_comments_per_post: int = 50) -> List[Dict[str, Any]]:
    scraper = InstagramScraperV2()
    return await scraper.scrape_profile(username, candidato_id, max_posts, max_comments_per_post)
