Feature: Production access to the HR assistant
  Only clients with an API key may ask questions, each client has a request
  limit per minute, and production refuses to start with an unsafe setup.

  Scenario: Asking without an API key is rejected
    Given the HR assistant is running and the client sends no API key
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 401
    And the body is exactly the invalid key message
    And the response has an X-Request-ID header
    And the log has a request event with status 401 and no client

  Scenario: A wrong API key is rejected on the question and on the metrics
    Given the HR assistant is running and the client sends no API key
    When I ask "¿Quién es EMP-0004?" with the API key "wrong-key-0123456789"
    And I read the metrics with the API key "wrong-key-0123456789"
    Then both responses have status 401

  Scenario: The page, the health check and the docs need no API key
    Given the HR assistant is running and the client sends no API key
    Then "/", "/health" and "/docs" respond with status 200

  Scenario: A valid API key answers and the log names the client but never the key
    Given the HR assistant is running with the test API key
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 200
    And the request, agent_run and audit events name the test client
    And no log line contains the API key

  Scenario: Too many requests in a minute are refused until the minute passes
    Given the HR assistant is running with a limit of 2 requests per minute
    When I ask "¿Quién es EMP-0004?" 3 times in the same minute
    Then the last response status is 429 with the rate limit message
    And the Retry-After header is a positive number of seconds
    When 60 seconds pass
    And I ask "¿Quién es EMP-0004?"
    Then the response status is 200

  Scenario: One client reaching its limit does not block another client
    Given the HR assistant is running with clients "ops" and "web" and a limit of 1
    When "ops" asks twice and then "web" asks once
    Then the statuses are 200, 429 and 200

  Scenario: Production refuses to start with the fake model
    When I load the settings for production with the fake model
    Then the configuration fails naming "MODEL_PROVIDER"
