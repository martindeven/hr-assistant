"""Directorio de empleados de solo lectura, cargado una vez desde un JSON."""

import json
from pathlib import Path


class EmployeeDirectory:
    """Consulta de empleados en solo lectura.

    Los datos se leen una sola vez al construir el objeto y no cambian después.

    :param path: Ruta al archivo JSON con la lista de empleados
    :type path: str or pathlib.Path
    """

    def __init__(self, path):
        with Path(path).open(encoding="utf-8") as handle:
            employees = json.load(handle)
        # Tupla de diccionarios: se devuelven copias para no exponer el estado.
        self._employees = tuple(dict(employee) for employee in employees)

    def get_employee(self, employee_id):
        """Busca un empleado por su id.

        :param employee_id: Id del empleado, por ejemplo ``EMP-0004``
        :type employee_id: str
        :return: Los datos del empleado, o ``{"error": ...}`` si no existe
        :rtype: dict
        """
        wanted = str(employee_id).strip().upper()
        for employee in self._employees:
            if employee["id"].upper() == wanted:
                return dict(employee)
        return {"error": f"No existe un empleado con id {employee_id}."}

    def search_by_department(self, department):
        """Lista los empleados de un departamento, sin distinguir mayúsculas.

        :param department: Nombre del departamento
        :type department: str
        :return: Los empleados del departamento (vacía si no hay)
        :rtype: list[dict]
        """
        wanted = str(department).strip().casefold()
        if not wanted:
            return []
        return [
            dict(employee)
            for employee in self._employees
            if employee["department"].casefold() == wanted
        ]

    def departments(self):
        """Devuelve los departamentos existentes, sin repetir y en orden.

        :return: Nombres de los departamentos
        :rtype: list[str]
        """
        names = []
        for employee in self._employees:
            if employee["department"] not in names:
                names.append(employee["department"])
        return names
