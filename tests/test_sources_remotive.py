import json
from pathlib import Path

import httpx
import pytest
import respx

from app.limitador import sin_espera
from app.schemas import SearchQuery
from app.sources.remotive import URL_API, RemotiveSource

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "remotive_sample.json").read_text(encoding="utf-8")
)


class LimitadorEspia:
    def __init__(self) -> None:
        self.turnos: list[str] = []

    def espera_turno(self, url: str) -> float:
        self.turnos.append(url)
        return 0.0


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


def test_declara_que_no_filtra_en_servidor():
    assert RemotiveSource().filtra_en_servidor is False


@respx.mock
def test_varias_busquedas_se_resuelven_con_una_sola_descarga():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    queries = [
        SearchQuery(nombre="php", texto="php"),
        SearchQuery(nombre="ventas", texto="sell"),
        SearchQuery(nombre="diseno", texto="designer"),
    ]

    ofertas = RemotiveSource(limitador=sin_espera()).busca_varias(queries)

    assert ruta.call_count == 1
    assert {o.external_id for o in ofertas} == {"2091081", "2091082", "2091083"}


@respx.mock
def test_una_oferta_que_encaja_con_dos_busquedas_se_devuelve_una_vez():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    queries = [
        SearchQuery(nombre="php", texto="php"),
        SearchQuery(nombre="symfony", texto="symfony"),
    ]

    ofertas = RemotiveSource(limitador=sin_espera()).busca_varias(queries)

    assert [o.external_id for o in ofertas].count("2091082") == 1


@respx.mock
def test_pide_turno_al_limitador_antes_de_descargar():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    espia = LimitadorEspia()

    RemotiveSource(limitador=espia).search(SearchQuery(nombre="php", texto="php"))

    assert espia.turnos == [URL_API]
