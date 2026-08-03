from datetime import datetime

from app.schemas import Preferencias, RawJob, SearchQuery


def test_rawjob_tiene_valores_por_defecto_sensatos():
    job = RawJob(
        fuente="remotive",
        external_id="123",
        url="https://example.com/1",
        titulo="Backend Developer",
        empresa="Acme",
        descripcion="Buscamos backend.",
    )

    assert job.modalidad == "desconocida"
    assert job.tags == []
    assert job.salario_min is None


def test_searchquery_por_defecto_apunta_a_espana():
    query = SearchQuery(nombre="php senior", texto="php senior")

    assert query.pais == "es"
    assert query.solo_remoto is False


def test_preferencias_aceptan_las_tres_modalidades_por_defecto():
    prefs = Preferencias()

    assert set(prefs.modalidades) == {"remoto", "hibrido", "presencial"}
    assert prefs.idiomas == ["es", "en"]


def test_rawjob_acepta_fecha_de_publicacion():
    job = RawJob(
        fuente="arbeitnow",
        external_id="abc",
        url="https://example.com/2",
        titulo="Dev",
        empresa="Beta",
        descripcion="x",
        publicada_en=datetime(2026, 7, 28, 14, 23, 5),
    )

    assert job.publicada_en.year == 2026
