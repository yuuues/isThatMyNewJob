"""Devuelve a la cola ofertas ya juzgadas, para rehacerlas con reglas o modelo nuevos.

Este módulo NO clasifica: marca y sale. El trabajo lo hace el bucle de `ejecuta_run()`,
que ya sabe hacerlo, respeta el tope por run y registra los fallos. Duplicar aquí la
llamada al modelo sería mantener dos caminos para lo mismo.

Existe porque el veredicto guardado no dice sólo "encaja o no": dice con qué prompt y con
qué modelo se decidió eso, y esos dos cambian. `oferta.html` muestra ambos justamente para
que se pueda saber cuándo un veredicto se ha quedado viejo.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Clasificacion, Decision, Job


def marca_para_reclasificar(sesion: Session, *, saltar_decididas: bool = True) -> int:
    """Devuelve a "pendiente" las ofertas ya clasificadas. Da cuántas marcó.

    Las que el usuario ya decidió a mano se saltan por defecto: su veredicto ya no manda
    nada, y reabrirlas sólo las devolvería a la lista de revisión.

    Sólo entran las que tienen un veredicto del modelo. Las `descartada_por_regla` se
    quedan fuera a propósito: el prefiltro no cambia en esta versión, así que volverían a
    pasar por la misma regla con los mismos datos y saldrían descartadas igual. Marcarlas
    sería trasiego con un recuento que engaña.

    `intentos_clasificacion` vuelve a cero por el mismo motivo que en `reintentar()` de
    app/web/routes_runs.py: sin eso el pipeline ve la oferta agotada nada más sacarla de
    la cola y la devuelve al estado terminal sin llegar a intentarlo.
    """
    decididas = {d.job_id for d in sesion.scalars(select(Decision)).all()}

    marcadas = 0
    for job in sesion.scalars(
        select(Job).where(Job.estado_clasificacion == "clasificada")
    ).all():
        if saltar_decididas and job.id in decididas:
            continue

        sesion.execute(delete(Clasificacion).where(Clasificacion.job_id == job.id))
        job.estado_clasificacion = "pendiente"
        job.intentos_clasificacion = 0
        marcadas += 1

    sesion.commit()
    return marcadas
