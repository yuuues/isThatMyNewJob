"""Búsquedas guardadas: CRUD, coste en créditos y el botón de "buscar ahora".

Tres cosas que este fichero vigila y que no son opcionales:

- **Editar actualiza de verdad.** `carga_semilla()` es idempotente por nombre y
  deja lo existente como estaba; ese defecto no puede heredarlo la web, porque
  aquí editar es la única forma de corregir una búsqueda.
- **El botón no lanza dos runs seguidos.** El aviso legal de Remotive pide un
  máximo aproximado de cuatro peticiones diarias.
- **El run no bloquea la petición HTTP.**
- **El run del botón es el mismo que el de la CLI.** Lo que se cablee en uno y no en
  el otro deja media funcionalidad muerta sin que nadie se entere.
"""

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import _busquedas_activas
from app.db import crear_engine, crear_sesion, crear_tablas
from app.models import BusquedaGuardada, Run
from app.config import get_settings
from app.web import routes_config
from app.web.routes_config import LimitadorDeRuns, get_lanzador_run

NUEVA = {
    "nombre": "backend remoto",
    "texto": "python backend",
    "pais": "es",
    "ubicacion": "Valencia",
    "solo_remoto": "on",
    "fuentes": ["adzuna", "remotive"],
    "activa": "on",
}


class Reloj:
    """Reloj de mentira, para no esperar seis horas dentro de un test."""

    def __init__(self, instante: datetime):
        self.instante = instante

    def __call__(self) -> datetime:
        return self.instante

    def avanza(self, horas: float) -> None:
        self.instante += timedelta(hours=horas)


class LanzadorFalso:
    """Doble del lanzador de runs: cuenta llamadas y no ejecuta nada."""

    def __init__(self):
        self.llamadas = 0

    def __call__(self) -> None:
        self.llamadas += 1


@pytest.fixture
def lanzador(cliente: TestClient) -> LanzadorFalso:
    doble = LanzadorFalso()
    cliente.app.dependency_overrides[get_lanzador_run] = lambda: doble
    return doble


@pytest.fixture
def reloj(cliente: TestClient) -> Reloj:
    """Sustituye el limitador de la aplicación por otro con reloj controlado."""
    falso = Reloj(datetime(2026, 8, 3, 9, 0, 0))
    cliente.app.state.limitador_runs = LimitadorDeRuns(reloj=falso)
    return falso


def _crea(sesion, **campos) -> BusquedaGuardada:
    datos = {
        "nombre": "semilla",
        "texto": "python",
        "pais": "es",
        "ubicacion": None,
        "solo_remoto": False,
        "fuentes": ["remotive"],
        "activa": True,
    }
    datos.update(campos)
    fila = BusquedaGuardada(**datos)
    sesion.add(fila)
    sesion.commit()
    return fila


# --- CRUD ---------------------------------------------------------------


def test_la_vista_lista_las_busquedas(cliente: TestClient, sesion):
    _crea(sesion, nombre="datos en remoto", texto="ingeniero de datos")

    respuesta = cliente.get("/searches")

    assert respuesta.status_code == 200
    assert "datos en remoto" in respuesta.text
    assert "ingeniero de datos" in respuesta.text


def test_la_vista_responde_sin_busquedas(cliente: TestClient):
    assert cliente.get("/searches").status_code == 200


def test_crear_una_busqueda(cliente: TestClient, sesion):
    respuesta = cliente.post("/searches", data=NUEVA)

    assert respuesta.status_code == 200
    fila = sesion.scalar(select(BusquedaGuardada))
    assert fila.nombre == "backend remoto"
    assert fila.texto == "python backend"
    assert fila.ubicacion == "Valencia"
    assert fila.solo_remoto is True
    assert fila.fuentes == ["adzuna", "remotive"]
    assert fila.activa is True


@pytest.mark.parametrize("campo", ["nombre", "texto"])
def test_crear_sin_nombre_o_sin_texto_se_rechaza(cliente: TestClient, sesion, campo):
    respuesta = cliente.post("/searches", data={**NUEVA, campo: "   "})

    assert respuesta.status_code == 400
    assert sesion.scalar(select(BusquedaGuardada)) is None


