from ingesta.provincias import PROVINCIAS, normalizar_cp, provincia


def test_sevilla():
    assert provincia("41001") == ("41", "Sevilla")
    assert provincia("41927") == ("41", "Sevilla")


def test_espacios_y_cero_inicial():
    assert provincia("46910 ") == ("46", "Valencia/València")  # tal cual llega en la web
    assert normalizar_cp("8720") == "08720"
    assert provincia("8720") == ("08", "Barcelona")


def test_invalidos():
    for cp in (None, "", "ABCDE", "123", "99999", "00123", "530000"):
        assert provincia(cp) == (None, None)


def test_tabla_completa():
    assert sorted(PROVINCIAS) == [f"{i:02d}" for i in range(1, 53)]


def test_comunidades_cubren_las_52_provincias_una_vez():
    from ingesta.provincias import COMUNIDADES, PROVINCIAS, comunidad
    todas = [p for _, provs in COMUNIDADES.values() for p in provs]
    assert sorted(todas) == sorted(PROVINCIAS)
    assert comunidad("41")[1] == "Andalucía" and comunidad("47")[1] == "Castilla y León"
