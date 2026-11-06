"""Process-level configuration loaded from environment variables / .env.

Anything secret (keys, SIP credentials, passwords) lives here and is never
written to the database or returned by the API. Runtime-editable behaviour
(prompts, voice, hours, ...) lives in the database via services.settings.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]  # env vars accept "a,b" or a JSON list

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env",), env_file_encoding="utf-8", extra="ignore"
    )

    # --- core ---
    environment: str = "development"  # development | production | test
    app_name: str = "BongoHive AI Voice Agent"
    secret_key: SecretStr = SecretStr("dev-insecure-change-me-0123456789abcdef0123456789")
    database_url: str = f"sqlite+aiosqlite:///{ROOT_DIR / 'data' / 'voiceagent.db'}"
    data_dir: Path = ROOT_DIR / "data"
    models_dir: Path = ROOT_DIR / "models"
    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: CsvList = Field(default_factory=list)
    public_base_url: str = "http://localhost:8000"

    # --- bootstrap admin (only used when no users exist) ---
    admin_email: str = "admin@example.com"
    admin_password: SecretStr = SecretStr("ChangeMe!12345")

    # --- auth ---
    session_hours: int = 12
    cookie_secure: bool = False
    # Local demos only: every request acts as the first active admin, no sign-in.
    # Refused in production (see validate_for_production).
    auth_disabled: bool = False

    # --- LLM ---
    llm_provider: str = "ollama"  # ollama | openai (any OpenAI-compatible API)
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:3b-instruct"
    llm_api_key: SecretStr = SecretStr("")
    llm_fallback_provider: str = ""  # optional second provider used on failure
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: SecretStr = SecretStr("")
    llm_timeout_s: float = 20.0
    llm_first_token_timeout_s: float = 8.0
    llm_num_ctx: int = 4096

    # --- STT ---
    stt_model: str = "small.en"  # faster-whisper model name or path
    stt_device: str = "auto"  # auto | cpu | cuda
    stt_compute_type: str = "int8"
    stt_beam_size: int = 1
    stt_threads: int = 4

    # --- TTS ---
    tts_engine: str = "kokoro"  # kokoro | piper
    tts_kokoro_model: str = "kokoro/kokoro-v1.0.int8.onnx"
    tts_kokoro_voices: str = "kokoro/voices-v1.0.bin"
    tts_piper_voice: str = "piper/en_GB-cori-high.onnx"
    tts_threads: int = 4

    # --- embeddings ---
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # --- capacity ---
    max_concurrent_calls: int = 4
    preload_models: bool = True

    # --- telephony ---
    telephony_provider: str = "simulator"  # simulator | asterisk
    audiosocket_host: str = "0.0.0.0"
    audiosocket_port: int = 9092
    asterisk_ami_host: str = "localhost"
    asterisk_ami_port: int = 5038
    asterisk_ami_user: str = "voiceagent"
    asterisk_ami_password: SecretStr = SecretStr("")
    asterisk_trunk_endpoint: str = "zm-trunk"  # PJSIP endpoint name of the SIP trunk
    asterisk_dial_format: str = "e164"  # e164 | e164_no_plus | national | international (what the trunk expects)
    asterisk_outbound_context: str = "outbound-agent"
    asterisk_transfer_context: str = "agent-transfer"
    asterisk_callback_token: SecretStr = SecretStr("")  # shared secret for dialplan CURL()
    outbound_caller_id: str = ""  # E.164 number presented on outbound calls
    human_transfer_target: str = ""  # e.g. PJSIP/reception or +2602118xxxxx

    # --- outbound safety (off by default) ---
    outbound_enabled: bool = False
    allow_bulk_outbound: bool = False
    bulk_threshold: int = 50
    outbound_max_calls_per_hour: int = 30
    outbound_min_hours_between_calls: int = 24
    allowed_country_codes: CsvList = Field(default_factory=lambda: ["260"])

    # --- integrations ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_starttls: bool = True
    sms_provider: str = ""  # "" | twilio | http
    sms_from: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    sms_http_url: str = ""
    sms_http_token: SecretStr = SecretStr("")
    n8n_base_url: str = ""
    webhook_signing_secret: SecretStr = SecretStr("")
    webhook_allowed_hosts: CsvList = Field(default_factory=list)
    knowledge_allowed_domains: CsvList = Field(default_factory=lambda: ["bongohive.co.zm"])

    # --- data protection ---
    recordings_dir: Path = ROOT_DIR / "data" / "recordings"
    uploads_dir: Path = ROOT_DIR / "data" / "uploads"

    @field_validator(
        "cors_origins", "allowed_country_codes", "webhook_allowed_hosts",
        "knowledge_allowed_domains", mode="before",
    )
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def model_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.models_dir / p

    def validate_for_production(self) -> list[str]:
        """Return a list of problems that must be fixed before running in production."""
        problems = []
        if self.secret_key.get_secret_value().startswith("dev-insecure") or len(
            self.secret_key.get_secret_value()
        ) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if self.admin_password.get_secret_value() == "ChangeMe!12345":
            problems.append("ADMIN_PASSWORD must be changed from the example value")
        if self.auth_disabled:
            problems.append("AUTH_DISABLED must be false in production")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true behind HTTPS in production")
        if self.telephony_provider == "asterisk" and not self.asterisk_callback_token.get_secret_value():
            problems.append("ASTERISK_CALLBACK_TOKEN is required when TELEPHONY_PROVIDER=asterisk")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
