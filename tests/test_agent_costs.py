"""Pruebas del agente con un modelo por corrida y llamadas cacheadas."""

import json

from app.agent import Agent
from app.llm import Completion

TOOL_CALL = json.dumps({"tool": "get_employee", "args": {"employee_id": "EMP-0004"}})
FINAL_ANSWER = json.dumps({"answer": "Listo."})
QUESTION = [{"role": "user", "content": "¿Quién es EMP-0004?"}]


class CacheScriptedModel:
    """Modelo guionado: cada respuesta dice si llegó desde la caché.

    :param replies: Pares ``(contenido, cacheada)``, uno por llamada
    :type replies: list[tuple[str, bool]]
    """

    def __init__(self, replies):
        self._replies = list(replies)

    def complete(self, messages):
        """Devuelve la siguiente respuesta (10 tokens de entrada, 2 de salida).

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :return: La respuesta guionada
        :rtype: app.llm.Completion
        """
        del messages  # El guion no depende de la conversación.
        content, cached = self._replies.pop(0)
        return Completion(
            content=content, input_tokens=10, output_tokens=2, cached=cached
        )


def test_the_model_given_to_a_run_replaces_the_default_model(
    directory, scripted_model
):
    default_model = scripted_model([FINAL_ANSWER])
    chosen_model = scripted_model([FINAL_ANSWER])
    agent = Agent(model=default_model, directory=directory, max_steps=3)

    result = agent.run(QUESTION, model=chosen_model)

    assert result.answer == "Listo."
    assert len(chosen_model.calls) == 1
    assert not default_model.calls


def test_cached_calls_are_counted_and_left_out_of_the_billed_tokens(directory):
    model = CacheScriptedModel([(TOOL_CALL, True), (FINAL_ANSWER, False)])
    agent = Agent(model=model, directory=directory, max_steps=3)

    result = agent.run(QUESTION)

    assert result.cached_calls == 1
    assert (result.input_tokens, result.output_tokens) == (20, 4)
    assert (result.billed_input_tokens, result.billed_output_tokens) == (10, 2)
