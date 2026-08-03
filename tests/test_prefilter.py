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


def test_un_veto_no_descarta_una_palabra_que_solo_lo_contiene():
    prefs = Preferencias(tecnologias_veto=["java"])
    oferta = job(
        titulo="Senior JavaScript Developer",
        descripcion="We are looking for a JavaScript developer to join the team and build our platform.",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is False


def test_un_veto_descarta_cuando_aparece_como_palabra_completa():
    prefs = Preferencias(tecnologias_veto=["java"])
    oferta = job(
        titulo="Desarrollador Java senior",
        descripcion="Buscamos un desarrollador Java senior para el equipo de la empresa en Madrid",
    )

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "java" in resultado.motivo


def test_un_veto_corto_no_casa_dentro_de_otra_palabra():
    prefs = Preferencias(tecnologias_veto=["go"])
    oferta = job(
        titulo="Backend Developer",
        descripcion="We are looking for a developer with Django and Python to join our team.",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is False


def test_un_veto_de_varias_palabras_sigue_descartando():
    prefs = Preferencias(sectores_veto=["business intelligence"])
    oferta = job(
        titulo="Consultor Business Intelligence",
        descripcion="Buscamos un consultor para el equipo de reporting de la empresa en Madrid",
    )

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "business intelligence" in resultado.motivo


def test_un_veto_de_varias_palabras_casa_aunque_las_separe_un_salto_de_linea():
    prefs = Preferencias(sectores_veto=["business intelligence"])
    oferta = job(
        titulo="Consultor",
        descripcion="Buscamos un consultor de business\nintelligence para el equipo de la empresa",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is True


def test_un_veto_de_c_no_descarta_una_oferta_de_cpp():
    # Decisión deliberada: '+' y '#' cuentan como parte del token, así que 'c' no
    # casa dentro de 'C++' ni de 'C#'. Vetar C++ requiere escribir 'c++'.
    prefs = Preferencias(tecnologias_veto=["c"])
    oferta = job(
        titulo="C++ Developer",
        descripcion="We are looking for a C++ developer to join the team and build our platform.",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is False


def test_un_veto_de_cpp_si_descarta_una_oferta_de_cpp():
    prefs = Preferencias(tecnologias_veto=["c++"])
    oferta = job(
        titulo="C++ Developer",
        descripcion="We are looking for a C++ developer to join the team and build our platform.",
    )

    resultado = aplica_prefiltro(oferta, prefs)

    assert resultado.descartada is True
    assert "c++" in resultado.motivo


def test_un_veto_de_c_descarta_cuando_c_aparece_suelta():
    prefs = Preferencias(tecnologias_veto=["c"])
    oferta = job(
        titulo="Embedded Developer",
        descripcion="We are looking for a developer with C and assembly to join our team.",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is True


def test_un_veto_de_csharp_no_lo_confunde_con_c():
    prefs = Preferencias(tecnologias_veto=["c#"])
    oferta = job(
        titulo="Embedded Developer",
        descripcion="We are looking for a developer with C and assembly to join our team.",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is False


def test_un_veto_con_punto_conserva_el_punto():
    prefs = Preferencias(tecnologias_veto=["node.js"])
    oferta = job(
        titulo="Node.js Backend Developer",
        descripcion="We are looking for a backend developer to join the team and build our platform.",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is True


def test_el_veto_ignora_mayusculas_y_acentos():
    prefs = Preferencias(sectores_veto=["Automoción"])
    oferta = job(
        titulo="Ingeniero de software",
        descripcion="Buscamos un ingeniero para el sector de la automocion en la empresa de Madrid",
    )

    assert aplica_prefiltro(oferta, prefs).descartada is True


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
