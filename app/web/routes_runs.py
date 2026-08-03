"""Histórico de ejecuciones, descartes por regla y cupos.

La vista de diagnóstico. Es la que permite descubrir que algo va mal antes de que
pasen semanas: leyendo los descartes por regla aparecieron tres defectos reales
(ofertas de ámbito nacional descartadas por un filtro de ciudades, tarifas por hora
comparadas contra un mínimo anual, y una fuente entera marcada como presencial por
defecto). Un veto mal puesto no da error: simplemente deja de traer ofertas.

Este módulo no contiene lógica de dominio. Lee `run`, `job` y `source_usage`, y las
dos acciones que ofrece se limitan a devolver una oferta a la cola: no reclasifican
ni lanzan runs, de eso se encarga el pipeline en su siguiente pasada.

El router se monta SIN prefijo: declara la ruta completa (`/runs`...), según la
tabla de rutas del spec.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ConsumoFuente, Job, Run
from app.web.deps import get_plantillas, get_sesion
from app.cerradas import cerradas_por_fuente

router = APIRouter()

# La única fuente con cupo mensual de verdad. Adzuna, Remotive y Arbeitnow no lo
# tienen (`presupuesto.SinLimite`), así que enseñar un contador para ellas sería
# ruido que tapa el único número que importa.
FUENTE_CON_CUPO = "jsearch"

# Los descartes sin motivo escrito no pueden quedarse fuera del agrupado: si se
# filtraran, una oferta descartada sin razón desaparecería de la única vista que
# permite recuperarla.
SIN_MOTIVO = "sin motivo registrado"

ESTADO_DESCARTADA_POR_REGLA = "descartada_por_regla"
ESTADO_ERROR = "error"
ESTADO_PENDIENTE = "pendiente"


def periodo_actual(ahora: datetime | None = None) -> str:
    """Mes natural en curso, en el mismo formato que escribe `PresupuestoMensual`."""
    return (ahora or datetime.now(UTC)).strftime("%Y-%m")


def _cupo(sesion: Session) -> dict:
    """Consumo, límite y restante de JSearch en el mes en curso.

    Un mes sin consumo no tiene fila en `source_usage` — la crea el presupuesto al
    gastar el primer crédito —, así que la ausencia de fila significa cero, no error.
    """
    periodo = periodo_actual()
    limite = get_settings().jsearch_limite_mensual
    fila = sesion.scalar(
        select(ConsumoFuente).where(
            ConsumoFuente.fuente == FUENTE_CON_CUPO, ConsumoFuente.periodo == periodo
        )
    )
    consumido = fila.peticiones if fila else 0
    restante = max(0, limite - consumido)
    return {
        "fuente": FUENTE_CON_CUPO,
        "periodo": periodo,
        "consumido": consumido,
        "limite": limite,
        "restante": restante,
        "agotado": restante == 0,
    }


def _referencia(error: dict) -> str:
    """Referencia legible de una entrada de `run.errores`.

    `run.errores` guarda `{tipo, fuente, job_id, error}` con `None` donde no aplica.
    Se muestran las dos partes que haya: la fuente sitúa el fallo y el identificador
    de oferta permite ir a mirarla.
    """
    partes = [p for p in (error.get("fuente"), error.get("job_id")) if p]
    if not partes:
        return "—"
    return " · ".join(
        str(p) if p == error.get("fuente") else f"oferta {p}" for p in partes
    )


def _errores(run: Run) -> list[dict]:
    return [
        {
            "tipo": error.get("tipo") or "desconocido",
            "referencia": _referencia(error),
            "mensaje": error.get("error") or "",
            "job_id": error.get("job_id"),
        }
        for error in (run.errores or [])
        if isinstance(error, dict)
    ]


def _fuentes(run: Run) -> list[dict]:
    """Estadísticas por fuente de un run.

    `stats` mezcla las fuentes con la clave reservada `_totales`, que no es una
    fuente. Se separa aquí para que la tabla no invente una llamada "_totales".
    """
    stats = run.stats or {}
    return [
        {"fuente": nombre, **cifras}
        for nombre, cifras in stats.items()
        if not nombre.startswith("_") and isinstance(cifras, dict)
    ]


def _totales(run: Run) -> dict:
    totales = (run.stats or {}).get("_totales") or {}
    return {
        "clasificadas": totales.get("clasificadas", 0),
        "descartadas_por_regla": totales.get("descartadas_por_regla", 0),
        "agotadas": totales.get("agotadas", 0),
        "interrumpido_por": totales.get("interrumpido_por"),
    }


def _runs(sesion: Session) -> list[dict]:
    filas = sesion.scalars(select(Run).order_by(Run.inicio.desc(), Run.id.desc())).all()
    return [
        {
            "id": run.id,
            "inicio": run.inicio,
            "fin": run.fin,
            "fuentes": _fuentes(run),
            "totales": _totales(run),
            "errores": _errores(run),
        }
        for run in filas
    ]


def _descartes(sesion: Session) -> list[dict]:
    """Descartes por regla agrupados por motivo, el grupo más numeroso primero.

    El orden importa: el motivo que más ofertas se está comiendo es justo el que hay
    que mirar antes, porque es el que más caro sale si está mal puesto.
    """
    ofertas = sesion.scalars(
        select(Job)
        .where(Job.estado_clasificacion == ESTADO_DESCARTADA_POR_REGLA)
        .order_by(Job.ingerida_en.desc(), Job.id.desc())
    ).all()

    grupos: dict[str, list[Job]] = {}
    for oferta in ofertas:
        grupos.setdefault(oferta.motivo_regla or SIN_MOTIVO, []).append(oferta)

    return [
        {"motivo": motivo, "recuento": len(ofertas_del_grupo), "ofertas": ofertas_del_grupo}
        for motivo, ofertas_del_grupo in sorted(
            grupos.items(), key=lambda par: (-len(par[1]), par[0])
        )
    ]


def _fallidas(sesion: Session) -> list[Job]:
    """Ofertas en estado terminal `error`: agotaron los intentos y ya no vuelven solas."""
    return list(
        sesion.scalars(
            select(Job).where(Job.estado_clasificacion == ESTADO_ERROR).order_by(Job.id.desc())
        ).all()
    )


def _pagina(request: Request, sesion: Session, aviso: str | None = None) -> HTMLResponse:
    return get_plantillas().TemplateResponse(
        request,
        "runs.html",
        {
            "titulo": "Ejecuciones",
            "cerradas": cerradas_por_fuente(sesion),
            "cupo": _cupo(sesion),
            "runs": _runs(sesion),
            "descartes": _descartes(sesion),
            "fallidas": _fallidas(sesion),
            "aviso": aviso,
        },
    )


def _oferta_en_estado(sesion: Session, job_id: int, estado: str, explicacion: str) -> Job:
    """Busca la oferta y exige que esté en el estado que la acción espera.

    Se distingue el 404 del 409 a propósito: que la oferta no exista es un error de
    quien llama, pero que exista en otro estado es una acción que habría hecho daño
    —reencolar una clasificada le borraría la clasificación en el run siguiente— y
    conviene que se note en lugar de tragársela con un 200.
    """
    oferta = sesion.get(Job, job_id)
    if oferta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la oferta {job_id}.")
    if oferta.estado_clasificacion != estado:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=explicacion)
    return oferta


@router.get("/runs", response_class=HTMLResponse)
def historico(request: Request, sesion: Session = Depends(get_sesion)) -> HTMLResponse:
    return _pagina(request, sesion)


@router.post("/runs/descartes/{job_id}/reencolar", response_class=HTMLResponse)
def reencolar(
    request: Request, job_id: int, sesion: Session = Depends(get_sesion)
) -> HTMLResponse:
    """Devuelve a la cola una oferta descartada por una regla que no debía aplicarle.

    Limpia `motivo_regla` además del estado: dejarlo puesto haría que la oferta
    apareciera para siempre en la lista de descartes aunque ya no lo esté.
    """
    oferta = _oferta_en_estado(
        sesion,
        job_id,
        ESTADO_DESCARTADA_POR_REGLA,
        "Sólo se devuelven a la cola las ofertas descartadas por regla.",
    )
    oferta.estado_clasificacion = ESTADO_PENDIENTE
    oferta.motivo_regla = None
    sesion.commit()

    return _pagina(
        request,
        sesion,
        aviso=f"«{oferta.titulo}» vuelve a la cola: se clasificará en el próximo run.",
    )


@router.post("/runs/errores/{job_id}/reintentar", response_class=HTMLResponse)
def reintentar(
    request: Request, job_id: int, sesion: Session = Depends(get_sesion)
) -> HTMLResponse:
    """Devuelve a la cola una oferta que agotó los intentos de clasificación.

    Los intentos vuelven a cero: sin eso el pipeline la ve agotada nada más sacarla
    de la cola y la devuelve al estado terminal sin llegar a intentarlo.
    """
    oferta = _oferta_en_estado(
        sesion,
        job_id,
        ESTADO_ERROR,
        "Sólo se reintentan las ofertas que agotaron los intentos de clasificación.",
    )
    oferta.estado_clasificacion = ESTADO_PENDIENTE
    oferta.intentos_clasificacion = 0
    sesion.commit()

    return _pagina(
        request,
        sesion,
        aviso=f"«{oferta.titulo}» vuelve a la cola con los intentos a cero.",
    )
