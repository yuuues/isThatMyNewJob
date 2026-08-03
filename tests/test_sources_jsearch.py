import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.limitador import sin_espera
from app.presupuesto import PresupuestoAgotadoError
from app.schemas import SearchQuery
from app.sources.jsearch import URL_API, JSearchSource, fecha_relativa, limpia_ubicacion

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jsearch_sample.json").read_text(encoding="utf-8"))

AHORA = datetime(2026, 8, 3, 12, 0)


class PresupuestoFalso:
    def __init__(self, disponible: int = 100) -> None:
        self.disponible = disponible
        self.consumido = 0

    def intenta_consumir(self, n: int = 1) -> bool:
        if self.disponible - self.consumido < n:
            return False
        self.consumido += n
        return True

    def restante(self) -> float:
        return self.disponible - self.consumido

    def exige(self, n: int = 1) -> None:
        if not self.intenta_consumir(n):
            raise PresupuestoAgotadoError("cupo agotado: jsearch")


def fuente(**kwargs) -> JSearchSource:
    kwargs.setdefault("limitador", sin_espera())
    kwargs.setdefault("presupuesto", PresupuestoFalso())
    kwargs.setdefault("reloj", lambda: AHORA)
    return JSearchSource(api_key="k", **kwargs)


@respx.mock
def test_normaliza_una_oferta_al_esquema_comun():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="desarrollador php"))

    primera = ofertas[0]
    assert primera.fuente == "jsearch"
    assert primera.titulo == "Senior PHP Developer"
    assert primera.empresa == "CHECK24 España"
    assert primera.url.startswith("https://es.linkedin.com/jobs/view/")
    assert "PHP 8" in primera.descripcion
    assert primera.descripcion_truncada is False


def test_limpia_el_publisher_pegado_a_la_ubicacion():
    """job_location llega como 'Madrid     •  a través de LinkedIn'."""
    assert limpia_ubicacion("Madrid     •  a través de LinkedIn") == "Madrid"
    assert limpia_ubicacion("País Vasco     •  a través de Spain Jobs Expertini") == "País Vasco"
    assert limpia_ubicacion("Barcelona") == "Barcelona"
    assert limpia_ubicacion(None) is None


def test_traduce_las_fechas_relativas_en_espanol():
    """job_posted_at_datetime_utc llega nulo; sólo hay texto como 'hace 5 días'."""
    assert fecha_relativa("hace 15 horas", AHORA) == datetime(2026, 8, 2, 21, 0)
    assert fecha_relativa("hace 5 días", AHORA) == datetime(2026, 7, 29, 12, 0)
    assert fecha_relativa("hace 2 semanas", AHORA) == datetime(2026, 7, 20, 12, 0)
    assert fecha_relativa("hace 30 minutos", AHORA) == datetime(2026, 8, 3, 11, 30)
    assert fecha_relativa("ayer", AHORA) is None
    assert fecha_relativa(None, AHORA) is None


@respx.mock
def test_la_modalidad_no_se_fia_solo_de_job_is_remote():
    """job_is_remote dice false en ofertas cuyo título pone 'Remoto'. Medido sobre
    datos reales: el campo no es fiable para el mercado español."""
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))
    por_titulo = {o.titulo: o for o in ofertas}

    assert por_titulo["Desarrollador PHP/Laravel Senior — Remoto - NTT Ltd."].modalidad == "remoto"
    assert por_titulo["Desarrollador PHP, hibrido"].modalidad == "hibrido"


@respx.mock
def test_recoge_el_salario_cuando_lo_hay():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))
    con_salario = next(o for o in ofertas if o.salario_min is not None)

    assert con_salario.salario_min == 42000
    assert con_salario.salario_max == 55000


@respx.mock
def test_envia_pais_idioma_y_autenticacion():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente().search(SearchQuery(nombre="php", texto="desarrollador php", pais="es"))

    peticion = ruta.calls.last.request
    assert peticion.url.params["query"] == "desarrollador php in Spain"
    assert peticion.url.params["country"] == "es"
    assert peticion.url.params["language"] == "es"
    assert peticion.headers["X-RapidAPI-Key"] == "k"


@respx.mock
def test_la_ubicacion_de_la_busqueda_entra_en_la_query():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente().search(SearchQuery(nombre="x", texto="backend", ubicacion="Barcelona"))

    assert ruta.calls.last.request.url.params["query"] == "backend in Barcelona"


@respx.mock
def test_solo_remoto_usa_el_parametro_de_la_api():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente().search(SearchQuery(nombre="x", texto="backend", solo_remoto=True))

    assert ruta.calls.last.request.url.params["work_from_home"] == "true"


@respx.mock
def test_cada_pagina_consume_un_credito():
    """La API cobra un crédito por página de 10 resultados, no por petición."""
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    presupuesto = PresupuestoFalso(disponible=10)

    fuente(presupuesto=presupuesto, paginas=3).search(SearchQuery(nombre="x", texto="php"))

    assert presupuesto.consumido == 3


@respx.mock
def test_sin_cupo_no_se_llama_a_la_api():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    presupuesto = PresupuestoFalso(disponible=0)

    with pytest.raises(PresupuestoAgotadoError):
        fuente(presupuesto=presupuesto).search(SearchQuery(nombre="x", texto="php"))

    assert ruta.call_count == 0


@respx.mock
def test_un_error_http_no_consume_credito_de_mas():
    respx.get(URL_API).mock(return_value=httpx.Response(500, text="boom"))
    presupuesto = PresupuestoFalso(disponible=10)

    with pytest.raises(RuntimeError):
        fuente(presupuesto=presupuesto).search(SearchQuery(nombre="x", texto="php"))

    assert presupuesto.consumido == 1


def test_sin_api_key_falla_al_construir():
    with pytest.raises(ValueError, match="JSEARCH_API_KEY"):
        JSearchSource(api_key="")


@respx.mock
def test_un_404_indica_que_la_ruta_de_la_api_cambio():
    """El gateway devuelve 404 cuando el endpoint no existe; el mensaje debe decirlo,
    porque parece un fallo de credenciales y no lo es."""
    respx.get(URL_API).mock(
        return_value=httpx.Response(404, json={"message": "Endpoint '/search-v2' does not exist"})
    )

    with pytest.raises(RuntimeError, match="endpoint"):
        fuente().search(SearchQuery(nombre="x", texto="php"))
