"""Ofertas cerradas: el puesto ya no existe cuando se abre el enlace.

Es un atributo de la OFERTA, no una decisión del candidato, y por eso vive aquí y no
en `app/decisiones.py`. Se puede haber aplicado y que además la cierren; modelarlo
como un sexto estado de decisión habría perdido justo esa combinación, que es la que
más interesa conservar.

No se detecta solo. La fuente principal (Indeed vía Scrappa) no publica fecha de
caducidad, y deducirlo por ausencia en los runs daría falsos positivos: pedimos 50
resultados por búsqueda, así que una oferta puede desaparecer por caer al puesto 51 y
seguir perfectamente viva.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.decisiones import OfertaNoEncontrada
from app.models import Job


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _busca(sesion: Session, job_id: int) -> Job:
    job = sesion.get(Job, job_id)
    if job is None:
        raise OfertaNoEncontrada(f"No existe la oferta {job_id}")
    return job


def cierra_oferta(
    sesion: Session, job_id: int, ahora: Callable[[], datetime] | None = None
) -> Job:
    """Marca la oferta como cerrada, conservando la decisión que hubiera.

    Idempotente y conservando la PRIMERA fecha: si ya estaba cerrada, volver a
    marcarla no debe mover el momento en que se descubrió que lo estaba.
    """
    job = _busca(sesion, job_id)
    if not job.cerrada:
        job.cerrada = True
        job.cerrada_en = (ahora or _ahora)()
        sesion.commit()
    return job


def reabre_oferta(sesion: Session, job_id: int) -> Job:
    """Deshace la marca, para cuando se marca una por error."""
    job = _busca(sesion, job_id)
    job.cerrada = False
    job.cerrada_en = None
    sesion.commit()
    return job


def cerradas_por_fuente(sesion: Session) -> dict[str, dict[str, int]]:
    """Cuántas ofertas cerradas acumula cada fuente, sobre su total.

    Existe para convertir una sensación en un dato. Una fuente que sirve ofertas ya
    cerradas hace perder el tiempo: se abren, se leen y no llevan a ninguna parte.
    Con este recuento, en unas semanas se sabe cuál conviene retirar.
    """
    recuento: dict[str, dict[str, int]] = defaultdict(lambda: {"cerradas": 0, "total": 0})
    for fuente, cerrada in sesion.execute(select(Job.fuente, Job.cerrada)):
        recuento[fuente]["total"] += 1
        if cerrada:
            recuento[fuente]["cerradas"] += 1
    return dict(recuento)
