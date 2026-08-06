import json
from pathlib import Path

import httpx
import pytest
import respx

from app.limitador import sin_espera
from app.schemas import SearchQuery
from app.sources.adzuna import AdzunaSource, detecta_modalidad, url_api

# El `encoding` es obligatorio, no cosmético: `read_text()` a secas usa la codificación
# del locale, que en Windows es cp1252. Los fixtures llevan "…" y acentos, y al leerlos
# como cp1252 el carácter se parte en tres, de modo que `_esta_truncada()` deja de
# reconocer el corte de Adzuna y el test de descripciones truncadas falla. Sólo pasaba
# en máquinas con PYTHONUTF8=1, así que el fallo parecía intermitente y ajeno.
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "adzuna_sample.json").read_text(encoding="utf-8")
)


class LimitadorEspia:
    def __init__(self) -> None:
        self.turnos: list[str] = []

    def espera_turno(self, url: str) -> float:
        self.turnos.append(url)
        return 0.0


def fuente(**kwargs) -> AdzunaSource:
    kwargs.setdefault("limitador", sin_espera())
    return AdzunaSource(app_id="id", app_key="key", **kwargs)


@respx.mock
def test_normaliza_una_oferta_al_esquema_comun():
    respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))

    primera = ofertas[0]
    assert primera.fuente == "adzuna"
    assert primera.external_id == "5742309584"
    assert primera.empresa == "Levata"
    assert primera.ubicacion == "Madrid"
    assert primera.salario_min == 36000
    assert primera.salario_max == 45000
    assert primera.url == "https://www.adzuna.es/details/5742309584"


@respx.mock
def test_un_salario_estimado_por_adzuna_no_cuenta_como_publicado():
    """`salary_is_predicted` = "1" significa que la cifra la estima Adzuna, no la oferta.

    Tratarla como publicada haría que el prefiltro descartase ofertas por un sueldo
    que nadie llegó a ofrecer, y que el prompt presentase una estimación como dato.
    """
    respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))

    estimada = next(o for o in ofertas if o.external_id == "5799001122")
    assert estimada.salario_min is None
    assert estimada.salario_max is None


@respx.mock
def test_marca_las_descripciones_cortadas_por_adzuna():
    """Adzuna corta a 500 caracteres. El clasificador debe saber que no lo ve todo."""
    respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))

    ofertas = fuente().search(SearchQuery(nombre="php", texto="php"))

    truncada = next(o for o in ofertas if o.external_id == "5787686624")
    completa = next(o for o in ofertas if o.external_id == "5799001122")
    assert truncada.descripcion_truncada is True
    assert completa.descripcion_truncada is False


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


@respx.mock
def test_un_429_con_cuerpo_json_lanza_excepcion():
    respx.get(url_api("es")).mock(
        return_value=httpx.Response(429, json={"exception": "AUTH_FAIL", "display": "limit"})
    )

    with pytest.raises(RuntimeError, match="429"):
        fuente().search(SearchQuery(nombre="php", texto="php"))


@respx.mock
def test_un_401_con_cuerpo_json_lanza_excepcion():
    respx.get(url_api("es")).mock(
        return_value=httpx.Response(401, json={"exception": "AUTH_FAIL"})
    )

    with pytest.raises(RuntimeError, match="401"):
        fuente().search(SearchQuery(nombre="php", texto="php"))


@respx.mock
def test_pide_turno_al_limitador_antes_de_cada_peticion():
    respx.get(url_api("es")).mock(return_value=httpx.Response(200, json=FIXTURE))
    espia = LimitadorEspia()

    fuente(limitador=espia).search(SearchQuery(nombre="php", texto="php"))

    assert espia.turnos == [url_api("es")]


def test_filtra_en_servidor():
    assert fuente().filtra_en_servidor is True
