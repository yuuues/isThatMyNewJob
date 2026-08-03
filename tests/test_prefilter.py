from app.prefilter import aplica_prefiltro, detecta_idioma
from app.schemas import Preferencias, RawJob


def job(**kwargs) -> RawJob:
    base = dict(
        fuente="test",
        external_id="1",
        url="https://example.com",
        titulo="Backend Developer",
        empresa="Acme",
        descripcion="We are looking for a backend developer to join the team and build our platform.",
        modalidad="remoto",
    )
    base.update(kwargs)
    return RawJob(**base)


def test_detecta_idioma_espanol():
    texto = "Buscamos un desarrollador para el equipo de backend en la empresa de Madrid con experiencia"
    assert detecta_idioma(texto) == "es"


def test_detecta_idioma_ingles():
    texto = "We are looking for a backend developer to join the team and build our platform with us"
    assert detecta_idioma(texto) == "en"


def test_detecta_idioma_aleman():
    texto = "Wir sind eine Sparkasse und suchen einen Berater für die Region mit Erfahrung in der Beratung"
    assert detecta_idioma(texto) == "de"


def test_texto_corto_o_ambiguo_devuelve_desconocido():
    assert detecta_idioma("Dev") == "desconocido"


def test_descarta_por_idioma_no_aceptado():
    prefs = Preferencias(idiomas=["es", "en"])
    oferta = job(
        titulo="Private Banking Berater",
        descripcion="Wir sind eine Sparkasse und suchen einen Berater für die Region mit Erfahrung in der Beratung",
    )

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "idioma" in resultado.motivo


def test_descarta_por_tecnologia_vetada():
    prefs = Preferencias(tecnologias_veto=["cobol"])
    oferta = job(descripcion="Mantenimiento de sistemas COBOL en banca legacy for the team")

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "cobol" in resultado.motivo


def test_descarta_por_modalidad_no_aceptada():
    prefs = Preferencias(modalidades=["remoto"])
    oferta = job(modalidad="presencial")

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "modalidad" in resultado.motivo


def test_descarta_presencial_fuera_de_zona():
    prefs = Preferencias(modalidades=["remoto", "presencial"], zonas=["madrid"])
    oferta = job(modalidad="presencial", ubicacion="Passau")

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "zona" in resultado.motivo


def test_no_descarta_remoto_aunque_la_ubicacion_este_fuera_de_zona():
    prefs = Preferencias(modalidades=["remoto"], zonas=["madrid"])
    oferta = job(modalidad="remoto", ubicacion="Worldwide")

    assert aplica_prefiltro(oferta, prefs).descartada is False


def test_descarta_cuando_el_salario_maximo_no_llega_al_minimo():
    prefs = Preferencias(salario_min=45000)
    oferta = job(salario_max=30000)

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "salario" in resultado.motivo


def test_no_descarta_cuando_el_salario_no_esta_publicado():
    prefs = Preferencias(salario_min=45000)

    assert aplica_prefiltro(job(), prefs).descartada is False


def test_una_oferta_que_encaja_sobrevive():
    prefs = Preferencias(salario_min=40000, modalidades=["remoto"], tecnologias_veto=["cobol"])
    oferta = job(salario_min=50000, salario_max=60000)

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is False
    assert resultado.motivo is None
