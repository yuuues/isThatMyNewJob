"""Pone al día una base creada con la clave de deduplicación vieja.

La clave dejó de incluir la ubicación (ver app/dedup.py), pero las filas ya guardadas
llevan el hash antiguo. Sin recalcularlo pasan dos cosas: los duplicados que la clave
nueva reconoce siguen ocupando una fila cada uno, y las ofertas que NO tienen duplicado
volverían a entrar como nuevas en el siguiente run, porque su hash guardado ya no es el
que calcula la ingesta.

Es de un solo uso, pero idempotente: pasarlo dos veces no cambia nada la segunda.

No borra a ciegas. De cada grupo se conserva la fila que más información lleva encima
—primero la que tiene una decisión tuya, luego la que tiene veredicto del modelo— y sus
ubicaciones se acumulan en ella, para no perder ni el historial ni la ciudad que hacía
que la oferta pasara el filtro de zona.
"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.dedup import hash_dedup, normaliza, ubicaciones_conocidas
from app.models import Clasificacion, Decision, Job


@dataclass(frozen=True)
class ResumenFusion:
    grupos: int  # grupos con más de una fila
    borradas: int  # filas que se han ido
    reclaveadas: int  # filas cuyo hash se ha recalculado
    # Ofertas en las que habías decidido cosas distintas sobre dos copias de lo mismo.
    # Se conserva la decisión más reciente, pero se avisa: la otra se pierde y sólo tú
    # sabes cuál valía.
    conflictos: tuple[str, ...] = ()


def _recencia_decision(decision: Decision | None) -> float:
    """Ordena las decisiones de más reciente a más antigua.

    `actualizada_en` puede ser NULL: `asegura_esquema()` añadió la columna a las bases
    que ya existían y las filas anteriores se quedaron sin ella. Esas van al final.
    """
    if decision is None or decision.actualizada_en is None:
        return 0.0
    return -decision.actualizada_en.timestamp()


def _prioridad(job: Job, decisiones: dict[int, Decision], clasificadas: set[int]) -> tuple:
    """Orden de preferencia para elegir superviviente. Menor es mejor.

    Manda la decisión del usuario sobre el veredicto del modelo, y el veredicto sobre una
    fila que el prefiltro descartó: si una copia se descartó por zona y otra no, la que
    sirve es la que sobrevivió. Entre dos filas decididas gana la decisión más reciente,
    que es la que refleja lo último que sabías. El `id` desempata para que el resultado no
    dependa del orden en que la base devuelva las filas.
    """
    decision = decisiones.get(job.id)
    return (
        0 if decision is not None else 1,
        _recencia_decision(decision),
        0 if job.id in clasificadas else 1,
        1 if job.estado_clasificacion == "descartada_por_regla" else 0,
        job.id,
    )


def _todas_las_ubicaciones(filas: list[Job]) -> list[str]:
    """Las ubicaciones de todo el grupo, sin repetir y en el orden en que aparecen."""
    acumulado: list[str] = []
    vistas: set[str] = set()
    for fila in filas:
        for ubicacion in ubicaciones_conocidas(fila):
            clave = normaliza(ubicacion)
            if clave and clave not in vistas:
                vistas.add(clave)
                acumulado.append(ubicacion)
    return acumulado


def fusiona_duplicados(sesion: Session) -> ResumenFusion:
    """Recalcula la clave de todas las ofertas y funde las que resulten ser la misma."""
    decisiones = {d.job_id: d for d in sesion.scalars(select(Decision)).all()}
    clasificadas = {c.job_id for c in sesion.scalars(select(Clasificacion)).all()}

    grupos: dict[str, list[Job]] = {}
    for job in sesion.scalars(select(Job)).all():
        grupos.setdefault(hash_dedup(job.empresa, job.titulo), []).append(job)

    fusionados = 0
    borradas = 0
    reclaveadas = 0
    conflictos: list[str] = []

    for clave, filas in grupos.items():
        filas.sort(key=lambda j: _prioridad(j, decisiones, clasificadas))
        superviviente, sobrantes = filas[0], filas[1:]

        estado_bueno = decisiones[superviviente.id].estado if superviviente.id in decisiones else None
        perdidas = {
            decisiones[j.id].estado
            for j in sobrantes
            if j.id in decisiones and decisiones[j.id].estado != estado_bueno
        }
        if perdidas:
            conflictos.append(
                f"{superviviente.empresa} — {superviviente.titulo}: se conserva "
                f"'{estado_bueno}' y se pierde {', '.join(sorted(perdidas))}"
            )

        ubicaciones = _todas_las_ubicaciones(filas)
        if ubicaciones != (superviviente.ubicaciones or []):
            superviviente.ubicaciones = ubicaciones

        if sobrantes:
            fusionados += 1
            ids = [j.id for j in sobrantes]
            # Primero los hijos: la FK apunta a `job.id` y borrar el padre antes deja
            # clasificaciones y decisiones huérfanas que revientan el listado.
            sesion.execute(delete(Clasificacion).where(Clasificacion.job_id.in_(ids)))
            sesion.execute(delete(Decision).where(Decision.job_id.in_(ids)))
            sesion.execute(delete(Job).where(Job.id.in_(ids)))
            borradas += len(ids)

        # Después de borrar los sobrantes, nunca antes: la clave nueva es la misma para
        # todo el grupo y `uq_job_hash_dedup` no admite dos filas con ella a la vez.
        if superviviente.hash_dedup != clave:
            superviviente.hash_dedup = clave
            reclaveadas += 1

        sesion.flush()

    sesion.commit()
    return ResumenFusion(
        grupos=fusionados,
        borradas=borradas,
        reclaveadas=reclaveadas,
        conflictos=tuple(conflictos),
    )
