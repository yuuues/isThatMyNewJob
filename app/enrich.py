"""Completa las descripciones que Adzuna sirve truncadas, leyendo su ficha pública.

Este módulo sabe de base de datos y no sabe de HTTP: el scraper entra como una función
inyectada. La frontera importa porque el scraper se prueba con HTML fijo y este módulo
con una base de datos en memoria, sin que ninguno de los dos necesite al otro.

Corre entre `ingesta()` y el bucle de clasificación, nunca después: el prefiltro decide
por modalidad, y la modalidad sólo es fiable con el texto completo.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import Clasificacion, Decision, Job
from app.sources.adzuna_web import DescripcionNoDisponible
from app.sources.comun import detecta_modalidad

FUENTE = "adzuna"

# Mismo tope que `MAX_INTENTOS` de app/pipeline.py, y por el mismo motivo: una oferta
# que falla tres veces deja de gastar peticiones pero sigue consultable.
MAX_INTENTOS_SCRAPE = 3

# Circuit breaker. El día que Adzuna cambie el WAF y devuelva 403 a todo, sin este corte
# un run quemaría el cupo entero y en tres runs el atraso completo quedaría marcado como
# definitivamente fallido por culpa de un bloqueo temporal.
MAX_FALLOS_SEGUIDOS = 5
MOTIVO_RACHA = "racha_de_fallos"


@dataclass
class ResumenEnriquecimiento:
    """Lo que hizo el paso. `fallos` no va a `run.stats`: va a `run.errores`."""

    intentadas: int = 0
    completadas: int = 0
    fallidas: int = 0
    agotadas: int = 0
    reevaluadas: int = 0
    cortado_por: str | None = None
    fallos: list[tuple[int, str]] = field(default_factory=list)

    def a_stats(self) -> dict:
        return {
            "intentadas": self.intentadas,
            "completadas": self.completadas,
            "fallidas": self.fallidas,
            "agotadas": self.agotadas,
            "reevaluadas": self.reevaluadas,
            "cortado_por": self.cortado_por,
        }


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


def _reevalua(sesion: Session, job: Job) -> bool:
    """Devuelve la oferta a la cola para que se juzgue con el texto completo.

    El veredicto viejo se borra en vez de archivarse: `Clasificacion` tiene `job_id`
    único, y guardar historial pediría un cambio de modelo de datos para conservar una
    opinión emitida sobre datos malos.

    Las ofertas que el usuario ya decidió a mano se respetan. Comprobado antes de
    escribir esto: borrar clasificaciones no rompe el few-shot, porque
    `ejemplos_few_shot()` lee `Decision` y `Job` y nunca `Clasificacion`.
    """
    if sesion.scalar(select(Decision.id).where(Decision.job_id == job.id)) is not None:
        return False

    sesion.execute(delete(Clasificacion).where(Clasificacion.job_id == job.id))
    job.estado_clasificacion = "pendiente"
    job.motivo_regla = None
    return True


def enriquece_descripciones(
    sesion: Session,
    *,
    scraper: Callable[[str], str],
    max_por_run: int = 40,
) -> ResumenEnriquecimiento:
    """Completa las descripciones truncadas de Adzuna y devuelve esas ofertas a la cola.

    El commit es por oferta, como en el bucle de clasificación: un fallo a mitad no se
    lleva por delante el trabajo ya hecho.
    """
    resumen = ResumenEnriquecimiento()
    seguidos = 0

    for job in pendientes_de_enriquecer(sesion, max_por_run):
        resumen.intentadas += 1

        try:
            texto = scraper(job.url)
        except DescripcionNoDisponible as e:
            # La oferta se retiró. Es un fallo de ESTA oferta, no del sitio: el servidor
            # contestó. Por eso agota sus intentos pero reinicia la racha, o drenar el
            # atraso con cinco ofertas retiradas seguidas cortaría el paso sin motivo.
            job.intentos_scrape = MAX_INTENTOS_SCRAPE
            resumen.agotadas += 1
            resumen.fallos.append((job.id, f"{type(e).__name__}: {e}"))
            seguidos = 0
            sesion.commit()
            continue
        except Exception as e:  # noqa: BLE001 - la oferta se reintenta en el run siguiente
            job.intentos_scrape = (job.intentos_scrape or 0) + 1
            resumen.fallidas += 1
            resumen.fallos.append((job.id, f"{type(e).__name__}: {e}"))
            seguidos += 1
            sesion.commit()
            if seguidos >= MAX_FALLOS_SEGUIDOS:
                resumen.cortado_por = MOTIVO_RACHA
                break
            continue

        seguidos = 0
        job.descripcion = texto
        job.descripcion_truncada = False
        job.modalidad = detecta_modalidad(f"{job.titulo} {texto}")
        resumen.completadas += 1

        if _reevalua(sesion, job):
            resumen.reevaluadas += 1

        sesion.commit()

    return resumen
