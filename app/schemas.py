from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Modalidad = Literal["remoto", "hibrido", "presencial", "desconocida"]
Categoria = Literal["aplicar_ya", "revisar", "descartar"]
Confianza = Literal["alta", "media", "baja"]


class SearchQuery(BaseModel):
    """Búsqueda guardada. Algunas fuentes no la aplican en servidor y se filtra en local."""

    nombre: str
    texto: str
    pais: str = "es"
    ubicacion: str | None = None
    solo_remoto: bool = False
    max_resultados: int = 50


class RawJob(BaseModel):
    """Oferta ya normalizada al esquema común, antes de persistirse."""

    fuente: str
    external_id: str
    url: str
    titulo: str
    empresa: str
    ubicacion: str | None = None
    modalidad: Modalidad = "desconocida"
    salario_min: float | None = None
    salario_max: float | None = None
    salario_texto: str | None = None
    descripcion: str
    publicada_en: datetime | None = None
    tags: list[str] = Field(default_factory=list)


class Preferencias(BaseModel):
    salario_min: float | None = None
    modalidades: list[Modalidad] = Field(
        default_factory=lambda: ["remoto", "hibrido", "presencial"]
    )
    zonas: list[str] = Field(default_factory=list)
    sectores_veto: list[str] = Field(default_factory=list)
    tecnologias_veto: list[str] = Field(default_factory=list)
    idiomas: list[str] = Field(default_factory=lambda: ["es", "en"])
    notas: str = ""


class SkillPerfil(BaseModel):
    nombre: str
    nivel: str
    anios: float | None = None


class PerfilCandidato(BaseModel):
    anios_experiencia: float | None = None
    titulo_actual: str | None = None
    roles: list[str] = Field(default_factory=list)
    skills: list[SkillPerfil] = Field(default_factory=list)
    sectores: list[str] = Field(default_factory=list)
    idiomas: list[str] = Field(default_factory=list)
    formacion: list[str] = Field(default_factory=list)
    certificaciones: list[str] = Field(default_factory=list)
    ubicacion: str | None = None
    resumen: str = ""


class EjesEncaje(BaseModel):
    tecnico: str
    seniority: str
    modalidad: str
    salario: str
    sector: str


class ResultadoClasificacion(BaseModel):
    categoria: Categoria
    confianza: Confianza
    razonamiento: str
    ejes: EjesEncaje
    skills_faltantes: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
