"""Comprueba contra el LLM real que la regla 8 funciona. Excluido de la suite.

El resto de tests del clasificador usan un proveedor falso, así que seguirían verdes
aunque el modelo ignorase por completo la instrucción de deducir la ubicación del texto.
Éste es el único que se enteraría.

Reproduce el caso que motivó el cambio: el job 87 de la base real, cuyo campo `ubicacion`
dice "España" mientras su descripción dice "Híbrido (presencial en Alicante)". Ninguna de
las dos fuentes que sirven esa oferta acierta con la ubicación —Adzuna dice "España" y
Scrappa "Para, Asturias provincia"—, y el usuario la descartó a mano tras leerla. Con
zonas ["barcelona"], la respuesta correcta es descartar.

Ejecutar:

    python -m pytest tests/test_classify_contrato.py -m contrato -v

Si falla, la regla 8 no basta y NO conviene rehacer las clasificaciones: el mensaje del
assert incluye el razonamiento y el eje de zona, que es por dónde empezar a mirar.
"""

import pytest

from app.classify import clasifica
from app.config import get_settings
from app.llm.factory import crear_provider
from app.schemas import PerfilCandidato, Preferencias, RawJob, SkillPerfil

OFERTA = RawJob(
    fuente="adzuna",
    external_id="contrato-87",
    url="https://www.adzuna.es/details/0",
    titulo="Programador/a PHP (Híbrido)",
    empresa="Empresa de ejemplo",
    # El campo que da el agregador: genérico y por tanto inútil para filtrar. La ciudad
    # real sólo aparece en la descripción, que es lo que la regla 8 obliga a mirar.
    ubicacion="España",
    modalidad="hibrido",
    descripcion=(
        "Buscamos Programador/a PHP para incorporarse a nuestro equipo de desarrollo.\n\n"
        "Requisitos:\n"
        "- Experiencia demostrable con PHP y Laravel\n"
        "- Conocimientos de MySQL y control de versiones con Git\n\n"
        "Condiciones:\n"
        "– Contrato indefinido a jornada completa\n"
        "– Híbrido (presencial en Alicante)\n"
        "– Incorporación inmediata\n"
    ),
)

PERFIL = PerfilCandidato(
    anios_experiencia=8,
    titulo_actual="Backend Developer",
    roles=["Backend Developer"],
    skills=[SkillPerfil(nombre="PHP", nivel="alto", anios=8.0)],
    resumen="Backend PHP con ocho años de experiencia.",
)

# Encaje técnico perfecto a propósito: si el modelo descarta, sólo puede ser por la zona.
PREFERENCIAS = Preferencias(modalidades=["remoto", "hibrido"], zonas=["barcelona"])


@pytest.mark.contrato
def test_el_modelo_descarta_por_una_ubicacion_que_solo_esta_en_el_texto():
    veredicto = clasifica(
        OFERTA,
        perfil=PERFIL,
        prefs=PREFERENCIAS,
        ejemplos=[],
        provider=crear_provider(get_settings()),
    )

    assert veredicto.categoria == "descartar", (
        f"El modelo dijo {veredicto.categoria!r}. Razonamiento: {veredicto.razonamiento} "
        f"Eje de zona: {veredicto.ejes.zona}"
    )
    assert "alicante" in veredicto.ejes.zona.lower()
