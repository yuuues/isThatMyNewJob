"""Selector de tema: automático, claro y oscuro.

Pico ya trae las dos paletas y, sin `data-theme`, sigue la preferencia del sistema
operativo. Lo que aquí se comprueba es lo que añade la aplicación encima: que la
navegación ofrece las tres opciones, que el tema se aplica ANTES del primer
pintado y que el servidor no decide nada al respecto.

Lo que estos tests NO cubren: el comportamiento del JavaScript. El proyecto no
tiene runner de JS y no se añade uno por un selector de tres opciones, así que
`localStorage`, el evento `change` y la ausencia de parpadeo se verifican en
navegador. Lo que sí queda blindado aquí es el CONTRATO del que depende ese
JavaScript: los identificadores, los tres valores y la posición del script.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web import deps

BASE_HTML = Path(deps.DIR_PLANTILLAS) / "base.html"
ESTILO_CSS = Path(deps.DIR_ESTATICOS) / "estilo.css"

VISTAS = ["/", "/profile", "/preferences", "/searches", "/runs"]

# Los tres valores son contrato entre la plantilla, el script y `localStorage`.
# `auto` es el de serie y significa "no escribas `data-theme` y deja mandar al SO".
VALORES = ["auto", "light", "dark"]


def _cabeza(html: str) -> str:
    fin = html.find("</head>")
    assert fin != -1, "el documento no tiene <head>"
    return html[:fin]


def test_la_navegacion_ofrece_el_selector_de_tema(cliente: TestClient):
    html = cliente.get("/").text

    # Sin <label> visible —la navegación es de una línea—, así que el nombre
    # accesible tiene que venir del atributo.
    assert 'aria-label="Tema"' in html
    assert 'id="tema"' in html


@pytest.mark.parametrize("valor", VALORES)
def test_el_selector_ofrece_los_tres_temas(cliente: TestClient, valor):
    assert f'value="{valor}"' in cliente.get("/").text


def test_el_tema_se_aplica_antes_de_pintar(cliente: TestClient):
    """El script que restaura el tema va en el <head> y SIN `defer`.

    Es la única forma de que no se vea el parpadeo: con `defer` el navegador
    pintaría el documento con el tema del sistema y lo cambiaría después.
    """
    cabeza = _cabeza(cliente.get("/").text)

    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", cabeza, flags=re.DOTALL)
    restauradores = [attrs for attrs, cuerpo in scripts if "localStorage" in cuerpo]

    assert restauradores, "ningún script del <head> restaura el tema guardado"
    for attrs in restauradores:
        assert "defer" not in attrs, "el script del tema no puede llevar defer"
        assert "async" not in attrs, "el script del tema no puede llevar async"


@pytest.mark.parametrize("vista", VISTAS)
def test_el_servidor_no_fija_el_tema(cliente: TestClient, vista):
    """El tema es cosa del navegador, no de la respuesta.

    Si el servidor emitiese `data-theme`, pisaría la elección del usuario y
    obligaría a persistirla en la base de datos, que es justo lo que se descartó:
    es una preferencia del dispositivo, no del candidato.
    """
    respuesta = cliente.get(vista)

    assert respuesta.status_code == 200
    # Se mira la etiqueta de apertura, que es donde iría el atributo, y no el
    # documento entero: el script del tema lo escribe desde JavaScript y nombrarlo
    # en un comentario no puede hacer fallar el test.
    etiqueta = re.search(r"<html\b[^>]*>", respuesta.text)
    assert etiqueta, "el documento no abre con <html>"
    assert "data-theme" not in etiqueta.group(0)


def test_el_selector_no_parte_la_navegacion_en_dos():
    """Pico estiliza los <select> para formularios: ancho completo y margen abajo.

    Dentro de <nav>, esos dos valores de serie tirarían el resto de la barra a la
    línea siguiente. La regla los cancela; sin ella el selector se ve, pero la
    navegación deja de ser de una línea.
    """
    css = re.sub(r"/\*.*?\*/", "", ESTILO_CSS.read_text(encoding="utf-8"), flags=re.DOTALL)

    regla = re.search(r"nav\s+select\s*\{([^}]*)\}", css)

    assert regla, "estilo.css no ajusta el <select> de la navegación"
    cuerpo = regla.group(1)
    assert re.search(r"width\s*:\s*auto", cuerpo)
    assert re.search(r"margin-bottom\s*:\s*0", cuerpo)


def test_el_selector_de_tema_no_carga_nada_de_fuera():
    """Los dos scripts son inline: ni CDN ni fichero nuevo en /static."""
    contenido = BASE_HTML.read_text(encoding="utf-8")

    assert "http://" not in contenido
    assert "https://" not in contenido
