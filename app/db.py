from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base


def crear_engine(ruta: str | None = None):
    ruta = ruta or get_settings().ruta_bd
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{ruta}")


def asegura_esquema(engine) -> list[str]:
    """Añade a las tablas ya existentes las columnas que el modelo tenga de más.

    `create_all()` crea tablas nuevas pero NO altera las que ya existen. Cuando la
    tabla `decision` ganó `aplicada_en` y `actualizada_en`, cualquier base de datos
    anterior se quedó corta y el listado y el detalle de ofertas respondían 500. La
    salida fácil era pedir al usuario que borrase su base de datos; eso tira a la
    basura clasificaciones que costaron dinero y tiempo de API.

    Alembic sería desproporcionado para una herramienta local monousuario. Esto cubre
    el único cambio que ocurre en la práctica: añadir columnas.

    Las columnas se añaden siempre sin `NOT NULL`, aunque el modelo las declare
    obligatorias: SQLite no permite añadir una columna obligatoria sin valor por
    defecto a una tabla que ya tiene filas. El ORM las rellena al escribir, y las
    filas antiguas quedan a NULL, que es exactamente lo que se sabe de ellas.

    Devuelve las columnas añadidas, en formato "tabla.columna", para poder registrarlo.
    Lo que NO hace, a propósito: renombrar, borrar ni cambiar el tipo de una columna.
    Eso no se puede adivinar sin riesgo de perder datos, y aquí nadie va a mirar.
    """
    inspector = inspect(engine)
    tablas_existentes = set(inspector.get_table_names())

    pendientes: list[tuple[str, str, str]] = []
    for tabla in Base.metadata.sorted_tables:
        if tabla.name not in tablas_existentes:
            continue  # create_all() se encarga de las tablas nuevas
        actuales = {col["name"] for col in inspector.get_columns(tabla.name)}
        for columna in tabla.columns:
            if columna.name not in actuales:
                tipo = columna.type.compile(engine.dialect)
                pendientes.append((tabla.name, columna.name, tipo))

    with engine.begin() as conexion:
        for tabla, columna, tipo in pendientes:
            conexion.execute(text(f'ALTER TABLE "{tabla}" ADD COLUMN "{columna}" {tipo}'))

    return [f"{tabla}.{columna}" for tabla, columna, _ in pendientes]


def crear_tablas(engine) -> None:
    """Crea lo que falte y repara lo que se quedó atrás.

    La reparación va aquí porque es el punto por el que pasan todos los puntos de
    entrada: la web, la CLI y el scheduler.
    """
    Base.metadata.create_all(engine)
    asegura_esquema(engine)


def crear_sesion(engine) -> Session:
    return sessionmaker(bind=engine)()
