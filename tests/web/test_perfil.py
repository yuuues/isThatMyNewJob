"""Vista de perfil: subir el CV, editarlo a mano y recuperar lo anterior.

Regla dura de este fichero: **ningún test llama a Gemini**. La extracción entra
por una dependencia sustituible, y hay un test que comprueba que resolver esa
dependencia no construye ningún cliente: si el perfil no cambia, no debe hacer
falta ni tener credenciales.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.models import Perfil
from app.profile import huella_pdf, perfil_vigente
from app.schemas import PerfilCandidato, SkillPerfil
from app.web.routes_config import get_extractor_perfil
from tests.web.conftest import PERFIL_SEMILLA

PDF = b"%PDF-1.4\nun curriculum de mentira\n%%EOF"
OTRO_PDF = b"%PDF-1.4\nun curriculum distinto\n%%EOF"

PERFIL_EXTRAIDO = PerfilCandidato(
    anios_experiencia=5,
    titulo_actual="Ingeniera de datos",
    roles=["datos"],
    skills=[SkillPerfil(nombre="Spark", nivel="alto", anios=4)],
    idiomas=["es"],
    ubicacion="Bilbao",
    resumen="Perfil extraído por el doble de prueba.",
)


class ExtractorFalso:
    """Doble del extractor de CV: cuenta llamadas y no toca la red."""

    def __init__(self, resultado=PERFIL_EXTRAIDO, error: Exception | None = None):
        self.resultado = resultado
        self.error = error
        self.llamadas: list[bytes] = []

    def __call__(self, pdf: bytes) -> PerfilCandidato:
        self.llamadas.append(pdf)
        if self.error is not None:
            raise self.error
        return self.resultado


@pytest.fixture
def extractor(cliente: TestClient) -> ExtractorFalso:
    doble = ExtractorFalso()
    cliente.app.dependency_overrides[get_extractor_perfil] = lambda: doble
    return doble


def _sube(cliente: TestClient, contenido: bytes, nombre: str = "cv.pdf", tipo="application/pdf"):
    return cliente.post("/profile/pdf", files={"pdf": (nombre, contenido, tipo)})


def test_la_vista_responde_sin_perfil_cargado(cliente: TestClient):
    """Entrar a /profile antes de subir nada no puede reventar."""
    respuesta = cliente.get("/profile")

    assert respuesta.status_code == 200
    assert "<html" in respuesta.text


def test_la_vista_muestra_el_perfil_en_campos_legibles(cliente: TestClient, perfil_y_preferencias):
    respuesta = cliente.get("/profile")

    assert respuesta.status_code == 200
    assert "Desarrollador backend" in respuesta.text
    assert "Python" in respuesta.text
    # Lo que distingue "legible" de "JSON crudo": el volcado literal no aparece.
    crudo = json.dumps(PERFIL_SEMILLA.model_dump(), ensure_ascii=False)
    assert crudo not in respuesta.text


def test_subir_un_pdf_nuevo_crea_el_perfil(cliente: TestClient, sesion, extractor):
    respuesta = _sube(cliente, PDF)

    assert respuesta.status_code == 200
    assert extractor.llamadas == [PDF]

    fila = perfil_vigente(sesion)
    assert fila is not None
    assert fila.datos["titulo_actual"] == "Ingeniera de datos"
    assert fila.hash_pdf == huella_pdf(PDF)


def test_subir_el_mismo_pdf_no_vuelve_a_extraer(cliente: TestClient, sesion, extractor):
    _sube(cliente, PDF)
    respuesta = _sube(cliente, PDF)

    assert respuesta.status_code == 200
    assert len(extractor.llamadas) == 1
    assert sesion.query(Perfil).count() == 1


def test_subir_el_mismo_pdf_no_necesita_el_extractor_de_verdad(cliente: TestClient, sesion):
    """Sin sustituir nada: si el PDF no ha cambiado, no se construye cliente alguno.

    Es la comprobación de que la dependencia es perezosa. Si `get_extractor_perfil`
    construyera el cliente de Gemini al resolverse, este test fallaría (o peor:
    saldría a la red) aunque no haya que extraer nada.
    """
    sesion.add(Perfil(ruta_pdf="cv.pdf", hash_pdf=huella_pdf(PDF), datos=PERFIL_SEMILLA.model_dump()))
    sesion.commit()

    respuesta = _sube(cliente, PDF)

    assert respuesta.status_code == 200
    assert sesion.query(Perfil).count() == 1


def test_subir_un_pdf_distinto_vuelve_a_extraer_y_conserva_el_anterior(
    cliente: TestClient, sesion, extractor
):
    _sube(cliente, PDF)
    _sube(cliente, OTRO_PDF)

    assert len(extractor.llamadas) == 2
    assert sesion.query(Perfil).count() == 2
    assert perfil_vigente(sesion).hash_pdf == huella_pdf(OTRO_PDF)


def test_subir_un_pdf_distinto_avisa_si_habia_ediciones_manuales(
    cliente: TestClient, sesion, extractor
):
    sesion.add(
        Perfil(
            ruta_pdf="cv.pdf",
            hash_pdf=huella_pdf(PDF),
            datos=PERFIL_SEMILLA.model_dump(),
            editado_a_mano=True,
        )
    )
    sesion.commit()

    respuesta = _sube(cliente, OTRO_PDF)

    assert respuesta.status_code == 200
    assert "manual" in respuesta.text.lower()


def test_un_fichero_que_no_es_pdf_se_rechaza_con_mensaje(cliente: TestClient, sesion, extractor):
    respuesta = _sube(cliente, b"esto es texto plano", nombre="notas.txt", tipo="text/plain")

    assert respuesta.status_code == 400
    assert "PDF" in respuesta.text
    assert extractor.llamadas == []
    assert sesion.query(Perfil).count() == 0


def test_un_pdf_ilegible_muestra_el_error_en_la_vista(cliente: TestClient, sesion):
    doble = ExtractorFalso(error=ValueError("No se pudo extraer el perfil del PDF"))
    cliente.app.dependency_overrides[get_extractor_perfil] = lambda: doble

    respuesta = _sube(cliente, PDF)

    assert respuesta.status_code == 400
    assert "No se pudo extraer el perfil del PDF" in respuesta.text
    assert sesion.query(Perfil).count() == 0


def test_editar_y_guardar_marca_editado_a_mano_y_conserva_lo_editado(
    cliente: TestClient, sesion, perfil_y_preferencias
):
    respuesta = cliente.post(
        "/profile",
        data={
            "anios_experiencia": "9",
            "titulo_actual": "Arquitecto de software",
            "ubicacion": "Valencia",
            "resumen": "Corregido a mano.",
            "roles": "backend, plataforma",
            "sectores": "software",
            "idiomas": "es, en",
            "formacion": "Ingeniería Informática",
            "certificaciones": "",
            "skills": "Python | alto | 9\nGo | medio | 2",
        },
    )

    assert respuesta.status_code == 200

    fila = perfil_vigente(sesion)
    assert fila.editado_a_mano is True
    assert fila.datos["titulo_actual"] == "Arquitecto de software"
    assert fila.datos["roles"] == ["backend", "plataforma"]
    assert fila.datos["skills"][1] == {"nombre": "Go", "nivel": "medio", "anios": 2.0}
    assert "Arquitecto de software" in cliente.get("/profile").text


def test_editar_conserva_la_huella_del_pdf(cliente: TestClient, sesion, extractor):
    """Corregir un dato a mano no puede obligar a re-extraer el mismo CV.

    Si la edición manual creara una fila sin huella, volver a subir el mismo PDF
    gastaría una llamada al modelo para nada.
    """
    _sube(cliente, PDF)

    cliente.post("/profile", data={"titulo_actual": "Corregido"})
    _sube(cliente, PDF)

    assert len(extractor.llamadas) == 1
    assert perfil_vigente(sesion).datos["titulo_actual"] == "Corregido"


def test_unos_anios_no_numericos_se_rechazan_sin_romper(
    cliente: TestClient, sesion, perfil_y_preferencias
):
    respuesta = cliente.post("/profile", data={"anios_experiencia": "muchos"})

    assert respuesta.status_code == 400
    assert perfil_vigente(sesion).datos["titulo_actual"] == "Desarrollador backend"


def test_el_historico_lista_los_perfiles_anteriores(cliente: TestClient, sesion, extractor):
    _sube(cliente, PDF, nombre="cv-viejo.pdf")
    _sube(cliente, OTRO_PDF, nombre="cv-nuevo.pdf")

    texto = cliente.get("/profile").text

    # El histórico está plegado en un <details>, pero el contenido se sirve igual.
    assert "<details" in texto
    assert "cv-viejo.pdf" in texto
    assert sesion.query(Perfil).count() == 2
