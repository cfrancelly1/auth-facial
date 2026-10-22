const usersBody = document.getElementById("usersBody");
const usersResult = document.getElementById("usersResult");
const camera = document.getElementById("camera");
const canvas = document.getElementById("buffer");
const streamStatus = document.getElementById("streamStatus");
const createUserForm = document.getElementById("createUserForm");
const btnCreate = document.getElementById("btnCreate");
const btnToggleCamera = document.getElementById("btnToggleCamera");
const btnSwitchCamera = document.getElementById("btnSwitchCamera");
const usersSearch = document.getElementById("usersSearch");
const usersPager = document.getElementById("usersPager");
const btnAuthenticate = document.getElementById("btnAuthenticate");
const authResult = document.getElementById("authResult");
const apiKeyForm = document.getElementById("apiKeyForm");
const apiKeysBody = document.getElementById("apiKeysBody");
const apiKeysResult = document.getElementById("apiKeysResult");
const apiKeyCreateResult = document.getElementById("apiKeyCreateResult");
const btnCreateApiKey = document.getElementById("btnCreateApiKey");
const apiKeyNameInput = document.getElementById("apiKeyName");
const apiKeyClientIdInput = document.getElementById("apiKeyClientId");
const apiKeyExpiresDaysInput = document.getElementById("apiKeyExpiresDays");
const scopeAdminLogin = document.getElementById("scopeAdminLogin");
const scopePluginToken = document.getElementById("scopePluginToken");

let stream = null;
let facingMode = "user";
let usersPage = 1;
let usersSearchTerm = "";
let usersSearchTimer = null;
const USERS_PER_PAGE = 10;
let createLoopTimer = null;
let createRequestInFlight = false;
let authLoopTimer = null;
let authRequestInFlight = false;

function setResult(message, kind = "") {
  usersResult.textContent = message;
  usersResult.className = `result ${kind}`.trim();
}

function isLivenessPendingError(message) {
  return /verificacion de vida requerida|parpadea|mueve ligeramente/i.test(message || "");
}

function escapeHtml(value) {
  return (value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function startCamera() {
  if (stream) {
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("La camara requiere HTTPS o localhost. Abre el sitio con una conexion segura.");
  }

  streamStatus.textContent = "Solicitando permisos de camara...";

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
  updateCameraUi(true);
}

async function switchCamera() {
  const previousMode = facingMode;
  facingMode = facingMode === "user" ? "environment" : "user";

  if (!stream) {
    return;
  }

  stopCamera();

  try {
    await startCamera();
  } catch (error) {
    facingMode = previousMode;
    try {
      await startCamera();
    } catch {
      updateCameraUi(false);
    }
    setResult(`No se pudo cambiar de camara: ${error.message}`, "bad");
  }
}

function stopCamera() {
  if (!stream) {
    updateCameraUi(false);
    return;
  }

  stream.getTracks().forEach((track) => track.stop());
  stream = null;
  camera.srcObject = null;
  updateCameraUi(false);
}

function updateCameraUi(isOn) {
  streamStatus.textContent = isOn ? "Camara activa" : "Camara apagada";
  btnToggleCamera.textContent = isOn ? "Apagar camara" : "Encender camara";
  btnCreate.disabled = !isOn;
  btnAuthenticate.disabled = !isOn;
  btnSwitchCamera.disabled = !isOn;
  camera.classList.toggle("camera-off", !isOn);
}

function captureFrameDataURL() {
  if (!stream || !camera.srcObject) {
    throw new Error("La camara esta apagada. Enciendela para continuar.");
  }

  const width = camera.videoWidth || 640;
  const height = camera.videoHeight || 480;

  canvas.width = width;
  canvas.height = height;

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(camera, 0, 0, width, height);

  return canvas.toDataURL("image/jpeg", 0.85);
}

function formatDate(value) {
  if (!value) {
    return "-";
  }

  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function renderUsers(users) {
  if (!Array.isArray(users) || users.length === 0) {
    usersBody.innerHTML = '<tr><td colspan="6">No hay usuarios registrados.</td></tr>';
    return;
  }

  usersBody.innerHTML = users
    .map((user) => {
      const shortHash = (user.face_hash || "").slice(0, 16);
      return `
      <tr>
        <td>${user.id}</td>
        <td>${escapeHtml(user.nombre)}</td>
        <td>${escapeHtml(user.documento)}</td>
        <td title="${escapeHtml(user.face_hash || "")}">${escapeHtml(shortHash)}...</td>
        <td>${escapeHtml(formatDate(user.fecha_creacion))}</td>
        <td>
          <div class="actions">
            <button type="button" class="btn" data-edit="${user.id}">Editar</button>
            <button type="button" class="btn danger" data-delete="${user.id}">Eliminar</button>
          </div>
        </td>
      </tr>`;
    })
    .join("");
}

function renderPager(pagination) {
  const { page, pages, total } = pagination;

  usersPager.innerHTML = `
    <button type="button" class="btn" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Anterior</button>
    <span>Pagina ${page} de ${pages} (${total} usuarios)</span>
    <button type="button" class="btn" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Siguiente</button>`;
}

async function loadUsers() {
  try {
    const params = new URLSearchParams({
      page: usersPage,
      per_page: USERS_PER_PAGE,
      q: usersSearchTerm
    });
    const response = await fetch(`/admin/api/users?${params}`);
    const body = await response.json();

    if (!response.ok || !body.ok) {
      throw new Error(body.error || "No se pudieron cargar los usuarios.");
    }

    usersPage = body.pagination.page;
    renderUsers(body.data || []);
    renderPager(body.pagination);
    setResult(`Usuarios encontrados: ${body.pagination.total}`, "ok");
  } catch (error) {
    setResult(error.message, "bad");
  }
}

async function createUser(nombre, documento) {
  const frame = captureFrameDataURL();

  const response = await fetch("/admin/api/users", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ nombre, documento, frame })
  });

  const body = await response.json();

  if (!response.ok || !body.ok) {
    throw new Error(body.error || "No se pudo crear el usuario.");
  }

  return body.data;
}

