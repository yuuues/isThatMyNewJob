"""Conector de Scrappa, que sirve ofertas de Indeed para España.

Es la mejor fuente del proyecto para el mercado español, medido sobre datos reales:
descripciones de mediana 3285 caracteres (frente a los 500 exactos de Adzuna y los
1831 de JSearch), 20 ofertas por crédito y 500 créditos gratis al mes.
"""

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.limitador import sin_espera
from app.presupuesto import PresupuestoAgotadoError
from app.schemas import SearchQuery
from app.sources.scrappa import URL_API, ScrappaSource

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "scrappa_sample.json").read_text(encoding="utf-8")
)


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
            raise PresupuestoAgotadoError("cupo agotado: scrappa")


def fuente(**kwargs) -> ScrappaSource:
    kwargs.setdefault("limitador", sin_espera())
    kwargs.setdefault("presupuesto", PresupuestoFalso())
    return ScrappaSource(api_key="k", **kwargs)


def buscar(**kwargs):
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    return fuente(**kwargs).search(SearchQuery(nombre="php", texto="desarrollador php"))


@respx.mock
def test_normaliza_una_oferta_al_esquema_comun():
    ofertas = buscar()

    primera = ofertas[0]
    assert primera.fuente == "scrappa"
    assert primera.external_id == "c94ecc5e19f29d11"
    assert primera.titulo == "Developer Fullstack (Swift-PHP) – M/H/NB"
    assert primera.empresa == "CEGID"
    assert primera.url.startswith("https://jobs.cegid.com/")
    assert primera.publicada_en == datetime(2026, 7, 30, 5, 0, 0)


@respx.mock
def test_la_ubicacion_sale_del_campo_formateado():
    """`location` es un objeto, no una cadena. Sin desanidarlo, la clave de
    deduplicación llevaría un diccionario entero."""
    ofertas = buscar()

    assert ofertas[0].ubicacion == "Manresa, Barcelona provincia"
    assert ofertas[2].ubicacion == "Madrid, Madrid provincia"


@respx.mock
def test_la_descripcion_llega_completa_y_sin_html():
    ofertas = buscar()

    assert "<p>" not in ofertas[0].descripcion
    assert "<b>" not in ofertas[0].descripcion
    assert "Laravel" in ofertas[0].descripcion
    assert ofertas[0].descripcion_truncada is False


@respx.mock
def test_la_modalidad_usa_las_senales_propias_de_la_fuente():
    """A diferencia de las otras cuatro fuentes, aquí no hay que adivinar: la API trae
    `location.is_remote` y etiquetas explícitas en `attributes`."""
    ofertas = buscar()

    assert ofertas[1].modalidad == "remoto"  # location.is_remote = true
    assert ofertas[2].modalidad == "hibrido"  # attributes incluye "Hybrid work"


@respx.mock
def test_hibrido_gana_a_remoto_cuando_ambos_aparecen():
    """La primera oferta trae "Remote" en attributes pero is_remote a false y el texto
    dice "modalidad remota parcial": eso es híbrido, no remoto total."""
    ofertas = buscar()

    assert ofertas[0].modalidad == "hibrido"


@respx.mock
def test_las_etiquetas_de_la_fuente_llegan_como_tags():
    ofertas = buscar()

    assert "Laravel" in ofertas[0].tags
    assert "PHP" in ofertas[0].tags


@respx.mock
def test_un_salario_nulo_no_inventa_nada():
    """Medido: salary llega null en 20 de 20 ofertas españolas. No conocemos la forma
    de uno no nulo, así que el conector no puede suponerla."""
    ofertas = buscar()

    assert all(o.salario_min is None and o.salario_max is None for o in ofertas)
    assert all(o.salario_texto is None for o in ofertas)


@respx.mock
def test_un_salario_en_texto_se_conserva_sin_interpretarlo():
    """Si algún día llega un salario, se pasa como texto al modelo en vez de meterlo
    en los campos numéricos: no sabemos su periodo y compararlo contra un mínimo anual
    es el error que ya cometimos con Adzuna."""
    con_salario = json.loads(json.dumps(FIXTURE))
    con_salario["data"]["jobs"][0]["salary"] = "30.000 € - 40.000 € al año"
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=con_salario))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))

    assert ofertas[0].salario_texto == "30.000 € - 40.000 € al año"
    assert ofertas[0].salario_min is None


@respx.mock
def test_envia_la_consulta_la_ubicacion_y_la_clave():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente().search(SearchQuery(nombre="x", texto="backend", ubicacion="Barcelona", pais="es"))

    peticion = ruta.calls.last.request
    assert peticion.url.params["query"] == "backend"
    assert peticion.url.params["location"] == "Barcelona"
    assert peticion.url.params["country"] == "es"
    assert peticion.headers["X-API-KEY"] == "k"


@respx.mock
def test_sin_ubicacion_busca_en_todo_el_pais():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente().search(SearchQuery(nombre="x", texto="backend", pais="es"))

    assert ruta.calls.last.request.url.params["location"] == "Spain"


@respx.mock
def test_cada_llamada_consume_un_credito():
    """Scrappa cobra un crédito por llamada y devuelve hasta 20 ofertas, así que el
    coste NO es por oferta como en JobsPipe."""
    respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    presupuesto = PresupuestoFalso(disponible=10)

    fuente(presupuesto=presupuesto).search(SearchQuery(nombre="x", texto="php"))

    assert presupuesto.consumido == 1


@respx.mock
def test_sin_cupo_no_se_llama_a_la_api():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))
    presupuesto = PresupuestoFalso(disponible=0)

    with pytest.raises(PresupuestoAgotadoError):
        fuente(presupuesto=presupuesto).search(SearchQuery(nombre="x", texto="php"))

    assert ruta.call_count == 0


@respx.mock
def test_una_clave_invalida_da_un_error_legible():
    respx.get(URL_API).mock(
        return_value=httpx.Response(
            401, json={"error": "Invalid API key", "code": "UNAUTHORIZED", "status": 401}
        )
    )

    with pytest.raises(RuntimeError, match="401"):
        fuente().search(SearchQuery(nombre="x", texto="php"))


@respx.mock
def test_una_respuesta_sin_exito_no_se_procesa():
    respx.get(URL_API).mock(return_value=httpx.Response(200, json={"success": False, "data": {}}))

    assert fuente().search(SearchQuery(nombre="x", texto="php")) == []


def test_sin_api_key_falla_al_construir():
    with pytest.raises(ValueError, match="SCRAPPA_API_KEY"):
        ScrappaSource(api_key="")


@respx.mock
def test_pide_el_maximo_de_resultados_que_permite_un_credito():
    """`limit` llega a 100 y el precio no cambia: es un crédito por llamada. Pedir los
    20 por defecto desperdiciaba cuatro quintas partes de cada crédito."""
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente(resultados=100).search(SearchQuery(nombre="x", texto="php", pais="es"))

    peticion = ruta.calls.last.request
    assert peticion.url.params["limit"] == "100"
    assert peticion.url.params["hl"] == "es"
    assert peticion.url.params["sort"] == "relevance"


@respx.mock
def test_el_limite_se_acota_a_lo_que_admite_la_api():
    ruta = respx.get(URL_API).mock(return_value=httpx.Response(200, json=FIXTURE))

    fuente(resultados=500).search(SearchQuery(nombre="x", texto="php"))

    assert ruta.calls.last.request.url.params["limit"] == "100"
