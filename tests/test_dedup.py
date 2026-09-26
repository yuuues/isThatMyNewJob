import pytest

from app.dedup import hash_dedup, normaliza, normaliza_empresa, ubicaciones_conocidas
from app.schemas import EMPRESA_DESCONOCIDA


def test_normaliza_quita_acentos_y_puntuacion():
    assert normaliza("Programación Sénior, S.L.!") == "programacion senior s l"


def test_normaliza_empresa_ignora_la_forma_juridica():
    assert normaliza_empresa("Acme S.L.") == normaliza_empresa("ACME SL")
    assert normaliza_empresa("Beta GmbH") == "beta"


def test_la_misma_oferta_en_dos_fuentes_produce_el_mismo_hash():
    a = hash_dedup("Acme S.L.", "Senior Backend Developer", "adzuna", "1")
    b = hash_dedup("ACME SL", "senior backend developer", "scrappa", "zzz")

    assert a == b


def test_ofertas_distintas_producen_hashes_distintos():
    a = hash_dedup("Acme", "Senior Backend Developer", "adzuna", "1")
    b = hash_dedup("Acme", "Junior Backend Developer", "adzuna", "2")

    assert a != b


@pytest.mark.parametrize("sin_empresa", [EMPRESA_DESCONOCIDA, "", None])
def test_sin_empresa_el_mismo_titulo_no_basta_para_ser_la_misma_oferta(sin_empresa):
    """«Desconocida» es el relleno de las fuentes, no una empresa: dos ofertas suyas con
    el mismo título son, casi siempre, de empresas distintas y no pueden colapsar."""
    a = hash_dedup(sin_empresa, "Senior Backend Developer", "scrappa", "aaa")
    b = hash_dedup(sin_empresa, "Senior Backend Developer", "scrappa", "bbb")

    assert a != b


def test_sin_empresa_la_misma_oferta_de_la_misma_fuente_conserva_la_clave():
    a = hash_dedup(EMPRESA_DESCONOCIDA, "Senior Backend Developer", "scrappa", "aaa")
    b = hash_dedup(EMPRESA_DESCONOCIDA, "Senior Backend Developer", "scrappa", "aaa")

    assert a == b


def test_sin_empresa_el_mismo_external_id_en_otra_fuente_es_otra_oferta():
    a = hash_dedup(EMPRESA_DESCONOCIDA, "Backend", "adzuna", "123")
    b = hash_dedup(EMPRESA_DESCONOCIDA, "Backend", "scrappa", "123")

    assert a != b


def test_la_clave_sin_empresa_no_choca_con_una_empresa_llamada_igual_que_la_fuente():
    """La clave de respaldo no puede coincidir con la de ninguna oferta con empresa."""
    sin_empresa = hash_dedup(EMPRESA_DESCONOCIDA, "Backend", "adzuna", "123")

    assert sin_empresa != hash_dedup("adzuna", "Backend", "adzuna", "123")
    assert sin_empresa != hash_dedup("adzuna 123", "Backend", "adzuna", "123")


class _Oferta:
    def __init__(self, ubicacion=None, ubicaciones=None):
        self.ubicacion = ubicacion
        self.ubicaciones = ubicaciones


def test_ubicaciones_conocidas_incluye_siempre_la_principal():
    assert ubicaciones_conocidas(_Oferta("Madrid", ["Madrid", "Lugo"])) == ["Madrid", "Lugo"]
    assert ubicaciones_conocidas(_Oferta("Madrid", ["Lugo"])) == ["Madrid", "Lugo"]


def test_ubicaciones_conocidas_cubre_las_filas_antiguas_sin_lista():
    """`asegura_esquema()` añade `ubicaciones` a NULL en las bases ya existentes."""
    assert ubicaciones_conocidas(_Oferta("Madrid", None)) == ["Madrid"]
    assert ubicaciones_conocidas(_Oferta(None, None)) == []