function stopCreateLoop() {
  if (createLoopTimer) {
    clearInterval(createLoopTimer);
    createLoopTimer = null;
  }
}

function startCreateLoop(nombre, documento) {
  stopCreateLoop();
  setResult("Verificacion de vida: parpadea o mueve ligeramente el rostro frente a la camara.", "");

  createLoopTimer = setInterval(async () => {
    if (createRequestInFlight || !stream) {
      return;
    }

    createRequestInFlight = true;

    try {
      const created = await createUser(nombre, documento);
      stopCreateLoop();
      setResult(`Usuario creado: ${created.nombre} (${created.documento})`, "ok");
      createUserForm.reset();
      await loadUsers();
      btnCreate.disabled = false;
      return;
    } catch (error) {
      if (!isLivenessPendingError(error.message)) {
        stopCreateLoop();
        setResult(error.message, "bad");
        btnCreate.disabled = false;
        return;
      }

      setResult("Verificacion de vida pendiente: parpadea o mueve ligeramente el rostro.", "");
    } finally {
      createRequestInFlight = false;
    }
  }, 900);
}

function setAuthResult(message, kind = "") {
  authResult.textContent = message;
  authResult.className = `result auth-result ${kind}`.trim();
}

async function authenticateCurrentFrame() {
  const frame = captureFrameDataURL();

  const response = await fetch("/api/stream/authenticate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ frame })
  });

  const body = await response.json();
  if (response.status === 400) {
    throw new Error(body.error || "No se pudo autenticar en este momento.");
  }

  if (!body.data) {
    throw new Error(body.error || "No se pudo autenticar en este momento.");
  }

  return body.data;
}

function stopAuthLoop() {
  if (authLoopTimer) {
    clearInterval(authLoopTimer);
    authLoopTimer = null;
  }
}

function startAuthLoop() {
  stopAuthLoop();
  setAuthResult("Verificacion de vida: parpadea o mueve ligeramente el rostro frente a la camara.", "");

  authLoopTimer = setInterval(async () => {
    if (authRequestInFlight || !stream) {
      return;
    }

    authRequestInFlight = true;

    try {
      const result = await authenticateCurrentFrame();

      if (!result.authenticated) {
        stopAuthLoop();
        setAuthResult(
          `${result.message || "Rostro no reconocido."} (score: ${result.score ?? 0}, umbral: ${result.threshold ?? "n/a"})`,
          "bad"
        );
        btnAuthenticate.disabled = false;
        return;
      }

      stopAuthLoop();
      setAuthResult(
        `Autenticado: ${result.user?.nombre || "Usuario"} (${result.user?.documento || "-"}) | score: ${result.score}`,
        "ok"
      );
      btnAuthenticate.disabled = false;
    } catch (error) {
      if (isLivenessPendingError(error.message)) {
        setAuthResult("Verificacion de vida pendiente: parpadea o mueve ligeramente el rostro.", "");
      } else {
        stopAuthLoop();
        setAuthResult(error.message, "bad");
        btnAuthenticate.disabled = false;
      }
    } finally {
      authRequestInFlight = false;
    }
  }, 900);
}

