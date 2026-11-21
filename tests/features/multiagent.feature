Feature: Supervisor, specialists and resilience
  Every question goes through a guard, a supervisor that picks the
  specialists, the specialists themselves and a reviewer. When the large
  model keeps failing, the question is answered with the small model.

  Scenario: An employee question goes to the directory specialist
    Given the HR assistant is running with the fake model
    When I ask "¿Quién es EMP-0004?"
    Then the response status is 200
    And the agents are "guard, supervisor, directory, reviewer"
    And the answer contains "Jorge Díaz"
    And the review is "passed"
    And the answer did not use the fallback

  Scenario: A policy question goes only to the policies specialist
    Given the HR assistant is running with the fake model
    When I ask "¿Cuántos días de vacaciones me tocan el primer año?"
    Then the response status is 200
    And the agents are "guard, supervisor, policies, reviewer"
    And the tools used are "search_policy"
    And the answer contains "12"

  Scenario: A mixed question runs the directory first and then the policies
    Given the HR assistant is running with the fake model
    When I ask "¿EMP-0004 tiene vacaciones este año?"
    Then the agents are "guard, supervisor, directory, policies, reviewer"
    And the tools used are "get_employee, search_policy"
    And the answer contains "Jorge Díaz"
    And the answer contains "12"
    And the steps add up to 3

  Scenario: A question without a topic gets the usual help
    Given the HR assistant is running with the fake model
    When I ask "Hola"
    Then the agents are "guard, supervisor, directory, reviewer"
    And the answer contains "EMP-0001"
    And the review is "passed"

  Scenario: The small model answers when the large model keeps failing
    Given the HR assistant is running with a large model that always fails
    When I ask "Explica quién es EMP-0004"
    Then the response status is 200
    And the answer used the fallback
    And the answer reports the small model
    And the log has an audit event "fallback"

  Scenario: A failure of both models is still a 502
    Given the HR assistant is running with two models that always fail
    When I ask "Explica quién es EMP-0004"
    Then the response status is 502

  Scenario: Readiness answers without an API key
    Given the HR assistant is running and the client sends no API key
    When I check the readiness
    Then the response status is 200
    And the readiness reports ready with its checks
