from sqlalchemy import select

from app.cli import carga_semilla, construye_fuentes
from app.config import Settings
from app.models import BusquedaGuardada, PreferenciasRow
from app.schemas import Preferencias

SEMILLA = """
preferencias:
  salario_min: 45000
  modalidades: [remoto]
  tecnologias_veto: [cobol]
busquedas:
  - nombre: PHP senior
    texto: php senior
    pais: es
    solo_remoto: true
    fuentes: [remotive]
"""


def test_la_semilla_crea_preferencias_y_busquedas(sesion, tmp_path):
    ruta = tmp_path / "seed.yaml"
    ruta.write_text(SEMILLA)

    carga_semilla(sesion, ruta)

    prefs = Preferencias.model_validate(sesion.scalar(select(PreferenciasRow)).datos)
    busqueda = sesion.scalar(select(BusquedaGuardada))
    assert prefs.salario_min == 45000
    assert prefs.tecnologias_veto == ["cobol"]
    assert busqueda.nombre == "PHP senior"
    assert busqueda.fuentes == ["remotive"]


def test_recargar_la_semilla_no_duplica_las_busquedas(sesion, tmp_path):
    ruta = tmp_path / "seed.yaml"
    ruta.write_text(SEMILLA)

    carga_semilla(sesion, ruta)
    carga_semilla(sesion, ruta)

    assert len(sesion.scalars(select(BusquedaGuardada)).all()) == 1


def test_solo_se_construyen_las_fuentes_con_credenciales():
    settings = Settings(adzuna_app_id="", adzuna_app_key="")

    fuentes = construye_fuentes(["adzuna", "remotive", "arbeitnow"], settings)

    assert {f.nombre for f in fuentes} == {"remotive", "arbeitnow"}


def test_adzuna_se_construye_cuando_hay_credenciales():
    settings = Settings(adzuna_app_id="id", adzuna_app_key="key")

    fuentes = construye_fuentes(["adzuna"], settings)

    assert [f.nombre for f in fuentes] == ["adzuna"]
