from app.schemas import RawJob, SearchQuery
from app.sources.fake import FakeSource


def test_la_fuente_fake_devuelve_lo_que_se_le_cargo():
    oferta = RawJob(
        fuente="fake",
        external_id="1",
        url="https://example.com/1",
        titulo="Backend",
        empresa="Acme",
        descripcion="d",
    )
    fuente = FakeSource([oferta])

    resultado = fuente.search(SearchQuery(nombre="x", texto="x"))

    assert resultado == [oferta]


def test_la_fuente_fake_registra_las_busquedas_recibidas():
    fuente = FakeSource([])
    query = SearchQuery(nombre="php", texto="php")

    fuente.search(query)

    assert fuente.llamadas == [query]


def test_la_fuente_fake_puede_simular_un_fallo():
    import pytest

    fuente = FakeSource([], error=RuntimeError("caída"))

    with pytest.raises(RuntimeError, match="caída"):
        fuente.search(SearchQuery(nombre="x", texto="x"))
