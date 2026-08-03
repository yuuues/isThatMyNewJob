import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classify import PROMPT_VERSION, clasifica
from app.feedback import ejemplos_few_shot
from app.ingest import ingesta
from app.llm.base import CuotaAgotadaError, LLMProvider
from app.models import Clasificacion, Job, Perfil, PreferenciasRow, Run
from app.prefilter import aplica_prefiltro
from app.resiliencia import REINTENTOS, con_reintentos
from app.schemas import PerfilCandidato, Preferencias, RawJob, SearchQuery

MAX_INTENTOS = 3

# Estado terminal de una oferta que agotó los intentos. `models.py` ya lo documenta en
# el comentario de `estado_clasificacion`; aquí es donde se asigna. Sin él la oferta se
# quedaba en "pendiente" para siempre: fuera de la cola por el filtro de intentos y
# fuera de `run.errores` por no volver a intentarse. Desaparecía sin rastro.
ESTADO_AGOTADA = "error"

MOTIVO_CUOTA = "cuota_agotada"


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
        descripcion_truncada=job.descripcion_truncada,
        publicada_en=job.publicada_en,
        tags=job.tags or [],
    )


def _error(
    tipo: str, *, error: str, fuente: str | None = None, job_id: int | None = None
) -> dict:
    """Una sola forma para todas las entradas de `run.errores`.

    Las cuatro claves están siempre presentes, con `None` donde no aplican, y `tipo`
    dice de qué se trata. Antes convivían `{"fuente", "error"}` y `{"job_id", "error"}`
    y quien leyera `e["fuente"]` sobre un error de clasificación se llevaba un KeyError.
    """
    return {"tipo": tipo, "fuente": fuente, "job_id": job_id, "error": error}


def _errores_de_ingesta(stats: dict[str, dict]) -> list[dict]:
    """Traduce lo que reporte `ingesta()` a la forma común.

    Acepta tanto un único `error` por fuente como una lista `errores`: el aislamiento
    por unidad de trabajo puede dejar más de un fallo en la misma fuente.
    """
    errores: list[dict] = []
    for nombre, datos in stats.items():
        if not isinstance(datos, dict):
            continue
        crudos = datos.get("errores") or ([datos["error"]] if datos.get("error") else [])
        errores.extend(
            _error("fuente", fuente=nombre, error=str(crudo)) for crudo in crudos
        )
    return errores


def ejecuta_run(
    sesion: Session,
    *,
    fuentes: list,
    queries: list[SearchQuery],
    provider: LLMProvider,
    max_clasificaciones: int = 200,
    reintentos: int = REINTENTOS,
    dormir: Callable[[float], None] = time.sleep,
) -> Run:
    """Ingesta, prefiltra y clasifica. Deja constancia de todo en la tabla `run`.

    Ante un fallo del modelo se reintenta con backoff antes de rendirse; sólo después
    la oferta vuelve a la cola del run siguiente. Tras `MAX_INTENTOS` runs fallidos
    pasa a `ESTADO_AGOTADA` y deja de consumir llamadas, pero sigue consultable.

    Si el proveedor avisa de cuota agotada se corta en seco: no se le hace ni una
    llamada más, la cola queda intacta para mañana y el run se cierra registrando el
    motivo. `dormir` se inyecta para que los tests no duerman de verdad.
    """
    perfil = _carga_perfil(sesion)
    prefs = _carga_preferencias(sesion)

    run = Run(inicio=datetime.now(UTC).replace(tzinfo=None))
    sesion.add(run)
    sesion.commit()

    stats = ingesta(sesion, fuentes, queries)
    errores: list[dict] = _errores_de_ingesta(stats)

    pendientes = sesion.scalars(
        select(Job).where(Job.estado_clasificacion == "pendiente").order_by(Job.ingerida_en)
    ).all()

    ejemplos = ejemplos_few_shot(sesion)
    descartadas_por_regla = 0
    clasificadas = 0
    agotadas = 0
    interrumpido_por: str | None = None

    for job in pendientes:
        oferta = _a_rawjob(job)

        resultado_regla = aplica_prefiltro(oferta, prefs)
        if resultado_regla.descartada:
            job.estado_clasificacion = "descartada_por_regla"
            job.motivo_regla = resultado_regla.motivo
            descartadas_por_regla += 1
            sesion.commit()
            continue

        # Filas heredadas de cuando nadie asignaba el estado terminal: agotadas pero
        # todavía en "pendiente". Se cierran aquí en lugar de quedarse invisibles.
        if job.intentos_clasificacion >= MAX_INTENTOS:
            job.estado_clasificacion = ESTADO_AGOTADA
            agotadas += 1
            sesion.commit()
            continue

        if clasificadas >= max_clasificaciones:
            continue

        try:
            veredicto = con_reintentos(
                lambda: clasifica(
                    oferta, perfil=perfil, prefs=prefs, ejemplos=ejemplos, provider=provider
                ),
                reintentos=reintentos,
                dormir=dormir,
            )
        except CuotaAgotadaError as e:
            # Circuit breaker: la cuota no vuelve dentro de este run. Esta oferta no
            # gasta intento — el fallo no es suyo — y las que quedan ni se tocan.
            interrumpido_por = MOTIVO_CUOTA
            errores.append(
                _error("cuota", fuente=job.fuente, job_id=job.id, error=f"{type(e).__name__}: {e}")
            )
            sesion.commit()
            break
        except Exception as e:  # noqa: BLE001 - la oferta vuelve a la cola del run siguiente
            job.intentos_clasificacion += 1
            tipo = "clasificacion"
            if job.intentos_clasificacion >= MAX_INTENTOS:
                job.estado_clasificacion = ESTADO_AGOTADA
                agotadas += 1
                tipo = "clasificacion_agotada"
            errores.append(
                _error(tipo, fuente=job.fuente, job_id=job.id, error=f"{type(e).__name__}: {e}")
            )
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
            "agotadas": agotadas,
            "interrumpido_por": interrumpido_por,
        },
    }
    run.errores = errores
    sesion.commit()
    return run
