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
  <p class="hint">Choisis ton train, puis compte. Le reste vient du flux.</p>
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
    <p class="hint">Trains vus par le flux, deux heures avant et après maintenant. L'heure lointaine n'y est pas encore.</p>
    <div id="trains" class="choices"></div>
    <button class="ghost" id="missing" type="button">Mon train n'est pas dans la liste</button>
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
    value = crypto.randomUUID();
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
  if (train.status === "CANCELED") return time + " · supprimé";
  if (train.delay_seconds) return time + " · +" + Math.round(train.delay_seconds / 60) + " min";
  return time + " · à l'heure";
}
async function loadTrains() {
  show("train-step");
  $("trains").textContent = "Recherche…";
  const at = new Date().toISOString();
  const url = "/api/trips?from=" + encodeURIComponent(state.origin.stop_id)
    + "&to=" + encodeURIComponent(state.destination.stop_id)
    + "&at=" + encodeURIComponent(at);
  const response = await fetch(url);
  state.trains = await response.json();
  $("trains").replaceChildren();
  if (!state.trains.length) {
    $("trains").textContent = "Aucun train vu par le flux sur cette origine-destination.";
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
      $("train-chip").textContent = formatTrain(train);
      show("form-step");
      $("passengers").focus();
    };
    $("trains").appendChild(button);
  }
}
$("origin-q").addEventListener("input", () => search($("origin-q").value, $("origin-list")));
$("destination-q").addEventListener("input", () => search($("destination-q").value, $("destination-list")));
$("change-origin").onclick = () => show("origin-step");
$("change-od").onclick = () => show("destination-step");
$("change-train").onclick = () => show("train-step");
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
$("send").onclick = async () => {
  $("error").textContent = "";
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_id: clientId(),
      origin_stop_id: state.origin.stop_id,
      destination_stop_id: state.destination.stop_id,
      trip_id: state.trip.trip_id,
      passengers: Number($("passengers").value),
      reliability: Number($("reliability").value),
      pseudo: $("pseudo").value,
      comment: $("comment").value,
      standing: $("standing").value === "" ? null : Number($("standing").value),
      snapshot: state.trains
    })
  });
  if (!response.ok) {
    $("error").textContent = "Le compte n'a pas été gardé. Vérifie l'effectif et la fiabilité.";
    return;
  }
  localStorage.removeItem("comptagefer-client");
  show("done-step");
};
$("again").onclick = () => location.reload();
</script>
</body>
</html>
"""
