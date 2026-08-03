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

    scheduler = BackgroundScheduler(timezone="Europe/Madrid")
    scheduler.add_job(
        _run_diario,
        trigger=CronTrigger(hour=hora, minute=minuto),
        id="run_diario",
        replace_existing=True,
        # Si un run se alarga más de un día, el siguiente se salta en vez de solaparse:
        # dos runs a la vez duplicarían el gasto de cupo sobre las mismas búsquedas.
        max_instances=1,
    )
    log.info("Run diario programado a las %02d:%02d (Europe/Madrid)", hora, minuto)
    return scheduler
