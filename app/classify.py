import json

from app.feedback import EjemploDecision
from app.llm.base import LLMProvider
from app.schemas import PerfilCandidato, Preferencias, RawJob, ResultadoClasificacion

PROMPT_VERSION = 1

MAX_CARACTERES_DESCRIPCION = 6000

PROMPT_SISTEMA = """\
Evalúas si una oferta de empleo encaja con un candidato concreto. Devuelves una \
categoría, no una nota.

Categorías:
- aplicar_ya: encaja con el perfil y respeta todas las preferencias. El candidato debería aplicar.
- revisar: encaja parcialmente, o falta información relevante para decidir.
- descartar: no encaja, o incumple alguna preferencia del candidato.

Reglas que no puedes saltarte:
1. Si un dato no aparece en la oferta (típicamente el salario), escribe "no publicado". \
Nunca lo estimes ni lo infieras.
2. Si la oferta incumple una preferencia explícita del candidato, la categoría es \
"descartar" por muy bueno que sea el encaje técnico.
3. Si no hay información suficiente para decidir, usa "revisar" con confianza "baja". \
No adivines.
4. El razonamiento son 2 o 3 frases, en español, dirigidas al candidato. Sin adjetivos \
vacíos ni lenguaje de venta.
5. En red_flags anota sólo señales objetivas presentes en el texto de la oferta \
(ausencia de salario, requisitos desproporcionados, contrato precario, exigencias \
incoherentes con el puesto). No especules sobre la empresa.
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
        "anterior está cortado. Los requisitos, el stack y las condiciones probablemente "
        "no aparecen. No deduzcas que faltan del puesto: no los estás viendo."
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
