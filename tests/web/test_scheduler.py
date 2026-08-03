"""Arranque conjunto de la web y el scheduler del run diario.

Lo que se prueba aquí no es que APScheduler funcione — eso es cosa suya —, sino las
dos propiedades de las que depende que este proyecto no se dispare solo:

1. **El scheduler está apagado salvo que se encienda a propósito.** Si arrancara por
   defecto, cualquier test que levante la aplicación con `TestClient` dejaría un hilo
   programando un run que llama a las APIs de verdad y gasta cupo de JSearch.
2. **Encenderlo va con el comando que arranca la web, no con el contenedor.** Por eso
   hay tests que leen el `Dockerfile` y el `compose.yaml`: `docker compose run app
   pytest` sustituye el comando, así que la suite nunca hereda el scheduler encendido.
   Mover ese ajuste a `environment:` del servicio rompería justo eso, y en silencio.

Ningún test de este fichero ejecuta un run: `_run_diario` se sustituye por un espía
antes de registrar el job.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import scheduler as scheduler_mod
from app.scheduler import AjustesScheduler, crear_scheduler, scheduler_habilitado
from app.web.deps import get_sesion
from app.web.main import crear_app

RAIZ = Path(__file__).resolve().parents[2]
DOCKERFILE = RAIZ / "Dockerfile"
COMPOSE = RAIZ / "compose.yaml"
README = RAIZ / "README.md"


@pytest.fixture
def espia_run(monkeypatch) -> list[int]:
    """Sustituye el run diario por un contador.

    Se aplica ANTES de crear el scheduler a propósito: `add_job` guarda la referencia
    a la función en el momento de registrarla, así que parchear después no serviría de
    nada y un job mal programado ejecutaría el pipeline de verdad.
    """
    llamadas: list[int] = []
    monkeypatch.setattr(scheduler_mod, "_run_diario", lambda: llamadas.append(1))
    return llamadas


def _cliente(sesion) -> TestClient:
    """Cliente sobre una aplicación nueva con la base de datos en memoria.

    No se usa la fixture `cliente` del conftest porque aquí hace falta manipular el
    entorno ANTES de que corra el ciclo de vida, y esa fixture ya lo ha corrido.
    """
    aplicacion = crear_app()
    aplicacion.dependency_overrides[get_sesion] = lambda: sesion
    return TestClient(aplicacion)


# --------------------------------------------------------------------------
# El ajuste
# --------------------------------------------------------------------------


def test_por_defecto_el_scheduler_esta_apagado(monkeypatch):
    """Sin entorno ni fichero: apagado. Es lo que protege a toda la suite."""
    monkeypatch.delenv("SCHEDULER_ACTIVO", raising=False)

    assert AjustesScheduler(_env_file=None).scheduler_activo is False


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("1", True), ("true", True), ("0", False), ("false", False)],
    ids=["uno", "true", "cero", "false"],
)
def test_el_ajuste_se_lee_del_entorno(monkeypatch, valor, esperado):
    monkeypatch.setenv("SCHEDULER_ACTIVO", valor)

    assert scheduler_habilitado() is esperado


def test_el_entorno_de_los_tests_no_enciende_el_scheduler():
    """El propio entorno donde corre esta suite debe tener el scheduler apagado.

    Si alguien mete `SCHEDULER_ACTIVO=1` en `.env` o en el servicio de compose, este
    test lo dice en vez de que la suite se ponga a lanzar runs de verdad.
    """
    assert scheduler_habilitado() is False


# --------------------------------------------------------------------------
# Arranque de la aplicación
# --------------------------------------------------------------------------


def test_la_app_arranca_y_sirve_sin_scheduler_con_el_ajuste_apagado(monkeypatch, sesion):
    monkeypatch.setenv("SCHEDULER_ACTIVO", "0")

    with _cliente(sesion) as cliente:
        respuesta = cliente.get("/")

        assert respuesta.status_code == 200
        assert cliente.app.state.scheduler is None


def test_crear_la_app_no_arranca_nada_por_si_solo(monkeypatch, espia_run):
    """Instanciar la aplicación no puede tener efectos: `main.py` la crea al importarse."""
    monkeypatch.setenv("SCHEDULER_ACTIVO", "1")

    aplicacion = crear_app()

    assert aplicacion.state.scheduler is None
    assert espia_run == []


def test_con_el_ajuste_encendido_se_registra_run_diario_a_la_hora_configurada(
    monkeypatch, espia_run, sesion
):
    monkeypatch.setenv("SCHEDULER_ACTIVO", "1")
    monkeypatch.setenv("HORA_RUN_DIARIO", "06:30")

    with _cliente(sesion) as cliente:
        planificador = cliente.app.state.scheduler

        assert planificador is not None
        assert planificador.running

        job = planificador.get_job("run_diario")
        assert job is not None

        campos = {c.name: str(c) for c in job.trigger.fields}
        assert campos["hour"] == "6"
        assert campos["minute"] == "30"


def test_arrancar_la_web_no_lanza_un_run(monkeypatch, espia_run, sesion):
    """Levantar la web programa el run; no lo ejecuta. Si no, cada reinicio gastaría cupo."""
    monkeypatch.setenv("SCHEDULER_ACTIVO", "1")
    # Muy lejos de cualquier "ahora", para que ni por casualidad toque disparar.
    monkeypatch.setenv("HORA_RUN_DIARIO", "04:17")

    with _cliente(sesion) as cliente:
        cliente.get("/")

    assert espia_run == []


def test_al_cerrar_la_app_el_scheduler_se_para(monkeypatch, espia_run, sesion):
    """Un scheduler que sobrevive al proceso web deja hilos sueltos y runs fantasma."""
    monkeypatch.setenv("SCHEDULER_ACTIVO", "1")

    with _cliente(sesion) as cliente:
        planificador = cliente.app.state.scheduler

    assert planificador.running is False


def test_crear_scheduler_devuelve_el_planificador_parado(espia_run):
    """Quien lo crea decide cuándo arranca; crearlo no puede tener efectos por sí solo."""
    planificador = crear_scheduler()

    assert planificador.running is False
    assert planificador.get_job("run_diario") is not None


# --------------------------------------------------------------------------
# Arranque en Docker
# --------------------------------------------------------------------------


def test_el_contenedor_sirve_en_el_8000_y_el_puerto_del_host_es_configurable():
    """El puerto de dentro del contenedor es fijo; el del host, no.

    El 8000 es de los puertos mas disputados en una maquina de desarrollo: en la
    maquina donde se probo esto ya lo ocupaba otro proyecto y `docker compose up`
    fallaba con "port is already allocated". Publicarlo fijo convertia el primer
    arranque en un fallo para cualquiera que tuviese algo ahi.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "app.web.main:app" in dockerfile
    assert "--port 8000" in dockerfile

    servicio = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["app"]
    publicado = servicio["ports"][0]
    assert publicado.endswith(":8000"), "dentro del contenedor uvicorn escucha en el 8000"
    assert "PUERTO_WEB" in publicado, "el puerto del host debe poder cambiarse sin tocar compose"
    assert ":-" in publicado, "y debe traer un valor por defecto para funcionar sin configurar nada"


