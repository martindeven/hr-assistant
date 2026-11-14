"""Pruebas de ``app.directory.EmployeeDirectory``."""


def test_employee_lookup_finds_known_ids_and_reports_unknown_ones(directory):
    assert directory.get_employee("EMP-0004")["name"] == "Jorge Díaz"

    missing = directory.get_employee("EMP-9999")

    assert "EMP-9999" in missing["error"]


def test_department_search_ignores_case_and_finds_nobody_elsewhere(directory):
    people = directory.search_by_department("FINANZAS")

    assert [person["name"] for person in people] == ["Sofía Gómez"]
    assert directory.search_by_department("Marketing") == []
    assert directory.search_by_department("") == []
    assert sorted(directory.departments()) == ["Finanzas", "Tecnología", "Ventas"]
