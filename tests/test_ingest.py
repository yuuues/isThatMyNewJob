import json
from pathlib import Path

import httpx
import respx
from sqlalchemy import func, select

from app.ingest import ingesta
from app.limitador import sin_espera
from app.models import Job
from app.schemas import RawJob, SearchQuery
from app.sources.adzuna import AdzunaSource
from app.sources.adzuna import url_api as url_adzuna
from app.sources.fake import FakeSource
from app.sources.remotive import URL_API as URL_REMOTIVE
from app.sources.remotive import RemotiveSource

FIXTURE_REMOTIVE = json.loads(
    (Path(__file__).parent / "fixtures" / "remotive_sample.json").read_text()
)
FIXTURE_ADZUNA = json.loads(
    (Path(__file__).parent / "fixtures" / "adzuna_sample.json").read_text()
)


class FuentePorBusqueda:
    """Doble que responde distinto según la búsqueda, para probar el aislamiento.

    Vive aquí y no en `FakeSource` porque sólo interesa a los tests de ingesta.
    """

    filtra_en_servidor = True

    def __init__(self, respuestas: dict, nombre: str = "fake") -> None:
        self.nombre = nombre
        self.respuestas = respuestas
        self.llamadas: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[RawJob]:
        self.llamadas.append(query)
        respuesta = self.respuestas[query.nombre]
        if isinstance(respuesta, Exception):
            raise respuesta
        return list(respuesta)

    def busca_varias(self, queries: list[SearchQuery]) -> list[RawJob]:
        ofertas: list[RawJob] = []
        for query in queries:
            ofertas.extend(self.search(query))
        return ofertas


def raw(external_id: str, **kwargs) -> RawJob:
    base = dict(
        fuente="fake",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        titulo="Backend Developer",
        empresa="Acme",
        ubicacion="Madrid",
        descripcion="descripción",
    )
    base.update(kwargs)
    return RawJob(**base)


def test_persiste_las_ofertas_nuevas(sesion):
    fuente = FakeSource([raw("1"), raw("2", titulo="Frontend Developer")])

    stats = ingesta(sesion, [fuente], [SearchQuery(nombre="x", texto="x")])

    assert sesion.scalar(select(func.count()).select_from(Job)) == 2
    assert stats["fake"]["nuevas"] == 2
    assert stats["fake"]["recibidas"] == 2


def test_no_duplica_una_oferta_ya_vista(sesion):
    fuente = FakeSource([raw("1")])
    query = [SearchQuery(nombre="x", texto="x")]

    ingesta(sesion, [fuente], query)
    stats = ingesta(sesion, [fuente], query)

    assert sesion.scalar(select(func.count()).select_from(Job)) == 1
    assert stats["fake"]["nuevas"] == 0
    assert stats["fake"]["duplicadas"] == 1


def test_deduplica_la_misma_oferta_llegada_por_dos_fuentes(sesion):
    a = FakeSource([raw("1", fuente="adzuna", empresa="Acme S.L.")], nombre="adzuna")
    b = FakeSource(
        [raw("zzz", fuente="remotive", empresa="ACME SL", titulo="backend developer")],
        nombre="remotive",
    )

    ingesta(sesion, [a, b], [SearchQuery(nombre="x", texto="x")])

    assert sesion.scalar(select(func.count()).select_from(Job)) == 1


def test_una_fuente_caida_no_impide_que_las_demas_ingesten(sesion):
    rota = FakeSource([], nombre="rota", error=RuntimeError("503"))
    buena = FakeSource([raw("1")], nombre="fake")

    stats = ingesta(sesion, [rota, buena], [SearchQuery(nombre="x", texto="x")])

    assert sesion.scalar(select(func.count()).select_from(Job)) == 1
    assert "503" in stats["rota"]["error"]
    assert stats["fake"]["nuevas"] == 1


