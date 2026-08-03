from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.cli import comando_run
from app.config import get_settings


def _run_diario() -> None:
    comando_run(args=None)


def crear_scheduler() -> BackgroundScheduler:
    """Programa el run diario a la hora indicada en HORA_RUN_DIARIO (formato HH:MM)."""
    settings = get_settings()
    hora, minuto = (int(p) for p in settings.hora_run_diario.split(":"))

    scheduler = BackgroundScheduler(timezone="Europe/Madrid")
    scheduler.add_job(
        _run_diario,
        trigger=CronTrigger(hour=hora, minute=minuto),
        id="run_diario",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler
