/* Studio Vidéo IA — orchestration du pipeline via l'API FastAPI (JS pur). */

"use strict";

const form = document.getElementById("video-form");
const ideaInput = document.getElementById("idea");
const ideaError = document.getElementById("idea-error");
const imageInput = document.getElementById("images");
const voiceInput = document.getElementById("voice");
const submitButton = document.getElementById("submit-btn");
const submitLabel = document.getElementById("submit-label");
const spinner = document.getElementById("spinner");
const alertBox = document.getElementById("alert");
const preview = document.getElementById("preview");
const download = document.getElementById("download");
const previewMeta = document.getElementById("preview-meta");
const statusPill = document.getElementById("api-status");
const statusLabel = document.getElementById("api-status-label");
const tabs = Array.from(document.querySelectorAll(".tab"));

const STEPS = ["script", "audio", "video", "result"];
const ICONS = { pending: "○", running: "◐", done: "✓", error: "!" };
const FORMAT_SIZES = {
  "9:16": [540, 960],
  "16:9": [960, 540],
  "1:1": [720, 720],
};

const state = { scenario: null, audio: null, video: null };

/* ---------- Utilitaires ---------- */

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload?.detail
        ? payload.detail
        : `Erreur HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

function showAlert(message) {
  alertBox.textContent = message;
  alertBox.hidden = false;
}

function hideAlert() {
  alertBox.hidden = true;
  alertBox.textContent = "";
}

function setLoading(loading) {
  submitButton.disabled = loading;
  spinner.hidden = !loading;
  submitLabel.textContent = loading ? "Génération en cours…" : "Générer la vidéo";
}

function setApiStatus(label, variant) {
  statusLabel.textContent = label;
  statusPill.classList.remove("online", "offline");
  if (variant) statusPill.classList.add(variant);
}

function activateStep(step) {
  tabs.forEach((tab) => {
    const active = tab.dataset.step === step;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  STEPS.forEach((name) => {
    document.getElementById(`panel-${name}`).hidden = name !== step;
  });
}

function markStep(step, status) {
  const tab = tabs.find((item) => item.dataset.step === step);
  if (!tab) return;
  tab.classList.remove("done", "running", "error");
  if (status !== "pending") tab.classList.add(status);
  document.getElementById(`icon-${step}`).textContent = ICONS[status];
}

function setPanel(step, html) {
  document.getElementById(`panel-${step}`).innerHTML = html;
}

function resetPipeline() {
  STEPS.forEach((step) => markStep(step, "pending"));
  setPanel("script", '<p class="muted">Génération du scénario en cours…</p>');
  setPanel("audio", '<p class="muted">En attente du scénario.</p>');
  setPanel("video", '<p class="muted">En attente de la voix off.</p>');
  setPanel("result", '<p class="muted">En attente du montage.</p>');
  preview.removeAttribute("src");
  download.hidden = true;
  download.removeAttribute("href");
  previewMeta.textContent = "Aucune vidéo générée pour le moment.";
  state.scenario = null;
  state.audio = null;
  state.video = null;
  activateStep("script");
}

/* ---------- Visuels de démonstration ---------- */

function wrapText(context, text, centerX, startY, maxWidth, lineHeight) {
  const words = String(text).split(/\s+/).filter(Boolean);
  let line = "";
  let y = startY;
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (context.measureText(candidate).width > maxWidth && line) {
      context.fillText(line, centerX, y);
      line = word;
      y += lineHeight;
    } else {
      line = candidate;
    }
  }
  if (line) context.fillText(line, centerX, y);
}

async function makePlaceholderFiles(scenes, format) {
  const [width, height] = FORMAT_SIZES[format] || FORMAT_SIZES["9:16"];
  const files = [];

  for (const [index, scene] of scenes.entries()) {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");

    const gradient = context.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, "#1a1236");
    gradient.addColorStop(0.55, "#2b1e63");
    gradient.addColorStop(1, "#0d7f70");
    context.fillStyle = gradient;
    context.fillRect(0, 0, width, height);

    context.fillStyle = "rgba(255, 255, 255, 0.06)";
    context.beginPath();
    context.arc(width * 0.82, height * 0.22, width * 0.28, 0, Math.PI * 2);
    context.fill();

    context.fillStyle = "#24d3a5";
    context.font = `bold ${Math.round(width * 0.045)}px sans-serif`;
    context.textAlign = "center";
    context.fillText(`SCÈNE ${index + 1}`, width / 2, height * 0.42);

    context.fillStyle = "#ffffff";
    context.font = `600 ${Math.round(width * 0.05)}px sans-serif`;
    wrapText(
      context,
      scene.description_visuelle || "",
      width / 2,
      height * 0.52,
      width * 0.82,
      Math.round(width * 0.075),
    );

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!blob) throw new Error("Impossible de créer le visuel de démonstration.");
    files.push(new File([blob], `scene-${index + 1}.png`, { type: "image/png" }));
  }
  return files;
}

async function uploadImages(files) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file, file.name));
  return apiRequest("/api/upload-images", { method: "POST", body });
}

/* ---------- Rendu des panneaux ---------- */

function renderScenario(scenario) {
  const scenes = scenario.scenes
    .map(
      (scene) => `
        <li>
          <strong>Scène ${escapeHtml(scene.index)}</strong>
          <p>${escapeHtml(scene.description_visuelle)}</p>
          <p><em>« ${escapeHtml(scene.texte_voix_off)} »</em></p>
          <div class="keywords"># ${scene.mots_cles_visuels.map(escapeHtml).join(" · # ")}</div>
        </li>`,
    )
    .join("");

  setPanel(
    "script",
    `<h3>${escapeHtml(scenario.titre)}</h3>
     <p class="muted">Format ${escapeHtml(scenario.format_video)} · ${scenario.scenes.length} scènes</p>
     <ul class="scene-list">${scenes}</ul>`,
  );
}

function renderAudio(audio) {
  setPanel(
    "audio",
    `<h3>Voix off et sous-titres prêts</h3>
     <div class="info-row"><span>Voix</span><span>${escapeHtml(audio.voix)}</span></div>
     <div class="info-row"><span>Audio</span><code>${escapeHtml(audio.audio_path)}</code></div>
     <div class="info-row"><span>Sous-titres</span><code>${escapeHtml(audio.sous_titres_path)}</code></div>
     <div class="info-row"><span>Format</span><span>${escapeHtml(audio.format_sous_titres).toUpperCase()}</span></div>`,
  );
}

function renderVideo(video, note) {
  setPanel(
    "video",
    `<h3>Montage terminé</h3>
     ${note ? `<p class="muted">${escapeHtml(note)}</p>` : ""}
     <div class="info-row"><span>Vidéo</span><code>${escapeHtml(video.video_path)}</code></div>
     <div class="info-row"><span>Durée</span><span>${video.duree_secondes} s</span></div>
     <div class="info-row">
       <span>Sous-titres incrustés</span>
       <span>${video.sous_titres_integres ? "oui" : "non (fichier séparé)"}</span>
     </div>`,
  );
}

function renderResult(video) {
  const sousTitres = video.sous_titres_integres
    ? "sous-titres incrustés"
    : "sous-titres livrés séparément";
  setPanel(
    "result",
    `<h3>Votre vidéo est prête 🎬</h3>
     <p class="muted">Durée : ${video.duree_secondes} s · ${sousTitres}</p>
     <p class="muted">Utilisez le bouton « Télécharger » ci-dessous pour récupérer le fichier.</p>`,
  );
  previewMeta.textContent = `Durée : ${video.duree_secondes} s · ${sousTitres}`;
}

/* ---------- Ordre d'exécution du pipeline ---------- */

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideAlert();

  const idea = ideaInput.value.trim();
  if (idea.length < 3) {
    ideaError.hidden = false;
    ideaInput.focus();
    return;
  }
  ideaError.hidden = true;

  const format = document.querySelector('input[name="format"]:checked').value;
  const voice = voiceInput.value;
  let currentStep = "script";

  resetPipeline();
  setLoading(true);

  try {
    markStep("script", "running");
    const scenario = await apiRequest("/api/generate-script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: idea, format_video: format }),
    });
    state.scenario = scenario;
    renderScenario(scenario);
    markStep("script", "done");

    currentStep = "audio";
    markStep("audio", "running");
    activateStep("audio");
    const narration = scenario.scenes
      .map((scene) => scene.texte_voix_off)
      .join(" ");
    const audio = await apiRequest("/api/generate-audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: narration,
        voice,
        format_sous_titres: "srt",
        dossier: "temp",
      }),
    });
    state.audio = audio;
    renderAudio(audio);
    markStep("audio", "done");

    currentStep = "video";
    markStep("video", "running");
    activateStep("video");
    let files = Array.from(imageInput.files || []);
    let note = "";
    if (files.length === 0) {
      files = await makePlaceholderFiles(scenario.scenes, format);
      note = "Visuels de démonstration générés automatiquement.";
    }
    setPanel(
      "video",
      `<p class="muted">Envoi de ${files.length} image(s) et montage en cours…</p>`,
    );
    const uploaded = await uploadImages(files);
    const video = await apiRequest("/api/assemble-video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_paths: uploaded.image_paths,
        audio_path: audio.audio_path,
        subtitle_path: audio.sous_titres_path,
        format_video: format,
      }),
    });
    state.video = video;
    renderVideo(video, note);
    markStep("video", "done");

    currentStep = "result";
    markStep("result", "done");
    activateStep("result");
    renderResult(video);

    const source = `/${video.video_path.replace(/^\/+/, "")}`;
    preview.src = source;
    preview.load();
    download.href = source;
    download.download = video.video_path.split("/").pop() || "video.mp4";
    download.hidden = false;
  } catch (error) {
    markStep(currentStep, "error");
    showAlert(error instanceof Error ? error.message : String(error));
  } finally {
    setLoading(false);
  }
});

tabs.forEach((tab) => {
  tab.addEventListener("click", () => activateStep(tab.dataset.step));
});

async function checkApi() {
  try {
    await apiRequest("/api/status");
    setApiStatus("API en ligne", "online");
  } catch {
    setApiStatus("API hors ligne", "offline");
  }
}

checkApi();
