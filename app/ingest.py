from collections.abc import Callable

from sqlalchemy import and_, or_, select
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


def _ya_conocida(sesion: Session, oferta: RawJob, clave: str) -> bool:
    """Comprueba las dos claves de deduplicación que exige el spec.

    `hash_dedup` reconoce la misma oferta llegada por fuentes distintas; el par
    (fuente, external_id) reconoce la misma oferta republicada con el título
    retocado. Sin la segunda, la reinserción chocaba contra la UniqueConstraint y
    el rollback se llevaba por delante todo el lote.
    """
    return (
        sesion.scalar(
            select(Job.id).where(
                or_(
                    Job.hash_dedup == clave,
                    and_(
                        Job.fuente == oferta.fuente,
                        Job.external_id == oferta.external_id,
                    ),
                )
            )
        )
        is not None
    )


def _unidades_de_trabajo(
    fuente: JobSource, queries: list[SearchQuery]
) -> list[tuple[str, Callable[[], list[RawJob]]]]:
    """Trocea el trabajo de una fuente en unidades aisladas (una llamada + su commit).

    Una fuente que filtra en servidor necesita una petición por búsqueda. Una que no
    filtra devuelve siempre el feed entero, así que se descarga una sola vez por run
    y se filtra en local contra todas las búsquedas: repetir la descarga por cada
    búsqueda sólo gastaría cuota ajena.

    Una fuente que no declare `filtra_en_servidor` se trata como si filtrara: es el
    contrato antiguo, una petición por búsqueda. Sin el defecto, una fuente de terceros
    mal implementada lanzaría AttributeError aquí, fuera del try de la unidad de
    trabajo, tumbando la ingesta de todas las demás fuentes y no sólo la suya.
    """
    if getattr(fuente, "filtra_en_servidor", True):
        return [(q.nombre, (lambda q=q: fuente.search(q))) for q in queries]
    return [("todas las búsquedas", lambda: fuente.busca_varias(list(queries)))]


def ingesta(
    sesion: Session, fuentes: list[JobSource], queries: list[SearchQuery]
) -> dict[str, dict]:
    """Consulta cada fuente, deduplica y persiste lo nuevo.

    El aislamiento es por unidad de trabajo, no por fuente: una fuente que falla no
    interrumpe a las demás, y dentro de una fuente el fallo de una búsqueda no borra
    lo que otra ya había ingerido.

    Las cifras de `stats` cuentan sólo lo confirmado: lo que se va en un rollback no
    se suma, porque estas cifras acaban en `run.stats` y son las que ve el usuario.
    """
    stats: dict[str, dict] = {}

    for fuente in fuentes:
        resumen = {"recibidas": 0, "nuevas": 0, "duplicadas": 0}
        errores: list[str] = []

        for etiqueta, buscar in _unidades_de_trabajo(fuente, queries):
            parcial = {"recibidas": 0, "nuevas": 0, "duplicadas": 0}
            try:
                ofertas = buscar()
                parcial["recibidas"] = len(ofertas)

                for oferta in ofertas:
                    clave = hash_dedup(oferta.empresa, oferta.titulo, oferta.ubicacion)
                    if _ya_conocida(sesion, oferta, clave):
                        parcial["duplicadas"] += 1
                        continue

                    sesion.add(_a_modelo(oferta, clave))
                    sesion.flush()
                    parcial["nuevas"] += 1

                sesion.commit()
            except Exception as e:  # noqa: BLE001 - se registra y se sigue con el resto
                sesion.rollback()
                errores.append(f"{etiqueta}: {type(e).__name__}: {e}")
                continue  # nada se persistió: las cifras de esta unidad no se suman

            for clave_cifra, valor in parcial.items():
                resumen[clave_cifra] += valor

        if errores:
            resumen["error"] = " | ".join(errores)

        stats[fuente.nombre] = resumen

    return stats
