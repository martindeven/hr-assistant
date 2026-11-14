Feature: Every answer says which model it used and how much it cost
  A fixed rule picks a small or a large model for each question. Only the last
  messages of the history are sent, cached calls are free, and the cost of each
  answer is added to the metrics.

  Scenario: A simple question goes to the small model and has a cost
    Given the HR assistant is running with priced fake models
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 200
    And the model used is the small model
    And the route is "simple"
    And the cost is greater than zero

  Scenario: A comparison question goes to the large model
    Given the HR assistant is running with priced fake models
    When I ask "Compara a los empleados del departamento de Finanzas"
    Then the response status is 200
    And the model used is the large model
    And the route is "keyword:compara"

  Scenario Outline: Only the last messages of the history reach the model
    Given the HR assistant is running with priced fake models and a history window of <window>
    When I send "¿Quién es EMP-0004?" with 10 messages of history
    Then the model first receives <sent> messages

    Examples:
      | window | sent |
      | 6      | 7    |
      | 0      | 1    |

  Scenario: The metrics add up the cost of every answer
    Given the HR assistant is running with priced fake models
    When I ask "¿Quién es EMP-0004?" and then "¿Quiénes trabajan en Finanzas?"
    Then the metrics cost is the sum of both answer costs

  Scenario: An unavailable model gives a 502 with the request id
    Given the HR assistant is running with a model that is unavailable
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 502
    And the body says "model unavailable" with the request id of the header
    And the log has an error event of type "ModelUnavailableError"

  Scenario: A cached call is counted and costs nothing
    Given the HR assistant is running with a model whose calls are cached
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 200
    And the cached calls are 1
    And the cost is zero
