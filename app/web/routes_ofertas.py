"""Listado, detalle, decisiones y reclasificar.

La vista principal del producto. Sobre datos reales conviven 26 `aplicar_ya`, 55
`revisar` y 113 `descartar`, así que aquí manda una idea: **densidad**. El usuario
revisa más de cien ofertas de una sentada, de modo que la lista es una tabla
compacta, `descartar` viene plegado y cada fila cabe en dos líneas.

Este módulo no contiene lógica de dominio. Los estados de decisión, la memoria por
empresa y el recuento de candidaturas viven en `app/decisiones.py`; clasificar,
en `app/classify.py`; qué decisión enseña qué al modelo, en `app/feedback.py`.
Aquí sólo se consulta, se ordena y se pinta.

El router se monta SIN prefijo: declara la ruta completa (`/`, `/job/{id}`...),
según la tabla de rutas del spec.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.classify import PROMPT_VERSION, clasifica
from app.config import get_settings
from app.cerradas import cierra_oferta, reabre_oferta
from app.decisiones import (
    ESTADO_APLICADA,
    ESTADO_DESCARTADA_POR_MI,
    ESTADO_EN_PROCESO,
    ESTADO_GUARDADA,
    ESTADO_RECHAZADO_POR_ELLOS,
    ESTADOS,
    ETIQUETAS,
    EntradaHistorial,
    EstadoDesconocido,
    OfertaNoEncontrada,
    historial_de,
    historial_por_empresa,
    registra_decision,
    resumen_candidaturas,
)
from app.dedup import normaliza
from app.feedback import ejemplos_few_shot
from app.llm.base import LLMProvider
from app.llm.factory import crear_provider
from app.models import Clasificacion, Job, ahora
from app.schemas import ResultadoClasificacion

# `pipeline.py` ya sabe cargar el perfil vigente, las preferencias vigentes y
# convertir una fila `job` en el `RawJob` que espera el clasificador. Se importan
# esos tres helpers en vez de reescribirlos aquí: son las mismas reglas que aplica
# el run diario y, duplicadas, acabarían divergiendo — que es justo cómo se
# consigue que reclasificar a mano dé un resultado distinto al del run.
from app.pipeline import _a_rawjob, _carga_perfil, _carga_preferencias
from app.web.deps import es_peticion_htmx, get_plantillas, get_sesion

router = APIRouter()

ESTADO_CLASIFICADA = "clasificada"

# El orden de los grupos no es alfabético ni casual: es el orden en que al usuario
# le compensa mirarlos.
ORDEN_CATEGORIAS: tuple[str, ...] = ("aplicar_ya", "revisar", "descartar")
ETIQUETAS_CATEGORIA: dict[str, str] = {
    "aplicar_ya": "Aplicar ya",
    "revisar": "Revisar",
    "descartar": "Descartar",
}

# `descartar` es más de la mitad de la lista y el usuario no ha entrado a leer
# descartes: se pliega. Sigue estando ahí, a un clic, porque los descartes mal
# hechos sólo se descubren mirándolos.
CATEGORIAS_PLEGADAS = frozenset({"descartar"})

ORDEN_CONFIANZA: dict[str, int] = {"alta": 0, "media": 1, "baja": 2}

# Valor del filtro de estado que significa "no filtres nada". El vacío significa
# otra cosa distinta: "las que aún no he decidido", que es lo que se ve al entrar.
ESTADO_TODAS = "todas"
ESTADO_SIN_DECIDIR = ""

ETIQUETAS_EJES: dict[str, str] = {
    "tecnico": "Encaje técnico",
    "seniority": "Seniority",
    "modalidad": "Modalidad",
    "salario": "Salario",
    "sector": "Sector",
}

# Cómo se lee en la fila un historial previo con la misma empresa. En segunda
# persona y con fecha, porque lo que decide si el rechazo de hace seis meses
# importa o no es cuándo fue.
FRASES_HISTORIAL: dict[str, str] = {
    ESTADO_GUARDADA: "guardaste una oferta suya el {fecha}",
    ESTADO_APLICADA: "aplicaste aquí el {fecha}",
    ESTADO_EN_PROCESO: "hablas con ellos desde el {fecha}",
    ESTADO_RECHAZADO_POR_ELLOS: "te rechazaron el {fecha}",
    ESTADO_DESCARTADA_POR_MI: "descartaste una oferta suya el {fecha}",
}

SIN_FECHA = "sin fecha"


def get_provider() -> LLMProvider:
    """Proveedor del clasificador, como dependencia para poder sustituirlo.

    Se declara aquí y no en `deps.py` porque sólo reclasificar lo necesita: si
    fuera una dependencia global, cada visita al listado construiría un cliente de
    un servicio externo sin usarlo. Los tests lo sustituyen por `FakeProvider`.
    """
    return crear_provider(get_settings())


def _fecha(momento: datetime | None) -> str:
    return momento.strftime("%d/%m/%Y") if momento else SIN_FECHA


def frase_historial(entrada: EntradaHistorial) -> str:
    """Una línea que resuma qué pasó ya con esta empresa.

    Para `aplicada` se usa `aplicada_en`, que es cuándo se presentó, y no la fecha
    del último cambio: si la empresa contesta en octubre, uno no aplicó en octubre.
    """
    momento = entrada.aplicada_en if entrada.estado == ESTADO_APLICADA else entrada.decidida_en
    plantilla = FRASES_HISTORIAL.get(entrada.estado, "hay una decisión previa del {fecha}")
    return plantilla.format(fecha=_fecha(momento or entrada.decidida_en))


def _marca_temporal(momento: datetime | None) -> float:
    """Fecha comparable, ignorando la zona horaria.

    SQLite devuelve las fechas sin zona y las fuentes las traen con ella; mezclar
    ambas al ordenar levanta un TypeError. Como sólo se usa para ordenar, basta con
    quitar la zona: el desfase máximo es de horas y aquí se compara por días.
    """
    if momento is None:
        return 0.0
    return momento.replace(tzinfo=None).timestamp()


def _clave_orden(fila: dict) -> tuple:
    """Confianza alta primero; a igual confianza, lo más reciente.

    Las ofertas sin fecha van al final: `None` no es 'recién publicada', y
    colocarlas arriba llenaría la cabecera de la lista con lo que menos se sabe.
    """
    clasificacion = fila["clasificacion"]
    confianza = clasificacion.confianza if clasificacion else ""
    fecha = fila["oferta"].publicada_en
    return (
        ORDEN_CONFIANZA.get(confianza, len(ORDEN_CONFIANZA)),
        0 if fecha else 1,
        -_marca_temporal(fecha),
    )


def _construye_fila(job: Job, entradas: list[EntradaHistorial]) -> dict:
    """Todo lo que la plantilla necesita de una oferta, ya masticado.

    Las plantillas no calculan: reciben las cadenas hechas. Así el formato de una
    fecha o el texto de un historial se prueban desde Python y no leyendo HTML.
    """
    decision = job.decision
    return {
        "oferta": job,
        "clasificacion": job.clasificacion,
        "decision": decision,
        "etiqueta_estado": ETIQUETAS.get(decision.estado, decision.estado) if decision else "",
        "fecha": _fecha(job.publicada_en),
        "cerrada": job.cerrada,
        "cerrada_en": _fecha(job.cerrada_en),
        "historial": [frase_historial(entrada) for entrada in entradas],
    }


def _coincide_texto(job: Job, texto: str) -> bool:
    """Búsqueda sobre título y empresa, insensible a mayúsculas y acentos.

    Se filtra en Python y no con un LIKE porque `normaliza()` es la misma función
    que usa la deduplicación: buscar 'diseñador' tiene que encontrar 'Disenador'.
    """
    if not texto:
        return True
    aguja = normaliza(texto)
    return aguja in normaliza(job.titulo) or aguja in normaliza(job.empresa)


def _coincide_estado(job: Job, estado: str) -> bool:
    if estado == ESTADO_TODAS:
        return True
    if estado == ESTADO_SIN_DECIDIR:
        # Lo que se ve al entrar: lo que queda por decidir. Las decididas siguen
        # accesibles por el filtro, no se borran de la vista.
        return job.decision is None
    return job.decision is not None and job.decision.estado == estado


def _ofertas_clasificadas(sesion: Session, *, fuente: str, categoria: str) -> list[Job]:
    consulta = (
        select(Job)
        .join(Clasificacion, Clasificacion.job_id == Job.id)
        .options(joinedload(Job.clasificacion), joinedload(Job.decision))
    )
    if fuente:
        consulta = consulta.where(Job.fuente == fuente)
    if categoria:
        consulta = consulta.where(Clasificacion.categoria == categoria)
    return list(sesion.scalars(consulta).unique().all())


def _agrupa(filas: list[dict]) -> list[dict]:
    """Grupos por categoría en el orden en que conviene mirarlos.

    Los grupos vacíos no se pintan: una cabecera con cero filas es ruido en una
    pantalla donde lo que escasea es el espacio vertical.
    """
    por_categoria: dict[str, list[dict]] = {}
    for fila in filas:
        clasificacion = fila["clasificacion"]
        if clasificacion is None:
            continue
        por_categoria.setdefault(clasificacion.categoria, []).append(fila)

    # Primero las tres conocidas y en su orden; después cualquier categoría nueva,
    # que es preferible enseñar al final antes que perderla en silencio.
    orden = list(ORDEN_CATEGORIAS) + sorted(set(por_categoria) - set(ORDEN_CATEGORIAS))
    return [
        {
            "categoria": categoria,
            "etiqueta": ETIQUETAS_CATEGORIA.get(categoria, categoria),
            "plegado": categoria in CATEGORIAS_PLEGADAS,
            "filas": sorted(por_categoria[categoria], key=_clave_orden),
        }
        for categoria in orden
        if por_categoria.get(categoria)
    ]


def _fuentes(sesion: Session) -> list[str]:
    return sorted(f for f in sesion.scalars(select(Job.fuente).distinct()).all() if f)


def _estados_para_formulario() -> list[tuple[str, str]]:
    return [(estado, ETIQUETAS[estado]) for estado in ESTADOS]


def _contexto_comun() -> dict:
    return {"estados": _estados_para_formulario()}


@router.get("/", response_class=HTMLResponse)
def listado(
    request: Request,
    fuente: str = Query(default=""),
    categoria: str = Query(default=""),
    estado: str = Query(default=ESTADO_SIN_DECIDIR),
    q: str = Query(default=""),
    cerradas: str = Query(default=""),
    sesion: Session = Depends(get_sesion),
) -> HTMLResponse:
    """Tablero agrupado por categoría, con filtros.

    Ante una petición de HTMX devuelve sólo `_lista.html`: la navegación, los
    contadores y el formulario de filtros no cambian al filtrar, y reenviarlos
    perdería el foco del campo de búsqueda a cada tecla.
    """
    candidatas = _ofertas_clasificadas(sesion, fuente=fuente, categoria=categoria)
    visibles = [
        job
        for job in candidatas
        if _coincide_texto(job, q)
        and _coincide_estado(job, estado)
        # Las cerradas se ocultan salvo que se pidan: el puesto ya no existe y
        # revisarlas es tiempo perdido. Se ocultan, no se borran, porque su recuento
        # es lo que dice qué fuente sirve enlaces muertos.
        and (cerradas == "si" or not job.cerrada)
    ]

    historial = historial_por_empresa(sesion, visibles)
    filas = [_construye_fila(job, historial_de(historial, job)) for job in visibles]

    contexto = {
        **_contexto_comun(),
        "titulo": "Ofertas",
        "grupos": _agrupa(filas),
        "total": len(filas),
        "fuentes": _fuentes(sesion),
        "categorias": [(c, ETIQUETAS_CATEGORIA[c]) for c in ORDEN_CATEGORIAS],
        "filtros": {
            "fuente": fuente,
            "categoria": categoria,
            "estado": estado,
            "q": q,
            "cerradas": cerradas,
        },
        "resumen": resumen_candidaturas(sesion),
    }

    plantilla = "_lista.html" if es_peticion_htmx(request) else "ofertas.html"
    return get_plantillas().TemplateResponse(request, plantilla, contexto)


def _oferta(sesion: Session, job_id: int) -> Job:
    job = sesion.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la oferta {job_id}.")
    return job


def _fila_de(sesion: Session, job: Job) -> dict:
    historial = historial_por_empresa(sesion, [job])
    return _construye_fila(job, historial_de(historial, job))


def _ejes(clasificacion: Clasificacion | None) -> list[tuple[str, str]]:
    """Los cinco ejes en orden fijo, más los que traiga de más una versión futura."""
    if clasificacion is None:
        return []
    ejes = clasificacion.ejes or {}
    orden = list(ETIQUETAS_EJES) + sorted(set(ejes) - set(ETIQUETAS_EJES))
    return [(ETIQUETAS_EJES.get(eje, eje), ejes[eje]) for eje in orden if eje in ejes]


def _pagina_detalle(
    request: Request, sesion: Session, job: Job, *, error: str | None = None
) -> HTMLResponse:
    fila = _fila_de(sesion, job)
    return get_plantillas().TemplateResponse(
        request,
        "oferta.html",
        {
            **_contexto_comun(),
            "titulo": job.titulo,
            "fila": fila,
            "ejes": _ejes(fila["clasificacion"]),
            "error": error,
        },
    )


@router.get("/job/{job_id}", response_class=HTMLResponse)
def detalle(
    request: Request, job_id: int, sesion: Session = Depends(get_sesion)
) -> HTMLResponse:
    return _pagina_detalle(request, sesion, _oferta(sesion, job_id))


@router.post("/job/{job_id}/decision", response_class=HTMLResponse)
def decidir(
    request: Request,
    job_id: int,
    estado: str = Form(...),
    motivo: str = Form(default=""),
    sesion: Session = Depends(get_sesion),
) -> HTMLResponse:
    """Crea o actualiza la decisión sobre una oferta y devuelve sólo ese bloque.

    El motivo se pide en el mismo gesto que el estado, no en un segundo paso: sin
    motivo escrito la decisión se guarda igual, pero `feedback.py` la ignora, así
    que esconder el campo equivaldría a apagar el aprendizaje sin decirlo.
    """
    try:
        registra_decision(sesion, job_id, estado, motivo.strip())
    except OfertaNoEncontrada as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except EstadoDesconocido as e:
        # 400 y no 422: el formulario sólo ofrece estados válidos, así que esto es
        # una petición mal formada, no un dato del usuario que haya que validar.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    job = _oferta(sesion, job_id)
    return get_plantillas().TemplateResponse(
        request,
        "_acciones.html",
        {**_contexto_comun(), "fila": _fila_de(sesion, job)},
    )


def _guarda_clasificacion(
    sesion: Session, job: Job, veredicto: ResultadoClasificacion, provider: LLMProvider
) -> None:
    """Sustituye la clasificación de la oferta, no añade otra.

    `classification.job_id` es único y, aunque no lo fuera, dos clasificaciones de
    la misma oferta harían que el listado enseñase una categoría y el detalle otra.
    """
    clasificacion = sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id))
    if clasificacion is None:
        clasificacion = Clasificacion(job_id=job.id)
        sesion.add(clasificacion)

    clasificacion.categoria = veredicto.categoria
    clasificacion.confianza = veredicto.confianza
    clasificacion.razonamiento = veredicto.razonamiento
    clasificacion.ejes = veredicto.ejes.model_dump()
    clasificacion.skills_faltantes = veredicto.skills_faltantes
    clasificacion.red_flags = veredicto.red_flags
    clasificacion.modelo = getattr(provider, "nombre", "desconocido")
    clasificacion.prompt_version = PROMPT_VERSION
    clasificacion.creada_en = ahora()

    job.estado_clasificacion = ESTADO_CLASIFICADA
    sesion.commit()


@router.post("/job/{job_id}/reclasificar", response_class=HTMLResponse)
def reclasificar(
    request: Request,
    job_id: int,
    sesion: Session = Depends(get_sesion),
    provider: LLMProvider = Depends(get_provider),
) -> HTMLResponse:
    """Vuelve a juzgar la oferta con el prompt y las preferencias de ahora.

    Si el proveedor falla, la clasificación anterior se queda como estaba y el
    mensaje real del fallo se enseña en la vista: una pantalla en blanco o un
    "ha ocurrido un error" obligarían a ir a mirar los logs del contenedor.
    """
    job = _oferta(sesion, job_id)

    try:
        veredicto = clasifica(
            _a_rawjob(job),
            perfil=_carga_perfil(sesion),
            prefs=_carga_preferencias(sesion),
            ejemplos=ejemplos_few_shot(sesion),
            provider=provider,
        )
    except Exception as e:  # noqa: BLE001 - cualquier fallo se cuenta, no se traga
        sesion.rollback()
        return _pagina_detalle(request, sesion, job, error=f"{type(e).__name__}: {e}")

    _guarda_clasificacion(sesion, job, veredicto, provider)
    return _pagina_detalle(request, sesion, job)


@router.post("/job/{job_id}/cerrada", response_class=HTMLResponse)
def marca_cerrada(
    request: Request,
    job_id: int,
    abierta: str = Form(default=""),
    sesion: Session = Depends(get_sesion),
) -> HTMLResponse:
    """Marca la oferta como cerrada, o la reabre si se marcó por error.

    No toca la decisión: haber aplicado y que además cierren el puesto son dos cosas
    ciertas a la vez, y perder la primera al registrar la segunda sería falsear el
    seguimiento de candidaturas.
    """
    job = _oferta(sesion, job_id)
    job = reabre_oferta(sesion, job.id) if abierta == "si" else cierra_oferta(sesion, job.id)

    contexto = {**_contexto_comun(), "fila": _fila_de(sesion, job)}
    return get_plantillas().TemplateResponse(request, "_acciones.html", contexto)
