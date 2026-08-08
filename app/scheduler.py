"""Programación del run diario.

El scheduler vive dentro del mismo proceso que la web (`app/web/main.py` lo arranca
en su ciclo de vida) porque esto es una herramienta local monousuario: un segundo
contenedor sólo para un cron diario sería más piezas de las que hacen falta.

**El interruptor está apagado por defecto y eso no es un detalle.** Con él encendido,
cualquier test que levante la aplicación con `TestClient` dejaría programado un run
que llama a las APIs de verdad y descuenta cupo de JSearch. Por eso `SCHEDULER_ACTIVO`
se enciende en el `CMD` que arranca la web, junto al comando y no en el entorno del
contenedor: `docker compose run --rm app pytest` sustituye el comando, así que la
suite nunca lo hereda.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.cli import comando_run
from app.config import get_settings

log = logging.getLogger(__name__)

# Una sola fuente para la zona: estaba escrita en el scheduler, ausente en el trigger y
# repetida en el mensaje del log, que es justo lo que permitió que dijeran cosas distintas.
ZONA = "Europe/Madrid"


class AjustesScheduler(BaseSettings):
    """El interruptor del scheduler, leído del entorno o de `.env`.

    Vive aquí y no en `app/config.py` a propósito: es un ajuste del proceso web (¿me
    programo el run o no?), no de la configuración del dominio, y quien lea este
    módulo tiene que ver el valor por defecto sin ir a buscarlo a otro fichero.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    scheduler_activo: bool = False


def scheduler_habilitado() -> bool:
    """Si el proceso actual debe programar el run diario."""
    return AjustesScheduler().scheduler_activo


def _run_diario() -> None:
    comando_run(args=None)


def crear_scheduler() -> BackgroundScheduler:
    """Programa el run diario a la hora indicada en HORA_RUN_DIARIO (formato HH:MM).

    Devuelve el planificador PARADO: arrancarlo es decisión de quien lo crea, y así
    construirlo en un test no pone en marcha ningún hilo.
    """
    settings = get_settings()
    hora, minuto = (int(p) for p in settings.hora_run_diario.split(":"))

    scheduler = BackgroundScheduler(timezone=ZONA)
    scheduler.add_job(
        _run_diario,
        # La zona va en el TRIGGER, no sólo en el scheduler. Un `CronTrigger` construido
        # suelto usa la zona local del proceso, y la del scheduler sólo se aplica a los
        # triggers que él mismo crea. Dentro del contenedor la local es UTC, así que
        # durante meses esto disparó a las 07:00 UTC —las 09:00 de Madrid en verano—
        # mientras el log decía "(Europe/Madrid)".
        trigger=CronTrigger(hour=hora, minute=minuto, timezone=ZONA),
        id="run_diario",
        replace_existing=True,
        # Si un run se alarga más de un día, el siguiente se salta en vez de solaparse:
        # dos runs a la vez duplicarían el gasto de cupo sobre las mismas búsquedas.
        max_instances=1,
        # Sin esto el run diario no se ejecutaba NUNCA. Es una herramienta de escritorio
        # y el equipo está apagado o suspendido a las siete de la mañana: cuando vuelve,
        # APScheduler ve que la hora pasó hace horas y con su `misfire_grace_time` de un
        # segundo la descarta en silencio. Medido en los logs: "was missed by 7:03:49" y
        # "was missed by 8:39:20" en días consecutivos.
        #
        # `None` significa "ejecútalo por tarde que sea". Es lo que se quiere aquí: el
        # run del día importa, la hora exacta no.
        misfire_grace_time=None,
        # Y si el equipo pasa tres días apagado, al volver se ejecuta UNA vez y no tres
        # seguidas gastando el triple de cupo sobre las mismas búsquedas.
        coalesce=True,
    )
    log.info("Run diario programado a las %02d:%02d (%s)", hora, minuto, ZONA)
    return scheduler
