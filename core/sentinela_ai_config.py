import os
import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

class Settings:
    PROJECT_NAME: str = "Sentinela Democrática"
    VERSION: str = "20.5.7"

    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # AI Cloud APIs
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # AI Local (Ollama)
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

    # AI Strategy
    IA_PROVIDER: str = os.getenv("IA_PROVIDER", "hybrid")  # hybrid, gemini, groq, ollama

    # External APIs
    RAPIDAPI_KEY: str = os.getenv("RAPIDAPI_KEY", "")
    META_ACCESS_TOKEN: str = os.getenv("META_ACCESS_TOKEN", "")
    META_API_VERSION: str = os.getenv("META_API_VERSION", "v19.0")

    # Abstra Cloud
    ABSTRA_API_KEY: str = os.getenv("ABSTRA_API_KEY", "")

    # Emergent & AskCodi APIs
    EMERGENT_API_KEY: str = os.getenv("EMERGENT_API_KEY", "")
    ASKCODI_API_KEY: str = os.getenv("ASKCODI_API_KEY", "")

    # Aureo APIs
    AUREO_API_KEY: str = os.getenv("AUREO_API_KEY", "")
    AUREO_API_SECRET: str = os.getenv("AUREO_API_SECRET", "")

    # Datasette
    DATASETTE_URL: str = os.getenv("DATASETTE_URL", "http://localhost:8002")

    # Security
    DASHBOARD_PIN: str = os.getenv("DASHBOARD_PIN", "1234")
    ADMIN_TOTP_SECRET: str = os.getenv("SENTINELA_ADMIN_TOTP_SECRET", "")

# Instantiate settings
settings = Settings()

# Load fallback providers from YAML
try:
    yaml_path = os.path.join(os.path.dirname(__file__), "..", "config_data", "fallback_providers.yaml")
    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)
        FALLBACK_PROVIDERS = yaml_data.get("fallback_providers", [])
except Exception:
    FALLBACK_PROVIDERS = []
