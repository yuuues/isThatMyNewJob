from pathlib import Path

import pytest

from app.sources.adzuna_web import DescripcionNoDisponible, extrae_descripcion

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
    assert "  " not in texto  # nada de dobles espacios sueltos


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
