"""
PASA v94.3 - Circuit Breaker Avançado (IA Mesh & Resilience)
Implementa máquina de estados (CLOSED, OPEN, HALF_OPEN) com backoff exponencial.
"""
import time
import logging
from typing import Dict, Optional, Literal, Any
from dataclasses import dataclass, field

logger = logging.getLogger("CircuitBreaker")

State = Literal["CLOSED", "OPEN", "HALF_OPEN"]

@dataclass
class ServiceStatus:
    state: State = "CLOSED"
    failures: int = 0
    successes: int = 0
    last_failure_time: float = 0
    open_until: float = 0
    retry_count: int = 0 # Para backoff exponencial
    last_error: Optional[str] = None

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, base_cooldown: int = 60, max_cooldown: int = 3600):
        self.failure_threshold = failure_threshold
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self.services: Dict[str, ServiceStatus] = {}

    def _get_status(self, name: str) -> ServiceStatus:
        if name not in self.services:
            self.services[name] = ServiceStatus()
        return self.services[name]

    def can_execute(self, service_name: str) -> bool:
        """Verifica se o circuito permite a execução para o serviço."""
        status = self._get_status(service_name)
        now = time.time()

        if status.state == "OPEN":
            if now >= status.open_until:
                status.state = "HALF_OPEN"
                logger.info(f"🔄 [CB] {service_name} transicionou para HALF_OPEN. Testando resiliência...")
                return True
            return False
        
        if status.state == "HALF_OPEN":
            # No estado HALF_OPEN, permitimos apenas uma execução por vez (ou taxa reduzida)
            # Para simplificar aqui, permitimos a execução, mas record_success/failure decidirá o futuro
            return True

        return True

    def record_success(self, service_name: str):
        """Registra sucesso e fecha o circuito."""
        status = self._get_status(service_name)
        status.successes += 1
        
        if status.state != "CLOSED":
            logger.info(f"✅ [CB] {service_name} recuperado! Circuito FECHADO.")
            status.state = "CLOSED"
            status.failures = 0
            status.retry_count = 0
            status.open_until = 0
            
            # Nimrod local: sem telemetria supabase
            pass

    def record_failure(self, service_name: str, status_code: Optional[int] = None, error_msg: str = ""):
        """Registra falha e aplica backoff se necessário."""
        status = self._get_status(service_name)
        status.failures += 1
        status.last_failure_time = time.time()
        status.last_error = error_msg

        # Falhas fatais (Auth) abrem o circuito por tempo longo imediatamente
        if status_code in [401, 403]:
            logger.error(f"🚫 [CB] Falha FATAL ({status_code}) em {service_name}. Circuito ABERTO (1h).")
            status.state = "OPEN"
            status.open_until = time.time() + 3600
            return

        # Lógica de abertura do circuito
        if status.state == "CLOSED":
            if status.failures >= self.failure_threshold:
                self._open_circuit(service_name, status, status_code)
        elif status.state == "HALF_OPEN":
            # Falhou no teste de recuperação: volta para OPEN com penalidade maior
            status.retry_count += 1
            self._open_circuit(service_name, status, status_code)

    def _open_circuit(self, name: str, status: ServiceStatus, status_code: Optional[int]):
        """Abre o circuito com backoff exponencial."""
        wait_time = min(self.base_cooldown * (2 ** status.retry_count), self.max_cooldown)
        
        # Ajuste para Rate Limits
        if status_code == 429:
            wait_time = max(wait_time, 300) # Mínimo 5 min para 429
            
        status.state = "OPEN"
        status.open_until = time.time() + wait_time
        logger.warning(f"💥 [CB] {name} falhou. Circuito ABERTO por {wait_time}s. (Code: {status_code})")
        
        # try:
        #     from core.supabase_service import get_supabase_client
        #     db = get_supabase_client()
        #     db.table("system_events").insert({
        #         "event_type": "circuit_state_changed",
        #         "source": "circuit_breaker",
        #         "severity": "warning",
        #         "description": f"Circuito de {name} foi aberto por {wait_time}s devido a falhas.",
        #         "metadata": {
        #             "breaker": name,
        #             "from_state": "CLOSED/HALF_OPEN",
        #             "to_state": "OPEN",
        #             "wait_time_s": wait_time,
        #             "status_code": status_code,
        #             "status": "circuit_open"
        #         }
        #     }).execute()
        # except Exception as e:
        #     logger.error(f"[CB] Falha ao gravar telemetria de abertura: {e}")

    def get_metrics(self) -> Dict[str, Any]:
        """Retorna as métricas de todos os serviços para o Dashboard."""
        return {
            name: {
                "state": s.state,
                "failures": s.failures,
                "successes": s.successes,
                "open_until": s.open_until,
                "last_error": s.last_error
            } for name, s in self.services.items()
        }

# --- Instâncias Globais dos Circuit Breakers (PASA v94.3) ---

# Para os serviços de Inteligência Artificial (Cloud e Local)
ai_circuit_breaker = CircuitBreaker(failure_threshold=3, base_cooldown=60)

# Para o serviço de Scraping (Proxies, Zyte, etc.)
scraper_circuit_breaker = CircuitBreaker(failure_threshold=2, base_cooldown=300)

# Para o Banco de Dados (Supabase)
db_circuit_breaker = CircuitBreaker(failure_threshold=5, base_cooldown=30)
