Feature: Policy answers are always grounded in the consulted policy
  The policies specialist always reads the policy before the model writes the
  answer, and the reviewer checks that every number of the policies answer is
  in that policy. Internal messages of the agent never reach a person.

  Scenario Outline: An invented number in a policy answer is replaced by the policy
    Given the HR assistant is running with a model that answers "<invented>" without asking for a tool
    When I ask "<question>"
    Then the response status is 200
    And the answer contains "<policy>"
    And the answer does not contain "<invented>"
    And the tools used are "search_policy"
    And the review is "replaced"
    And the log has one tool_call audit for "search_policy" with the topic "<topic>"
    And the log has an output_reviewed audit with outcome "replaced"

    Examples:
      | question                                            | invented          | policy                | topic      |
      | ¿Cuántos días de vacaciones me tocan el primer año? | Te tocan 15 días. | 12 días hábiles       | vacaciones |
      | ¿Cuántos días de vacaciones me tocan el primer año? | Te tocan 14 días. | 12 días hábiles       | vacaciones |
      | ¿Cuánto aguinaldo me dan?                           | Te dan 30 días.   | 15 días de salario    | aguinaldo  |

  Scenario: A policy answer that uses the numbers of the policy passes the review
    Given the HR assistant is running with a model that answers "Te tocan 12 días hábiles el primer año." without asking for a tool
    When I ask "¿Cuántos días de vacaciones me tocan el primer año?"
    Then the response status is 200
    And the answer contains "12 días hábiles"
    And the tools used are "search_policy"
    And the review is "passed"
    And the log has one tool_call audit for "search_policy" with the topic "vacaciones"
    And the model was called once and had already received the policy text "12 días hábiles"

  Scenario: The fake model writes the policy answer in a single step
    Given the HR assistant is running with the fake model for policies
    When I ask "¿Cuántos días de vacaciones me tocan el primer año?"
    Then the response status is 200
    And the answer contains "12"
    And the tools used are "search_policy"
    And the review is "passed"
    And the steps are 1

  Scenario: Numbers of the directory part of a mixed answer do not trigger the policy rule
    Given the HR assistant is running with a model whose directory part says "EMP-0004 tiene 7 años en la empresa." and policies part says "Te tocan 12 días hábiles."
    When I ask "¿EMP-0004 tiene vacaciones este año?"
    Then the response status is 200
    And the agents are "guard, supervisor, directory, policies, reviewer"
    And the tools used are "get_employee, search_policy"
    And the answer contains "7 años"
    And the answer contains "12 días hábiles"
    And the review is "passed"

  Scenario: Only the policies part of a mixed answer is replaced
    Given the HR assistant is running with a model whose directory part says "EMP-0004 tiene 7 años en la empresa." and policies part says "Te tocan 14 días."
    When I ask "¿EMP-0004 tiene vacaciones este año?"
    Then the response status is 200
    And the answer contains "EMP-0004 tiene 7 años en la empresa."
    And the answer contains "Según la política: Vacaciones: 12 días hábiles"
    And the answer does not contain "14 días"
    And the review is "replaced"

  Scenario: An internal message of the agent is replaced by the help
    Given the HR assistant is running with a model that answers "La respuesta debe tener 'answer' o 'tool'." without asking for a tool
    When I ask "Hola, ¿me ayudas?"
    Then the response status is 200
    And the answer is the help
    And the review is "replaced"
    And the log has an output_reviewed audit with outcome "replaced"
