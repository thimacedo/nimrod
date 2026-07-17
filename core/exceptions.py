"""
Hierarquia de exceções customizadas para o Sentinela.
Substitui a estratégia de string matching por type-safe handling.
"""

from typing import Optional
from datetime import datetime


class ScrapeException(Exception):
    """Exceção base para o subsistema de scraping"""
    
    def __init__(self, message: str, context: dict = None):
        self.message = message
        self.context = context or {}
        self.timestamp = datetime.now().isoformat()
        super().__init__(message)


class ScrapeControlSignal(ScrapeException):
    """
    Base para sinais de controle de fluxo (não são erros de verdade).
    Não devem ser tratados como falhas que afetam métricas ou circuit breaker.
    """
    pass


class DOMHealerRestartSignal(ScrapeControlSignal):
    """
    Sinal emitido quando DOM Healer completou com sucesso
    e o browser precisa ser reiniciado para continuar.
    
    NÃO conta como falha.
    NÃO afeta circuit breaker.
    """
    
    def __init__(
        self,
        reason: str = "dom_healer_completed",
        username: str = None,
        shortcode: str = None,
        selector_cache_key: str = None,
        healing_attempts: int = 0,
        healing_duration_s: float = 0.0,
    ):
        self.reason = reason
        self.username = username
        self.shortcode = shortcode
        self.selector_cache_key = selector_cache_key
        self.healing_attempts = healing_attempts
        self.healing_duration_s = healing_duration_s
        
        context = {
            "reason": reason,
            "username": username,
            "shortcode": shortcode,
            "selector_cache_key": selector_cache_key,
            "healing_attempts": healing_attempts,
            "healing_duration_s": healing_duration_s,
        }
        
        super().__init__(
            f"DOMHealer restart signal: {reason} "
            f"(user={username}, attempts={healing_attempts})",
            context=context
        )


class SessionExpiredError(ScrapeControlSignal):
    """
    Detectado login wall, sessão expirada ou redirecionamento para login.
    
    Semântica: A sessão de browser/cookies já não é válida.
    Ação: Fechar browser, renovar cookies, tentar novamente.
    """
    
    def __init__(self, username: str, page_url: str, reason: str = "unknown"):
        self.username = username
        self.page_url = page_url
        self.reason = reason  # "login_wall", "session_expired", "challenge", etc.
        
        context = {
            "username": username,
            "page_url": page_url,
            "reason": reason,
        }
        
        super().__init__(
            f"Session expired for @{username}: {reason} at {page_url}",
            context=context
        )


class ChallengeRequiredError(ScrapeControlSignal):
    """
    Instagram Challenge detectada (Soft Block - checkpoint de segurança).
    Requer ação humana ou espera.
    """
    
    def __init__(
        self,
        username: str,
        challenge_type: str = "unknown",
        page_url: str = None,
    ):
        self.username = username
        self.challenge_type = challenge_type
        self.page_url = page_url
        
        context = {
            "username": username,
            "challenge_type": challenge_type,
            "page_url": page_url,
        }
        
        super().__init__(
            f"Challenge required for @{username}: {challenge_type}",
            context=context
        )


class SelectorValidationError(ScrapeException):
    """Seletor CSS proposto é inválido ou não funciona"""
    
    def __init__(
        self,
        selector: str,
        reason: str,
        elements_found: int = 0,
        expected_min: int = 1,
    ):
        self.selector = selector
        self.reason = reason  # "no_elements", "too_many", "empty_content", etc.
        self.elements_found = elements_found
        self.expected_min = expected_min
        
        context = {
            "selector": selector,
            "reason": reason,
            "elements_found": elements_found,
            "expected_min": expected_min,
        }
        
        super().__init__(
            f"Selector validation failed: {reason} "
            f"({elements_found} elements, expected >={expected_min})",
            context=context
        )


class DOMHealingFailedError(ScrapeException):
    """DOM Healing completou todas as tentativas sem sucesso"""
    
    def __init__(
        self,
        username: str,
        reason: str,
        attempts: int,
        last_selector: str = None,
    ):
        self.username = username
        self.reason = reason  # "max_attempts", "repeated_selector", etc.
        self.attempts = attempts
        self.last_selector = last_selector
        
        context = {
            "username": username,
            "reason": reason,
            "attempts": attempts,
            "last_selector": last_selector,
        }
        
        super().__init__(
            f"DOM Healing failed for @{username}: {reason} "
            f"(tried {attempts} times)",
            context=context
        )


class InvalidTargetError(ScrapeException):
    """Perfil alvo não existe, é privado ou bloqueado"""
    
    def __init__(self, username: str, reason: str):
        self.username = username
        self.reason = reason  # "not_found", "private", "blocked", etc.
        
        context = {
            "username": username,
            "reason": reason,
        }
        
        super().__init__(
            f"Invalid target @{username}: {reason}",
            context=context
        )


class SessionBlockedError(ScrapeException):
    """Todas as sessões estão em cooldown ou bloqueadas"""
    
    def __init__(self, blocked_count: int, total_count: int):
        self.blocked_count = blocked_count
        self.total_count = total_count
        
        context = {
            "blocked_count": blocked_count,
            "total_count": total_count,
        }
        
        super().__init__(
            f"All sessions blocked: {blocked_count}/{total_count}",
            context=context
        )


class AIServiceError(ScrapeException):
    """Erro na comunicação com serviço de IA (Gemini, etc.)"""
    
    def __init__(self, service: str, reason: str, original_error: str = None):
        self.service = service
        self.reason = reason
        self.original_error = original_error
        
        context = {
            "service": service,
            "reason": reason,
            "original_error": original_error,
        }
        
        super().__init__(
            f"AI Service error ({service}): {reason}",
            context=context
        )


class ExtractionFailure(ScrapeException):
    """Levantada quando todos os métodos de extração de comentários falham para um post."""
    pass
