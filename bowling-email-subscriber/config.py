from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class _ApplicationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APPLICATION_",
    )

    NAME: str
    VERSION: str


class _GoogleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="GOOGLE_OAUTH_",
    )

    CLIENT_ID: str
    CLIENT_SECRET: SecretStr


class _OpenaiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="OPENAI_",
    )

    API_KEY: SecretStr


class Settings(BaseModel):
    app: _ApplicationSettings = Field(default_factory=_ApplicationSettings)
    google: _GoogleSettings = Field(default_factory=_GoogleSettings)
    openai: _OpenaiSettings = Field(default_factory=_OpenaiSettings)


def get_settings() -> Settings:
    return Settings()
