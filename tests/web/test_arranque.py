"""Esqueleto de la aplicación web: arranque, estáticos y contrato entre módulos.

Buena parte de estos tests no comprueban una vista, sino el CONTRATO del que
dependen los demás módulos de rutas: que los tres routers existen, que `main.py`
ya los incluye y que la sesión se puede sustituir por una en memoria. Si alguno se
rompe, se rompe todo lo construido encima.
"""

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job
from app.web import deps, routes_config, routes_ofertas, routes_runs
from app.web.deps import get_sesion
from app.web.main import crear_app

BASE_HTML = Path(deps.DIR_PLANTILLAS) / "base.html"
PICO_CSS = Path(deps.DIR_ESTATICOS) / "pico.min.css"
ESTILO_CSS = Path(deps.DIR_ESTATICOS) / "estilo.css"

# Rutas canónicas de las cinco vistas, según la tabla del spec. La navegación de
# `base.html` las enlaza y los demás módulos las declaran.
VISTAS = ["/", "/profile", "/preferences", "/searches", "/runs"]


@contextmanager
def sonda(router: APIRouter, ruta: str, funcion):
    """Registra una ruta temporal en un router y la retira al salir.

    Es la única forma honesta de comprobar que `main.py` incluye de verdad los
    routers vacíos: un router sin rutas no aporta nada observable a la aplicación.
    """
    router.get(ruta)(funcion)
    try:
        yield
    finally:
        router.routes = [r for r in router.routes if getattr(r, "path", None) != ruta]


def test_la_portada_responde_html(cliente: TestClient):
    respuesta = cliente.get("/")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    assert "<html" in respuesta.text


def test_una_ruta_inexistente_responde_404(cliente: TestClient):
    assert cliente.get("/esto-no-existe").status_code == 404


def test_htmx_se_sirve_desde_static_y_no_esta_vacio(cliente: TestClient):
    respuesta = cliente.get("/static/htmx.min.js")

    assert respuesta.status_code == 200
    # No se comprueba el contenido del fichero, que es de terceros; sólo que hay
    # algo servible y no un fichero vacío o un marcador de "pendiente de bajar".
    assert len(respuesta.content) > 10_000


def test_la_hoja_de_estilo_se_sirve_desde_static(cliente: TestClient):
    respuesta = cliente.get("/static/estilo.css")

    assert respuesta.status_code == 200
    assert respuesta.content


def test_pico_se_sirve_desde_static(cliente: TestClient):
    """Pico es el framework de estilos y, como HTMX, se sirve en local."""
    respuesta = cliente.get("/static/pico.min.css")

    assert respuesta.status_code == 200
    assert len(respuesta.content) > 50_000


def test_pico_no_carga_nada_de_fuera(cliente: TestClient):
    """Pico no puede arrastrar peticiones a la red por la puerta de atrás.

    Una hoja de estilo puede pedir recursos igual que el HTML: `@import` o
    `url(https://…)`. Los iconos de Pico van embebidos como `data:`, y eso es lo
    único aceptable aquí.
    """
    respuesta = cliente.get("/static/pico.min.css")

    # Sin esta comprobación el test pasaría contra un 404, que tampoco tiene URLs.
    assert respuesta.status_code == 200
    css = respuesta.text

    assert "@import" not in css
    externos = [u for u in re.findall(r"url\(\s*['\"]?([^'\")]+)", css) if not u.startswith("data:")]
    assert externos == []


def test_la_plantilla_base_enlaza_pico_antes_que_estilo():
    """El orden importa: `estilo.css` ajusta a Pico, así que va después.

    Al revés, las variables de densidad las pisaría el propio Pico y la lista de
    ofertas volvería a ocupar el triple.
    """
    contenido = BASE_HTML.read_text(encoding="utf-8")

    posicion_pico = contenido.find("/static/pico.min.css")
    posicion_estilo = contenido.find("/static/estilo.css")

    assert posicion_pico != -1, "base.html no enlaza pico.min.css"
    assert posicion_estilo != -1, "base.html no enlaza estilo.css"
    assert posicion_pico < posicion_estilo


def test_la_plantilla_base_no_carga_nada_de_fuera():
    """Ni CDN ni fuentes externas: la herramienta tiene que funcionar sin red.

    Se miran los ORÍGENES de las etiquetas que cargan recursos, no el texto del
    fichero. Buscar `https://` a pelo era lo que hacía antes este test, y desde que
    el pie enlaza a GitHub, a la página del autor y a Liberapay eso daría un falso
    positivo: un <a> no carga nada, lo sigue el usuario si quiere. Un <script src>
    o un <link href> contra un CDN sí, y son los que dejarían la herramienta
    dependiendo de la red.
    """
    contenido = BASE_HTML.read_text(encoding="utf-8")

    for etiqueta in re.findall(r"<(?:script|link|img|iframe)\b[^>]*>", contenido):
        for url in re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', etiqueta):
            assert not url.startswith(("http://", "https://", "//")), (
                f"{etiqueta.strip()} carga algo de fuera"
            )


