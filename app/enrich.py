"""Completa las descripciones que Adzuna sirve truncadas, leyendo su ficha pública.

Este módulo sabe de base de datos y no sabe de HTTP: el scraper entra como una función
inyectada. La frontera importa porque el scraper se prueba con HTML fijo y este módulo
con una base de datos en memoria, sin que ninguno de los dos necesite al otro.

Corre entre `ingesta()` y el bucle de clasificación, nunca después: el prefiltro decide
por modalidad, y la modalidad sólo es fiable con el texto completo.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Job

FUENTE = "adzuna"

# Mismo tope que `MAX_INTENTOS` de app/pipeline.py, y por el mismo motivo: una oferta
# que falla tres veces deja de gastar peticiones pero sigue consultable.
MAX_INTENTOS_SCRAPE = 3


def pendientes_de_enriquecer(sesion: Session, limite: int) -> list[Job]:
    """Ofertas de Adzuna truncadas que aún tienen intentos, la más reciente primero.

    Dos detalles que no son estilo:

    `intentos_scrape` puede ser NULL en las filas anteriores a la columna, porque
    `asegura_esquema()` la añade sin valor por defecto (ver app/db.py). Un
    `intentos_scrape < MAX` a secas descarta esas filas en silencio, y son justo las 136
    del atraso.

    El orden es descendente para que el atraso no desplace a las ofertas del día, que
    son las que se van a clasificar en este mismo run.
    """
    return list(
        sesion.scalars(
            select(Job)
            .where(
                Job.fuente == FUENTE,
                Job.descripcion_truncada.is_(True),
                or_(
                    Job.intentos_scrape.is_(None),
                    Job.intentos_scrape < MAX_INTENTOS_SCRAPE,
                ),
            )
            .order_by(Job.ingerida_en.desc())
            .limit(limite)
        )
    )
