from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Decision, Job

_POSITIVOS = {"interesa", "aplicada"}


@dataclass(frozen=True)
class EjemploDecision:
    titulo: str
    empresa: str
    estado: str
    motivo: str

    @property
    def positivo(self) -> bool:
        return self.estado in _POSITIVOS


def ejemplos_few_shot(sesion: Session, maximo: int = 8) -> list[EjemploDecision]:
    """Decisiones recientes con motivo escrito, equilibradas entre positivas y negativas.

    Las decisiones sin motivo se ignoran: no enseñan nada al modelo, sólo gastan tokens.
    El equilibrio evita que una racha de descartes convierta al clasificador en un
    descartador sistemático.
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
        ejemplo = EjemploDecision(
            titulo=job.titulo,
            empresa=job.empresa,
            estado=decision.estado,
            motivo=decision.motivo,
        )
        (positivos if ejemplo.positivo else negativos).append(ejemplo)

    # Se reserva media cuota para los negativos, los positivos llenan el resto y
    # después los negativos se expanden hasta el hueco que hayan dejado. Así el
    # total nunca pasa de `maximo` y, si un lado escasea, el otro rellena.
    reserva_negativos = maximo // 2
    hueco_positivos = maximo - len(negativos[:reserva_negativos])
    tomados_pos = positivos[:hueco_positivos]
    tomados_neg = negativos[: maximo - len(tomados_pos)]

    return tomados_pos + tomados_neg
