import json

from app.feedback import EjemploDecision
from app.llm.base import LLMProvider
from app.schemas import PerfilCandidato, Preferencias, RawJob, ResultadoClasificacion

# v2: la v1 producía 111 "revisar" de 179 ofertas, 93 de ellas con confianza baja.
# Dos causas medidas: la regla del salario se disparaba en casi todas las ofertas (en
# España casi nadie lo publica) y las descripciones truncadas llevaban al modelo a
# abstenerse en lugar de juzgar con lo visible.
#
# v3: la regla 8. El campo `ubicacion` del agregador no es de fiar y falla siempre en la
# misma dirección, dejando pasar: medido, 11 ofertas híbridas dicen "España" mientras su
# texto nombra Madrid, Sevilla, Alicante, Burgos o Baleares, y otras 6 traen una provincia
# que el texto contradice. El prefiltro no puede arreglarlo porque exime el ámbito
# nacional a propósito, así que el juicio fino pasa aquí.
PROMPT_VERSION = 3

MAX_CARACTERES_DESCRIPCION = 6000

PROMPT_SISTEMA = """\
Evalúas si una oferta de empleo encaja con un candidato concreto. Devuelves una \
categoría, no una nota.

Tu cometido es AHORRARLE TRABAJO al candidato: decidir cuáles merece abrir. Una lista \
donde casi todo queda en "revisar" no le sirve de nada, porque le obliga a leerlas todas \
igualmente. Mójate.

Categorías:
- aplicar_ya: encaja con el perfil y respeta las preferencias. Merece que lo abra hoy.
- revisar: puede encajar, pero hay algo concreto que sólo él puede decidir.
- descartar: no encaja, o incumple alguna preferencia del candidato.

Reglas que no puedes saltarte:
1. Si un dato no aparece en la oferta, escribe "no publicado". Nunca lo estimes ni lo \
infieras.
2. Si la oferta incumple una preferencia explícita del candidato, la categoría es \
"descartar" por muy bueno que sea el encaje técnico.
3. EL SALARIO CASI NUNCA SE PUBLICA EN ESPAÑA: medido, aparece en menos del 10% de las \
ofertas. Su ausencia es lo normal, no información que falte. NO bajes la confianza ni \
uses "revisar" por no saber el salario. Anótalo en red_flags y decide por lo demás.
4. Si la descripción viene cortada, juzga con lo que tienes: el título y el primer \
párrafo casi siempre revelan la tecnología y el nivel, que es lo que determina el encaje. \
Usa "revisar" sólo cuando lo VISIBLE sea ambiguo sobre el encaje, no por el mero hecho de \
estar incompleto.
5. La confianza mide lo seguro que estás de TU DECISIÓN, no lo completa que esté la \
oferta. Si el título dice "Java Senior" y el candidato es de PHP, tu confianza es alta \
aunque no veas el resto.
6. El razonamiento son 2 o 3 frases, en español, dirigidas al candidato. Sin adjetivos \
vacíos ni lenguaje de venta.
7. En red_flags anota sólo señales objetivas presentes en el texto de la oferta \
(ausencia de salario, requisitos desproporcionados, contrato precario, exigencias \
incoherentes con el puesto). No especules sobre la empresa.
8. El campo `Ubicación` lo da el agregador y a menudo es genérico ("España", "Remote") o \
directamente erróneo. Deduce del TEXTO dónde se trabaja de verdad. Si el puesto es \
presencial o híbrido y la ubicación real cae fuera de las zonas del candidato, la \
categoría es "descartar" aunque el campo `Ubicación` diga otra cosa. En el eje `zona` \
escribe qué ubicación has deducido y de dónde la has sacado; si el texto no da ninguna \
pista, dilo y no descartes por ello.
"""


def _bloque_preferencias(prefs: Preferencias) -> str:
    datos = prefs.model_dump(exclude={"notas"})
    bloque = json.dumps(datos, ensure_ascii=False, indent=2)
    if prefs.notas.strip():
        bloque += f"\n\nNotas adicionales del candidato:\n{prefs.notas.strip()}"
    return bloque


def _bloque_ejemplos(ejemplos: list[EjemploDecision]) -> str:
    if not ejemplos:
        return ""

    lineas = [
        f"- \"{e.titulo}\" en {e.empresa} → {e.estado}. Motivo: {e.motivo}"
        for e in ejemplos
    ]
    return (
        "\n## DECISIONES PREVIAS DEL CANDIDATO\n"
        "Ofertas que ya valoró y qué decidió. Úsalas para calibrar el criterio.\n"
        + "\n".join(lineas)
        + "\n"
    )


def _importe(valor: float) -> str:
    """Sin decimales cuando no los hay: el modelo no gana nada leyendo '50000.0'."""
    return f"{valor:.0f}" if float(valor).is_integer() else f"{valor:g}"


def _formatea_salario(job: RawJob) -> str:
    """Un extremo ausente se nombra ausente, nunca se presenta como valor.

    La regla 1 del prompt de sistema prohíbe al modelo inventar datos que no
    están; enviarle 'Salario: 50000.0 - None' es pedirle justo lo contrario.
    """
    if job.salario_texto:
        return job.salario_texto
    if job.salario_min is not None and job.salario_max is not None:
        return f"{_importe(job.salario_min)} - {_importe(job.salario_max)}"
    if job.salario_min is not None:
        return f"desde {_importe(job.salario_min)} (máximo no publicado)"
    if job.salario_max is not None:
        return f"hasta {_importe(job.salario_max)} (mínimo no publicado)"
    return "no publicado"


def _bloque_oferta(job: RawJob) -> str:
    descripcion = job.descripcion[:MAX_CARACTERES_DESCRIPCION]
    salario = _formatea_salario(job)
    aviso = (
        "\n\nAVISO: la fuente sólo publica el principio de la descripción, así que lo "
        "anterior está cortado. No deduzcas que un requisito falte del puesto por no "
        "verlo aquí. Aun así, decide con lo visible: el título y este fragmento suelen "
        "bastar para saber la tecnología y el nivel."
        if job.descripcion_truncada
        else ""
    )
    return (
        f"Título: {job.titulo}\n"
        f"Empresa: {job.empresa}\n"
        f"Ubicación: {job.ubicacion or 'no publicada'}\n"
        f"Modalidad detectada: {job.modalidad}\n"
        f"Salario: {salario}\n"
        f"Fuente: {job.fuente}\n"
        f"Descripción:\n{descripcion}{aviso}"
    )


def construye_prompt_usuario(
    job: RawJob,
    *,
    perfil: PerfilCandidato,
    prefs: Preferencias,
    ejemplos: list[EjemploDecision],
) -> str:
    return (
        "## PERFIL DEL CANDIDATO\n"
        f"{perfil.model_dump_json(indent=2, exclude_none=True)}\n\n"
        "## PREFERENCIAS DEL CANDIDATO\n"
        f"{_bloque_preferencias(prefs)}\n"
        f"{_bloque_ejemplos(ejemplos)}\n"
        "## OFERTA A EVALUAR\n"
        f"{_bloque_oferta(job)}\n"
    )


def clasifica(
    job: RawJob,
    *,
    perfil: PerfilCandidato,
    prefs: Preferencias,
    ejemplos: list[EjemploDecision],
    provider: LLMProvider,
) -> ResultadoClasificacion:
    return provider.complete_json(
        system=PROMPT_SISTEMA,
        user=construye_prompt_usuario(job, perfil=perfil, prefs=prefs, ejemplos=ejemplos),
        modelo_salida=ResultadoClasificacion,
    )
