const camera = document.getElementById("camera");
const canvas = document.getElementById("buffer");
const resultBox = document.getElementById("result");
const streamStatus = document.getElementById("streamStatus");

const registerForm = document.getElementById("registerForm");
const authCard = document.getElementById("authCard");
const tabRegister = document.getElementById("tabRegister");
const tabAuth = document.getElementById("tabAuth");

const btnStartAuth = document.getElementById("btnStartAuth");
const btnStartRegister = document.getElementById("btnStartRegister");

const pluginConfig = window.__FACE_PLUGIN__ || {};
const pluginMode = pluginConfig.pluginMode === true || pluginConfig.pluginMode === "true";
const openerOrigin = pluginConfig.openerOrigin || "*";
const pluginQueryMode = new URLSearchParams(window.location.search).get("plugin") === "1";

let stream = null;
let facingMode = "user";
let activeMode = "register";
let loopTimer = null;
let requestInFlight = false;
let rateLimitedUntil = 0;

function isLivenessPendingError(message) {
  return /verificacion de vida requerida|parpadea/i.test(message || "");
}

function notifyPluginHost(eventName, payload) {
  if (!isPluginFlow()) {
    return;
  }

  const hostWindow = window.opener || (window.parent !== window ? window.parent : null);

  if (!hostWindow) {
    return;
  }

  const message = {
    source: "face-auth-plugin",
    event: eventName,
    payload
  };

  try {
    hostWindow.postMessage(message, openerOrigin);
  } catch (error) {
    // If token origin and real opener origin differ, fallback to wildcard target.
    hostWindow.postMessage(message, "*");
  }
}

function isPluginFlow() {
  return pluginMode || pluginQueryMode || !!window.opener || window.parent !== window;
}

function closePluginWindow() {
  // First attempt: standard close for script-opened popup/tab.
  window.close();

  // Second attempt: some browsers only allow close after self-target open.
  setTimeout(() => {
    if (window.closed) {
      return;
    }

    try {
      window.open("", "_self");
      window.close();
    } catch (error) {
      console.error("No se pudo ejecutar cierre forzado del plugin:", error);
    }
  }, 120);

  // Final fallback: hide sensitive content even if browser blocks close.
  setTimeout(() => {
    if (window.closed) {
      return;
    }

    try {
      window.location.replace("about:blank#face-plugin-closed");
    } catch (error) {
      console.error("No se pudo redirigir la pestaña de plugin:", error);
    }
  }, 260);
}

function setResult(message, kind = "") {
  resultBox.textContent = message;
  resultBox.className = `result ${kind}`.trim();
}

function setMode(mode) {
  activeMode = mode;
  const register = mode === "register";

  tabRegister.classList.toggle("active", register);
  tabAuth.classList.toggle("active", !register);

  registerForm.classList.toggle("hidden", !register);
  authCard.classList.toggle("hidden", register);

  stopLoop();
  requestInFlight = false;
  btnStartRegister.disabled = false;
  btnStartAuth.disabled = false;

  setResult("Listo para comenzar.");
}

async function switchCamera() {
  const previousMode = facingMode;
  facingMode = facingMode === "user" ? "environment" : "user";

  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }

  try {
    await startCamera();
  } catch (error) {
    facingMode = previousMode;
    await startCamera();
    setResult("No se pudo cambiar de cámara.", "bad");
  }
}

document.getElementById("btnSwitchCamera").addEventListener("click", switchCamera);

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("La camara requiere HTTPS o localhost. Abre el sitio con una conexion segura.");
  }

  streamStatus.textContent = "Solicitando permisos de cámara...";

  stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: { ideal: facingMode },
      width: { ideal: 1280 },
      height: { ideal: 720 }
    },
    audio: false
  });

  camera.srcObject = stream;

  await camera.play();
  streamStatus.textContent = "Streaming activo";
}

function captureFrameDataURL() {
  const width = camera.videoWidth || 640;
  const height = camera.videoHeight || 480;

  canvas.width = width;
  canvas.height = height;

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(camera, 0, 0, width, height);

  return canvas.toDataURL("image/jpeg", 0.82);
}