def test_la_pagina_carga_pico_desde_local(cliente: TestClient):
    assert "/static/pico.min.css" in cliente.get("/").text


def _valor_de_variable(css: str, variable: str) -> str:
    """Primera declaración de una variable CSS, que es la del `:root`."""
    coincidencias = re.findall(rf"{re.escape(variable)}\s*:\s*([^;}}]+)", css)
    assert coincidencias, f"{variable} no está declarada"
    return coincidencias[0].strip()


def _numero(valor: str) -> float:
    return float(valor.replace("rem", "").strip())


@pytest.mark.parametrize(
    "variable",
    ["--pico-spacing", "--pico-line-height", "--pico-typography-spacing-vertical"],
)
def test_estilo_aprieta_la_densidad_de_pico(variable):
    """Pico viene muy aireado y aquí se listan más de cien ofertas.

    No se comprueba el aspecto, sino que los valores propios son MENORES que los de
    Pico: es lo que hace que quepan del orden de quince filas en un portátil.
    """
    propio = _numero(_valor_de_variable(ESTILO_CSS.read_text(encoding="utf-8"), variable))
    de_pico = _numero(_valor_de_variable(PICO_CSS.read_text(encoding="utf-8"), variable))

    assert propio < de_pico


def test_estilo_no_pelea_con_important():
    """La densidad se ajusta con las variables de Pico, no a martillazos.

    Un `!important` aquí rompería cualquier ajuste puntual que necesite una vista
    concreta más adelante.
    """
    # Se miran las declaraciones, no los comentarios: explicar por qué NO se usa
    # !important es justamente lo que se quiere que ponga el fichero.
    sin_comentarios = re.sub(
        r"/\*.*?\*/", "", ESTILO_CSS.read_text(encoding="utf-8"), flags=re.DOTALL
    )

    assert "!important" not in sin_comentarios


def test_la_pagina_carga_htmx_desde_local(cliente: TestClient):
    assert "/static/htmx.min.js" in cliente.get("/").text


def test_la_pagina_no_carga_nada_de_fuera(cliente: TestClient):
    """Ningún script, hoja de estilo o imagen puede venir de un servidor externo.

    Se miran las etiquetas que cargan recursos, no el HTML entero: los enlaces a las
    ofertas originales (`<a href="https://...">`) sí son externos y deben serlo.
    """
    recursos = re.findall(
        r'<(?:script|link|img)\b[^>]*?\b(?:src|href)="([^"]+)"', cliente.get("/").text
    )

    assert recursos, "la página no carga ningún recurso: el test no está comprobando nada"
    externos = [r for r in recursos if r.startswith(("http://", "https://", "//"))]
    assert externos == []


def test_la_plantilla_base_no_contiene_ninguna_url_externa():
    contenido = BASE_HTML.read_text(encoding="utf-8")

    assert "http://" not in contenido
    assert "https://" not in contenido


def test_la_plantilla_base_enlaza_las_cinco_vistas():
    contenido = BASE_HTML.read_text(encoding="utf-8")

    for vista in VISTAS:
        assert f'href="{vista}"' in contenido, f"falta el enlace a {vista} en la navegación"


def test_la_plantilla_base_deja_un_hueco_de_contenido():
    assert "{% block contenido %}" in BASE_HTML.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "modulo",
    [routes_ofertas, routes_config, routes_runs],
    ids=["ofertas", "config", "runs"],
)
def test_cada_modulo_de_rutas_expone_un_router(modulo):
    assert isinstance(modulo.router, APIRouter)


@pytest.mark.parametrize(
    "modulo",
    [routes_ofertas, routes_config, routes_runs],
    ids=["ofertas", "config", "runs"],
)
def test_main_ya_incluye_los_tres_routers(modulo):
    """Nadie debería tener que editar `main.py` para añadir sus rutas."""
    ruta = "/_sonda"

    with sonda(modulo.router, ruta, lambda: {"ok": True}):
        with TestClient(crear_app()) as cliente:
            respuesta = cliente.get(ruta)

    assert respuesta.status_code == 200


def test_la_sesion_de_las_rutas_se_puede_sustituir(cliente: TestClient, crea_oferta):
    """La dependencia de sesión es sustituible y apunta a la base en memoria."""
    crea_oferta()
    crea_oferta()

    def _cuenta(sesion: Session = Depends(get_sesion)):
        return {"ofertas": sesion.scalar(select(func.count()).select_from(Job))}

    with sonda(routes_ofertas.router, "/_sonda_ofertas", _cuenta):
        # El cliente se construyó antes de registrar la sonda, así que se pide otro
        # con la misma sustitución de sesión.
        aplicacion = crear_app()
        aplicacion.dependency_overrides = dict(cliente.app.dependency_overrides)
        with TestClient(aplicacion) as otro:
            respuesta = otro.get("/_sonda_ofertas")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ofertas": 2}


def test_las_plantillas_apuntan_al_directorio_de_la_web():
    plantillas = deps.get_plantillas()

    assert BASE_HTML.exists()
    assert plantillas.get_template("base.html") is not None
