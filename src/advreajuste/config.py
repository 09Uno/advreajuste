from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="ADVREAJUSTE_", case_sensitive=False, extra="ignore"
    )

    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None

    # Modelos Anthropic
    llm_model: str = "claude-sonnet-4-5-20250929"
    llm_model_vision: str = "claude-sonnet-4-5-20250929"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"
    llm_model_opus: str = "claude-opus-4-1-20250805"

    # Modelos Gemini (vision primário por custo-benefício — ~7× mais barato que Claude Haiku)
    gemini_model_vision: str = "gemini-2.5-flash"
    gemini_model_vision_cheap: str = "gemini-2.0-flash"

    # Escolha de provider para vision fallback: "gemini" (default) | "claude"
    vision_provider: str = "gemini"
    data_dir: Path = Path("data")
    templates_dir: Path = Path("templates")
    log_level: str = "INFO"
    use_batch: bool = False

    @property
    def casos_dir(self) -> Path:
        return self.data_dir / "casos"

    @property
    def custody_dir(self) -> Path:
        return self.data_dir / "custody"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def ans_dir(self) -> Path:
        return self.data_dir / "ans"


settings = Settings()
