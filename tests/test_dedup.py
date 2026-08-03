from app.dedup import hash_dedup, normaliza, normaliza_empresa


def test_normaliza_quita_acentos_y_puntuacion():
    assert normaliza("Programación Sénior, S.L.!") == "programacion senior s l"


def test_normaliza_empresa_ignora_la_forma_juridica():
    assert normaliza_empresa("Acme S.L.") == normaliza_empresa("ACME SL")
    assert normaliza_empresa("Beta GmbH") == "beta"


def test_la_misma_oferta_en_dos_fuentes_produce_el_mismo_hash():
    a = hash_dedup("Acme S.L.", "Senior Backend Developer", "Madrid")
    b = hash_dedup("ACME SL", "senior backend developer", "madrid")

    assert a == b


def test_ofertas_distintas_producen_hashes_distintos():
    a = hash_dedup("Acme", "Senior Backend Developer", "Madrid")
    b = hash_dedup("Acme", "Junior Backend Developer", "Madrid")

    assert a != b


def test_la_ubicacion_ausente_no_rompe_el_hash():
    assert hash_dedup("Acme", "Backend", None) == hash_dedup("Acme", "Backend", "")
