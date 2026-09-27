PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComptageFer</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }
  main { max-width: 32rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
  h1 { font-size: 1.6rem; margin: 0 0 0.25rem; }
  p.hint { color: #5c564c; margin: 0 0 1rem; }
  label { display: block; font-weight: 650; margin: 1rem 0 0.4rem; }
  input, button, select { font: inherit; }
  input[type="search"], input[type="number"], input[type="text"] {
    width: 100%; box-sizing: border-box; min-height: 3.2rem; padding: 0.6rem 0.8rem;
    border: 1px solid #c9c1b4; border-radius: 0.8rem; background: #fff;
  }
  button { min-height: 3.2rem; border: 0; border-radius: 0.8rem; padding: 0.7rem 1rem; background: #1c1915; color: #fff; }
  button.ghost { background: #fff; color: #1c1915; border: 1px solid #c9c1b4; }
  .choices { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.6rem; }
  .choices button, .train { text-align: left; background: #fff; color: #1c1915; border: 1px solid #c9c1b4; }
  .train strong { display: block; font-size: 1.5rem; }
  .chip { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; background: #fff; border-radius: 0.8rem; padding: 0.7rem 0.8rem; margin-top: 0.6rem; }
  .hidden { display: none; }
  .status { font-size: 0.95rem; color: #5c564c; }
  .bad { color: #8a2b1b; }
  .counter { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.6rem; }
  .counter button { flex: 1; min-height: 3.6rem; font-size: 1.1rem; }
  #count-display { flex: 0 0 6rem; font: 1.8rem/1 system-ui, sans-serif; text-align: center; }
</style>
</head>
<body>
<main>
  <h1>ComptageFer</h1>
  <p class="hint">Choisis ton train, puis compte. Le reste vient du flux. <a href="/comptages">Voir les comptages</a> · <a href="/carte">Carte</a> · <a href="/rechercher">Rechercher une ligne</a></p>
  <section id="origin-step">
    <label for="origin-q">Origine</label>
    <input id="origin-q" type="search" enterkeyhint="search" autocomplete="off" placeholder="Gare de départ">
    <div id="origin-list" class="choices"></div>
    <button class="ghost" id="near" type="button">Gare la plus proche</button>
  </section>
  <section id="destination-step" class="hidden">
    <div class="chip"><span id="origin-chip"></span><button class="ghost" id="change-origin" type="button">Changer</button></div>
    <label for="destination-q">Destination</label>
    <input id="destination-q" type="search" enterkeyhint="search" autocomplete="off" placeholder="Gare d'arrivée">
    <div id="destination-list" class="choices"></div>
  </section>
  <section id="train-step" class="hidden">
    <div class="chip"><span id="od-chip"></span><button class="ghost" id="change-od" type="button">Changer</button></div>
    <p class="hint">Trains des deux heures avant et après, TER, car, Intercités et TGV. Le retard et la suppression viennent du flux. Sinon le train est seulement programmé.</p>
    <div id="trains" class="choices"></div>
    <button class="ghost" id="missing" type="button">Mon train n'est pas dans la liste</button>
  </section>
  <section id="mode-step" class="hidden">
    <div class="chip"><span id="mode-chip"></span><button class="ghost" id="change-mode" type="button">Changer</button></div>
    <div class="choices">
      <button id="mode-unique" type="button">Un seul compte</button>
      <button id="mode-snake" type="button">Serpent de charge</button>
    </div>
    <p class="hint">Le serpent : un compte portes fermées, puis montées et descentes à chaque arrêt, jusqu'à ta descente.</p>
  </section>
  <section id="snake-step" class="hidden">
    <div class="chip"><span id="snake-chip"></span><button class="ghost" id="snake-back" type="button">Retour</button></div>
    <p id="snake-title"></p>
    <p id="snake-resume" class="status"></p>
    <p id="snake-drop-wrap" class="hidden"><button class="ghost" id="snake-drop" type="button">Abandonner la reprise</button></p>
    <div id="snake-fields"></div>
    <p id="snake-load" class="status"></p>
    <p><button id="snake-next" type="button">Suivant</button></p>
    <p id="snake-error" class="bad"></p>
  </section>
  <section id="form-step" class="hidden">
    <div class="chip"><span id="train-chip"></span><button class="ghost" id="change-train" type="button">Changer</button></div>
    <label for="passengers">Voyageurs dans le train</label>
    <div class="counter" id="counter">
      <button type="button" id="minus">−1</button>
      <span id="count-display" aria-live="polite">0</span>
      <button type="button" id="plus1">+1</button>
      <button type="button" id="plus5">+5</button>
      <button type="button" id="plus10">+10</button>
    </div>
    <p class="hint">Ou écris le nombre exact.</p>
    <input id="passengers" type="number" inputmode="numeric" min="0" step="1" placeholder="0">
    <p id="plausibilite" class="status" role="status" hidden></p>
    <label for="reliability">Fiabilité du compte, de 0 à 100</label>
    <input id="reliability" type="number" inputmode="numeric" min="0" max="100" step="1" value="80">
    <details>
      <summary>Pseudo, commentaire</summary>
      <label for="standing">Part de gens debout, si tu la vois</label>
      <input id="standing" type="number" inputmode="numeric" min="0" max="100" placeholder="0 à 100">
      <label for="seats">Part de places assises restantes</label>
      <input id="seats" type="number" inputmode="numeric" min="0" max="100" placeholder="0 à 100">
      <label for="imbalance">Écart de charge entre les voitures</label>
      <input id="imbalance" type="number" inputmode="numeric" min="0" max="100" placeholder="0 à 100">
      <label for="pseudo">Pseudo, si tu veux</label>
      <input id="pseudo" type="text" maxlength="40" autocomplete="nickname">
      <label for="comment">Commentaire</label>
      <input id="comment" type="text" maxlength="280">
    </details>
    <p><button id="send" type="button">Enregistrer le comptage</button></p>
    <p id="error" class="bad"></p>
  </section>
  <section id="done-step" class="hidden">
    <h2>C'est noté.</h2>
    <p class="hint">Le comptage est gardé. Merci.</p>
    <button id="again" type="button">Un autre comptage</button>
  </section>
</main>
<script src="/offline.js"></script>
<script>
const state = { origin: null, destination: null, trip: null, trains: [], passengers: 0 };
const $ = (id) => document.getElementById(id);
// Les étapes se listent ici à la main depuis l'ajout du serpent : mode-step
// et snake-step manquaient, donc choisir un train masquait toutes les
// sections et l'utilisateur restait sur un écran vide. On prend le DOM comme
// source de vérité, plus besoin d'entretenir la liste.
function show(id) {
  for (const step of document.querySelectorAll("main > section")) {
    step.classList.toggle("hidden", step.id !== id);
  }
}
function clientId() {
  let value = localStorage.getItem("comptagefer-client");
  if (!value) {
    value = newId();
    localStorage.setItem("comptagefer-client", value);
  }
  return value;
}
async function search(query, target) {
  target.replaceChildren();
  if (query.trim().length < 2) return;
  const response = await fetch("/api/stops?q=" + encodeURIComponent(query));
  const stops = await response.json();
  for (const stop of stops) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = stop.name;
    button.onclick = () => chooseStop(target.id, stop);
    target.appendChild(button);
  }
}
function chooseStop(listId, stop) {
  if (listId === "origin-list") {
    state.origin = stop;
    $("origin-chip").textContent = stop.name;
    show("destination-step");
    $("destination-q").focus();
  } else {
    state.destination = stop;
    $("od-chip").textContent = state.origin.name + " → " + stop.name;
    loadTrains();
  }
}
function formatTrain(train) {
  const time = new Date(train.departure_time).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const kind = train.kind ? " · " + train.kind : "";
  if (train.etat === "supprimé" || train.status === "CANCELED") return time + kind + " · supprimé";
  if (train.delay_seconds) {
    const minutes = Math.round(train.delay_seconds / 60);
    return time + kind + " · " + (minutes > 0 ? "+" : "") + minutes + " min";
  }
  return time + kind + " · " + (train.etat || "à l'heure");
}
async function loadTrains() {
  show("train-step");
  $("trains").textContent = "Recherche…";
  const at = new Date().toISOString();
  const url = "/api/trips?from=" + encodeURIComponent(state.origin.stop_id)
    + "&to=" + encodeURIComponent(state.destination.stop_id)
    + "&at=" + encodeURIComponent(at);
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    $("trains").textContent = "Le choix du train demande le réseau.";
    return;
  }
  if (!response.ok) {
    $("trains").textContent = "Le choix du train demande le réseau.";
    return;
  }
  state.trains = await response.json();
  $("trains").replaceChildren();
  if (!state.trains.length) {
    $("trains").textContent = "Aucun train sur cette origine-destination dans les 4 heures.";
    return;
  }
  for (const train of state.trains) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "train";
    const strong = document.createElement("strong");
    strong.textContent = formatTrain(train);
    button.appendChild(strong);
    button.onclick = () => {
      state.trip = train;
      state.photo = snapshot(train);
      $("mode-chip").textContent = formatTrain(train);
      show("mode-step");
    };
    $("trains").appendChild(button);
  }
}
$("origin-q").addEventListener("input", () => search($("origin-q").value, $("origin-list")));
$("destination-q").addEventListener("input", () => search($("destination-q").value, $("destination-list")));
$("change-origin").onclick = () => show("origin-step");
$("change-od").onclick = () => show("destination-step");
$("change-train").onclick = () => show("train-step");
$("change-mode").onclick = () => show("train-step");
$("mode-unique").onclick = () => {
  $("train-chip").textContent = formatTrain(state.trip);
  state.passengers = 0;
  $("count-display").textContent = "0";
  $("passengers").value = 0;
  signalerPlausibilite(0);
  show("form-step");
  $("passengers").focus();
};

// Un seuil de plausibilité, pas une capacité. Le GTFS national n'a aucun
// fichier de matériel et le flux temps réel ne donne pas de capacité : on
// ne peut donc pas dire « ce train a 240 places, c'est trop ». Ce seuil sert
// seulement à attraper une erreur de frappe ou un double envoi. Il est
// volontairement au-dessus de tout matériel français réel, et il ne bloque
// jamais : le plan dit « signalé, pas bloqué ».
const SEUIL_PLAUSIBILITE = 1200;

function signalerPlausibilite(valeur) {
  const message = $("plausibilite");
  if (valeur > SEUIL_PLAUSIBILITE) {
    message.textContent =
      valeur + " personnes, c'est au-delà de ce que contient un train français. " +
      "C'est peut-être une erreur de frappe — vérifie, mais tu peux envoyer quand même.";
    message.hidden = false;
  } else {
    message.hidden = true;
    message.textContent = "";
  }
}

function updateCount(delta) {
  state.passengers = Math.max(0, (state.passengers || 0) + delta);
  $("count-display").textContent = state.passengers;
  $("passengers").value = state.passengers;
  signalerPlausibilite(state.passengers);
}

$("plus1").onclick = () => updateCount(1);
$("plus5").onclick = () => updateCount(5);
$("plus10").onclick = () => updateCount(10);
$("minus").onclick = () => updateCount(-1);

$("passengers").addEventListener("input", (e) => {
  const v = parseInt(e.target.value, 10);
  state.passengers = isNaN(v) || v < 0 ? 0 : v;
  $("count-display").textContent = state.passengers;
  signalerPlausibilite(state.passengers);
});

$("mode-snake").onclick = startSnake;
$("snake-back").onclick = () => show("mode-step");
$("snake-drop").onclick = () => {
  clearSnake();
  $("snake-resume").textContent = "";
  $("snake-drop-wrap").classList.add("hidden");
  show("mode-step");
};
function field(id, label, placeholder) {
  const wrap = document.createElement("label");
  wrap.htmlFor = id;
  wrap.textContent = label;
  const input = document.createElement("input");
  input.id = id;
  input.type = "number";
  input.inputMode = "numeric";
  input.min = "0";
  input.step = "1";
  input.placeholder = placeholder || "0";
  return [wrap, input];
}
// Reprise après fermeture d'onglet. La file hors ligne ne suffisait pas : elle
// ne transporte que ce qui doit partir vers le serveur, pas un serpent à
// moitié saisi qu'on était en train de remplir.
const CLE_SERPENT = "comptagefer-serpent";

function writeSnake() {
  // Rien à reprendre tant qu'aucun arrêt n'est filled : un serpent vide
  // proposerait « reprendre » pour rien.
  if (!state.snakeIndex && !state.legs.length) {
    localStorage.removeItem(CLE_SERPENT);
    return;
  }
  try {
    localStorage.setItem(CLE_SERPENT, JSON.stringify({
      origin: state.origin,
      destination: state.destination,
      trip: state.trip,
      stops: state.stops,
      snakeIndex: state.snakeIndex,
      legs: state.legs,
    }));
  } catch (error) {
    // Un navigateur en quota plein, ou en navigation privée : la saisie
    // continue, elle ne sera simplement pas reprenable.
  }
}

function clearSnake() {
  localStorage.removeItem(CLE_SERPENT);
}

function offerSnake() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(CLE_SERPENT) || "null");
  } catch (error) {
    return false;
  }
  if (!saved || !saved.stops || saved.stops.length < 2) {
    clearSnake();
    return false;
  }
  state.origin = saved.origin;
  state.destination = saved.destination;
  state.trip = saved.trip;
  state.stops = saved.stops;
  state.snakeIndex = saved.snakeIndex || 0;
  state.legs = Array.isArray(saved.legs) ? saved.legs : [];
  show("snake-step");
  renderSnake();
  $("snake-resume").textContent = "Reprise sur ce téléphone : arrêt " + (state.snakeIndex + 1)
    + " sur " + state.stops.length + ".";
  $("snake-drop-wrap").classList.remove("hidden");
  return true;
}

async function startSnake() {
  $("snake-error").textContent = "";
  show("snake-step");
  $("snake-title").textContent = "Chargement des arrêts…";
  $("snake-fields").replaceChildren();
  const url = "/api/trip-stops?trip=" + encodeURIComponent(state.trip.trip_id)
    + "&from=" + encodeURIComponent(state.origin.stop_id)
    + "&to=" + encodeURIComponent(state.destination.stop_id);
  try {
    const response = await fetch(url);
    state.stops = await response.json();
  } catch (error) {
    state.stops = [];
  }
  if (!state.stops || state.stops.length < 2) {
    $("snake-title").textContent = "Les arrêts de ce train ne sont pas chargés.";
    $("snake-next").textContent = "Faire un seul compte";
    $("snake-next").onclick = () => $("mode-unique").click();
    return;
  }
  state.snakeIndex = 0;
  state.legs = [];
  clearSnake();
  renderSnake();
}
function renderSnake() {
  const stop = state.stops[state.snakeIndex];
  const last = state.snakeIndex === state.stops.length - 1;
  $("snake-chip").textContent = (state.snakeIndex + 1) + " / " + state.stops.length;
  $("snake-title").textContent = state.snakeIndex === 0
    ? "Portes fermées à " + stop.name
    : "À " + stop.name;
  $("snake-fields").replaceChildren();
  if (state.snakeIndex === 0) {
    $("snake-fields").append(...field("snake-onboard", "Voyageurs à bord", "0"));
  } else {
    $("snake-fields").append(...field("snake-boarded", "Montées", "0"));
    $("snake-fields").append(...field("snake-alighted", "Descentes", last ? "si tu les comptes" : "0"));
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Indicateurs de cette interstation";
    details.append(summary, ...field("snake-standing", "Part debout", ""), ...field("snake-seats", "Places assises restantes", ""), ...field("snake-imbalance", "Écart de charge", ""));
    $("snake-fields").append(details);
  }
  if (last) {
    $("snake-fields").append(...field("snake-reliability", "Fiabilité, de 0 à 100", "80"));
    $("snake-reliability").value = "80";
  }
  $("snake-load").textContent = aboardText();
  $("snake-next").textContent = last ? "Enregistrer le serpent" : "Suivant";
  $("snake-next").onclick = last ? saveSnake : nextSnake;
  $("snake-error").textContent = "";
}
function readInt(id, required) {
  const value = $(id).value;
  if (value === "") return required ? NaN : null;
  return Number(value);
}
function nextSnake() {
  const leg = currentLeg();
  if (!leg) {
    $("snake-error").textContent = "Il manque un nombre.";
    return;
  }
  state.legs.push(leg);
  state.snakeIndex += 1;
  writeSnake();
  renderSnake();
}
function currentLeg() {
  const stop = state.stops[state.snakeIndex];
  if (state.snakeIndex === 0) {
    const onboard = readInt("snake-onboard", true);
    if (!Number.isInteger(onboard) || onboard < 0) return null;
    return { stop_id: stop.stop_id, stop_name: stop.name, onboard };
  }
  const boarded = readInt("snake-boarded", true);
  const alighted = readInt("snake-alighted", state.snakeIndex !== state.stops.length - 1);
  if (!Number.isInteger(boarded) || boarded < 0) return null;
  if (alighted !== null && (!Number.isInteger(alighted) || alighted < 0)) return null;
  const leg = { stop_id: stop.stop_id, stop_name: stop.name, boarded, alighted };
  for (const [id, key] of [["snake-standing", "standing"], ["snake-seats", "seats_free"], ["snake-imbalance", "imbalance"]]) {
    const value = readInt(id, false);
    if (value !== null) leg[key] = value;
  }
  return leg;
}
function aboardText() {
  if (!state.legs.length) return "";
  let total = state.legs[0].onboard;
  for (const leg of state.legs.slice(1)) {
    if (leg.alighted === null) return "À bord : compte incomplet";
    total += leg.boarded - leg.alighted;
  }
  return "À bord avant cet arrêt : " + total;
}
async function saveSnake() {
  const leg = currentLeg();
  if (!leg) {
    $("snake-error").textContent = "Il manque un nombre.";
    return;
  }
  const reliability = readInt("snake-reliability", true);
  if (!Number.isInteger(reliability) || reliability < 0 || reliability > 100) {
    $("snake-error").textContent = "La fiabilité va de 0 à 100.";
    return;
  }
  const body = {
    client_id: clientId(),
    kind: "serpent",
    origin_stop_id: state.origin.stop_id,
    destination_stop_id: state.destination.stop_id,
    origin_name: state.origin.name,
    destination_name: state.destination.name,
    trip_id: state.trip.trip_id,
    passengers: state.legs[0].onboard,
    reliability,
    snapshot: state.photo,
    legs: state.legs.concat([leg])
  };
  try {
    const response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body)
    });
    if (response.ok) {
      localStorage.removeItem("comptagefer-client");
      // Le serpent est parti : le proposer à la reprise serait proposer de
      // compter deux fois le même trajet.
      clearSnake();
      show("done-step");
      return;
    }
    if (response.status < 500) {
      $("snake-error").textContent = "Le serpent n'a pas été gardé.";
      return;
    }
  } catch (error) {
    writeQueue(remember(readQueue(), body));
    // Le payload est dans la file, il partira au retour du réseau. Le serpent
    // n'est donc plus une saisie en cours : le garder en reprise proposerait de
    // compter deux fois le même trajet.
    clearSnake();
    $("snake-error").textContent = "Pas de réseau. Le serpent est gardé sur ce téléphone.";
    return;
  }
  writeQueue(remember(readQueue(), body));
  clearSnake();
  $("snake-error").textContent = "Pas de réseau. Le serpent est gardé sur ce téléphone.";
}
$("near").onclick = () => {
  navigator.geolocation.getCurrentPosition(async (position) => {
    const url = "/api/stops/nearest?lat=" + position.coords.latitude + "&lon=" + position.coords.longitude;
    const response = await fetch(url);
    const stops = await response.json();
    $("origin-list").replaceChildren();
    for (const stop of stops) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = stop.name;
      button.onclick = () => chooseStop("origin-list", stop);
      $("origin-list").appendChild(button);
    }
  });
};
$("missing").onclick = async () => {
  await fetch("/api/missing", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_id: clientId(),
      origin_stop_id: state.origin.stop_id,
      destination_stop_id: state.destination.stop_id
    })
  });
  show("done-step");
};
function optionalNumber(id) {
  return $(id).value === "" ? null : Number($(id).value);
}

