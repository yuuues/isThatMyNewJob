from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classify import PROMPT_VERSION, clasifica
from app.feedback import ejemplos_few_shot
from app.ingest import ingesta
from app.llm.base import LLMProvider
from app.models import Clasificacion, Job, Perfil, PreferenciasRow, Run
from app.prefilter import aplica_prefiltro
from app.schemas import PerfilCandidato, Preferencias, RawJob, SearchQuery

MAX_INTENTOS = 3


def _carga_perfil(sesion: Session) -> PerfilCandidato:
    fila = sesion.scalar(select(Perfil).order_by(Perfil.id.desc()))
    if fila is None or not fila.datos:
        raise RuntimeError("No hay perfil cargado. Sube el CV antes de ejecutar el run.")
    return PerfilCandidato.model_validate(fila.datos)


def _carga_preferencias(sesion: Session) -> Preferencias:
    fila = sesion.scalar(select(PreferenciasRow).order_by(PreferenciasRow.id.desc()))
    return Preferencias.model_validate(fila.datos) if fila and fila.datos else Preferencias()


def _a_rawjob(job: Job) -> RawJob:
    return RawJob(
        fuente=job.fuente,
        external_id=job.external_id,
        url=job.url,
        titulo=job.titulo,
        empresa=job.empresa,
        ubicacion=job.ubicacion,
        modalidad=job.modalidad,
        salario_min=job.salario_min,
        salario_max=job.salario_max,
        salario_texto=job.salario_texto,
        descripcion=job.descripcion,
        publicada_en=job.publicada_en,
        tags=job.tags or [],
    )


def ejecuta_run(
    sesion: Session,
    *,
    fuentes: list,
    queries: list[SearchQuery],
    provider: LLMProvider,
    max_clasificaciones: int = 200,
) -> Run:
    """Ingesta, prefiltra y clasifica. Deja constancia de todo en la tabla `run`.

    Las ofertas que no se llegan a clasificar (por límite o por fallo del modelo)
    quedan en estado `pendiente` y las recoge el run siguiente. No se pierden.
    """
    perfil = _carga_perfil(sesion)
    prefs = _carga_preferencias(sesion)

    run = Run(inicio=datetime.now(UTC).replace(tzinfo=None))
    sesion.add(run)
    sesion.commit()

    stats = ingesta(sesion, fuentes, queries)
    errores: list[dict] = [
        {"fuente": nombre, "error": datos["error"]}
        for nombre, datos in stats.items()
        if "error" in datos
    ]

    pendientes = sesion.scalars(
        select(Job)
        .where(Job.estado_clasificacion == "pendiente")
        .where(Job.intentos_clasificacion < MAX_INTENTOS)
        .order_by(Job.ingerida_en)
    ).all()

    ejemplos = ejemplos_few_shot(sesion)
    descartadas_por_regla = 0
    clasificadas = 0

    for job in pendientes:
        oferta = _a_rawjob(job)

        resultado_regla = aplica_prefiltro(oferta, prefs)
        if resultado_regla.descartada:
            job.estado_clasificacion = "descartada_por_regla"
            job.motivo_regla = resultado_regla.motivo
            descartadas_por_regla += 1
            sesion.commit()
            continue

        if clasificadas >= max_clasificaciones:
            continue

        try:
            veredicto = clasifica(
                oferta, perfil=perfil, prefs=prefs, ejemplos=ejemplos, provider=provider
            )
        except Exception as e:  # noqa: BLE001 - la oferta vuelve a la cola del run siguiente
            job.intentos_clasificacion += 1
            errores.append({"job_id": job.id, "error": f"{type(e).__name__}: {e}"})
            sesion.commit()
            continue

        sesion.add(
            Clasificacion(
                job_id=job.id,
                categoria=veredicto.categoria,
                confianza=veredicto.confianza,
                razonamiento=veredicto.razonamiento,
                ejes=veredicto.ejes.model_dump(),
                skills_faltantes=veredicto.skills_faltantes,
                red_flags=veredicto.red_flags,
                modelo=getattr(provider, "nombre", "desconocido"),
                prompt_version=PROMPT_VERSION,
            )
        )
        job.estado_clasificacion = "clasificada"
        clasificadas += 1
        sesion.commit()

    run.fin = datetime.now(UTC).replace(tzinfo=None)
    run.stats = {
        **stats,
        "_totales": {
            "clasificadas": clasificadas,
            "descartadas_por_regla": descartadas_por_regla,
        },
    }
    run.errores = errores
    sesion.commit()
    return run
