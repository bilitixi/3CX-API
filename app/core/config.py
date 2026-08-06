from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Shared secret required on the `Authorization: Bearer <token>` header of every
    # request to this service. Leave unset only for local development.
    api_auth_token: str = ""

    # 3CX Call Control API (ring-with-no-answer-fallback workflow)
    threecx_pbx_base_url: str = ""
    threecx_client_id: str = ""
    threecx_client_secret: str = ""
    threecx_grant_type: str = "client_credentials"
    threecx_token_safety_margin_seconds: int = 60

    # Default queue DN for POST /calls/dial-into-queue, e.g. "8003".
    threecx_queue_dn: str = ""

    # Default source_dn for POST /calls/dial-into-queue, e.g. the internal number that
    # should ring first before being connected into the queue.
    threecx_source_dn: str = ""


settings = Settings()
