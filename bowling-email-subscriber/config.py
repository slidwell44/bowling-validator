from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApplicationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APPLICATION_",
        extra="ignore",
    )

    NAME: str = "bowling-subscriber"
    VERSION: str = "0.1.0"


class GoogleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="GOOGLE_OAUTH_",
        extra="ignore",
    )

    CLIENT_ID: str
    CLIENT_SECRET: SecretStr


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="OPENAI_",
        extra="ignore",
    )

    API_KEY: SecretStr


class GmailSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="GMAIL_", extra="ignore"
    )

    PUBSUB_TOPIC: str = "projects/bowling-subscriber/topics/gmail-inbox"
    TOKEN_JSON: SecretStr | None = None
    DATABASE_URL: SecretStr | None = Field(
        default=None, validation_alias="DATABASE_URL"
    )
    PUSH_AUDIENCE: str | None = None
    PUSH_SERVICE_ACCOUNT: str | None = None
    WATCH_AUDIENCE: str | None = None
    WATCH_SERVICE_ACCOUNT: str | None = None
    ADMIN_TOKEN: SecretStr | None = None


class Settings(BaseModel):
    app: ApplicationSettings = Field(default_factory=ApplicationSettings)
    google: GoogleSettings = Field(default_factory=GoogleSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)


def get_settings() -> Settings:
    return Settings()
