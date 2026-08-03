from app.sources.comun import detecta_modalidad, salario_anual


def test_una_cifra_demasiado_baja_no_puede_ser_un_salario_anual():
    """Adzuna no publica el periodo. Medido en datos reales: 'Senior Laravel Developer'
    con salario 48-60, que es una tarifa por hora. Tratarla como anual hacía que el
    prefiltro descartase por sueldo bajo justo las ofertas mejor pagadas."""
    minimo, maximo, texto = salario_anual(48.0, 60.0)

    assert minimo is None
    assert maximo is None
    assert "48.0 - 60.0" in texto
    assert "periodo" in texto


def test_un_salario_anual_plausible_se_conserva_como_numero():
    assert salario_anual(45000, 60000) == (45000, 60000, None)


def test_sin_salario_no_hay_nada_que_decidir():
    assert salario_anual(None, None) == (None, None, None)


def test_se_juzga_por_el_maximo_cuando_existe():
    """4000-52000 es un rango anual raro pero posible; 40-52 no lo es."""
    assert salario_anual(4000, 52000) == (4000, 52000, None)
    assert salario_anual(40, 52)[0] is None


def test_solo_minimo_tambien_se_juzga():
    assert salario_anual(30.0, None)[0] is None
    assert salario_anual(38000, None) == (38000, None, None)


def test_el_hibrido_gana_al_remoto():
    """Una oferta híbrida menciona el teletrabajo de los días que toca."""
    assert detecta_modalidad("Modelo híbrido con dos días de teletrabajo") == "hibrido"
    assert detecta_modalidad("Puesto totalmente en remoto") == "remoto"
    assert detecta_modalidad("Desarrollador backend para el equipo") == "desconocida"


def test_remota_parcial_es_hibrido_no_remoto():
    """En las ofertas españolas "remota parcial" es la forma habitual de decir híbrido.
    Sin reconocerlo, una oferta presencial tres días colaba el prefiltro de quien sólo
    acepta remoto, que es peor que descartarla: le hace perder el tiempo."""
    assert detecta_modalidad("Ofrecemos modalidad remota parcial") == "hibrido"
    assert detecta_modalidad("Puesto parcialmente remoto en Madrid") == "hibrido"
    assert detecta_modalidad("Teletrabajo parcial, dos días en oficina") == "hibrido"


def test_el_remoto_total_sigue_siendo_remoto():
    assert detecta_modalidad("Puesto 100% en remoto") == "remoto"
    assert detecta_modalidad("Full remote position") == "remoto"
