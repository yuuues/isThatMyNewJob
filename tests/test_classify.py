import pytest

from app.classify import PROMPT_VERSION, construye_prompt_usuario, clasifica
from app.feedback import PRESUPUESTO_CARACTERES, EjemploDecision, ejemplos_few_shot
from app.llm.fake import FakeProvider
from app.models import Decision, Job
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


def linea_salario(prompt: str) -> str:
    lineas = [l for l in prompt.splitlines() if l.startswith("Salario:")]
    assert len(lineas) == 1, lineas
    return lineas[0]


def prompt_con_salario(**campos) -> str:
    # Los importes van como float porque así llegan de las fuentes reales, y el
    # prompt no debe enseñar '50000.0'.
    trabajo = oferta().model_copy(update={"salario_min": None, "salario_max": None, **campos})
    return construye_prompt_usuario(
        trabajo, perfil=perfil(), prefs=Preferencias(), ejemplos=[]
    )


def test_salario_con_minimo_y_maximo_se_muestra_como_rango():
    prompt = prompt_con_salario(salario_min=50000.0, salario_max=60000.0)

    assert linea_salario(prompt) == "Salario: 50000 - 60000"
    assert "None" not in prompt


def test_salario_solo_con_minimo_no_inventa_el_maximo():
    prompt = prompt_con_salario(salario_min=50000.0)

    linea = linea_salario(prompt)
    assert "None" not in prompt
    assert "50000" in linea
    assert "-" not in linea
    assert "no publicado" in linea


def test_salario_solo_con_maximo_no_inventa_el_minimo():
    prompt = prompt_con_salario(salario_max=60000.0)

    linea = linea_salario(prompt)
    assert "None" not in prompt
    assert "60000" in linea
    assert "-" not in linea
    assert "no publicado" in linea


def test_salario_ausente_se_declara_no_publicado():
    prompt = prompt_con_salario()

    assert linea_salario(prompt) == "Salario: no publicado"
    assert "None" not in prompt


def test_el_salario_en_texto_tiene_prioridad_sobre_las_cifras():
    prompt = prompt_con_salario(salario_min=50000.0, salario_texto="50.000 € brutos anuales")

    assert linea_salario(prompt) == "Salario: 50.000 € brutos anuales"


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


def test_el_bloque_de_ejemplos_respeta_el_presupuesto(sesion):
    for i in range(6):
        for estado, motivo in (("interesa", "me gusta "), ("descartada", "no me gusta ")):
            job = Job(
                fuente="test",
                external_id=f"{estado}{i}",
                url=f"https://example.com/{estado}{i}",
                titulo="Backend Developer",
                empresa="Empresa",
                descripcion="descripción",
                hash_dedup=f"hash{estado}{i}",
                estado_clasificacion="clasificada",
            )
            sesion.add(job)
            sesion.flush()
            sesion.add(Decision(job_id=job.id, estado=estado, motivo=motivo * 300))
    sesion.commit()

    prompt = construye_prompt_usuario(
        oferta(),
        perfil=perfil(),
        prefs=Preferencias(),
        ejemplos=ejemplos_few_shot(sesion),
    )

    bloque = prompt.split("## DECISIONES PREVIAS")[1].split("## OFERTA A EVALUAR")[0]
    assert len(bloque) <= PRESUPUESTO_CARACTERES


def test_la_version_del_prompt_es_un_entero_positivo():
    assert isinstance(PROMPT_VERSION, int)
    assert PROMPT_VERSION >= 1


def test_el_prompt_avisa_cuando_la_descripcion_viene_cortada():
    """Sin este aviso el modelo interpreta la ausencia de requisitos como que el puesto
    no los tiene, en vez de como que no los está viendo."""
    cortada = oferta().model_copy(update={"descripcion_truncada": True})

    prompt = construye_prompt_usuario(
        cortada, perfil=perfil(), prefs=Preferencias(), ejemplos=[]
    )

    assert "está cortado" in prompt
    assert "no los estás viendo" in prompt


def test_sin_truncar_no_aparece_el_aviso():
    prompt = construye_prompt_usuario(
        oferta(), perfil=perfil(), prefs=Preferencias(), ejemplos=[]
    )

    assert "está cortado" not in prompt
