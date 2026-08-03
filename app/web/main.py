"""Aplicación web: instancia FastAPI, estáticos y montaje de rutas.

Este fichero no debería volver a tocarse para añadir vistas. Los tres módulos de
rutas ya están incluidos, así que cada uno crece por su cuenta.

Dos decisiones que conviene no deshacer sin pensarlo:

- **Los routers se incluyen sin prefijo.** Cada módulo declara rutas completas
  (`/`, `/job/{id}`, `/profile`, `/preferences`, `/searches`, `/runs`, tal como
  las nombra el spec). Un prefijo aquí obligaría a coordinar dos ficheros para
  cambiar una URL.
- **La portada provisional se registra DESPUÉS de los routers.** FastAPI resuelve
  por orden de registro, así que en cuanto `routes_ofertas` declare `/`, la suya
  gana y ésta queda inerte sin tener que borrarla ni editar este fichero.

- **El scheduler se arranca en el ciclo de vida, no al construir la aplicación.**
  Construir la app es lo que hace `crear_app()` en cada test y al importar este
  módulo; sólo levantar el servidor de verdad debe programar el run diario. Y aun
  entonces, sólo si `SCHEDULER_ACTIVO` está encendido (ver `app/scheduler.py`).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.scheduler import crear_scheduler, scheduler_habilitado
from app.web import routes_config, routes_ofertas, routes_runs
from app.web.deps import DIR_ESTATICOS, get_plantillas

log = logging.getLogger(__name__)


@asynccontextmanager
async def _ciclo_de_vida(aplicacion: FastAPI) -> AsyncIterator[None]:
    """Arranca el scheduler al levantar el servidor y lo para al cerrarlo.

    El planificador se deja colgado de `app.state` (y no en una variable de módulo)
    porque cada test crea su propia aplicación con `crear_app()`: así ninguna hereda
    el scheduler de otra, y una vista futura puede consultarlo por la petición.

    Al cerrar no se espera a que termine un run en curso: la parada de la web no
    puede quedarse colgada media hora clasificando ofertas. El run se reanudará
    entero mañana; cortarlo a mitad no pierde nada porque cada oferta se persiste
    según se procesa.
    """
    if scheduler_habilitado():
        aplicacion.state.scheduler = crear_scheduler()
        aplicacion.state.scheduler.start()
    else:
        log.info("Scheduler apagado (SCHEDULER_ACTIVO): la web no programará el run diario")

    try:
        yield
    finally:
        planificador = getattr(aplicacion.state, "scheduler", None)
        if planificador is not None:
            planificador.shutdown(wait=False)


def crear_app() -> FastAPI:
    """Construye una aplicación nueva.

    Existe como fábrica y no sólo como instancia de módulo para que cada test pueda
    partir de una aplicación limpia, sin arrastrar las sustituciones de dependencias
    de otro.
    """
    aplicacion = FastAPI(
        title="isThatMyNewJob",
        docs_url=None,
        redoc_url=None,
        lifespan=_ciclo_de_vida,
    )

    # Siempre presente, aunque el ciclo de vida no llegue a correr: quien lo consulte
    # no tiene que distinguir entre "no hay scheduler" y "aún no se ha arrancado".
    aplicacion.state.scheduler = None

    aplicacion.mount("/static", StaticFiles(directory=str(DIR_ESTATICOS)), name="static")

    aplicacion.include_router(routes_ofertas.router)
    aplicacion.include_router(routes_config.router)
    aplicacion.include_router(routes_runs.router)

    @aplicacion.get("/", response_class=HTMLResponse)
    def portada(request: Request) -> HTMLResponse:
        """Portada mínima mientras el listado de ofertas no exista."""
        return get_plantillas().TemplateResponse(
            request,
            "base.html",
            {"titulo": "Ofertas"},
        )

    return aplicacion


app = crear_app()
