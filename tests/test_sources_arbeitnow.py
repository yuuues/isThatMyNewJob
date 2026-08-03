import json
from pathlib import Path

import httpx
import respx

from app.limitador import sin_espera
from app.schemas import SearchQuery
from app.sources.arbeitnow import URL_API, ArbeitnowSource

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "arbeitnow_sample.json").read_text())


class LimitadorEspia:
    def __init__(self) -> None:
        self.turnos: list[str] = []

    def espera_turno(self, url: str) -> float:
        self.turnos.append(url)
        return 0.0


@respx.mock
def test_normaliza_una_oferta_remota():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = ArbeitnowSource().search(SearchQuery(nombre="php", texto="php"))

    php = next(o for o in ofertas if o.external_id == "senior-php-developer-remote-303585")
    assert php.fuente == "arbeitnow"
    assert php.modalidad == "remoto"
    assert php.empresa == "Beta Digital GmbH"
    assert php.ubicacion == "Berlin"
    assert php.publicada_en is not None


@respx.mock
def test_una_oferta_no_remota_se_marca_presencial():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = ArbeitnowSource().search(SearchQuery(nombre="todo", texto="passau"))

    assert ofertas[0].modalidad == "presencial"


@respx.mock
def test_solo_remoto_descarta_las_presenciales():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = ArbeitnowSource().search(
        SearchQuery(nombre="todo", texto="", solo_remoto=True)
    )

    assert all(o.modalidad == "remoto" for o in ofertas)
    assert len(ofertas) == 1


@respx.mock
def test_el_created_at_unix_se_convierte_a_fecha():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = ArbeitnowSource().search(SearchQuery(nombre="todo", texto=""))

    assert ofertas[0].publicada_en.year >= 2026


@respx.mock
def test_sigue_la_paginacion_hasta_agotar_el_maximo():
    pagina_2 = {
        "data": [
            {
                "slug": "otro-php-303586",
                "company_name": "Gamma",
                "title": "PHP Engineer",
                "description": "<p>PHP work</p>",
                "remote": True,
                "url": "https://www.arbeitnow.com/jobs/gamma/otro-php-303586",
                "tags": [],
                "job_types": ["full-time"],
                "location": "Madrid",
                "created_at": 1785839500
            }
        ],
        "links": {"next": None},
        "meta": {"current_page": 2}
    }
    primera = dict(FIXTURE)
    primera["links"] = {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"}

    respx.get(URL_API, params={"page": "1"}).mock(return_value=httpx.Response(200, json=primera))
    respx.get(URL_API, params={"page": "2"}).mock(return_value=httpx.Response(200, json=pagina_2))

    fuente = ArbeitnowSource(max_paginas=2, limitador=sin_espera())
    ofertas = fuente.search(SearchQuery(nombre="php", texto="php"))

    assert "otro-php-303586" in {o.external_id for o in ofertas}


def test_declara_que_no_filtra_en_servidor():
    assert ArbeitnowSource().filtra_en_servidor is False


@respx.mock
def test_varias_busquedas_se_resuelven_con_una_sola_descarga():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    queries = [
        SearchQuery(nombre="php", texto="php"),
        SearchQuery(nombre="passau", texto="passau"),
    ]

    ofertas = ArbeitnowSource(limitador=sin_espera()).busca_varias(queries)

    assert ruta.call_count == 1
    assert len({o.external_id for o in ofertas}) == len(ofertas)
    assert "senior-php-developer-remote-303585" in {o.external_id for o in ofertas}


@respx.mock
def test_cada_busqueda_conserva_su_propio_solo_remoto():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    queries = [SearchQuery(nombre="remotas", texto="", solo_remoto=True)]

    ofertas = ArbeitnowSource(limitador=sin_espera()).busca_varias(queries)

    assert [o.modalidad for o in ofertas] == ["remoto"]


@respx.mock
def test_pide_turno_al_limitador_en_cada_pagina():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    espia = LimitadorEspia()

    ArbeitnowSource(limitador=espia).search(SearchQuery(nombre="php", texto="php"))

    assert espia.turnos == [URL_API]
