from google import genai
from google.genai import types

from app.schemas import PerfilCandidato

PROMPT_PERFIL = (
    "Extrae el perfil profesional de este currículum en el esquema indicado. "
    "Reglas: no inventes nada que no esté en el documento; si un dato no aparece, "
    "déjalo vacío o a null. Los años de experiencia deben calcularse desde las fechas "
    "del propio CV, no estimarse. El resumen debe ser de 2 o 3 frases y describir el "
    "perfil, no venderlo."
)


def crear_cliente(api_key: str) -> genai.Client:
    if not api_key:
        raise ValueError("La extracción del CV necesita GEMINI_API_KEY")
    return genai.Client(api_key=api_key)


def extrae_perfil(pdf: bytes, *, cliente, modelo: str) -> PerfilCandidato:
    """Convierte el PDF del CV en un perfil estructurado.

    Usa Gemini directamente y no LLMProvider: la ingesta de PDF es multimodal y
    DeepSeek no la soporta.
    """
    if not pdf:
        raise ValueError("PDF vacío: no hay nada que extraer")

    respuesta = cliente.models.generate_content(
        model=modelo,
        contents=[
            types.Part.from_bytes(data=pdf, mime_type="application/pdf"),
            PROMPT_PERFIL,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=PerfilCandidato,
        ),
    )

    if respuesta.parsed is None:
        raise ValueError(
            f"No se pudo extraer el perfil del PDF. Respuesta del modelo: "
            f"{(respuesta.text or '')[:200]}"
        )
    return respuesta.parsed