def test_editar_actualiza_de_verdad(cliente: TestClient, sesion):
    """El defecto conocido de `carga_semilla()` no se hereda."""
    fila = _crea(sesion, nombre="backend", texto="python", fuentes=["remotive"])

    respuesta = cliente.post(
        f"/searches/{fila.id}",
        data={
            "nombre": "backend senior",
            "texto": "python senior",
            "pais": "gb",
            "ubicacion": "Londres",
            "fuentes": ["adzuna", "jsearch"],
            "activa": "on",
        },
    )

    assert respuesta.status_code == 200
    sesion.refresh(fila)
    assert fila.nombre == "backend senior"
    assert fila.texto == "python senior"
    assert fila.pais == "gb"
    assert fila.ubicacion == "Londres"
    assert fila.fuentes == ["adzuna", "jsearch"]
    # Y no ha creado una segunda fila con el nombre nuevo.
    assert sesion.query(BusquedaGuardada).count() == 1


def test_editar_una_busqueda_inexistente_responde_404(cliente: TestClient):
    assert cliente.post("/searches/999", data=NUEVA).status_code == 404


def test_borrar_elimina_la_busqueda(cliente: TestClient, sesion):
    fila = _crea(sesion)

    respuesta = cliente.post(f"/searches/{fila.id}/borrar")

    assert respuesta.status_code == 200
    assert sesion.query(BusquedaGuardada).count() == 0


def test_borrar_una_busqueda_inexistente_responde_404(cliente: TestClient):
    assert cliente.post("/searches/999/borrar").status_code == 404


def test_desactivar_una_busqueda_la_excluye_del_run(cliente: TestClient, sesion):
    activa = _crea(sesion, nombre="sigue activa")
    apagada = _crea(sesion, nombre="apagada")

    cliente.post(
        f"/searches/{apagada.id}",
        data={"nombre": "apagada", "texto": "python", "pais": "es", "fuentes": ["remotive"]},
    )

    sesion.refresh(apagada)
    assert apagada.activa is False

    queries, _ = _busquedas_activas(sesion)
    assert [q.nombre for q in queries] == [activa.nombre]


# --- Créditos de JSearch ------------------------------------------------


@pytest.mark.parametrize(
    ("busquedas", "busquedas_con_jsearch"),
    [
        ([], 0),
        ([{"fuentes": ["remotive", "adzuna"]}], 0),
        ([{"fuentes": ["jsearch"]}], 1),
        ([{"fuentes": ["jsearch", "remotive"]}, {"fuentes": ["jsearch"]}], 2),
        ([{"fuentes": ["jsearch"], "activa": False}], 0),
        ([{"fuentes": ["jsearch"]}, {"fuentes": ["jsearch"], "activa": False}], 1),
    ],
    ids=["ninguna", "sin-jsearch", "una", "dos", "inactiva", "mezcla"],
)
def test_creditos_comprometidos_por_run(sesion, busquedas, busquedas_con_jsearch):
    """El coste es búsquedas x páginas, no búsquedas a secas.

    JSearch cobra un crédito por página de 10 resultados, así que subir las páginas
    multiplica el gasto. Estos tests fijaban un crédito por búsqueda y se pusieron en
    rojo al pasar a dos páginas: decían "una búsqueda cuesta uno" cuando la regla real
    siempre fue "una página cuesta uno".
    """
    for numero, campos in enumerate(busquedas):
        _crea(sesion, nombre=f"b{numero}", **campos)

    paginas = get_settings().jsearch_paginas
    assert routes_config.creditos_por_run(sesion) == busquedas_con_jsearch * paginas


def test_creditos_al_mes_son_los_del_run_por_los_dias(sesion):
    _crea(sesion, nombre="con jsearch", fuentes=["jsearch"])

    coste = routes_config.coste_jsearch(sesion)
    paginas = get_settings().jsearch_paginas

    assert coste["por_run"] == paginas
    assert coste["al_mes"] == routes_config.RUNS_AL_MES * paginas
    assert coste["limite"] > 0


def test_la_vista_muestra_el_coste_en_creditos(cliente: TestClient, sesion):
    _crea(sesion, nombre="con jsearch", fuentes=["jsearch"])

    texto = cliente.get("/searches").text

    assert "crédito" in texto
    assert str(routes_config.RUNS_AL_MES) in texto


# --- Buscar ahora -------------------------------------------------------


def test_buscar_ahora_lanza_un_run(cliente: TestClient, lanzador, reloj):
    respuesta = cliente.post("/searches/buscar")

    assert respuesta.status_code == 200
    assert lanzador.llamadas == 1


def test_pulsar_buscar_ahora_dos_veces_seguidas_no_lanza_dos_runs(
    cliente: TestClient, lanzador, reloj
):
    cliente.post("/searches/buscar")
    respuesta = cliente.post("/searches/buscar")

    assert respuesta.status_code == 200
    assert lanzador.llamadas == 1
    assert "espera" in respuesta.text.lower()


