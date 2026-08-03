from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    deepseek_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jsearch_api_key: str = ""

    proveedor_clasificacion: Literal["gemini", "deepseek"] = "gemini"
    modelo_gemini: str = "gemini-2.5-flash"
    modelo_deepseek: str = "deepseek-chat"

    ruta_bd: str = "data/app.db"
    hora_run_diario: str = "07:00"
    max_clasificaciones_por_run: int = 200


def get_settings() -> Settings:
    return Settings()