async function updateUser(id, nombre, documento) {
  const response = await fetch(`/admin/api/users/${id}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ nombre, documento })
  });

  const body = await response.json();

  if (!response.ok || !body.ok) {
    throw new Error(body.error || "No se pudo actualizar el usuario.");
  }
}

async function deleteUser(id) {
  const response = await fetch(`/admin/api/users/${id}`, {
    method: "DELETE"
  });

  const body = await response.json();

  if (!response.ok || !body.ok) {
    throw new Error(body.error || "No se pudo eliminar el usuario.");
  }
}

function setApiKeysResult(message, kind = "") {
  apiKeysResult.textContent = message;
  apiKeysResult.className = `result ${kind}`.trim();
}

function setApiKeyCreateResult(message, kind = "") {
  apiKeyCreateResult.innerHTML = message;
  apiKeyCreateResult.className = `result ${kind}`.trim();
}

function buildScopes() {
  const scopes = [];
  if (scopeAdminLogin.checked) {
    scopes.push("admin:login");
  }
  if (scopePluginToken.checked) {
    scopes.push("plugin:token");
  }
  return scopes;
}

function renderApiKeys(keys) {
  if (!Array.isArray(keys) || keys.length === 0) {
    apiKeysBody.innerHTML = '<tr><td colspan="6">No hay API keys registradas.</td></tr>';
    return;
  }

  apiKeysBody.innerHTML = keys
    .map((key) => {
      const state = key.is_active ? "Activa" : "Revocada";
      const scopes = Array.isArray(key.scopes) ? key.scopes.join(", ") : "-";
      return `
      <tr>
        <td>${key.id}</td>
        <td>${escapeHtml(key.name)}</td>
        <td>${escapeHtml(scopes)}</td>
        <td>${escapeHtml(key.client_id || "-")}</td>
        <td>${escapeHtml(state)}</td>
        <td>
          ${key.is_active ? `<button type="button" class="btn danger" data-revoke-key="${key.id}">Revocar</button>` : "-"}
        </td>
      </tr>`;
    })
    .join("");
}

async function loadApiKeys() {
  try {
    const response = await fetch("/admin/api/apikeys");
    const body = await response.json();

    if (!response.ok || !body.ok) {
      throw new Error(body.error || "No se pudieron cargar las API keys.");
    }

    renderApiKeys(body.data || []);
    setApiKeysResult(`API keys cargadas: ${(body.data || []).length}`, "ok");
  } catch (error) {
    setApiKeysResult(error.message, "bad");
  }
}

async function createApiKey(payload) {
  const response = await fetch("/admin/api/apikeys", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  const body = await response.json();
  if (!response.ok || !body.ok) {
    throw new Error(body.error || "No se pudo crear la API key.");
  }

  return body.data;
}

async function revokeApiKey(keyId) {
  const response = await fetch(`/admin/api/apikeys/${keyId}/revoke`, {
    method: "POST"
  });

  const body = await response.json();
  if (!response.ok || !body.ok) {
    throw new Error(body.error || "No se pudo revocar la API key.");
  }
}

createUserForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const nombre = document.getElementById("nombre").value.trim();
  const documento = document.getElementById("documento").value.trim();

  if (!nombre || !documento) {
    setResult("Nombre y documento son obligatorios.", "bad");
    return;
  }

  btnCreate.disabled = true;
  startCreateLoop(nombre, documento);
});

btnToggleCamera.addEventListener("click", async () => {
  if (stream) {
    stopCreateLoop();
    stopAuthLoop();
    stopCamera();
    setResult("Camara apagada manualmente.", "");
    return;
  }

  try {
    await startCamera();
    setResult("Camara encendida.", "ok");
  } catch (error) {
    updateCameraUi(false);
    setResult(`No se pudo encender la camara: ${error.message}`, "bad");
  }
});

btnAuthenticate.addEventListener("click", async () => {
  if (!stream) {
    setAuthResult("La camara esta apagada. Enciendela para autenticar.", "bad");
    return;
  }

  btnAuthenticate.disabled = true;
  startAuthLoop();
});

usersSearch.addEventListener("input", () => {
  clearTimeout(usersSearchTimer);
  usersSearchTimer = setTimeout(() => {
    usersSearchTerm = usersSearch.value.trim();
    usersPage = 1;
    loadUsers();
  }, 300);
});

usersPager.addEventListener("click", (event) => {
  const target = event.target.closest("[data-page]");
  if (!target || target.disabled) {
    return;
  }

  usersPage = Number(target.getAttribute("data-page"));
  loadUsers();
});

btnSwitchCamera.addEventListener("click", switchCamera);

usersBody.addEventListener("click", async (event) => {
  const editId = event.target.getAttribute("data-edit");
  const deleteId = event.target.getAttribute("data-delete");

  if (editId) {
    const currentRow = event.target.closest("tr");
    const currentNombre = currentRow.children[1].textContent.trim();
    const currentDocumento = currentRow.children[2].textContent.trim();

    const nombre = window.prompt("Nuevo nombre:", currentNombre);
    if (nombre === null) {
      return;
    }

    const documento = window.prompt("Nuevo documento:", currentDocumento);
    if (documento === null) {
      return;
    }

    try {
      await updateUser(editId, nombre.trim(), documento.trim());
      setResult("Usuario actualizado correctamente.", "ok");
      await loadUsers();
    } catch (error) {
      setResult(error.message, "bad");
    }

    return;
  }

  if (deleteId) {
    const confirmed = window.confirm("Esta accion eliminara el usuario. ¿Deseas continuar?");
    if (!confirmed) {
      return;
    }

    try {
      await deleteUser(deleteId);
      setResult("Usuario eliminado correctamente.", "ok");
      await loadUsers();
    } catch (error) {
      setResult(error.message, "bad");
    }
  }
});

apiKeyForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const name = apiKeyNameInput.value.trim();
  const clientId = apiKeyClientIdInput.value.trim();
  const expiresDaysRaw = apiKeyExpiresDaysInput.value.trim();
  const scopes = buildScopes();

  if (!name) {
    setApiKeyCreateResult("Nombre es obligatorio.", "bad");
    return;
  }

  if (scopes.length === 0) {
    setApiKeyCreateResult("Selecciona al menos un scope.", "bad");
    return;
  }

  const payload = {
    name,
    scopes,
    client_id: clientId || null
  };

  if (expiresDaysRaw) {
    payload.expires_in_days = Number(expiresDaysRaw);
  }

  btnCreateApiKey.disabled = true;
  setApiKeyCreateResult("Creando API key...", "");

  try {
    const created = await createApiKey(payload);
    const safeKey = escapeHtml(created.api_key || "");
    setApiKeyCreateResult(
      `API key creada. Copiala ahora (solo se muestra una vez):<br><span class="mono">${safeKey}</span>`,
      "ok"
    );

    apiKeyNameInput.value = "";
    apiKeyClientIdInput.value = "";
    apiKeyExpiresDaysInput.value = "";
    await loadApiKeys();
  } catch (error) {
    setApiKeyCreateResult(error.message, "bad");
  } finally {
    btnCreateApiKey.disabled = false;
  }
});

apiKeysBody.addEventListener("click", async (event) => {
  const keyId = event.target.getAttribute("data-revoke-key");
  if (!keyId) {
    return;
  }

  const confirmed = window.confirm("Se revocara la API key. Esta accion no se puede deshacer. ¿Continuar?");
  if (!confirmed) {
    return;
  }

  try {
    await revokeApiKey(keyId);
    setApiKeysResult("API key revocada correctamente.", "ok");
    await loadApiKeys();
  } catch (error) {
    setApiKeysResult(error.message, "bad");
  }
});

(async function init() {
  try {
    await startCamera();
  } catch (error) {
    updateCameraUi(false);
    streamStatus.textContent = "No se pudo abrir la camara";
    setResult(`Permiso de camara denegado o no disponible: ${error.message}`, "bad");
  }

  await loadUsers();
  await loadApiKeys();
})();

window.addEventListener("beforeunload", () => {
  stopCreateLoop();
  stopCamera();
});
