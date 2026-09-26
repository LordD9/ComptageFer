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
</style>
</head>
<body>
<main>
  <h1>ComptageFer</h1>
  <p class="hint">Choisis ton train, puis compte. Le reste vient du flux. <a href="/comptages">Voir les comptages</a></p>
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
    <div id="snake-fields"></div>
    <p id="snake-load" class="status"></p>
    <p><button id="snake-next" type="button">Suivant</button></p>
    <p id="snake-error" class="bad"></p>
  </section>
  <section id="form-step" class="hidden">
    <div class="chip"><span id="train-chip"></span><button class="ghost" id="change-train" type="button">Changer</button></div>
    <label for="passengers">Voyageurs dans le train</label>
    <input id="passengers" type="number" inputmode="numeric" min="0" step="1" placeholder="0">
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
const state = { origin: null, destination: null, trip: null, trains: [] };
const $ = (id) => document.getElementById(id);
function show(id) {
  for (const step of ["origin-step", "destination-step", "train-step", "form-step", "done-step"]) {
    $(step).classList.toggle("hidden", step !== id);
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
  show("form-step");
  $("passengers").focus();
};
$("mode-snake").onclick = startSnake;
$("snake-back").onclick = () => show("mode-step");
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
      show("done-step");
      return;
    }
    if (response.status < 500) {
      $("snake-error").textContent = "Le serpent n'a pas été gardé.";
      return;
    }
  } catch (error) {
    writeQueue(remember(readQueue(), body));
    $("snake-error").textContent = "Pas de réseau. Le serpent est gardé sur ce téléphone.";
    return;
  }
  writeQueue(remember(readQueue(), body));
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
    passengers: Number($("passengers").value),
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
