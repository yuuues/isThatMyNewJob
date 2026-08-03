from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base


def crear_engine(ruta: str | None = None):
    ruta = ruta or get_settings().ruta_bd
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{ruta}")


def crear_tablas(engine) -> None:
    Base.metadata.create_all(engine)


def crear_sesion(engine) -> Session:
    return sessionmaker(bind=engine)()
