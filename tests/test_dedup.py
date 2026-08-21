from app.dedup import hash_dedup, normaliza, normaliza_empresa, ubicaciones_conocidas


def test_normaliza_quita_acentos_y_puntuacion():
    assert normaliza("Programación Sénior, S.L.!") == "programacion senior s l"


def test_normaliza_empresa_ignora_la_forma_juridica():
    assert normaliza_empresa("Acme S.L.") == normaliza_empresa("ACME SL")
    assert normaliza_empresa("Beta GmbH") == "beta"


def test_la_misma_oferta_en_dos_fuentes_produce_el_mismo_hash():
    a = hash_dedup("Acme S.L.", "Senior Backend Developer")
    b = hash_dedup("ACME SL", "senior backend developer")

    assert a == b


def test_ofertas_distintas_producen_hashes_distintos():
    a = hash_dedup("Acme", "Senior Backend Developer")
    b = hash_dedup("Acme", "Junior Backend Developer")

    assert a != b


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
