import json
from pathlib import Path

import httpx
import pytest
import respx

from app.schemas import SearchQuery
from app.sources.remotive import URL_API, RemotiveSource

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "remotive_sample.json").read_text())


@respx.mock
def test_normaliza_una_oferta_al_esquema_comun():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = RemotiveSource().search(SearchQuery(nombre="php", texto="php"))

    php = next(o for o in ofertas if o.external_id == "2091082")
    assert php.fuente == "remotive"
    assert php.titulo == "Senior PHP Engineer"
    assert php.empresa == "Clipster"
    assert php.modalidad == "remoto"
    assert php.salario_texto == "60000 - 80000 EUR"
    assert php.publicada_en.year == 2026
    assert php.ubicacion == "Europe, UK, Germany, France"


@respx.mock
def test_filtra_en_local_porque_la_api_ignora_el_parametro_search():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = RemotiveSource().search(SearchQuery(nombre="php", texto="php"))

    assert {o.external_id for o in ofertas} == {"2091081", "2091082"}


@respx.mock
def test_el_filtro_local_busca_en_titulo_descripcion_y_tags():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = RemotiveSource().search(SearchQuery(nombre="sales", texto="sell"))

    assert [o.external_id for o in ofertas] == ["2091083"]


@respx.mock
def test_el_html_de_la_descripcion_se_convierte_a_texto():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = RemotiveSource().search(SearchQuery(nombre="php", texto="php"))

    php = next(o for o in ofertas if o.external_id == "2091082")
    assert "<b>" not in php.descripcion
    assert "Senior PHP" in php.descripcion


@respx.mock
def test_un_error_http_se_propaga_como_excepcion():
    respx.get(URL_API).mock(return_value=httpx.Response(503, text="unavailable"))

    with pytest.raises(httpx.HTTPStatusError):
        RemotiveSource().search(SearchQuery(nombre="php", texto="php"))
