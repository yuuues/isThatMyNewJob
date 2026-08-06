"""Créditos del pie: el repositorio, el autor y su Liberapay.

Están en `base.html` y no en una vista propia, así que aparecen en las seis
páginas sin que ninguna ruta tenga que colaborar. Lo que se comprueba aquí es que
siguen ahí y que salen con el `rel` entero: es el requisito que más fácil se
pierde en una edición futura, porque quitarlo no rompe nada visible.
"""

import re

import pytest
from fastapi.testclient import TestClient

# Las cinco entradas del <nav>. La sexta vista que extiende base.html, /job/{id},
# lleva parámetro y se comprueba aparte en test_los_creditos_en_el_detalle_de_la_oferta.
VISTAS = ["/", "/profile", "/preferences", "/searches", "/runs"]

# Texto visible y destino de cada enlace. El texto también es contrato: "Hecho por
# Yuuu" es texto plano y el enlace es el dominio, no el nombre.
ENLACES = [
    ("Código en GitHub", "https://github.com/yuuues/isThatMyNewJob"),
    ("yuuu.es", "https://yuuu.es"),
    ("Liberapay", "https://liberapay.com/YuuuES"),
]


def _pie(html: str) -> str:
    # `class="container"` y no `<footer\b` a secas: `oferta.html` tiene su propio
    # <footer> (el del botón «Reclasificar», dentro del <article>) que precede al de
    # base.html en el documento, y una búsqueda no voraz se quedaría con ése.
    encontrado = re.search(r'<footer class="container".*?</footer>', html, flags=re.DOTALL)
    assert encontrado, "el documento no tiene el <footer> de base.html"
    return encontrado.group(0)


@pytest.mark.parametrize("texto,destino", ENLACES)
def test_el_pie_lleva_los_creditos(cliente: TestClient, texto, destino):
    pie = _pie(cliente.get("/").text)

    assert f'href="{destino}"' in pie, f"falta el enlace a {destino}"
    assert texto in pie, f"falta el texto «{texto}»"


@pytest.mark.parametrize("destino", [destino for _, destino in ENLACES])
def test_los_creditos_no_filtran_la_url_de_la_vista(cliente: TestClient, destino):
    """Sin `noreferrer`, el navegador manda `localhost:8100/job/123` como `Referer`.

    Es justo lo que niega la línea de arriba del pie: los datos no salen de esta
    máquina. `noopener` es la higiene habitual de `target="_blank"` y va implícito
    en `noreferrer`, pero se escribe entero para que se lea la intención.
    """
    pie = _pie(cliente.get("/").text)

    etiqueta = re.search(rf'<a\b[^>]*href="{re.escape(destino)}"[^>]*>', pie)
    assert etiqueta, f"no hay <a> hacia {destino}"

    atributos = etiqueta.group(0)
    assert 'target="_blank"' in atributos
    assert "noopener" in atributos
    assert "noreferrer" in atributos


@pytest.mark.parametrize("vista", VISTAS)
def test_los_creditos_salen_en_todas_las_vistas(cliente: TestClient, vista):
    """Van en `base.html`, así que ninguna ruta tiene que acordarse de pasarlos."""
    respuesta = cliente.get(vista)

    assert respuesta.status_code == 200
    assert "https://github.com/yuuues/isThatMyNewJob" in _pie(respuesta.text)


def test_los_creditos_en_el_detalle_de_la_oferta(cliente: TestClient, crea_oferta):
    """`/job/{id}` es la única vista con un identificador en la URL.

    Es precisamente la que motiva el `rel="noreferrer"` de arriba: es la que de
    verdad tendría algo que filtrar por `Referer` si el pie no lo llevara. Va
    aparte de VISTAS porque la ruta necesita una oferta creada, no una cadena fija.
    """
    oferta = crea_oferta()

    respuesta = cliente.get(f"/job/{oferta.id}")

    assert respuesta.status_code == 200
    assert "https://github.com/yuuues/isThatMyNewJob" in _pie(respuesta.text)


def test_el_parcial_de_htmx_no_arrastra_el_pie(cliente: TestClient):
    """Los reemplazos de HTMX sólo tocan `#contenido`.

    Si el parcial trajese el pie, cada filtro del listado dejaría un juego de
    créditos más dentro de la página. Mismo control que ya hay para <nav>.
    """
    respuesta = cliente.get("/", headers={"HX-Request": "true"})

    assert respuesta.status_code == 200
    assert "<footer" not in respuesta.text
    assert "liberapay" not in respuesta.text.lower()
