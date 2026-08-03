from sqlalchemy import func, select

from app.ingest import ingesta
from app.models import Job
from app.schemas import RawJob, SearchQuery
from app.sources.fake import FakeSource


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
