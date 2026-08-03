import json
import httpx
import pytest
import respx
from pydantic import BaseModel

from app.llm.base import CuotaAgotadaError
from app.llm.deepseek import URL_API, DeepSeekProvider


class Salida(BaseModel):
    categoria: str
    razon: str


def respuesta_openai(contenido: str) -> dict:
    return {"choices": [{"message": {"content": contenido}}]}


@respx.mock
def test_parsea_y_valida_la_respuesta_contra_el_esquema():
    respx.post(URL_API).mock(
        return_value=httpx.Response(
            200,
            json=respuesta_openai('{"categoria": "revisar", "razon": "faltan datos"}'),
        )
    )

    resultado = DeepSeekProvider(api_key="k").complete_json(
        system="s", user="u", modelo_salida=Salida
    )

    assert resultado.categoria == "revisar"
    assert resultado.razon == "faltan datos"


@respx.mock
def test_envia_el_system_prompt_y_pide_json():
    ruta = respx.post(URL_API).mock(
        return_value=httpx.Response(
            200, json=respuesta_openai('{"categoria": "descartar", "razon": "x"}')
        )
    )

    DeepSeekProvider(api_key="k").complete_json(
        system="eres un evaluador", user="la oferta", modelo_salida=Salida
    )

    cuerpo = ruta.calls.last.request.content.decode()
    assert "eres un evaluador" in cuerpo
    assert "json_object" in cuerpo


@respx.mock
def test_un_json_invalido_lanza_error_de_validacion():
    respx.post(URL_API).mock(
        return_value=httpx.Response(200, json=respuesta_openai("esto no es json"))
    )

    with pytest.raises(ValueError, match="no devolvió JSON válido"):
        DeepSeekProvider(api_key="k").complete_json(
            system="s", user="u", modelo_salida=Salida
        )


@respx.mock
def test_un_error_http_se_propaga():
    respx.post(URL_API).mock(return_value=httpx.Response(500, json={"error": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        DeepSeekProvider(api_key="k").complete_json(
            system="s", user="u", modelo_salida=Salida
        )


@respx.mock
def test_un_429_se_traduce_a_la_excepcion_de_cuota_del_dominio():
    respx.post(URL_API).mock(return_value=httpx.Response(429, json={"error": "rate limit"}))

    with pytest.raises(CuotaAgotadaError):
        DeepSeekProvider(api_key="k").complete_json(
            system="s", user="u", modelo_salida=Salida
        )


@respx.mock
def test_un_402_sin_saldo_tambien_es_cuota_agotada():
    respx.post(URL_API).mock(
        return_value=httpx.Response(402, json={"error": "Insufficient Balance"})
    )

    with pytest.raises(CuotaAgotadaError):
        DeepSeekProvider(api_key="k").complete_json(
            system="s", user="u", modelo_salida=Salida
        )


@respx.mock
def test_el_pensamiento_va_desactivado_por_defecto():
    """Medido con deepseek-v4-pro: 62 s por oferta y 1612 de 1929 tokens de salida
    gastados en razonar. Además, DeepSeek ignora `temperature` en modo pensamiento, así
    que la temperatura 0.2 que el diseño exige no se estaba aplicando."""
    ruta = respx.post(URL_API).mock(
        return_value=httpx.Response(200, json=respuesta_openai('{"categoria":"x","razon":"y"}'))
    )

    DeepSeekProvider(api_key="k").complete_json(system="s", user="u", modelo_salida=Salida)

    cuerpo = json.loads(ruta.calls.last.request.content)
    assert cuerpo["thinking"] == {"type": "disabled"}
    assert cuerpo["temperature"] == 0.2


@respx.mock
def test_el_pensamiento_puede_activarse():
    ruta = respx.post(URL_API).mock(
        return_value=httpx.Response(200, json=respuesta_openai('{"categoria":"x","razon":"y"}'))
    )

    DeepSeekProvider(api_key="k", pensamiento=True).complete_json(
        system="s", user="u", modelo_salida=Salida
    )

    assert json.loads(ruta.calls.last.request.content)["thinking"] == {"type": "enabled"}