def test_pasado_el_intervalo_se_puede_volver_a_lanzar(cliente: TestClient, lanzador, reloj):
    cliente.post("/searches/buscar")
    reloj.avanza(6)
    cliente.post("/searches/buscar")

    assert lanzador.llamadas == 2


def test_como_mucho_cuatro_runs_manuales_en_veinticuatro_horas(
    cliente: TestClient, lanzador, reloj
):
    """El aviso legal de Remotive pide un máximo aproximado de 4 peticiones al día."""
    for _ in range(24):
        cliente.post("/searches/buscar")
        reloj.avanza(1)

    assert lanzador.llamadas == 4


def test_no_se_lanza_si_ya_hay_un_run_en_curso(cliente: TestClient, sesion, lanzador, reloj):
    sesion.add(Run(inicio=reloj.instante - timedelta(minutes=5)))
    sesion.commit()

    respuesta = cliente.post("/searches/buscar")

    assert respuesta.status_code == 200
    assert lanzador.llamadas == 0
    assert "en curso" in respuesta.text.lower()


def test_un_run_antiguo_sin_cerrar_no_bloquea_para_siempre(
    cliente: TestClient, sesion, lanzador, reloj
):
    """Un proceso muerto deja `fin` a nulo. No puede inutilizar el botón."""
    sesion.add(Run(inicio=reloj.instante - timedelta(days=2)))
    sesion.commit()

    cliente.post("/searches/buscar")

    assert lanzador.llamadas == 1


def test_lanzar_en_segundo_plano_no_bloquea_la_peticion():
    """El run tarda minutos: la respuesta HTTP no puede esperarlo."""
    puerta = threading.Event()
    terminado = threading.Event()

    def tarda():
        puerta.wait(5)
        terminado.set()

    hilo = routes_config.lanza_en_segundo_plano(tarda)

    assert not terminado.is_set(), "lanzar el run ha bloqueado hasta que terminó"
    puerta.set()
    hilo.join(timeout=5)
    assert terminado.is_set()


def test_el_run_del_boton_cablea_el_enriquecedor_y_su_cupo(monkeypatch, tmp_path):
    """El botón lanza el run de verdad, y nadie lo ejecutaba en la suite: los demás
    tests sustituyen el lanzador por un doble, a propósito. Eso dejaba el cableado del
    scraper de Adzuna sin cubrir por este lado, que es la mitad de la feature.

    Aquí se llama a `_ejecuta_run_completo()` en directo, con todo lo que sale fuera
    (base de datos, proveedor y fuentes) sustituido. El cupo se comprueba con un valor
    que NO es el de por defecto: con 40 a los dos lados, olvidar el parámetro daría el
    mismo resultado que pasarlo.
    """
    ruta_bd = tmp_path / "app.db"
    monkeypatch.setenv("RUTA_BD", str(ruta_bd))
    monkeypatch.setenv("ADZUNA_SCRAPE_ACTIVO", "1")
    monkeypatch.setenv("ADZUNA_SCRAPE_MAX_POR_RUN", "7")

    engine = crear_engine(str(ruta_bd))
    crear_tablas(engine)
    with crear_sesion(engine) as sesion_semilla:
        sesion_semilla.add(BusquedaGuardada(nombre="php", texto="php", fuentes=[]))
        sesion_semilla.commit()

    kwargs_vistos = {}

    def espia(sesion, **kwargs):
        kwargs_vistos.update(kwargs)
        return SimpleNamespace(id=1, stats={}, errores=[])

    # Los importes de `_ejecuta_run_completo()` son diferidos: se resuelven al llamarla,
    # así que sustituir el atributo del módulo de origen basta y sobra.
    monkeypatch.setattr("app.pipeline.ejecuta_run", espia)
    monkeypatch.setattr("app.llm.factory.crear_provider", lambda settings: "de mentira")
    monkeypatch.setattr("app.cli.construye_fuentes", lambda *args, **kw: [])

    routes_config._ejecuta_run_completo()

    assert callable(kwargs_vistos["enriquecedor"])
    assert kwargs_vistos["max_scrapes"] == 7


def test_el_lanzador_por_defecto_no_se_construye_al_resolver_la_dependencia():
    """Resolver la dependencia no puede abrir la base de datos ni salir a la red.

    Devuelve un invocable perezoso; el trabajo de verdad sólo ocurre al llamarlo,
    y eso no pasa en ningún test.
    """
    lanzador = get_lanzador_run()

    assert callable(lanzador)
