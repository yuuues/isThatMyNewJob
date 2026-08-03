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

    # Dos modelos distintos a propósito, porque las dos tareas no se parecen en nada.
    #
    # Clasificar son ~100 llamadas al día de texto plano con salida estructurada: manda
    # el precio. Flash-Lite tiene capa gratuita y cuesta $0.15/$1.25 por millón de
    # tokens, frente a los $0.75/$3.75 de Flash, que además no tiene capa gratuita.
    #
    # Extraer el perfil es UNA llamada en la vida del proyecto, multimodal sobre el PDF,
    # y de ella depende todo lo demás: un perfil mal extraído envenena cada clasificación
    # posterior. Ahí el precio es irrelevante y se usa el modelo bueno.
    modelo_gemini: str = "gemini-3.5-flash-lite"
    modelo_perfil: str = "gemini-3.6-flash"
    modelo_deepseek: str = "deepseek-v4-flash"

    # El plan gratuito son 200 créditos/mes con límite duro. Se deja margen para
    # las peticiones manuales de diagnóstico, que también descuentan.
    jsearch_limite_mensual: int = 180
    jsearch_paginas: int = 2

    # Scrappa sirve ofertas de Indeed. Su plan gratuito son 500 créditos/mes, pero a
    # un crédito por LLAMADA y hasta 20 ofertas por llamada, no un crédito por oferta
    # como JobsPipe. Es el mejor ratio del proyecto: ~10.000 ofertas al mes gratis.
    scrappa_api_key: str = ""
    scrappa_limite_mensual: int = 450

    ruta_bd: str = "data/app.db"
    hora_run_diario: str = "07:00"
    max_clasificaciones_por_run: int = 200


def get_settings() -> Settings:
    return Settings()
