Feature: Every request can be traced and measured
  Support must be able to answer "what happened with request X?" from the
  JSON log, the metrics endpoint and the request id shown to the person.

  Scenario: A request without an id gets a generated UUID
    Given the HR assistant is running with an in-memory log
    When I check the health endpoint without a request id
    Then the response status is 200
    And the X-Request-ID header is a UUID

  Scenario: The request id sent by the client is kept
    Given the HR assistant is running with an in-memory log
    When I ask "¿Quién es EMP-0004?" with the request id "demo-123"
    Then the response status is 200
    And the X-Request-ID header is "demo-123"
    And the response body has the request id "demo-123"

  Scenario Outline: An unsafe request id is replaced by a UUID
    Given the HR assistant is running with an in-memory log
    When I check the health endpoint with a request id that is <kind>
    Then the response status is 200
    And the X-Request-ID header is a UUID

    Examples:
      | kind                   |
      | with spaces            |
      | 200 characters long    |

  Scenario: A question leaves a full trail in the log without personal data
    Given the HR assistant is running with an in-memory log
    When I ask "¿Quién es EMP-0004?" with the request id "demo-123"
    Then the log has the events "audit, agent_step, agent_step, agent_step, agent_step, agent_run, request" for "demo-123"
    And the audit event records the tool "get_employee" with its arguments
    And the agent_run event counts the question without its text
    And no log line contains the question or the answer

  Scenario: An invalid question is logged with status 422
    Given the HR assistant is running with an in-memory log
    When I ask an empty question
    Then the response status is 422
    And the response has an X-Request-ID header
    And the request event reports status 422

  Scenario: A failing model returns a generic 500 and logs only the error type
    Given the HR assistant is running with a model that raises an exception
    When I ask "¿Quién es EMP-0004?" with the request id "demo-500"
    Then the response status is 500
    And the body is the internal error with the request id "demo-500"
    And the X-Request-ID header is "demo-500"
    And the error event reports the type "RuntimeError" without the message

  Scenario: Metrics report requests, latency, tokens and the SLO
    Given the HR assistant is running with an in-memory log
    When I ask "¿Quién es EMP-0004?" with the request id "demo-123"
    And I read the metrics
    Then the metrics count at least 1 request
    And the metrics have the latency percentiles p50 and p95
    And the metrics count input tokens
    And the SLO is met

  Scenario: A slow service does not meet the SLO
    Given the HR assistant is running with a clock that advances 3 seconds per reading
    When I ask "¿Quién es EMP-0004?" with the request id "demo-slow"
    And I read the metrics
    Then the SLO is not met

  Scenario: The home page shows the request id safely
    Given the HR assistant is running with an in-memory log
    When I open the home page
    Then the response status is 200
    And the page shows the request id without using innerHTML