def test_el_scheduler_se_enciende_con_el_comando_y_no_con_el_contenedor():
    """El ajuste viaja con el comando de arranque de la web, no con el servicio.

    Si estuviera en `environment:` del servicio, `docker compose run --rm app pytest`
    lo heredaría y la suite entera correría con el scheduler encendido.
    """
    assert "SCHEDULER_ACTIVO" in DOCKERFILE.read_text(encoding="utf-8")

    servicio = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["app"]
    entorno = servicio.get("environment") or {}
    claves = entorno.keys() if isinstance(entorno, dict) else [e.split("=")[0] for e in entorno]

    assert "SCHEDULER_ACTIVO" not in claves
    assert "command" not in servicio, "un command en el servicio lo heredaría también `run`"


# --------------------------------------------------------------------------
# README
# --------------------------------------------------------------------------


def test_el_readme_explica_arranque_uso_y_el_borrado_de_la_base_anterior():
    """El README es la única puerta de entrada para quien llega nuevo.

    Se comprueban las cuatro cosas sin las que no puede empezar: cómo levantar la web,
    cómo meter el CV, dónde se configuran las preferencias y que la base de datos
    anterior hay que borrarla porque `create_all` no altera tablas ya creadas.
    """
    readme = README.read_text(encoding="utf-8")

    assert "docker compose up" in readme
    assert "app.cli cv" in readme
    assert "http://localhost:8100" in readme
    assert "/preferences" in readme
    assert "data/app.db" in readme
    assert "create_all" in readme


def test_la_suite_nunca_corre_con_el_scheduler_encendido():
    """Guardia contra el escenario real: compose declara env_file: .env, así que un
    SCHEDULER_ACTIVO=1 en el .env se inyecta también en pytest. Sin apagarlo en
    tests/conftest.py, la suite arrancaría un scheduler que programa runs contra las
    APIs reales y gasta cupo de JSearch."""
    import os

    assert os.environ.get("SCHEDULER_ACTIVO") == "0"
    assert scheduler_habilitado() is False
