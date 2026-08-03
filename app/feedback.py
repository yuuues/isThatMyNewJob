from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.decisiones import NEGATIVO, POSITIVO, signo_estado
from app.models import Decision, Job

# Presupuesto del few-shot, en caracteres. Se mide en caracteres y no en tokens
# porque no hace falta la precisión: sobra con acotar el crecimiento. Como regla
# aproximada, 4 caracteres ~ 1 token, así que 3000 son unos 750 tokens.
#
# El presupuesto vive aquí y sólo aquí: `classify.py` se limita a renderizar lo
# que reciba. Un límite repartido entre dos capas es un límite que nadie entiende,
# y el bloque de ejemplos se puede consultar desde otros sitios (una vista de
# depuración, por ejemplo) que también deben recibirlo ya acotado.
PRESUPUESTO_CARACTERES = 3000
MAX_CARACTERES_MOTIVO = 300
MAX_CARACTERES_TITULO = 120
MAX_CARACTERES_EMPRESA = 80

# Coste de lo que `classify.py` añade alrededor de los datos al renderizar el
# bloque: la cabecera fija de la sección y la decoración de cada línea. Ambos
# están sobrestimados a propósito (la decoración real ronda los 22 caracteres),
# para que el bloque impreso nunca sea mayor que el presupuesto contabilizado.
_COSTE_CABECERA = 120
_COSTE_LINEA = 40


@dataclass(frozen=True)
class EjemploDecision:
    titulo: str
    empresa: str
    estado: str
    motivo: str

    @property
    def positivo(self) -> bool:
        return signo_estado(self.estado) == POSITIVO

    @property
    def negativo(self) -> bool:
        return signo_estado(self.estado) == NEGATIVO


def _trunca(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    return texto[: maximo - 1].rstrip() + "…"


def _coste(ejemplo: EjemploDecision) -> int:
    return (
        _COSTE_LINEA
        + len(ejemplo.titulo)
        + len(ejemplo.empresa)
        + len(ejemplo.estado)
        + len(ejemplo.motivo)
    )


def coste_ejemplos(ejemplos: list[EjemploDecision]) -> int:
    """Caracteres que ocuparán estos ejemplos en el prompt, con margen."""
    if not ejemplos:
        return 0
    return _COSTE_CABECERA + sum(_coste(e) for e in ejemplos)


def _recorta_al_presupuesto(
    positivos: list[EjemploDecision], negativos: list[EjemploDecision]
) -> list[EjemploDecision]:
    """Quita ejemplos hasta caber en el presupuesto, siempre del lado más numeroso.

    Recortar por la cola del lado mayor mantiene el equilibrio que acaba de
    calcular la selección: si sobran positivos se van positivos, y en caso de
    empate se alterna. Se recorta después de equilibrar, no antes, para que el
    presupuesto no decida qué signo sobrevive.
    """
    pos, neg = list(positivos), list(negativos)
    while (pos or neg) and coste_ejemplos(pos + neg) > PRESUPUESTO_CARACTERES:
        if len(pos) >= len(neg):
            pos.pop()
        else:
            neg.pop()
    return pos + neg


def ejemplos_few_shot(sesion: Session, maximo: int = 8) -> list[EjemploDecision]:
    """Decisiones recientes con motivo escrito, equilibradas entre positivas y negativas.

    Las decisiones sin motivo se ignoran: no enseñan nada al modelo, sólo gastan tokens.
    El equilibrio evita que una racha de descartes convierta al clasificador en un
    descartador sistemático.

    No todo estado es un ejemplo. `rechazado_por_ellos` se queda fuera de los dos
    lados, ni positivo ni negativo: es una decisión de la empresa, no del
    candidato, y meterla entre los negativos enseñaría al modelo a esconder
    ofertas que encajaban. El reparto lo decide `decisiones.signo_estado()`.

    El tamaño está acotado por dos vías: cada campo se trunca a su máximo y el
    conjunto se recorta hasta caber en `PRESUPUESTO_CARACTERES`. Sin lo segundo,
    limitar el número de ejemplos no limita nada: ocho motivos largos inflan el
    prompt igual que ochenta cortos.
    """
    filas = sesion.execute(
        select(Decision, Job)
        .join(Job, Job.id == Decision.job_id)
        .where(Decision.motivo != "")
        .order_by(Decision.creada_en.desc())
    ).all()

    positivos: list[EjemploDecision] = []
    negativos: list[EjemploDecision] = []
    for decision, job in filas:
        signo = signo_estado(decision.estado)
        if signo not in (POSITIVO, NEGATIVO):
            continue
        ejemplo = EjemploDecision(
            titulo=_trunca(job.titulo, MAX_CARACTERES_TITULO),
            empresa=_trunca(job.empresa, MAX_CARACTERES_EMPRESA),
            estado=decision.estado,
            motivo=_trunca(decision.motivo, MAX_CARACTERES_MOTIVO),
        )
        (positivos if signo == POSITIVO else negativos).append(ejemplo)

    # Se reserva media cuota para los negativos, los positivos llenan el resto y
    # después los negativos se expanden hasta el hueco que hayan dejado. Así el
    # total nunca pasa de `maximo` y, si un lado escasea, el otro rellena.
    reserva_negativos = maximo // 2
    hueco_positivos = maximo - len(negativos[:reserva_negativos])
    tomados_pos = positivos[:hueco_positivos]
    tomados_neg = negativos[: maximo - len(tomados_pos)]

    return _recorta_al_presupuesto(tomados_pos, tomados_neg)
