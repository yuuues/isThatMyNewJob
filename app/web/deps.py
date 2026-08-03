"""Dependencias compartidas por todos los módulos de rutas.

Aquí viven las dos únicas cosas que necesitan todas las vistas: la sesión de base
de datos y las plantillas. Las rutas no deben crear motores ni sesiones por su
cuenta; si lo hacen, los tests dejan de poder sustituir la base de datos y acaban
escribiendo en `data/app.db`.
"""

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from app.db import crear_engine, crear_tablas

DIR_WEB = Path(__file__).resolve().parent
DIR_PLANTILLAS = DIR_WEB / "templates"
DIR_ESTATICOS = DIR_WEB / "static"


@lru_cache(maxsize=1)
def _fabrica_de_sesiones() -> sessionmaker:
    """Motor único para todo el proceso web.

    Se crea perezosamente y se cachea: importar el módulo no debe abrir la base de
    datos ni crear `data/`, porque los tests importan estas rutas sin querer tocar
    el disco. Las tablas se crean al primer uso para que arrancar la web sobre una
    base vacía funcione sin un paso previo de migración.
    """
    motor = crear_engine()
    crear_tablas(motor)
    return sessionmaker(bind=motor)


def get_sesion() -> Iterator[Session]:
    """Sesión por petición, cerrada al terminar.

    Es la dependencia que los tests sustituyen con `dependency_overrides` por una
    sesión sobre SQLite en memoria.
    """
    with _fabrica_de_sesiones()() as sesion:
        yield sesion


@lru_cache(maxsize=1)
def get_plantillas() -> Jinja2Templates:
    """Entorno Jinja2 apuntando a `app/web/templates`.

    Se expone como dependencia (y no como constante) para poder sustituirlo en un
    test que quiera plantillas propias sin tocar las de verdad.
    """
    return Jinja2Templates(directory=str(DIR_PLANTILLAS))


def es_peticion_htmx(request: Request) -> bool:
    """Si la petición viene de HTMX, la vista debe devolver el parcial, no la página.

    HTMX marca sus peticiones con la cabecera `HX-Request`. Vive aquí porque lo van
    a necesitar todas las vistas con filtros o acciones en línea.
    """
    return request.headers.get("HX-Request", "").lower() == "true"
