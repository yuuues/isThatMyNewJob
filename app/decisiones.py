"""Estados de una decisión y memoria de lo que ya se decidió con cada empresa.

Dominio puro: aquí vive el vocabulario de estados, la escritura de decisiones y
la consulta del historial por empresa. Ni la web ni `feedback.py` deben repetir
estas reglas; las importan de aquí.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dedup import normaliza_empresa
from app.models import Decision, Job, ahora

ESTADO_GUARDADA = "guardada"
ESTADO_APLICADA = "aplicada"
ESTADO_EN_PROCESO = "en_proceso"
ESTADO_RECHAZADO_POR_ELLOS = "rechazado_por_ellos"
ESTADO_DESCARTADA_POR_MI = "descartada_por_mi"

# En el orden en que avanza una candidatura, que es el orden en que conviene
# pintarlos en la interfaz.
ESTADOS: tuple[str, ...] = (
    ESTADO_GUARDADA,
    ESTADO_APLICADA,
    ESTADO_EN_PROCESO,
    ESTADO_RECHAZADO_POR_ELLOS,
    ESTADO_DESCARTADA_POR_MI,
)

ETIQUETAS: dict[str, str] = {
    ESTADO_GUARDADA: "Me interesa, aún no he hecho nada",
    ESTADO_APLICADA: "Me he presentado",
    ESTADO_EN_PROCESO: "Hay conversación en marcha",
    ESTADO_RECHAZADO_POR_ELLOS: "La empresa me ha descartado",
    ESTADO_DESCARTADA_POR_MI: "No me interesa",
}

# Qué enseña cada estado al clasificador. Este reparto es el motivo de que este
# módulo exista.
#
# `rechazado_por_ellos` NO es un ejemplo negativo. Que una empresa rechace al
# candidato no dice nada sobre lo que el candidato quiere: dice que encajaba lo
# bastante como para presentarse. Contarlo como negativo enseñaría al
# clasificador a esconder justo las ofertas que mejor encajan, y el fallo sería
# invisible: no habría error, simplemente dejarían de aparecer buenas ofertas.
ESTADOS_POSITIVOS = frozenset({ESTADO_GUARDADA, ESTADO_APLICADA, ESTADO_EN_PROCESO})
ESTADOS_NEGATIVOS = frozenset({ESTADO_DESCARTADA_POR_MI})
ESTADOS_IGNORADOS = frozenset({ESTADO_RECHAZADO_POR_ELLOS})

POSITIVO = "positivo"
NEGATIVO = "negativo"
IGNORADO = "ignorado"


class EstadoDesconocido(ValueError):
    """Se intentó guardar un estado que no está en `ESTADOS`."""


class OfertaNoEncontrada(LookupError):
    """Se intentó decidir sobre una oferta que no existe."""


def signo_estado(estado: str) -> str:
    """Qué signo tiene un estado como ejemplo para el clasificador.

    Lo desconocido se ignora en vez de caer del lado negativo: si mañana aparece
    un estado nuevo y nadie lo clasifica, el coste de ignorarlo es perder un
    ejemplo; el de tomarlo por negativo es enseñar al modelo a descartar mal.
    """
    if estado in ESTADOS_POSITIVOS:
        return POSITIVO
    if estado in ESTADOS_NEGATIVOS:
        return NEGATIVO
    return IGNORADO


def registra_decision(
    sesion: Session, job_id: int, estado: str, motivo: str = ""
) -> Decision:
    """Crea o actualiza la decisión sobre una oferta y devuelve la fila.

    `decision.job_id` es único: decidir dos veces sobre la misma oferta tiene que
    actualizar, no insertar. Confirma la transacción, porque una decisión es un
    gesto completo del usuario y no la mitad de nada.

    `aplicada_en` se fija la primera vez que la decisión llega a `aplicada` y no
    se vuelve a tocar. Es la fecha en la que uno se presentó, no la del último
    cambio de estado: si se moviera, el recuento de candidaturas del mes mentiría
    en cuanto la empresa contestara.
    """
    if estado not in ESTADOS:
        raise EstadoDesconocido(f"Estado de decisión desconocido: {estado!r}")

    job = sesion.get(Job, job_id)
    if job is None:
        raise OfertaNoEncontrada(f"No existe la oferta {job_id}")

    decision = sesion.execute(
        select(Decision).where(Decision.job_id == job_id)
    ).scalar_one_or_none()

    if decision is None:
        decision = Decision(job_id=job_id, estado=estado, motivo=motivo)
        sesion.add(decision)
    else:
        decision.estado = estado
        decision.motivo = motivo

    decision.actualizada_en = ahora()
    if estado == ESTADO_APLICADA and decision.aplicada_en is None:
        decision.aplicada_en = decision.actualizada_en

    sesion.commit()
    return decision


@dataclass(frozen=True)
class EntradaHistorial:
    """Una decisión previa con una empresa, tal y como se enseña en la interfaz."""

    job_id: int
    titulo: str
    empresa: str  # nombre tal como lo publicó la fuente, no el normalizado
    estado: str
    motivo: str
    decidida_en: datetime
    aplicada_en: datetime | None


def clave_empresa(empresa: str | None) -> str:
    """Nombre canónico bajo el que se agrupa el historial de una empresa.

    Se usa `normaliza_empresa()`, la misma función que deduplica ofertas, para
    que 'Acme S.L.' y 'ACME SL' cuenten como la misma empresa aquí y allí.
    """
    return normaliza_empresa(empresa)


def historial_por_empresa(
    sesion: Session, ofertas: Iterable[Job]
) -> dict[str, list[EntradaHistorial]]:
    """Decisiones previas con las empresas de estas ofertas, de reciente a antigua.

    El diccionario incluye una entrada por cada empresa presente en `ofertas`,
    con lista vacía si no hay historial: una empresa desconocida es un caso
    normal, no un error, y el que pinta la fila no debería tener que protegerse.

    Devuelve todas las decisiones de esas empresas, incluida la de la propia
    oferta si la tiene. Usa `historial_de()` para el historial *previo* de una
    oferta concreta.
    """
    claves = {clave_empresa(o.empresa) for o in ofertas}
    historial: dict[str, list[EntradaHistorial]] = {clave: [] for clave in claves}
    if not claves:
        return historial

    filas = sesion.execute(
        select(Decision, Job)
        .join(Job, Job.id == Decision.job_id)
        .order_by(Decision.actualizada_en.desc(), Decision.id.desc())
    ).all()

    # El filtrado por empresa se hace en Python y no en SQL porque la clave es
    # `normaliza_empresa()`, que SQLite no sabe calcular. El volumen es de unos
    # cientos de decisiones: no compensa duplicar esa lógica en la base de datos.
    for decision, job in filas:
        clave = clave_empresa(job.empresa)
        if clave not in historial:
            continue
        historial[clave].append(
            EntradaHistorial(
                job_id=job.id,
                titulo=job.titulo,
                empresa=job.empresa,
                estado=decision.estado,
                motivo=decision.motivo,
                decidida_en=decision.actualizada_en,
                aplicada_en=decision.aplicada_en,
            )
        )
    return historial


def historial_de(
    historial: dict[str, list[EntradaHistorial]], oferta: Job
) -> list[EntradaHistorial]:
    """Historial previo de la empresa de una oferta, sin la decisión de esa oferta.

    La fila ya muestra su propia decisión; repetirla como historial haría creer
    que hay dos candidaturas donde sólo hay una.
    """
    entradas = historial.get(clave_empresa(oferta.empresa), [])
    return [e for e in entradas if e.job_id != oferta.id]


@dataclass(frozen=True)
class ResumenCandidaturas:
    periodo: str  # "YYYY-MM"
    aplicadas: int
    en_proceso: int


def periodo_actual() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def resumen_candidaturas(sesion: Session, periodo: str | None = None) -> ResumenCandidaturas:
    """Cuántas candidaturas se presentaron en el periodo y cuántas siguen vivas.

    `aplicadas` se cuenta por `aplicada_en` y no por el estado actual: una
    candidatura de este mes que ya recibió respuesta se presentó igualmente.
    `en_proceso` es un estado del presente, así que no se filtra por periodo.
    """
    periodo = periodo or periodo_actual()

    decisiones = sesion.execute(select(Decision)).scalars().all()
    # El filtro por mes se hace en Python: las fechas se guardan como texto ISO
    # y comparar rangos en SQL obligaría a construir los límites del mes a mano.
    aplicadas = sum(
        1 for d in decisiones if d.aplicada_en and d.aplicada_en.strftime("%Y-%m") == periodo
    )
    en_proceso = sum(1 for d in decisiones if d.estado == ESTADO_EN_PROCESO)
    return ResumenCandidaturas(periodo=periodo, aplicadas=aplicadas, en_proceso=en_proceso)
