from pathlib import Path

import httpx
import pytest
import respx

from app.limitador import sin_espera
from app.sources.adzuna_web import (
    CABECERAS,
    DescripcionNoDisponible,
    descarga_descripcion,
    extrae_descripcion,
    html_a_texto,
    url_ficha,
)

FIXTURES = Path(__file__).parent / "fixtures"
FICHA = (FIXTURES / "adzuna_ficha.html").read_text(encoding="utf-8")
FICHA_SIN_SECCION = (FIXTURES / "adzuna_ficha_sin_seccion.html").read_text(encoding="utf-8")


def test_saca_el_texto_de_la_seccion_adp_body():
    texto = extrae_descripcion(FICHA)

    assert "experiencia en Python" in texto
    assert "oficina de Sevilla en formato Híbrido" in texto


def test_no_se_traga_las_secciones_siguientes():
    """La expresión debe parar en el primer </section>, no en el último.

    Sin el modo no codicioso, la descripción se llevaría el bloque de "Ofertas
    similares" y el clasificador razonaría sobre puestos que no son el de la oferta.
    """
    assert "Ofertas similares" not in extrae_descripcion(FICHA)


def test_conserva_la_estructura_en_saltos_de_linea():
    """Una descripción de 3000 caracteres sin saltos es ilegible en la ficha web."""
    texto = extrae_descripcion(FICHA)

    assert "Requisitos:" in texto
    assert "\n" in texto


def test_colapsa_los_espacios_que_dejan_las_etiquetas_inline():
    """Las etiquetas de bloque las limpia el strip por línea; éstas no.

    Este test existe porque el de arriba llevaba un `assert "  " not in texto` que no
    medía nada: en la ficha de ejemplo, todo espacio sobrante nace al principio o al
    final de una línea y el strip lo barre solo, así que la aserción pasaba igual
    borrando `_ESPACIOS`. El caso que de verdad necesita esa limpieza es el de varias
    etiquetas inline seguidas en mitad de un párrafo, y aquí se ejerce directamente.
    """
    texto = html_a_texto("<p>Requiere <strong>SQL</strong> y <strong>Docker</strong></p>")

    assert texto == "Requiere SQL y Docker"


def test_traduce_las_entidades_html():
    texto = extrae_descripcion(FICHA)

    assert "años" in texto
    assert "SQL & Docker" in texto
    assert "&amp;" not in texto


def test_cae_al_json_ld_cuando_falta_la_seccion():
    """Medido: `adp-body` estaba en 10 de 10 fichas y el JSON-LD faltaba en 1.

    De ahí el orden. Pero cuando la sección falta, el JobPosting salva la oferta, y
    hay que encontrarlo entre varios bloques ld+json, no coger el primero.
    """
    texto = extrae_descripcion(FICHA_SIN_SECCION)

    assert "sólo disponible en el JSON-LD" in texto
    assert "Modalidad híbrida." in texto


def test_sin_seccion_ni_json_ld_falla_de_forma_reintentable():
    """Un HTML irreconocible NO es DescripcionNoDisponible.

    Esa excepción significa "la oferta ya no existe" y agota los intentos de golpe.
    Un cambio de maquetación de Adzuna debe reintentarse, no darse por perdido.
    """
    with pytest.raises(RuntimeError) as fallo:
        extrae_descripcion("<html><body><p>Nada útil</p></body></html>")

    assert not isinstance(fallo.value, DescripcionNoDisponible)


FICHA_URL = "https://www.adzuna.es/details/5812188567"


class LimitadorEspia:
    def __init__(self) -> None:
        self.turnos: list[str] = []

    def espera_turno(self, url: str) -> float:
        self.turnos.append(url)
        return 0.0


def test_quita_el_query_string_de_la_url():
    sucia = "https://www.adzuna.es/details/123?utm_medium=api&utm_source=a1e935a6"

    assert url_ficha(sucia) == "https://www.adzuna.es/details/123"


