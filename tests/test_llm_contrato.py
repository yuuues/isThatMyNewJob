"""Tests contra las APIs reales. Consumen cuota; no forman parte de la suite normal.

Ejecutar a mano:
    docker compose run --rm app pytest tests/test_llm_contrato.py -v -m contrato
"""

import os

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.contrato


class Respuesta(BaseModel):
    capital: str


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="sin GEMINI_API_KEY")
def test_gemini_devuelve_json_conforme_al_esquema():
    from app.llm.gemini import GeminiProvider

    resultado = GeminiProvider(api_key=os.environ["GEMINI_API_KEY"]).complete_json(
        system="Responde en JSON.",
        user="¿Cuál es la capital de España?",
        modelo_salida=Respuesta,
    )

    assert "madrid" in resultado.capital.lower()


@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="sin DEEPSEEK_API_KEY")
def test_deepseek_devuelve_json_conforme_al_esquema():
    from app.llm.deepseek import DeepSeekProvider

    resultado = DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"]).complete_json(
        system="Responde en JSON.",
        user="¿Cuál es la capital de España?",
        modelo_salida=Respuesta,
    )

    assert "madrid" in resultado.capital.lower()
