from predicates import aliases, canonical, same


def test_canonical_maps_aliases():
    assert canonical("date_of_birth") == "birth_date"
    assert canonical("Died") == "death_date"
    assert canonical("some_new_predicate") == "some_new_predicate"  # open vocabulary
    assert canonical(None) == ""


def test_same():
    assert same("birth_date", "date_of_birth")
    assert not same("birth_date", "death_date")
    assert not same("birth_date", None)


def test_aliases_canonical_first():
    a = aliases("dob")
    assert a[0] == "birth_date" and "date_of_birth" in a
    assert aliases(None) == []
