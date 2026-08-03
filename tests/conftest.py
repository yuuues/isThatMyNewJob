import os

# Se apaga ANTES de importar nada de app/, porque compose declara `env_file: .env` y eso
# inyecta las variables del .env en CUALQUIER comando del contenedor, también en pytest.
# El comentario del Dockerfile decía que poner el interruptor en el CMD bastaba para que
# la suite no heredase un scheduler encendido; es falso. Sin esta línea, un
# SCHEDULER_ACTIVO=1 en el .env hace que la suite arranque un scheduler de verdad que
# programa runs contra las APIs reales y gasta cupo de JSearch.
os.environ["SCHEDULER_ACTIVO"] = "0"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Base  # noqa: E402


@pytest.fixture
def sesion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
