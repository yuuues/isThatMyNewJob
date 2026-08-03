"""Fixtures compartidas por TODOS los tests de la web.

Reglas que este fichero garantiza:

- Ningún test de web toca `data/app.db`. La base de datos es SQLite en memoria y
  muere con el test.
- El cliente HTTP y el test comparten la MISMA sesión, así que lo que escribe una
  ruta se ve desde el test sin recargar nada, y al revés.

Este fichero es contrato entre varios agentes: las fixtures son genéricas a
propósito y no deben especializarse para una vista concreta.
"""

import itertools
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, Job, Perfil, PreferenciasRow
from app.schemas import Preferencias, PerfilCandidato, SkillPerfil
from app.web.deps import get_sesion
from app.web.main import crear_app


@pytest.fixture
def motor():
    """Motor SQLite en memoria compartido entre hilos.

    `StaticPool` + `check_same_thread=False` son imprescindibles: `TestClient`
    atiende las peticiones en otro hilo, y con el pool por defecto cada hilo
    abriría una base de datos en memoria distinta y vacía.
    """
    motor = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(motor)
    yield motor
    motor.dispose()


@pytest.fixture
def sesion(motor) -> Iterator[Session]:
    """Sesión de la base de datos en memoria.

    Sombrea a propósito la fixture `sesion` de `tests/conftest.py`: en los tests de
    web la sesión tiene que ser la misma que usa el cliente HTTP, no otra distinta.
    """
    with Session(motor) as sesion:
        yield sesion


@pytest.fixture
def cliente(sesion) -> Iterator[TestClient]:
    """Cliente HTTP contra una aplicación nueva con la sesión sustituida.

    Se construye con `crear_app()` en vez de reutilizar `app.web.main.app` para que
    los `dependency_overrides` de un test no se filtren a otro.
    """
    aplicacion = crear_app()
    aplicacion.dependency_overrides[get_sesion] = lambda: sesion
    with TestClient(aplicacion) as cliente:
        yield cliente
    aplicacion.dependency_overrides.clear()


PERFIL_SEMILLA = PerfilCandidato(
    anios_experiencia=8,
    titulo_actual="Desarrollador backend",
    roles=["backend", "datos"],
    skills=[
        SkillPerfil(nombre="Python", nivel="alto", anios=8),
        SkillPerfil(nombre="SQL", nivel="medio", anios=5),
    ],
    sectores=["software"],
    idiomas=["es", "en"],
    formacion=["Ingeniería Informática"],
    ubicacion="Valencia",
    resumen="Backend con ocho años de experiencia en Python y bases de datos.",
)

PREFERENCIAS_SEMILLA = Preferencias(
    salario_min=45000,
    modalidades=["remoto", "hibrido"],
    zonas=["Valencia", "remoto"],
    sectores_veto=["apuestas"],
    tecnologias_veto=["php"],
    idiomas=["es", "en"],
    notas="Prefiero equipos pequeños.",
)


@pytest.fixture
def perfil_y_preferencias(sesion) -> tuple[Perfil, PreferenciasRow]:
    """Siembra un perfil vigente y unas preferencias, y devuelve ambas filas.

    Es la fixture que usan las vistas que no tienen sentido sin perfil (clasificar,
    reclasificar, prefiltro). Los valores son plausibles pero irrelevantes: si un
    test depende de uno concreto, que lo escriba él.
    """
    perfil = Perfil(
        ruta_pdf="data/cv.pdf",
        hash_pdf="huella-de-prueba",
        datos=PERFIL_SEMILLA.model_dump(),
    )
    preferencias = PreferenciasRow(datos=PREFERENCIAS_SEMILLA.model_dump())
    sesion.add_all([perfil, preferencias])
    sesion.commit()
    return perfil, preferencias


@pytest.fixture
def crea_oferta(sesion) -> Callable[..., Job]:
    """Fábrica de ofertas con valores por defecto válidos.

    Cada llamada devuelve una oferta persistida; se sobreescribe por nombre sólo lo
    que el test necesite (`crea_oferta(fuente="adzuna", titulo="...")`). Los campos
    únicos (`external_id`, `hash_dedup`) se generan solos para no chocar.
    """
    contador = itertools.count(1)

    def _crea(**campos) -> Job:
        numero = next(contador)
        datos = {
            "fuente": "fake",
            "external_id": f"ext-{numero}",
            "url": f"https://ofertas.ejemplo/{numero}",
            "titulo": f"Oferta {numero}",
            "empresa": f"Empresa {numero}",
            "ubicacion": "Valencia",
            "modalidad": "remoto",
            "descripcion": "Descripción de prueba.",
            "hash_dedup": f"hash-{numero}",
        }
        datos.update(campos)
        oferta = Job(**datos)
        sesion.add(oferta)
        sesion.commit()
        return oferta

    return _crea
