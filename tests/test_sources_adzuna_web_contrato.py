"""Comprobación contra la web real de Adzuna. Excluida de la suite por defecto.

El resto de tests del scraper usan un HTML fijo, así que seguirían verdes el día que
Adzuna cambie la maquetación o el WAF. Este es el único que se enteraría, y por eso
existe pese a salir a la red.

Ejecutar a mano cuando el enriquecimiento empiece a fallar en los runs:

    ADZUNA_FICHA_URL="https://www.adzuna.es/details/<id>" \\
        python -m pytest tests/test_sources_adzuna_web_contrato.py -m contrato -v

La URL sale de cualquier `redirect_url` de la API, o de la columna `url` de una oferta
de Adzuna en la base de datos. Tiene que ser una oferta VIVA: una retirada devuelve 404
y el scraper lanzaría `DescripcionNoDisponible`, que es su comportamiento correcto.

Qué mirar según cómo falle:

- 403 -> el WAF. Revisar `CABECERAS` en app/sources/adzuna_web.py. Medido el 2026-08-06:
  lo que pide son `Accept` y `Accept-Language`, no un user-agent concreto.
- RuntimeError "ni adp-body ni JobPosting" -> cambió la maquetación. Revisar
  `_SECCION_CUERPO`.
- Pasa pero con menos de 500 caracteres -> la extracción coge un trozo equivocado de la
  página. Revisar que `_SECCION_CUERPO` no esté cortando en una `<section>` anidada.
"""

import os

import pytest

from app.sources.adzuna_web import descarga_descripcion

URL_FICHA = os.environ.get("ADZUNA_FICHA_URL", "")


@pytest.mark.contrato
@pytest.mark.skipif(not URL_FICHA, reason="define ADZUNA_FICHA_URL con una oferta viva")
def test_la_ficha_real_sigue_dando_mas_texto_que_la_api():
    texto = descarga_descripcion(URL_FICHA)

    # Medido sobre 10 ofertas reales: de 1078 a 3673 caracteres, mediana ~2100. La API
    # corta a 500, así que cualquier cosa por debajo de eso significa que ya no estamos
    # sacando el cuerpo de la oferta y el paso habría dejado de aportar nada.
    assert len(texto) > 500