async function sendFrame(endpoint, payloadBuilder, onSuccess, shouldStopOnError) {
  if (requestInFlight) {
    return;
  }

  if (Date.now() < rateLimitedUntil) {
    return;
  }

  requestInFlight = true;

  try {
    const frame = captureFrameDataURL();
    const payload = payloadBuilder(frame);

    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const body = await response.json();

    if (response.ok && body.ok) {
      onSuccess(body.data);
      stopLoop();
      return;
    }

    if (response.status === 429) {
      const retryAfterSeconds = Number(response.headers.get("Retry-After") || "1");
      rateLimitedUntil = Date.now() + Math.max(1, retryAfterSeconds) * 1000;
      setResult(
        `Demasiadas solicitudes. Reintentando en ${Math.max(1, retryAfterSeconds)}s...`,
        "bad"
      );
      return;
    }

    const err = body.error || body.data?.message || "No se pudo procesar el rostro.";
    setResult(err, "bad");

    if (typeof shouldStopOnError === "function" && shouldStopOnError(response, body)) {
      stopLoop();
      btnStartRegister.disabled = false;
      btnStartAuth.disabled = false;
    }
  } catch (error) {
    setResult(`Error de conexión: ${error.message}`, "bad");
  } finally {
    requestInFlight = false;
  }
}

function startLoop(endpoint, payloadBuilder, onSuccess, shouldStopOnError = null) {
  stopLoop();
  rateLimitedUntil = 0;

  setResult("Procesando stream en vivo... mantente frente a la cámara.");

  loopTimer = setInterval(() => {
    sendFrame(endpoint, payloadBuilder, onSuccess, shouldStopOnError);
  }, 850);
}

function stopLoop() {
  if (loopTimer) {
    clearInterval(loopTimer);
    loopTimer = null;
  }
}

registerForm.addEventListener("submit", (event) => {
  event.preventDefault();

  const nombre = document.getElementById("nombre").value.trim();
  const documento = document.getElementById("documento").value.trim();

  if (!nombre || !documento) {
    setResult("Completa nombre y documento.", "bad");
    return;
  }

  btnStartRegister.disabled = true;

  startLoop(
    "/api/stream/register",
    (frame) => ({ nombre, documento, frame }),
    (data) => {
      setResult(
        `Registro exitoso\nNombre: ${data.nombre}\nDocumento: ${data.documento}\nHash: ${data.face_hash}`,
        "ok"
      );
      notifyPluginHost("register:success", data);
      btnStartRegister.disabled = false;
    },
    (response, body) => {
      const err = body.error || body.data?.message || "";
      return response.status === 400 && !isLivenessPendingError(err);
    }
  );
});

btnStartAuth.addEventListener("click", () => {
  btnStartAuth.disabled = true;

  startLoop(
    "/api/stream/authenticate",
    (frame) => ({ frame }),
    (data) => {
      const user = data?.user || {};
      setResult(
        `Autenticación correcta\nNombre: ${user.nombre || "-"}\nDocumento: ${user.documento || "-"}\nHash: ${user.face_hash || "-"}\nScore: ${data.score} (umbral ${data.threshold})`,
        "ok"
      );

      try {
        notifyPluginHost("auth:success", data);
      } catch (error) {
        // If postMessage target origin is invalid/mismatched, still close popup after success.
        console.error("No se pudo notificar a la ventana padre:", error);
      }

      if (isPluginFlow()) {
        setTimeout(() => {
          closePluginWindow();
        }, 800);
      }

      btnStartAuth.disabled = false;
    }
  );
});

tabRegister.addEventListener("click", () => setMode("register"));
tabAuth.addEventListener("click", () => setMode("auth"));

(async function init() {
  try {
    await startCamera();
    setMode("register");
  } catch (error) {
    streamStatus.textContent = "No se pudo abrir la cámara";
    setResult(`Permiso de cámara denegado o no disponible: ${error.message}`, "bad");
  }
})();
