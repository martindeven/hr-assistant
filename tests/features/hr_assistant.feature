Feature: HR assistant answers questions about employees
  The HR staff asks questions in a web page. A stateless API sends them to a
  single agent that uses read-only tools over the employee directory.

  Scenario: Asking for an employee id uses get_employee
    Given the HR assistant is running with the fake model
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 200
    And the response has the fields answer, steps, tools_used, input_tokens and output_tokens
    And the answer mentions "Jorge Díaz"
    And the tools used are "get_employee"
    And the agent took 2 steps

  Scenario: Asking for a department lists its people
    Given the HR assistant is running with the fake model
    When I ask "¿Quiénes trabajan en Finanzas?"
    Then the response status is 200
    And the answer mentions "Sofía Gómez"
    And the tools used are "search_by_department"

  Scenario: An unknown employee is reported as missing
    Given the HR assistant is running with the fake model
    When I ask "¿Quién es EMP-9999?"
    Then the response status is 200
    And the answer mentions "EMP-9999"
    And the tools used are "get_employee"

  Scenario: A question without an id or a department gets the help message
    Given the HR assistant is running with the fake model
    When I ask "Hola, ¿cómo estás?"
    Then the response status is 200
    And the answer is exactly "Pregúntame por un empleado (EMP-0001) o un departamento."
    And no tools are used

  Scenario: The step cap stops the loop
    Given the HR assistant is running with a step cap of 1
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 200
    And the agent took 1 steps
    And the answer is exactly "No pude completar la respuesta. Intenta con una pregunta más concreta."

  Scenario Outline: Invalid questions are rejected
    Given the HR assistant is running with the fake model
    When I ask a question that is <kind>
    Then the response status is 422

    Examples:
      | kind                 |
      | empty                |
      | only spaces          |
      | 2001 characters long |

  Scenario: The history comes from the client
    Given the HR assistant is running with the fake model
    When I send "¿Quién es EMP-0004?" with a previous user message in the history
    Then the response status is 200
    And the answer mentions "Jorge Díaz"

  Scenario: A history message with an invalid role is rejected
    Given the HR assistant is running with the fake model
    When I send "¿Quién es EMP-0004?" with a history message whose role is "system"
    Then the response status is 422

  Scenario: The health check reports the process
    Given the HR assistant is running with the fake model
    When I check the health endpoint
    Then the response status is 200
    And the health status is ok and the process id is reported

  Scenario: The home page has the question form
    Given the HR assistant is running with the fake model
    When I open the home page
    Then the response status is 200
    And the page has the question, send and answer fields
