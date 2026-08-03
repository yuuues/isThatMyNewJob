import hashlib
from collections.abc import Callable
from typing import NamedTuple

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Perfil
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


# Motivos posibles de una sincronización, para que quien llame no tenga que deducirlos.
SIN_CAMBIOS = "sin_cambios"
PRIMERA_EXTRACCION = "primera_extraccion"
PDF_NUEVO = "pdf_nuevo"
PDF_NUEVO_CON_EDICION_PREVIA = "pdf_nuevo_con_edicion_previa"


class ResultadoPerfil(NamedTuple):
    """Qué perfil queda vigente y por qué, tras sincronizar un PDF."""

    perfil: PerfilCandidato
    extraido: bool
    motivo: str


def huella_pdf(pdf: bytes) -> str:
    """Identidad del CV por contenido.

    Se usa el contenido y no la ruta a propósito: renombrar o mover el fichero no es
    un CV nuevo, y volver a extraer costaría una llamada al modelo para nada.
    """
    return hashlib.sha256(pdf).hexdigest()


def perfil_vigente(sesion: Session) -> Perfil | None:
    """La fila que el pipeline usa: la más reciente. Las anteriores quedan como histórico."""
    return sesion.scalar(select(Perfil).order_by(Perfil.id.desc()))


def sincroniza_perfil(
    sesion: Session,
    pdf: bytes,
    *,
    ruta: str,
    extractor: Callable[[], PerfilCandidato],
) -> ResultadoPerfil:
    """Deja el perfil guardado al día con el PDF recibido, extrayendo sólo si hace falta.

    El spec manda dos cosas: la extracción sólo se repite si el PDF es distinto, y la
    corrección manual del usuario prevalece sobre lo que entendió el modelo.

    Casos:

    - **Mismo PDF** (misma huella): no se extrae nada y la fila vigente se devuelve tal
      cual, con sus ediciones manuales intactas. Ni se toca `ruta_pdf`: es informativa y
      reescribir una fila que el usuario ha corregido a mano sería una sorpresa gratuita.
    - **PDF distinto**: se extrae de nuevo y se inserta una fila nueva, que pasa a ser la
      vigente.
    - **PDF distinto con edición manual previa**: también se re-extrae, y esto es una
      decisión, no un descuido. Quien busca trabajo y acaba de actualizar su CV espera que
      el sistema clasifique contra el CV nuevo; conservar la extracción vieja significaría
      valorar ofertas contra una experiencia que ya no es la suya, que es el fallo caro y
      además silencioso. Bloquear y no hacer nada tampoco sirve: dejaría al usuario con un
      perfil desfasado y sin salida por línea de comandos. Las correcciones no se destruyen:
      la fila anterior se conserva como histórico y sigue consultable, y el motivo devuelto
      permite avisar de que hay que volver a aplicarlas sobre la extracción nueva.
    """
    if not pdf:
        raise ValueError("PDF vacío: no hay nada que extraer")

    huella = huella_pdf(pdf)
    actual = perfil_vigente(sesion)

    if actual is not None and actual.hash_pdf == huella:
        return ResultadoPerfil(PerfilCandidato.model_validate(actual.datos), False, SIN_CAMBIOS)

    if actual is None:
        motivo = PRIMERA_EXTRACCION
    elif actual.editado_a_mano:
        motivo = PDF_NUEVO_CON_EDICION_PREVIA
    else:
        motivo = PDF_NUEVO

    perfil = extractor()
    sesion.add(Perfil(ruta_pdf=ruta, hash_pdf=huella, datos=perfil.model_dump()))
    sesion.commit()
    return ResultadoPerfil(perfil, True, motivo)