@respx.mock
def test_descarga_y_devuelve_el_texto_de_la_ficha():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(200, text=FICHA))

    texto = descarga_descripcion(f"{FICHA_URL}?utm_medium=api", limitador=sin_espera())

    assert "oficina de Sevilla en formato Híbrido" in texto


@respx.mock
def test_manda_accept_y_accept_language():
    """Medido: CloudFront devuelve 403 sin estas dos cabeceras, sea cual sea el UA.

    Un user-agent de Chrome sin ellas se lleva el mismo 403 que uno propio, así que no
    hace falta fingir ser un navegador y nos identificamos con nuestro nombre.
    """
    ruta = respx.get(FICHA_URL).mock(return_value=httpx.Response(200, text=FICHA))

    descarga_descripcion(FICHA_URL, limitador=sin_espera())

    enviadas = ruta.calls.last.request.headers
    assert enviadas["accept-language"].startswith("es-ES")
    assert "text/html" in enviadas["accept"]
    assert enviadas["user-agent"] == CABECERAS["User-Agent"]
    assert "isThatMyNewJob" in enviadas["user-agent"]


@respx.mock
def test_un_404_significa_que_la_oferta_ya_no_existe():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(404, text="no such job"))

    with pytest.raises(DescripcionNoDisponible):
        descarga_descripcion(FICHA_URL, limitador=sin_espera())


@respx.mock
def test_un_410_tambien():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(410, text="gone"))

    with pytest.raises(DescripcionNoDisponible):
        descarga_descripcion(FICHA_URL, limitador=sin_espera())


@respx.mock
def test_un_403_es_reintentable_y_no_da_la_oferta_por_perdida():
    """Un bloqueo del WAF es transitorio y afecta a todas las ofertas por igual.

    Tratarlo como DescripcionNoDisponible marcaría el atraso entero como
    definitivamente fallido por culpa de un bloqueo de una tarde.
    """
    respx.get(FICHA_URL).mock(return_value=httpx.Response(403, text="Request blocked"))

    with pytest.raises(RuntimeError) as fallo:
        descarga_descripcion(FICHA_URL, limitador=sin_espera())

    assert not isinstance(fallo.value, DescripcionNoDisponible)
    assert "403" in str(fallo.value)


@respx.mock
def test_pide_turno_al_limitador_con_la_url_ya_limpia():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(200, text=FICHA))
    espia = LimitadorEspia()

    descarga_descripcion(f"{FICHA_URL}?utm_medium=api", limitador=espia)

    assert espia.turnos == [FICHA_URL]


@respx.mock
def test_sigue_las_redirecciones():
    """Sin `follow_redirects`, un 301 acabaría en RuntimeError en vez de en el texto.

    Adzuna redirige por cosas tan tontas como la barra final o http->https, y ninguna de
    ellas significa que la oferta no exista.
    """
    respx.get(FICHA_URL).mock(
        return_value=httpx.Response(301, headers={"Location": f"{FICHA_URL}/"})
    )
    respx.get(f"{FICHA_URL}/").mock(return_value=httpx.Response(200, text=FICHA))

    texto = descarga_descripcion(FICHA_URL, limitador=sin_espera())

    assert "experiencia en Python" in texto


@respx.mock
def test_un_fallo_de_red_sale_como_runtimeerror():
    """El contrato del docstring tiene que ser cierto.

    Las excepciones de transporte de httpx cuelgan de `httpx.HTTPError`, no de
    `RuntimeError`. Si no se tradujeran aquí, quien capturase `RuntimeError` fiándose de
    la documentación se comería el timeout sin enterarse.
    """
    respx.get(FICHA_URL).mock(side_effect=httpx.ConnectTimeout("agotado"))

    with pytest.raises(RuntimeError) as fallo:
        descarga_descripcion(FICHA_URL, limitador=sin_espera())

    assert not isinstance(fallo.value, DescripcionNoDisponible)
