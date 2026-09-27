from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg://eve:eve@localhost:5432/eve"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    webhook_secret: str = "change-me-too"
    log_level: str = "INFO"
    log_format: str = "json"  # json or text


settings = Settings()
