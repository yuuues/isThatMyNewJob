from sqlalchemy import select

from app.llm.fake import FakeProvider
from app.models import Clasificacion, Job, Perfil, PreferenciasRow, Run
from app.pipeline import ejecuta_run
from app.schemas import EjesEncaje, PerfilCandidato, Preferencias, RawJob, ResultadoClasificacion, SearchQuery
from app.sources.fake import FakeSource


def prepara(sesion, prefs: Preferencias | None = None) -> None:
    sesion.add(Perfil(datos=PerfilCandidato(anios_experiencia=8, resumen="Backend").model_dump()))
    sesion.add(PreferenciasRow(datos=(prefs or Preferencias()).model_dump()))
    sesion.commit()


def raw(external_id: str, **kwargs) -> RawJob:
    base = dict(
        fuente="fake",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        titulo="Backend Developer",
        empresa=f"Empresa {external_id}",
        ubicacion="Madrid",
        modalidad="remoto",
        descripcion="We are looking for a backend developer to join the team and build the platform",
    )
    base.update(kwargs)
    return RawJob(**base)


def veredicto(categoria: str = "aplicar_ya") -> ResultadoClasificacion:
    return ResultadoClasificacion(
        categoria=categoria,
        confianza="alta",
        razonamiento="Encaja.",
        ejes=EjesEncaje(
            tecnico="alto", seniority="ok", modalidad="remoto", salario="no publicado", sector="ok"
        ),
    )


def test_el_run_ingesta_clasifica_y_registra(sesion):
    prepara(sesion)
    provider = FakeProvider([veredicto()])

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=provider,
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "clasificada"
    assert job.clasificacion.categoria == "aplicar_ya"
    assert job.clasificacion.prompt_version >= 1
    assert run.fin is not None
    assert run.stats["fake"]["nuevas"] == 1


def test_las_ofertas_descartadas_por_regla_no_gastan_llamada_al_llm(sesion):
    prepara(sesion, Preferencias(tecnologias_veto=["cobol"]))
    provider = FakeProvider([veredicto()])

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1", descripcion="Mantenimiento COBOL en banca for the team")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "descartada_por_regla"
    assert "cobol" in job.motivo_regla
    assert provider.llamadas == []


def test_un_fallo_del_llm_deja_la_oferta_pendiente_para_el_siguiente_run(sesion):
    prepara(sesion)
    provider = FakeProvider([], error=RuntimeError("timeout"))

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "pendiente"
    assert job.intentos_clasificacion == 1
    assert any("timeout" in e["error"] for e in run.errores)


def test_el_limite_por_run_deja_el_resto_en_cola(sesion):
    prepara(sesion)
    provider = FakeProvider([veredicto()])

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1"), raw("2"), raw("3")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        max_clasificaciones=2,
    )

    clasificadas = sesion.scalars(select(Job).where(Job.estado_clasificacion == "clasificada")).all()
    pendientes = sesion.scalars(select(Job).where(Job.estado_clasificacion == "pendiente")).all()
    assert len(clasificadas) == 2
    assert len(pendientes) == 1


def test_un_run_posterior_recoge_las_pendientes(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1"), raw("2")])
    queries = [SearchQuery(nombre="x", texto="")]

    ejecuta_run(sesion, fuentes=[fuente], queries=queries, provider=FakeProvider([veredicto()]), max_clasificaciones=1)
    ejecuta_run(sesion, fuentes=[fuente], queries=queries, provider=FakeProvider([veredicto()]), max_clasificaciones=5)

    pendientes = sesion.scalars(select(Job).where(Job.estado_clasificacion == "pendiente")).all()
    assert pendientes == []
    assert len(sesion.scalars(select(Clasificacion)).all()) == 2


def test_sin_perfil_el_run_falla_con_un_mensaje_claro(sesion):
    import pytest

    with pytest.raises(RuntimeError, match="No hay perfil"):
        ejecuta_run(
            sesion,
            fuentes=[FakeSource([])],
            queries=[SearchQuery(nombre="x", texto="")],
            provider=FakeProvider([]),
        )


def test_el_run_queda_registrado_en_la_tabla(sesion):
    prepara(sesion)

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([veredicto()]),
    )

    assert len(sesion.scalars(select(Run)).all()) == 1
