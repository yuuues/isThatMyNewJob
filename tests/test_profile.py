import pytest
from sqlalchemy import select

from app.models import Perfil
from app.profile import (
    PROMPT_PERFIL,
    extrae_perfil,
    huella_pdf,
    perfil_vigente,
    sincroniza_perfil,
)
from app.schemas import PerfilCandidato, SkillPerfil


class GeminiFalso:
    """Doble del cliente de Gemini: registra lo que recibe y devuelve un perfil fijo."""

    def __init__(self, perfil: PerfilCandidato | None = None):
        self.recibido = None
        self.models = self
        self._perfil = perfil or PerfilCandidato(anios_experiencia=8, resumen="Backend")

    def generate_content(self, *, model, contents, config):
        self.recibido = {"model": model, "contents": contents, "config": config}
        return type("R", (), {"parsed": self._perfil, "text": "{}"})()


def test_extrae_el_perfil_del_pdf():
    cliente = GeminiFalso(
        PerfilCandidato(
            anios_experiencia=8,
            titulo_actual="Backend Developer",
            skills=[SkillPerfil(nombre="PHP", nivel="alto", anios=6)],
        )
    )

    perfil = extrae_perfil(b"%PDF-1.4 contenido", cliente=cliente, modelo="gemini-2.5-flash")

    assert perfil.anios_experiencia == 8
    assert perfil.skills[0].nombre == "PHP"


def test_el_pdf_se_envia_como_parte_binaria_con_su_mime():
    cliente = GeminiFalso()

    extrae_perfil(b"%PDF-1.4 x", cliente=cliente, modelo="gemini-2.5-flash")

    contents = cliente.recibido["contents"]
    partes = [p for p in contents if not isinstance(p, str)]
    binarias = [p for p in partes if getattr(p, "inline_data", None) is not None]

    assert binarias, "el PDF debe viajar como parte binaria, no como texto"
    assert binarias[0].inline_data.mime_type == "application/pdf"
    assert binarias[0].inline_data.data == b"%PDF-1.4 x"
    assert PROMPT_PERFIL in [p for p in contents if isinstance(p, str)]


def test_un_pdf_ilegible_da_un_error_claro():
    class GeminiSinParseo(GeminiFalso):
        def generate_content(self, *, model, contents, config):
            return type("R", (), {"parsed": None, "text": "no he podido leer el documento"})()

    with pytest.raises(ValueError, match="No se pudo extraer el perfil"):
        extrae_perfil(b"basura", cliente=GeminiSinParseo(), modelo="gemini-2.5-flash")


def test_un_pdf_vacio_se_rechaza_antes_de_llamar_al_modelo():
    cliente = GeminiFalso()

    with pytest.raises(ValueError, match="PDF vacío"):
        extrae_perfil(b"", cliente=cliente, modelo="gemini-2.5-flash")

    assert cliente.recibido is None


class ExtractorFalso:
    """Cuenta las extracciones para poder afirmar que no se repiten sin motivo."""

    def __init__(self, perfil: PerfilCandidato | None = None):
        self.llamadas = 0
        self._perfil = perfil or PerfilCandidato(anios_experiencia=8, resumen="Backend")

    def __call__(self) -> PerfilCandidato:
        self.llamadas += 1
        return self._perfil


def _edita_a_mano(sesion, resumen: str) -> Perfil:
    fila = perfil_vigente(sesion)
    fila.datos = {**fila.datos, "resumen": resumen}
    fila.editado_a_mano = True
    sesion.commit()
    return fila


def test_el_mismo_pdf_no_se_vuelve_a_extraer(sesion):
    extractor = ExtractorFalso()

    sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)
    resultado = sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)

    assert extractor.llamadas == 1
    assert resultado.extraido is False
    assert resultado.motivo == "sin_cambios"
    assert len(sesion.scalars(select(Perfil)).all()) == 1


def test_el_mismo_pdf_cuenta_igual_aunque_cambie_de_ruta(sesion):
    extractor = ExtractorFalso()

    sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)
    sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="otra/carpeta/cv-2026.pdf", extractor=extractor)

    assert extractor.llamadas == 1


def test_un_pdf_distinto_se_extrae_de_nuevo(sesion):
    extractor = ExtractorFalso()

    sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)
    resultado = sincroniza_perfil(sesion, b"%PDF-1.4 cv nuevo", ruta="cv.pdf", extractor=extractor)

    assert extractor.llamadas == 2
    assert resultado.extraido is True
    assert resultado.motivo == "pdf_nuevo"
    assert perfil_vigente(sesion).hash_pdf == huella_pdf(b"%PDF-1.4 cv nuevo")


def test_el_mismo_pdf_no_pisa_la_edicion_manual(sesion):
    extractor = ExtractorFalso()
    sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)
    _edita_a_mano(sesion, "Corregido a mano")

    resultado = sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)

    assert extractor.llamadas == 1
    assert resultado.perfil.resumen == "Corregido a mano"
    vigente = perfil_vigente(sesion)
    assert vigente.datos["resumen"] == "Corregido a mano"
    assert vigente.editado_a_mano is True


def test_un_pdf_distinto_reextrae_y_conserva_la_fila_editada(sesion):
    """Decisión: manda el CV nuevo, pero la fila editada no se destruye."""
    extractor = ExtractorFalso()
    sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=extractor)
    editada = _edita_a_mano(sesion, "Corregido a mano")
    id_editada = editada.id

    resultado = sincroniza_perfil(sesion, b"%PDF-1.4 cv nuevo", ruta="cv.pdf", extractor=extractor)

    assert extractor.llamadas == 2
    assert resultado.motivo == "pdf_nuevo_con_edicion_previa"
    assert resultado.perfil.resumen == "Backend"

    antigua = sesion.get(Perfil, id_editada)
    assert antigua is not None, "la edición manual anterior debe seguir consultable"
    assert antigua.datos["resumen"] == "Corregido a mano"

    vigente = perfil_vigente(sesion)
    assert vigente.id != id_editada
    assert vigente.editado_a_mano is False


def test_la_primera_extraccion_se_distingue_de_un_cv_actualizado(sesion):
    resultado = sincroniza_perfil(sesion, b"%PDF-1.4 cv", ruta="cv.pdf", extractor=ExtractorFalso())

    assert resultado.motivo == "primera_extraccion"
    assert perfil_vigente(sesion).ruta_pdf == "cv.pdf"


def test_un_pdf_vacio_no_llega_al_extractor(sesion):
    extractor = ExtractorFalso()

    with pytest.raises(ValueError, match="PDF vacío"):
        sincroniza_perfil(sesion, b"", ruta="cv.pdf", extractor=extractor)

    assert extractor.llamadas == 0
