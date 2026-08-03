import httpx
import pytest
import respx
from pydantic import BaseModel

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
    respx.post(URL_API).mock(return_value=httpx.Response(429, json={"error": "rate limit"}))

    with pytest.raises(httpx.HTTPStatusError):
        DeepSeekProvider(api_key="k").complete_json(
            system="s", user="u", modelo_salida=Salida
        )