def test_cada_busqueda_se_envia_a_cada_fuente(sesion):
    fuente = FakeSource([])
    queries = [SearchQuery(nombre="a", texto="php"), SearchQuery(nombre="b", texto="python")]

    ingesta(sesion, [fuente], queries)

    assert [q.texto for q in fuente.llamadas] == ["php", "python"]


def test_las_ofertas_nuevas_quedan_pendientes_de_clasificar(sesion):
    ingesta(sesion, [FakeSource([raw("1")])], [SearchQuery(nombre="x", texto="x")])

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "pendiente"


# --- A1: deduplicación por (fuente, external_id) --------------------------------------


def test_mismo_external_id_con_hash_distinto_cuenta_como_duplicada(sesion):
    query = [SearchQuery(nombre="x", texto="x")]
    ingesta(sesion, [FakeSource([raw("1")])], query)

    reingesta = FakeSource([raw("1", titulo="Backend Developer (remoto)")])
    stats = ingesta(sesion, [reingesta], query)

    assert sesion.scalar(select(func.count()).select_from(Job)) == 1
    assert stats["fake"]["duplicadas"] == 1
    assert stats["fake"]["nuevas"] == 0


def test_una_colision_de_external_id_no_tumba_el_resto_del_lote(sesion):
    query = [SearchQuery(nombre="x", texto="x")]
    ingesta(sesion, [FakeSource([raw("1")])], query)

    lote = FakeSource(
        [
            raw("1", titulo="Backend Developer (remoto)"),
            raw("2", titulo="Frontend Developer"),
            raw("3", titulo="Data Engineer"),
        ]
    )
    stats = ingesta(sesion, [lote], query)

    assert sesion.scalar(select(func.count()).select_from(Job)) == 3
    assert stats["fake"]["nuevas"] == 2
    assert stats["fake"]["duplicadas"] == 1
    assert "error" not in stats["fake"]


# --- A2: las cifras reflejan lo persistido --------------------------------------------


def test_las_cifras_no_cuentan_las_filas_que_el_rollback_borro(sesion, monkeypatch):
    def commit_que_falla():
        raise RuntimeError("la base de datos se cayó al confirmar")

    monkeypatch.setattr(sesion, "commit", commit_que_falla)

    stats = ingesta(
        sesion,
        [FakeSource([raw("1"), raw("2")])],
        [SearchQuery(nombre="x", texto="x")],
    )

    monkeypatch.undo()
    filas = sesion.scalar(select(func.count()).select_from(Job))
    assert filas == 0
    assert stats["fake"]["nuevas"] == filas
    assert "error" in stats["fake"]


def test_solo_cuenta_lo_confirmado_cuando_una_busqueda_se_cae_al_confirmar(sesion):
    fuente = FuentePorBusqueda(
        {"a": [raw("1")], "b": [raw("2", titulo="Frontend Developer")]}
    )
    queries = [SearchQuery(nombre="a", texto="php"), SearchQuery(nombre="b", texto="python")]

    commit_real = sesion.commit
    hechos = {"n": 0}

    def commit_vigilado():
        hechos["n"] += 1
        if hechos["n"] == 2:
            raise RuntimeError("commit caído en la segunda búsqueda")
        return commit_real()

    sesion.commit = commit_vigilado
    try:
        stats = ingesta(sesion, [fuente], queries)
    finally:
        del sesion.commit

    filas = sesion.scalar(select(func.count()).select_from(Job))
    assert filas == 1
    assert stats["fake"]["nuevas"] == filas


# --- A3: aislamiento por unidad de trabajo --------------------------------------------


def test_un_fallo_en_la_segunda_busqueda_no_borra_lo_de_la_primera(sesion):
    fuente = FuentePorBusqueda(
        {"a": [raw("1")], "b": RuntimeError("timeout en la segunda búsqueda")}
    )
    queries = [SearchQuery(nombre="a", texto="php"), SearchQuery(nombre="b", texto="python")]

    stats = ingesta(sesion, [fuente], queries)

    assert sesion.scalar(select(func.count()).select_from(Job)) == 1
    assert stats["fake"]["nuevas"] == 1
    assert "timeout en la segunda búsqueda" in stats["fake"]["error"]
    assert "b" in stats["fake"]["error"]


