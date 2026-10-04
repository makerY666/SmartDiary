from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "sqlite:///./.data/smartdiary.db"
    data_dir: Path = Path(".data")
    jwt_secret: str = "development-only-change-before-deploying-32chars"
    encryption_key: str = ""
    registration_token: str = ""
    ai_mode: str = "disabled"  # disabled, deterministic (tests), bailian
    dashscope_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    text_model: str = "qwen-plus"
    vision_model: str = "qwen3-vl-flash"
    asr_model: str = "qwen3-asr-flash"
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    monthly_budget_yuan: float = 300
    budget_warning_yuan: float = 240
    fixed_monthly_cost_yuan: float = 80
    reserve_yuan: float = 20
    text_input_yuan_per_million: float = 0.8
    text_output_yuan_per_million: float = 2
    vision_input_yuan_per_million: float = 0.5
    vision_output_yuan_per_million: float = 2
    embedding_yuan_per_million: float = 0.5
    asr_yuan_per_second: float = 0.00022
    storage_backend: str = "local"
    oss_endpoint: str = ""
    oss_bucket: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    max_attachment_bytes: int = 20 * 1024 * 1024
    allow_public_web_fetch: bool = False

    @field_validator("ai_mode")
    @classmethod
    def valid_ai_mode(cls, value):
        if value not in {"disabled", "deterministic", "bailian", "deepseek"}:
            raise ValueError("invalid ai_mode")
        return value

    @property
    def text_ai_enabled(self):
        return self.ai_mode in {"bailian", "deepseek"}

    def validate_production(self):
        if self.environment == "production":
            if self.jwt_secret.startswith("development") or len(self.jwt_secret) < 32:
                raise RuntimeError("Production requires a random JWT_SECRET (at least 32 characters)")
            if not self.encryption_key or not self.registration_token:
                raise RuntimeError("Production requires ENCRYPTION_KEY and REGISTRATION_TOKEN")
            if not self.database_url.startswith("postgresql"):
                raise RuntimeError("Production requires PostgreSQL")
            if self.ai_mode == "deterministic":
                raise RuntimeError("Deterministic test AI must not be used in production")


settings = Settings()
