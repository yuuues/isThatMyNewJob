from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import cli
from app.cli import carga_semilla, construye_fuentes
from app.config import Settings
from app.db import crear_engine, crear_sesion, crear_tablas
from app.models import BusquedaGuardada, Perfil, PreferenciasRow
from app.schemas import PerfilCandidato, Preferencias

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


def test_construye_enriquecedor_respeta_el_interruptor():
    """Devolver None es lo mismo que no pasar el parámetro: el interruptor del .env y el
    valor por defecto de `ejecuta_run()` son la misma cosa vista desde los dos lados."""
    from app.cli import construye_enriquecedor
    from app.config import Settings

    apagado = Settings(_env_file=None, adzuna_scrape_activo=False)
    encendido = Settings(_env_file=None, adzuna_scrape_activo=True)

    assert construye_enriquecedor(apagado) is None
    assert callable(construye_enriquecedor(encendido))


class EspiaRun:
    """Doble de `ejecuta_run()`: se queda con los kwargs y no ejecuta el pipeline."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, sesion, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id=1, stats={}, errores=[])


def siembra_una_busqueda_activa(ruta_bd) -> None:
    """Sin ninguna búsqueda activa, el punto de entrada vuelve antes de llegar a
    `ejecuta_run()` y el test pasaría sin comprobar nada."""
    engine = crear_engine(str(ruta_bd))
    crear_tablas(engine)
    with crear_sesion(engine) as sesion:
        sesion.add(BusquedaGuardada(nombre="php", texto="php", pais="es", fuentes=[]))
        sesion.commit()


def test_el_run_de_la_cli_cablea_el_enriquecedor_y_su_cupo(monkeypatch, tmp_path):
    """El cableado es la mitad de la feature y no lo probaba nadie: quitar estos dos
    argumentos dejaba el scraper en código muerto con la suite en verde.

    El cupo se comprueba con un valor que NO es el de por defecto: con 40 a los dos
    lados, olvidarse del parámetro daría el mismo resultado que pasarlo.
    """
    ruta_bd = tmp_path / "app.db"
    monkeypatch.setenv("RUTA_BD", str(ruta_bd))
    monkeypatch.setenv("ADZUNA_SCRAPE_ACTIVO", "1")
    monkeypatch.setenv("ADZUNA_SCRAPE_MAX_POR_RUN", "7")
    siembra_una_busqueda_activa(ruta_bd)
    espia = EspiaRun()
    monkeypatch.setattr(cli, "ejecuta_run", espia)
    monkeypatch.setattr(cli, "crear_provider", lambda settings: "provider de mentira")
    monkeypatch.setattr(cli, "construye_fuentes", lambda *args, **kwargs: [])

    assert cli.comando_run(None) == 0

    assert callable(espia.kwargs["enriquecedor"])
    assert espia.kwargs["max_scrapes"] == 7


class ArgsCv:
    def __init__(self, pdf):
        self.pdf = str(pdf)


@pytest.fixture
def cv(monkeypatch, tmp_path):
    """Prepara `comando_cv` sobre una BD temporal y con Gemini falseado."""
    ruta_bd = tmp_path / "app.db"
    monkeypatch.setattr(
        cli, "get_settings", lambda: Settings(ruta_bd=str(ruta_bd), gemini_api_key="clave")
    )

    class Gemini:
        llamadas = 0
        clientes = 0

    def crea_cliente(api_key):
        Gemini.clientes += 1
        return "cliente"

    monkeypatch.setattr(cli, "crear_cliente", crea_cliente)

    def extrae(pdf, *, cliente, modelo):
        Gemini.llamadas += 1
        return PerfilCandidato(anios_experiencia=8, resumen="Backend")

    monkeypatch.setattr(cli, "extrae_perfil", extrae)
    Gemini.ruta_bd = ruta_bd
    return Gemini


def _sesion_de(ruta_bd):
    return crear_sesion(crear_engine(str(ruta_bd)))


def test_resubir_el_mismo_cv_no_vuelve_a_llamar_a_gemini(cv, tmp_path, capsys):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 cv")

    assert cli.comando_cv(ArgsCv(pdf)) == 0
    assert cli.comando_cv(ArgsCv(pdf)) == 0

    assert cv.llamadas == 1
    assert "no ha cambiado" in capsys.readouterr().out
    with _sesion_de(cv.ruta_bd) as sesion:
        assert len(sesion.scalars(select(Perfil)).all()) == 1


def test_subir_un_cv_distinto_vuelve_a_extraer(cv, tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 cv")
    cli.comando_cv(ArgsCv(pdf))
    pdf.write_bytes(b"%PDF-1.4 cv actualizado")

    cli.comando_cv(ArgsCv(pdf))

    assert cv.llamadas == 2
    with _sesion_de(cv.ruta_bd) as sesion:
        assert len(sesion.scalars(select(Perfil)).all()) == 2


def test_un_cv_distinto_avisa_de_que_la_edicion_manual_no_se_arrastra(cv, tmp_path, capsys):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 cv")
    cli.comando_cv(ArgsCv(pdf))
    with _sesion_de(cv.ruta_bd) as sesion:
        fila = sesion.scalar(select(Perfil))
        fila.editado_a_mano = True
        sesion.commit()
    capsys.readouterr()

    pdf.write_bytes(b"%PDF-1.4 cv actualizado")
    cli.comando_cv(ArgsCv(pdf))

    salida = capsys.readouterr().out
    assert "Aviso" in salida
    assert "correcciones manuales" in salida


def test_resubir_el_mismo_cv_no_necesita_credenciales_de_gemini(cv, tmp_path, monkeypatch):
    """Si no hay que extraer, no se crea cliente: sin PDF nuevo no hace falta la API key."""
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 cv")
    cli.comando_cv(ArgsCv(pdf))
    clientes_tras_la_primera = cv.clientes

    assert cli.comando_cv(ArgsCv(pdf)) == 0
    assert cv.clientes == clientes_tras_la_primera


def test_jsearch_necesita_clave_y_sesion(sesion):
    """Sin sesión no hay dónde llevar el cupo mensual, así que la fuente se salta:
    construirla sin presupuesto se gastaría los 200 créditos a mitad de mes."""
    con_clave = Settings(jsearch_api_key="k")

    assert construye_fuentes(["jsearch"], con_clave, sesion=None) == []
    assert construye_fuentes(["jsearch"], Settings(jsearch_api_key=""), sesion=sesion) == []
    assert [f.nombre for f in construye_fuentes(["jsearch"], con_clave, sesion=sesion)] == ["jsearch"]


def test_scrappa_necesita_clave_y_sesion(sesion):
    """Como jsearch: sin sesión no hay dónde llevar el cupo mensual, y una fuente de
    cupo duro sin contador se lo gasta entero."""
    con_clave = Settings(scrappa_api_key="k")

    assert construye_fuentes(["scrappa"], con_clave, sesion=None) == []
    assert construye_fuentes(["scrappa"], Settings(scrappa_api_key=""), sesion=sesion) == []
    assert [f.nombre for f in construye_fuentes(["scrappa"], con_clave, sesion=sesion)] == ["scrappa"]


def test_el_comando_reclasificar_marca_y_no_clasifica(monkeypatch, tmp_path, capsys):
    """Marca y sale. Clasificar es trabajo del run, que ya respeta el tope y los fallos."""
    from app import cli
    from app.db import crear_engine, crear_sesion, crear_tablas
    from app.models import Clasificacion, Job

    ruta = tmp_path / "app.db"
    monkeypatch.setenv("RUTA_BD", str(ruta))

    engine = crear_engine(str(ruta))
    crear_tablas(engine)
    with crear_sesion(engine) as s:
        job = Job(
            fuente="adzuna", external_id="1", url="https://example.com/1",
            titulo="Backend", empresa="Empresa", descripcion="Texto",
            hash_dedup="h1", estado_clasificacion="clasificada",
        )
        s.add(job)
        s.commit()
        s.add(
            Clasificacion(
                job_id=job.id, categoria="aplicar_ya", confianza="alta",
                razonamiento="…", ejes={}, modelo="deepseek-v4-flash", prompt_version=2,
            )
        )
        s.commit()

    assert cli.main(["reclasificar"]) == 0

    salida = capsys.readouterr().out
    assert "1" in salida

    with crear_sesion(crear_engine(str(ruta))) as s:
        assert s.get(Job, 1).estado_clasificacion == "pendiente"
