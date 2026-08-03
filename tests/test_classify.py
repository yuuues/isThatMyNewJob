import pytest

from app.classify import PROMPT_VERSION, construye_prompt_usuario, clasifica
from app.feedback import EjemploDecision
from app.llm.fake import FakeProvider
from app.schemas import (
    EjesEncaje,
    PerfilCandidato,
    Preferencias,
    RawJob,
    ResultadoClasificacion,
    SkillPerfil,
)


def perfil() -> PerfilCandidato:
    return PerfilCandidato(
        anios_experiencia=8,
        titulo_actual="Backend Developer",
        skills=[SkillPerfil(nombre="PHP", nivel="alto", anios=6)],
        resumen="Backend con 8 años.",
    )


def oferta() -> RawJob:
    return RawJob(
        fuente="adzuna",
        external_id="1",
        url="https://example.com/1",
        titulo="Desarrollador PHP Senior",
        empresa="Acme",
        ubicacion="Madrid",
        modalidad="remoto",
        salario_min=50000,
        descripcion="Buscamos PHP senior con Laravel.",
    )


def resultado() -> ResultadoClasificacion:
    return ResultadoClasificacion(
        categoria="aplicar_ya",
        confianza="alta",
        razonamiento="Encaja con el perfil.",
        ejes=EjesEncaje(
            tecnico="alto", seniority="correcto", modalidad="remoto", salario="por encima del mínimo", sector="ok"
        ),
    )


def test_devuelve_el_resultado_del_modelo():
    provider = FakeProvider([resultado()])

    salida = clasifica(
        oferta(), perfil=perfil(), prefs=Preferencias(), ejemplos=[], provider=provider
    )

    assert salida.categoria == "aplicar_ya"
    assert salida.confianza == "alta"


def test_el_prompt_incluye_perfil_preferencias_y_oferta():
    prefs = Preferencias(salario_min=45000, tecnologias_veto=["cobol"], notas="Nada de banca")

    prompt = construye_prompt_usuario(oferta(), perfil=perfil(), prefs=prefs, ejemplos=[])

    assert "Backend con 8 años." in prompt
    assert "45000" in prompt
    assert "cobol" in prompt
    assert "Nada de banca" in prompt
    assert "Desarrollador PHP Senior" in prompt


def test_el_prompt_incluye_los_ejemplos_con_su_motivo():
    ejemplos = [
        EjemploDecision(
            titulo="Dev Java", empresa="Beta", estado="descartada", motivo="No quiero Java"
        )
    ]

    prompt = construye_prompt_usuario(
        oferta(), perfil=perfil(), prefs=Preferencias(), ejemplos=ejemplos
    )

    assert "Dev Java" in prompt
    assert "No quiero Java" in prompt


def test_sin_ejemplos_no_aparece_la_seccion_de_decisiones():
    prompt = construye_prompt_usuario(
        oferta(), perfil=perfil(), prefs=Preferencias(), ejemplos=[]
    )

    assert "DECISIONES PREVIAS" not in prompt


def test_la_descripcion_se_recorta_para_acotar_el_gasto():
    larga = oferta().model_copy(update={"descripcion": "x" * 20000})

    prompt = construye_prompt_usuario(
        larga, perfil=perfil(), prefs=Preferencias(), ejemplos=[]
    )

    assert len(prompt) < 15000


def test_un_fallo_del_provider_se_propaga():
    provider = FakeProvider([], error=RuntimeError("cuota agotada"))

    with pytest.raises(RuntimeError, match="cuota agotada"):
        clasifica(
            oferta(), perfil=perfil(), prefs=Preferencias(), ejemplos=[], provider=provider
        )


def test_la_version_del_prompt_es_un_entero_positivo():
    assert isinstance(PROMPT_VERSION, int)
    assert PROMPT_VERSION >= 1
