"""Listado de ofertas: agrupado, orden, filtros y avisos de la fila.

Es la vista principal del producto: sobre datos reales conviven ~26 `aplicar_ya`,
~55 `revisar` y ~113 `descartar`, así que lo que se comprueba aquí no es estética
sino que la lista siga siendo utilizable con cien ofertas encima.

Ningún test toca `data/app.db` ni la red: la base de datos es la de memoria que
monta `tests/web/conftest.py` y no interviene ningún proveedor.
"""

import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.decisiones import (
    ESTADO_APLICADA,
    ESTADO_DESCARTADA_POR_MI,
    ESTADO_GUARDADA,
    registra_decision,
)
from app.models import Clasificacion

EJES = {
    "tecnico": "Python coincide",
    "seniority": "Senior, encaja",
    "modalidad": "Remoto",
    "salario": "no publicado",
    "sector": "software",
}


@pytest.fixture
def crea_clasificada(sesion, crea_oferta):
    """Oferta ya clasificada, que es la única que sale en el listado.

    Vive aquí y no en el conftest porque el resto de vistas no la necesitan: el
    conftest es contrato entre agentes y no debe especializarse para el listado.
    """

    def _crea(
        *,
        categoria: str = "revisar",
        confianza: str = "alta",
        razonamiento: str = "Encaja con lo que buscas.",
        skills_faltantes: list[str] | None = None,
        red_flags: list[str] | None = None,
        modelo: str = "fake",
        prompt_version: int = 2,
        **campos,
    ):
        oferta = crea_oferta(estado_clasificacion="clasificada", **campos)
        sesion.add(
            Clasificacion(
                job_id=oferta.id,
                categoria=categoria,
                confianza=confianza,
                razonamiento=razonamiento,
                ejes=EJES,
                skills_faltantes=skills_faltantes or [],
                red_flags=red_flags or [],
                modelo=modelo,
                prompt_version=prompt_version,
            )
        )
        sesion.commit()
        return oferta

    return _crea


def posicion(html: str, texto: str) -> int:
    indice = html.find(texto)
    assert indice != -1, f"no aparece {texto!r} en la respuesta"
    return indice


def etiqueta_details(html: str, identificador: str) -> str:
    """Etiqueta `<details>` de un grupo, para poder mirar si trae `open`."""
    encontrada = re.search(rf'<details[^>]*id="{identificador}"[^>]*>', html)
    assert encontrada, f"no hay un <details> con id {identificador!r}"
    return encontrada.group(0)


def test_los_grupos_van_en_orden_aplicar_revisar_descartar(cliente: TestClient, crea_clasificada):
    crea_clasificada(categoria="descartar", titulo="Puesto descartable")
    crea_clasificada(categoria="revisar", titulo="Puesto dudoso")
    crea_clasificada(categoria="aplicar_ya", titulo="Puesto excelente")

    html = cliente.get("/").text

    assert (
        posicion(html, "Puesto excelente")
        < posicion(html, "Puesto dudoso")
        < posicion(html, "Puesto descartable")
    )


def test_descartar_viene_plegado_y_los_demas_abiertos(cliente: TestClient, crea_clasificada):
    """El usuario no ha entrado a leer descartes: 113 filas no pueden abrirse solas."""
    crea_clasificada(categoria="aplicar_ya", titulo="Puesto excelente")
    crea_clasificada(categoria="revisar", titulo="Puesto dudoso")
    crea_clasificada(categoria="descartar", titulo="Puesto descartable")

    html = cliente.get("/").text

    assert "open" in etiqueta_details(html, "grupo-aplicar_ya")
    assert "open" in etiqueta_details(html, "grupo-revisar")
    assert "open" not in etiqueta_details(html, "grupo-descartar")


def test_dentro_de_un_grupo_la_confianza_alta_va_primero(cliente: TestClient, crea_clasificada):
    crea_clasificada(categoria="revisar", confianza="baja", titulo="Confianza floja")
    crea_clasificada(categoria="revisar", confianza="media", titulo="Confianza intermedia")
    crea_clasificada(categoria="revisar", confianza="alta", titulo="Confianza firme")

    html = cliente.get("/").text

    assert (
        posicion(html, "Confianza firme")
        < posicion(html, "Confianza intermedia")
        < posicion(html, "Confianza floja")
    )