def test_un_fallo_en_la_primera_busqueda_no_impide_la_segunda(sesion):
    fuente = FuentePorBusqueda(
        {"a": RuntimeError("503"), "b": [raw("1"), raw("2", titulo="Frontend Developer")]}
    )
    queries = [SearchQuery(nombre="a", texto="php"), SearchQuery(nombre="b", texto="python")]

    stats = ingesta(sesion, [fuente], queries)

    assert sesion.scalar(select(func.count()).select_from(Job)) == 2
    assert stats["fake"]["nuevas"] == 2
    assert "503" in stats["fake"]["error"]


# --- A4: una descarga por run en las fuentes que no filtran en servidor ---------------


@respx.mock
def test_una_fuente_sin_filtro_en_servidor_se_descarga_una_vez_por_run(sesion):
    ruta = respx.get(URL_REMOTIVE).mock(
        return_value=httpx.Response(200, json=FIXTURE_REMOTIVE)
    )
    queries = [
        SearchQuery(nombre="php", texto="php"),
        SearchQuery(nombre="ventas", texto="sell"),
        SearchQuery(nombre="diseno", texto="designer"),
    ]

    ingesta(sesion, [RemotiveSource(limitador=sin_espera())], queries)

    assert ruta.call_count == 1


@respx.mock
def test_el_filtrado_local_aplica_todas_las_busquedas(sesion):
    respx.get(URL_REMOTIVE).mock(return_value=httpx.Response(200, json=FIXTURE_REMOTIVE))
    queries = [
        SearchQuery(nombre="php", texto="php"),
        SearchQuery(nombre="ventas", texto="sell"),
    ]

    ingesta(sesion, [RemotiveSource(limitador=sin_espera())], queries)

    ids = {job.external_id for job in sesion.scalars(select(Job))}
    assert ids == {"2091081", "2091082", "2091083"}


@respx.mock
def test_una_fuente_que_filtra_en_servidor_pregunta_por_cada_busqueda(sesion):
    ruta = respx.get(url_adzuna("es")).mock(
        return_value=httpx.Response(200, json=FIXTURE_ADZUNA)
    )
    queries = [
        SearchQuery(nombre="php", texto="php"),
        SearchQuery(nombre="python", texto="python"),
        SearchQuery(nombre="go", texto="go"),
    ]

    fuente = AdzunaSource(app_id="id", app_key="key", limitador=sin_espera())
    ingesta(sesion, [fuente], queries)

    assert ruta.call_count == 3


class FuenteDelContratoAntiguo:
    """Fuente que sólo cumple el contrato original: `nombre` y `search()`.

    Simula una fuente de terceros escrita antes de que existiera `filtra_en_servidor`.
    """

    def __init__(self, ofertas: list[RawJob], nombre: str = "vieja") -> None:
        self.nombre = nombre
        self.ofertas = ofertas

    def search(self, query: SearchQuery) -> list[RawJob]:
        return list(self.ofertas)


def test_una_fuente_sin_filtra_en_servidor_no_tumba_la_ingesta(sesion):
    """El atributo se lee fuera del try de la unidad de trabajo, así que su ausencia
    reventaba la ingesta entera y no sólo la de la fuente incompleta."""
    vieja = FuenteDelContratoAntiguo([raw("v1")])
    buena = FakeSource([raw("b1", empresa="Otra")], nombre="fake")

    stats = ingesta(sesion, [vieja, buena], [SearchQuery(nombre="x", texto="")])

    assert sesion.scalar(select(func.count()).select_from(Job)) == 2
    assert stats["fake"]["nuevas"] == 1
    assert stats["vieja"]["nuevas"] == 1
