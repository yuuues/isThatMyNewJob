from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dedup import hash_dedup
from app.models import Job
from app.schemas import RawJob, SearchQuery
from app.sources.base import JobSource


def _a_modelo(oferta: RawJob, clave: str) -> Job:
    return Job(
        fuente=oferta.fuente,
        external_id=oferta.external_id,
        url=oferta.url,
        titulo=oferta.titulo,
        empresa=oferta.empresa,
        ubicacion=oferta.ubicacion,
        modalidad=oferta.modalidad,
        salario_min=oferta.salario_min,
        salario_max=oferta.salario_max,
        salario_texto=oferta.salario_texto,
        descripcion=oferta.descripcion,
        tags=oferta.tags,
        publicada_en=oferta.publicada_en,
        hash_dedup=clave,
        estado_clasificacion="pendiente",
    )


def ingesta(
    sesion: Session, fuentes: list[JobSource], queries: list[SearchQuery]
) -> dict[str, dict]:
    """Consulta cada fuente con cada búsqueda, deduplica y persiste lo nuevo.

    Una fuente que falla se registra y no interrumpe a las demás: perder Adzuna un día
    no debe costar también las ofertas remotas de ese día.
    """
    stats: dict[str, dict] = {}

    for fuente in fuentes:
        resumen = {"recibidas": 0, "nuevas": 0, "duplicadas": 0}
        try:
            for query in queries:
                ofertas = fuente.search(query)
                resumen["recibidas"] += len(ofertas)

                for oferta in ofertas:
                    clave = hash_dedup(oferta.empresa, oferta.titulo, oferta.ubicacion)
                    existe = sesion.scalar(select(Job).where(Job.hash_dedup == clave))
                    if existe is not None:
                        resumen["duplicadas"] += 1
                        continue

                    sesion.add(_a_modelo(oferta, clave))
                    sesion.flush()
                    resumen["nuevas"] += 1

            sesion.commit()
        except Exception as e:  # noqa: BLE001 - se registra y se sigue con las demás fuentes
            sesion.rollback()
            resumen["error"] = f"{type(e).__name__}: {e}"

        stats[fuente.nombre] = resumen

    return stats