function countPayload() {
  return {
    client_id: clientId(),
    origin_stop_id: state.origin.stop_id,
    destination_stop_id: state.destination.stop_id,
    origin_name: state.origin.name,
    destination_name: state.destination.name,
    trip_id: state.trip.trip_id,
    // state.passengers est l'unique source : le compteur et le champ l'écrivent tous les deux.
    passengers: state.passengers,
    reliability: Number($("reliability").value),
    pseudo: $("pseudo").value,
    comment: $("comment").value,
    standing: optionalNumber("standing"),
    seats_free: optionalNumber("seats"),
    imbalance: optionalNumber("imbalance"),
    snapshot: state.photo
  };
}
function readQueue() {
  return JSON.parse(localStorage.getItem("comptagefer-queue") || "[]");
}
function writeQueue(queue) {
  localStorage.setItem("comptagefer-queue", JSON.stringify(queue));
}
async function postCount(body) {
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body)
  });
  if (response.status >= 500) return false;
  return response.ok || response.status < 500;
}
async function flushQueue() {
  const queue = readQueue();
  if (!queue.length) return;
  const kept = await drain(queue, postCount);
  const sent = new Set(queue.map((item) => item.client_id));
  for (const item of kept) sent.delete(item.client_id);
  if (sent.has(localStorage.getItem("comptagefer-client"))) {
    localStorage.removeItem("comptagefer-client");
  }
  writeQueue(kept);
}
window.addEventListener("online", flushQueue);
flushQueue();
// Un serpent à moitié saisi survit à la fermeture de l'onglet : on le propose
// au chargement, plutôt que de laisser repartir de zéro au premier arrêt.
offerSnake();
$("send").onclick = async () => {
  $("error").textContent = "";
  let body;
  try {
    body = countPayload();
  } catch (error) {
    $("error").textContent = "Le compte n'a pas pu partir. Réessaie.";
    return;
  }
  try {
    const response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body)
    });
    if (response.ok) {
      localStorage.removeItem("comptagefer-client");
      writeQueue(readQueue().filter((item) => item.client_id !== body.client_id));
      show("done-step");
      return;
    }
    if (response.status < 500) {
      $("error").textContent = "Le compte n'a pas été gardé. Vérifie l'effectif et la fiabilité.";
      return;
    }
  } catch (error) {
    writeQueue(remember(readQueue(), body));
    $("error").textContent = "Pas de réseau. Le comptage est gardé sur ce téléphone, photo comprise.";
    return;
  }
  writeQueue(remember(readQueue(), body));
  $("error").textContent = "Pas de réseau. Le comptage est gardé sur ce téléphone, photo comprise.";
};
$("again").onclick = () => location.reload();
</script>
</body>
</html>
"""
