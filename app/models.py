from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def ahora() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Perfil(Base):
    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    ruta_pdf: Mapped[str | None] = mapped_column(String, default=None)
    # Identidad del PDF por contenido: decide si hay que re-extraer o basta con lo guardado.
    hash_pdf: Mapped[str | None] = mapped_column(String, default=None)
    datos: Mapped[dict] = mapped_column(JSON, default=dict)
    editado_a_mano: Mapped[bool] = mapped_column(default=False)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class PreferenciasRow(Base):
    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    datos: Mapped[dict] = mapped_column(JSON, default=dict)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class BusquedaGuardada(Base):
    __tablename__ = "saved_search"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String)
    texto: Mapped[str] = mapped_column(String)
    pais: Mapped[str] = mapped_column(String, default="es")
    ubicacion: Mapped[str | None] = mapped_column(String, default=None)
    solo_remoto: Mapped[bool] = mapped_column(default=False)
    fuentes: Mapped[list] = mapped_column(JSON, default=list)
    activa: Mapped[bool] = mapped_column(default=True)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("hash_dedup", name="uq_job_hash_dedup"),
        UniqueConstraint("fuente", "external_id", name="uq_job_fuente_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fuente: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    titulo: Mapped[str] = mapped_column(String)
    empresa: Mapped[str] = mapped_column(String)
    ubicacion: Mapped[str | None] = mapped_column(String, default=None)
    modalidad: Mapped[str] = mapped_column(String, default="desconocida")
    salario_min: Mapped[float | None] = mapped_column(Float, default=None)
    salario_max: Mapped[float | None] = mapped_column(Float, default=None)
    salario_texto: Mapped[str | None] = mapped_column(String, default=None)
    descripcion: Mapped[str] = mapped_column(Text)
    descripcion_truncada: Mapped[bool] = mapped_column(default=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    publicada_en: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    ingerida_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    hash_dedup: Mapped[str] = mapped_column(String, index=True)

    # pendiente | clasificada | descartada_por_regla | error
    estado_clasificacion: Mapped[str] = mapped_column(String, default="pendiente")
    motivo_regla: Mapped[str | None] = mapped_column(String, default=None)

    # El puesto ya no existe cuando se abre el enlace. Es un atributo de la OFERTA,
    # no una decisión: se puede haber aplicado y que además la cierren. Ver
    # app/cerradas.py.
    cerrada: Mapped[bool] = mapped_column(default=False)
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    intentos_clasificacion: Mapped[int] = mapped_column(Integer, default=0)

    clasificacion: Mapped["Clasificacion | None"] = relationship(
        back_populates="job", uselist=False
    )
    decision: Mapped["Decision | None"] = relationship(back_populates="job", uselist=False)


class Clasificacion(Base):
    __tablename__ = "classification"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), unique=True)
    categoria: Mapped[str] = mapped_column(String)
    confianza: Mapped[str] = mapped_column(String)
    razonamiento: Mapped[str] = mapped_column(Text)
    ejes: Mapped[dict] = mapped_column(JSON, default=dict)
    skills_faltantes: Mapped[list] = mapped_column(JSON, default=list)
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    modelo: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[int] = mapped_column(Integer)
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    job: Mapped[Job] = relationship(back_populates="clasificacion")


class Decision(Base):
    __tablename__ = "decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), unique=True)
    # guardada | aplicada | en_proceso | rechazado_por_ellos | descartada_por_mi
    # El vocabulario y su significado viven en app/decisiones.py. No se declara
    # como Enum de base de datos: SQLite no lo comprueba y obligaría a migrar la
    # tabla cada vez que aparezca un estado nuevo.
    estado: Mapped[str] = mapped_column(String)
    motivo: Mapped[str] = mapped_column(Text, default="")
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    # Cuándo se presentó el candidato. Se fija la primera vez que la decisión
    # llega a `aplicada` y no se mueve después, para poder contar candidaturas
    # por mes aunque la empresa conteste más tarde.
    aplicada_en: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    job: Mapped[Job] = relationship(back_populates="decision")


class ConsumoFuente(Base):
    """Peticiones consumidas por fuente y mes natural.

    Existe porque JSearch tiene un cupo mensual con límite duro: sin llevar la
    cuenta, el sistema lo agota a mitad de mes y deja de traer ofertas sin avisar.
    """

    __tablename__ = "source_usage"
    __table_args__ = (UniqueConstraint("fuente", "periodo", name="uq_consumo_fuente_periodo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fuente: Mapped[str] = mapped_column(String, index=True)
    periodo: Mapped[str] = mapped_column(String)  # "YYYY-MM"
    peticiones: Mapped[int] = mapped_column(Integer, default=0)


class Run(Base):
    __tablename__ = "run"

    id: Mapped[int] = mapped_column(primary_key=True)
    inicio: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    fin: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    errores: Mapped[list] = mapped_column(JSON, default=list)
