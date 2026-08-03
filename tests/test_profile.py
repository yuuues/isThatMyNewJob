import pytest

from app.profile import PROMPT_PERFIL, extrae_perfil
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