def test_a_igual_confianza_manda_la_fecha_mas_reciente(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Publicada en enero", publicada_en=datetime(2026, 1, 15))
    crea_clasificada(titulo="Publicada en julio", publicada_en=datetime(2026, 7, 15))

    html = cliente.get("/").text

    assert posicion(html, "Publicada en julio") < posicion(html, "Publicada en enero")


def test_una_oferta_sin_fecha_no_adelanta_a_una_reciente(cliente: TestClient, crea_clasificada):
    """Sin fecha va al final, no al principio: `None` no es 'recién publicada'."""
    crea_clasificada(titulo="Sin fecha conocida", publicada_en=None)
    crea_clasificada(titulo="Publicada en julio", publicada_en=datetime(2026, 7, 15))

    html = cliente.get("/").text

    assert posicion(html, "Publicada en julio") < posicion(html, "Sin fecha conocida")


def test_una_oferta_truncada_lleva_marca_y_una_completa_no(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Oferta cortada", descripcion_truncada=True)

    html_truncada = cliente.get("/").text

    assert "fragmento" in html_truncada
    # La explicación de qué significa la marca tiene que estar, no sólo la marca.
    assert "sólo publicó" in html_truncada


def test_una_oferta_completa_no_lleva_la_marca(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Oferta entera", descripcion_truncada=False)

    html = cliente.get("/").text

    # La oferta tiene que estar: si no, el test pasaría contra una lista vacía.
    assert "Oferta entera" in html
    assert "fragmento" not in html


def test_la_fila_enlaza_al_original_en_pestana_nueva(cliente: TestClient, crea_clasificada):
    oferta = crea_clasificada(url="https://ofertas.ejemplo/original")

    html = cliente.get("/").text

    assert 'href="https://ofertas.ejemplo/original"' in html
    assert 'target="_blank"' in html
    # Sin `rel` la pestaña nueva puede manipular la original.
    assert "noopener" in html
    assert f'href="/job/{oferta.id}"' in html


def test_la_fila_muestra_empresa_ubicacion_modalidad_fuente_y_razonamiento(
    cliente: TestClient, crea_clasificada
):
    crea_clasificada(
        empresa="Acme S.L.",
        ubicacion="Valencia",
        modalidad="remoto",
        fuente="adzuna",
        razonamiento="Python y remoto, justo lo tuyo.",
    )

    html = cliente.get("/").text

    for dato in ("Acme S.L.", "Valencia", "remoto", "adzuna", "Python y remoto, justo lo tuyo."):
        assert dato in html


def test_el_filtro_por_fuente_devuelve_solo_esa_fuente(cliente: TestClient, crea_clasificada):
    crea_clasificada(fuente="adzuna", titulo="Viene de Adzuna")
    crea_clasificada(fuente="remotive", titulo="Viene de Remotive")

    html = cliente.get("/", params={"fuente": "adzuna"}).text

    assert "Viene de Adzuna" in html
    assert "Viene de Remotive" not in html


def test_el_filtro_por_categoria_devuelve_solo_esa_categoria(
    cliente: TestClient, crea_clasificada
):
    crea_clasificada(categoria="aplicar_ya", titulo="Puesto excelente")
    crea_clasificada(categoria="descartar", titulo="Puesto descartable")

    html = cliente.get("/", params={"categoria": "aplicar_ya"}).text

    assert "Puesto excelente" in html
    assert "Puesto descartable" not in html


def test_la_busqueda_de_texto_mira_titulo_y_empresa(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Backend Python", empresa="Uno")
    crea_clasificada(titulo="Frontend React", empresa="Backend Studio")
    crea_clasificada(titulo="Diseñador", empresa="Otra")

    html = cliente.get("/", params={"q": "backend"}).text

    assert "Backend Python" in html
    assert "Backend Studio" in html
    assert "Diseñador" not in html


def test_una_oferta_decidida_no_sale_por_defecto(
    cliente: TestClient, crea_clasificada, sesion
):
    decidida = crea_clasificada(titulo="Ya decidida")
    crea_clasificada(titulo="Todavía sin decidir")
    registra_decision(sesion, decidida.id, ESTADO_GUARDADA, "Me la guardo.")

    html = cliente.get("/").text

    assert "Todavía sin decidir" in html
    assert "Ya decidida" not in html


def test_el_filtro_de_estado_permite_recuperar_las_decididas(
    cliente: TestClient, crea_clasificada, sesion
):
    decidida = crea_clasificada(titulo="Ya decidida")
    registra_decision(sesion, decidida.id, ESTADO_GUARDADA, "Me la guardo.")

    assert "Ya decidida" in cliente.get("/", params={"estado": "todas"}).text


def test_el_filtro_de_estado_deja_ver_solo_las_aplicadas(
    cliente: TestClient, crea_clasificada, sesion
):
    """Es lo que convierte la lista en un seguimiento de candidaturas."""
    aplicada = crea_clasificada(titulo="A esta me presenté")
    descartada = crea_clasificada(titulo="Esta no me interesaba")
    registra_decision(sesion, aplicada.id, ESTADO_APLICADA, "Encaja.")
    registra_decision(sesion, descartada.id, ESTADO_DESCARTADA_POR_MI, "Presencial.")

    html = cliente.get("/", params={"estado": ESTADO_APLICADA}).text

    assert "A esta me presenté" in html
    assert "Esta no me interesaba" not in html


def test_una_oferta_sin_clasificar_no_aparece(cliente: TestClient, crea_oferta, crea_clasificada):
    crea_oferta(titulo="Aún en la cola", estado_clasificacion="pendiente")
    crea_clasificada(titulo="Ya juzgada")

    html = cliente.get("/").text

    assert "Ya juzgada" in html
    assert "Aún en la cola" not in html


def test_la_fila_avisa_del_historial_con_esa_empresa(
    cliente: TestClient, crea_clasificada, sesion
):
    """'Acme S.L.' y 'ACME SL' son la misma empresa, y la oferta se muestra igual.

    Ocultarla sería como se pierden oportunidades sin enterarse: un rechazo de hace
    seis meses no es para siempre.
    """
    anterior = crea_clasificada(titulo="La de mayo", empresa="Acme S.L.")
    crea_clasificada(titulo="La nueva", empresa="ACME SL")
    registra_decision(sesion, anterior.id, ESTADO_APLICADA, "Me presenté.")

    html = cliente.get("/").text

    assert "La nueva" in html
    assert "aplicaste" in html


def test_una_empresa_sin_historial_no_inventa_aviso(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="La nueva", empresa="Empresa Desconocida")

    html = cliente.get("/").text

    assert "La nueva" in html
    assert "aplicaste" not in html


def test_se_ven_los_contadores_de_candidaturas(cliente: TestClient, crea_clasificada, sesion):
    aplicada = crea_clasificada(titulo="Presentada")
    en_curso = crea_clasificada(titulo="Hablando con ellos")
    registra_decision(sesion, aplicada.id, ESTADO_APLICADA, "Me presenté.")
    registra_decision(sesion, en_curso.id, "en_proceso", "Primera llamada hecha.")

    html = cliente.get("/").text

    assert re.search(r"<strong>1</strong>\s*aplicadas este mes", html)
    assert re.search(r"<strong>1</strong>\s*en proceso", html)


def test_la_peticion_htmx_devuelve_el_parcial_y_no_la_pagina(
    cliente: TestClient, crea_clasificada
):
    crea_clasificada(titulo="Puesto excelente")

    respuesta = cliente.get("/", headers={"HX-Request": "true"})

    assert respuesta.status_code == 200
    assert "Puesto excelente" in respuesta.text
    assert "<nav" not in respuesta.text
    assert "<html" not in respuesta.text


def test_el_parcial_conserva_el_ancla_del_reemplazo(cliente: TestClient, crea_clasificada):
    """Si el parcial no vuelve con el mismo `id`, el segundo filtro ya no acierta."""
    crea_clasificada()

    assert 'id="lista"' in cliente.get("/", headers={"HX-Request": "true"}).text


def test_la_lista_vacia_lo_dice_en_vez_de_quedarse_en_blanco(cliente: TestClient):
    html = cliente.get("/").text

    assert "No hay ofertas" in html
