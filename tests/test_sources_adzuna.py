import json
from pathlib import Path

import httpx
import pytest
import respx

from app.schemas import SearchQuery
from app.sources.adzuna import AdzunaSource, detecta_modalidad, url_api

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "adzuna_sample.json").read_text())


def fuente() -> AdzunaSource:
    return AdzunaSource(app_id="id", app_key="key")


@respx.mock
def test_normaliza_una_oferta_al_esquema_comun():
    respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))

    primera = ofertas[0]
    assert primera.fuente == "adzuna"
    assert primera.external_id == "4912345678"
    assert primera.empresa == "Acme S.L."
    assert primera.ubicacion == "Madrid, Comunidad de Madrid"
    assert primera.salario_min == 45000
    assert primera.salario_max == 60000
    assert primera.url == "https://www.adzuna.es/land/ad/4912345678"


@respx.mock
def test_el_salario_ausente_queda_a_none():
    respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))

    assert ofertas[1].salario_min is None
    assert ofertas[1].salario_max is None


def test_detecta_modalidad_por_palabras_clave():
    assert detecta_modalidad("Puesto en remoto para toda España") == "remoto"
    assert detecta_modalidad("Trabajo con teletrabajo total") == "remoto"
    assert detecta_modalidad("Modelo híbrido, 2 días en oficina") == "hibrido"
    assert detecta_modalidad("Desarrollador para el equipo de backend") == "desconocida"


@respx.mock
def test_la_busqueda_va_en_el_parametro_what():
    ruta = respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente().search(SearchQuery(nombre="php senior", texto="php senior", ubicacion="Madrid"))

    peticion = ruta.calls.last.request
    assert peticion.url.params["what"] == "php senior"
    assert peticion.url.params["where"] == "Madrid"
    assert peticion.url.params["app_id"] == "id"


@respx.mock
def test_una_respuesta_no_json_da_un_error_claro():
    respx.get(url_api("es")).mock(
        return_value=httpx.Response(400, text="<html>Uh oh, something isn't right</html>")
    )

    with pytest.raises(RuntimeError, match="Adzuna respondió 400"):
        fuente().search(SearchQuery(nombre="php", texto="php"))


def test_sin_credenciales_falla_al_construir():
    with pytest.raises(ValueError, match="credenciales"):
        AdzunaSource(app_id="", app_key="")
