// Página del HR Assistant. Todo el texto se pone con textContent, nunca como HTML.
(function () {
  "use strict";

  // El historial vive solo en el navegador y se manda en cada pregunta.
  const history = [];
  const conversation = document.getElementById("conversation");
  const form = document.getElementById("ask-form");
  const input = document.getElementById("question");
  const button = document.getElementById("send");
  const keyInput = document.getElementById("api-key");
  const agentsTemplate = document.getElementById("agents-template");
  const noticeTemplate = document.getElementById("guardrail-notice-template");

  // La clave vive solo en sessionStorage: se borra al cerrar la pestaña y
  // nunca va escrita en la página.
  const API_KEY_STORAGE = "hrAssistantApiKey";
  function readStoredKey() {
    try {
      return sessionStorage.getItem(API_KEY_STORAGE) || "";
    } catch (storageError) {
      return "";
    }
  }
  function storeKey(value) {
    try {
      if (value) {
        sessionStorage.setItem(API_KEY_STORAGE, value);
      } else {
        sessionStorage.removeItem(API_KEY_STORAGE);
      }
    } catch (storageError) {
      // Sin almacenamiento, la clave se lee del campo en cada pregunta.
    }
  }
  keyInput.value = readStoredKey();
  keyInput.addEventListener("input", () => storeKey(keyInput.value.trim()));

  // Explica el error según el código HTTP de la respuesta.
  function errorReason(response) {
    if (response.status === 400) {
      return "La pregunta no se puede procesar.";
    }
    if (response.status === 401) {
      return "Clave inválida.";
    }
    if (response.status === 403) {
      return "No tienes permiso para esa consulta.";
    }
    if (response.status === 413) {
      return "La pregunta es demasiado grande.";
    }
    if (response.status === 429) {
      const seconds = parseInt(response.headers.get("Retry-After"), 10);
      const wait = Number.isInteger(seconds) && seconds > 0 ? seconds : 1;
      return "Demasiadas preguntas; espera " + wait + " segundos.";
    }
    if (response.status === 422) {
      return "La pregunta no es válida (vacía o demasiado larga).";
    }
    return "El servidor respondió con un error.";
  }

  // Topes del historial que acepta el servidor (los llena al servir la página).
  function readLimit(name) {
    const meta = document.querySelector('meta[name="' + name + '"]');
    const value = meta ? parseInt(meta.content, 10) : NaN;
    return Number.isInteger(value) && value > 0 ? value : null;
  }
  const maxHistoryMessages = readLimit("max-history-messages");
  const maxHistoryChars = readLimit("max-history-chars");

  // Manda solo los últimos mensajes y recorta cada texto al tope del servidor.
  function historyToSend() {
    const recent = maxHistoryMessages === null
      ? history
      : history.slice(-maxHistoryMessages);
    return recent.map((message) => ({
      role: message.role,
      content: maxHistoryChars === null
        ? message.content
        : message.content.slice(0, maxHistoryChars),
    }));
  }

  // Agrega un párrafo con texto plano (nunca HTML) a la conversación.
  function addLine(text, className) {
    const line = document.createElement("p");
    line.className = "message " + className;
    line.textContent = text;
    conversation.appendChild(line);
    return line;
  }

  // Copia el primer elemento de una plantilla de la página.
  function fromTemplate(template) {
    return template.content.firstElementChild.cloneNode(true);
  }

  // Muestra el id de la petición para que la persona lo pase a soporte.
  function addRequestId(requestId) {
    if (requestId) {
      addLine("Id de la petición: " + requestId, "request-id");
    }
  }

  // Muestra los agentes que corrieron (data-testid="agents").
  function addAgents(agents) {
    const line = fromTemplate(agentsTemplate);
    const names = Array.isArray(agents) && agents.length
      ? agents.join(", ")
      : "ninguno";
    line.textContent = "Agentes: " + names;
    conversation.appendChild(line);
  }

  // Avisa si el guard o el reviewer ocultaron datos sensibles.
  function addGuardrailNotice(guardrails) {
    const found = guardrails || {};
    const inputTypes = Array.isArray(found.pii_input) ? found.pii_input : [];
    const outputTypes = Array.isArray(found.pii_output) ? found.pii_output : [];
    if (inputTypes.length || outputTypes.length) {
      const notice = fromTemplate(noticeTemplate);
      notice.textContent = "Se ocultaron datos sensibles";
      conversation.appendChild(notice);
    }
  }

  async function ask(question) {
    let body;
    try {
      const apiKey = keyInput.value.trim();
      storeKey(apiKey);
      const response = await fetch("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ question: question, history: historyToSend() }),
      });
      if (!response.ok) {
        addLine("Error: " + errorReason(response), "error");
        let errorBody = {};
        try {
          errorBody = await response.json();
        } catch (parseError) {
          errorBody = {};
        }
        addRequestId(errorBody.request_id || response.headers.get("X-Request-ID"));
        return;
      }
      body = await response.json();
    } catch (error) {
      addLine("Error: no se pudo conectar con el servidor.", "error");
      return;
    }
    addLine("Tú: " + question, "user");
    addLine("Asistente: " + body.answer, "assistant");
    const tools = body.tools_used.length ? body.tools_used.join(", ") : "ninguna";
    addLine("Herramientas usadas: " + tools, "tools");
    addAgents(body.agents);
    addGuardrailNotice(body.guardrails);
    addRequestId(body.request_id);
    history.push({ role: "user", content: question });
    history.push({ role: "assistant", content: body.answer });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) {
      addLine("Error: escribe una pregunta.", "error");
      return;
    }
    button.disabled = true;
    await ask(question);
    button.disabled = false;
    input.value = "";
    input.focus();
  });
})();
