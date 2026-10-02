from comptagefer.affichage import NAV_STYLE, navigation_html


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComptagesFer</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }
  main { max-width: 32rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
  h1 { font-size: 1.6rem; margin: 0 0 0.25rem; }
  p.hint { color: #5c564c; margin: 0 0 1rem; }
  label { display: block; font-weight: 650; margin: 1rem 0 0.4rem; }
  input, button, select { font: inherit; }
  input[type="search"], input[type="number"], input[type="text"], select {
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
  .counter { position: relative; display: flex; align-items: center; gap: 0.6rem; margin-top: 0.6rem; }
  .counter button { flex: 1; min-height: 3.6rem; font-size: 1.1rem; }
  #count-display { flex: 0 0 6rem; font: 1.8rem/1 system-ui, sans-serif; text-align: center; }
  .bulle {
    position: absolute; z-index: 2; pointer-events: none; white-space: nowrap;
    font: 700 1.3rem/1 system-ui, sans-serif; color: #1c1915; background: #fff;
    border: 1px solid #c9c1b4; border-radius: 2rem; padding: 0.4rem 0.8rem;
    transform: translateX(-50%); animation: bulle 900ms ease-out forwards;
  }
  .bulle.retour { background: #8a2b1b; border-color: #8a2b1b; color: #fff; }
  @keyframes bulle {
    0% { opacity: 0; transform: translateX(-50%) translateY(0) scale(0.7); }
    25% { opacity: 1; transform: translateX(-50%) translateY(-0.7rem) scale(1.12); }
    100% { opacity: 0; transform: translateX(-50%) translateY(-3.4rem) scale(0.9); }
  }
  #count-display.saut { animation: saut 240ms ease-out; }
  @keyframes saut { 45% { transform: scale(1.3); } }
  @media (prefers-reduced-motion: reduce) {
    /* Le retour visuel reste, il ne bouge plus. On ne supprime pas le retour
       visuel parce qu'on a coupé l'animation : c'est lui qui dit à l'usager que
       l'appui est passé. */
    .bulle { animation: none; opacity: 1; }
    #count-display.saut { animation: none; }
  }
</style>
</head>
<body>
<main>
  <h1>ComptagesFer</h1>
  <p class="hint">Choisissez votre train, puis comptez. Le reste vient du flux.</p>
  <section id="origin-step">
    <p class="hint">Indiquer les gares <strong>du trajet compté</strong>, et non pas celles de la ligne.</p>
    <label for="origin-q">Origine</label>
    <input id="origin-q" type="search" enterkeyhint="search" autocomplete="off" placeholder="Gare de départ du comptage">
    <div id="origin-list" class="choices"></div>
    <button class="ghost" id="near" type="button">Gare la plus proche</button>
  </section>
  <section id="destination-step" class="hidden">
    <div class="chip"><span id="origin-chip"></span><button class="ghost" id="change-origin" type="button">Changer</button></div>
    <label for="destination-q">Destination</label>
    <input id="destination-q" type="search" enterkeyhint="search" autocomplete="off" placeholder="Gare d'arrivée du comptage">
    <div id="destination-list" class="choices"></div>
  </section>
  <section id="train-step" class="hidden">
    <div class="chip"><span id="od-chip"></span><button class="ghost" id="change-od" type="button">Changer</button></div>
    <p class="hint">Trains des deux heures avant et après&nbsp;: TER, car, Intercités, TGV, et les trains franciliens (RER, ligne U). Le retard et la suppression viennent du flux, quand le réseau les publie&nbsp;: les trains franciliens n'ont pas de flux temps réel ici, ils sont donc toujours « programmé ».</p>
    <div id="trains" class="choices"></div>
    <button class="ghost" id="missing" type="button">Mon train n'est pas dans la liste</button>
  </section>
  <section id="mode-step" class="hidden">
    <div class="chip"><span id="mode-chip"></span><button class="ghost" id="change-mode" type="button">Changer</button></div>
    <div class="choices">
      <button id="mode-unique" type="button">Comptage unique</button>
      <button id="mode-snake" type="button">Serpent de charge</button>
    </div>
    <p class="hint">Le serpent : un compte portes fermées, puis montées et descentes à chaque arrêt, jusqu'à votre descente.</p>
    <p class="hint">Rappel&nbsp;: les gares choisies plus haut sont celles du <strong>comptage</strong>. En serpent, de la première gare où vous comptez à la dernière, même si la ligne ou votre voyage vont plus loin.</p>
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
    <p class="hint">Ce compte vaut pour l'interstation entre <span id="od-rappel"></span>. En gare, sans monter dans le train&nbsp;: prendre la dernière gare desservie avant l'arrivée (terminus) ou la première après le départ (origine).</p>
    <label for="passengers">Voyageurs dans le train</label>
    <div class="counter" id="counter">
      <button type="button" id="minus">−1</button>
      <span id="count-display" aria-live="polite">0</span>
      <button type="button" id="plus1">+1</button>
      <button type="button" id="plus5">+5</button>
      <button type="button" id="plus10">+10</button>
    </div>
    <p class="hint">Ou écrivez le nombre exact.</p>
    <input id="passengers" type="number" inputmode="numeric" min="0" step="1" placeholder="0">
    <p id="plausibilite" class="status" role="status" hidden></p>
    <label for="reliability">Fiabilité du compte, de 0 à 100</label>
    <input id="reliability" type="number" inputmode="numeric" min="0" max="100" step="1" value="80">
    <details>
      <summary>Indicateurs, pseudo, commentaire</summary>
      <label for="standing">Estimation de la part de gens debout</label>
      <input id="standing" type="number" inputmode="numeric" min="0" max="100" placeholder="0 à 100">
      <label for="seats">Part de places assises restantes</label>
      <input id="seats" type="number" inputmode="numeric" min="0" max="100" placeholder="0 à 100">
      <label for="imbalance">Écart de charge entre les voitures</label>
      <input id="imbalance" type="number" inputmode="numeric" min="0" max="100" placeholder="0 à 100">
      <label for="pseudo">Pseudo (facultatif)</label>
      <input id="pseudo" type="text" maxlength="40" autocomplete="nickname">
      <label for="comment">Commentaire, publié dans le CSV</label>
      <input id="comment" type="text" maxlength="280">
    </details>
    <details>
      <summary>Matériel roulant et composition</summary>
      <p class="hint">Facultatif, mais c'est ce qui rend l'effectif comparable d'une rame à l'autre&nbsp;: un train peut être une UM3, et vous n'avez compté qu'une voiture. Sans cette précision, 180 voyageurs dans une voiture d'une UM3 et 180 dans les trois sont le même relevé.</p>
      <label for="materiel">Type de matériel roulant</label>
      <input id="materiel" type="text" maxlength="40" list="materiels" placeholder="Z 20500, Z 6400, 2N NG…">
      <datalist id="materiels">
        <option value="Z 20500"></option>
        <option value="Z 20900"></option>
        <option value="Z 22500"></option>
        <option value="Z 6400"></option>
        <option value="2N NG"></option>
        <option value="2N NP"></option>
        <option value="Z 50000"></option>
      </datalist>
      <label for="composition">Composition de la rame</label>
      <select id="composition">
        <option value="">Je ne sais pas</option>
        <option value="US">US — une voiture</option>
        <option value="UM2">UM2 — deux voitures</option>
        <option value="UM3">UM3 — trois voitures</option>
      </select>
      <label for="perimetre">Ce que vous avez compté</label>
      <select id="perimetre">
        <option value="">—</option>
        <option value="voiture">Une seule voiture</option>
        <option value="um">Toute la rame</option>
      </select>
      <p id="materiel-note" class="status" role="status" hidden></p>
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
  // Le rappel d'interstation n'existait pas : rien ne disait à l'écran que les
  // deux gares choisies plus haut sont celles du comptage, et pas l'OD de la
  // ligne (issue #25).
  $("od-rappel").textContent = state.origin.name + " et " + state.destination.name;
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
      valeur + " personnes, c'est peut-être une erreur de frappe. " +
      "Vérifiez, mais vous pouvez envoyer quand même.";
    message.hidden = false;
  } else {
    message.hidden = true;
    message.textContent = "";
  }
}

// Un appui doit se voir, sans regarder le total. Compter dans un train, c'est
// regarder les voyageurs, pas l'écran : les yeux sont levés. Le total change,
// mais entre deux appuis rapprochés rien ne dit que le second est passé, et un
// doigt qui glisse sur un bouton ne change rien du tout. Une bulle qui monte du
// bouton, comme le retour d'un message, ferme le délai entre le geste et sa
// preuve.
const DUREE_BULLE = 900;
// Un comptage à la main, c'est des centaines d'appuis. Sans plafond, le DOM
// accumule des marqueurs pendant tout le trajet — et le navigateur paie pour
// des nœuds que personne ne verra. On garde les derniers : ce sont les seuls
// que l'usager peut encore regarder.
const BULLES_MAXI = 8;

function bulle(delta, bouton) {
  const zone = $("counter");
  const boite = bouton.getBoundingClientRect();
  const zone_boite = zone.getBoundingClientRect();
  const marqueur = document.createElement("span");
  marqueur.className = delta < 0 ? "bulle retour" : "bulle";
  marqueur.textContent = (delta > 0 ? "+" : "−") + Math.abs(delta);
  marqueur.style.left = (boite.left + boite.width / 2 - zone_boite.left) + "px";
  marqueur.style.top = (boite.top - zone_boite.top) + "px";
  // La bulle est décorative : le compteur porte déjà aria-live et annonce le
  // total. Un lecteur d'écran qui lirait « +5 » puis « 15 » à chaque appui
  // ferait doublon sur le geste.
  marqueur.setAttribute("aria-hidden", "true");
  zone.appendChild(marqueur);
  marqueur.addEventListener("animationend", () => marqueur.remove(), { once: true });
  // Le filet : une animation jamais démarrée (onglet en arrière-plan, motion
  // réduit) ne renvoie pas animationend, et la bulle resterait à l'écran.
  setTimeout(() => marqueur.remove(), DUREE_BULLE + 400);
  for (const trop of zone.querySelectorAll(".bulle")) {
    if (zone.querySelectorAll(".bulle").length > BULLES_MAXI) trop.remove();
    else break;
  }
  const affiche = $("count-display");
  affiche.classList.remove("saut");
  // Reflow forcé : sans lecture intermédiaire, le navigateur voit la même
  // classe re-posée et ne rejoue pas l'animation du tout.
  void affiche.offsetWidth;
  affiche.classList.add("saut");
}

function updateCount(delta, bouton) {
  const avant = state.passengers || 0;
  state.passengers = Math.max(0, avant + delta);
  $("count-display").textContent = state.passengers;
  $("passengers").value = state.passengers;
  signalerPlausibilite(state.passengers);
  // Un −1 sur un compte déjà à zéro ne fait rien : on n'annonce pas un geste
  // qui n'a rien changé, sinon l'usager croit pouvoir descendre sous zéro.
  if (state.passengers !== avant && bouton) bulle(state.passengers - avant, bouton);
}

$("plus1").onclick = (e) => updateCount(1, e.currentTarget);
$("plus5").onclick = (e) => updateCount(5, e.currentTarget);
$("plus10").onclick = (e) => updateCount(10, e.currentTarget);
$("minus").onclick = (e) => updateCount(-1, e.currentTarget);

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
    $("snake-fields").append(...field("snake-alighted", "Descentes", last ? "si vous les comptez" : "0"));
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Indicateurs de cette interstation";
    details.append(summary, ...field("snake-standing", "Estimation de la part de gens debout", ""), ...field("snake-seats", "Part de places assises restantes", ""), ...field("snake-imbalance", "Écart de charge en % entre les voitures", ""));
    $("snake-fields").append(details);
  }
  if (last) {
    $("snake-fields").append(...field("snake-reliability", "Fiabilité, de 0 à 100", "80"));
    $("snake-reliability").value = "80";
    // Un commentaire vaut aussi pour un serpent : train supprimé, car de
    // substitution, retard du précédent. Sans ce repli, le texte de /methode
    // prometrait un commentaire que l'écran ne permet pas de saisir.
    const extra = document.createElement("details");
    const resume = document.createElement("summary");
    resume.textContent = "Pseudo, commentaire";
    const pseudo = document.createElement("label");
    pseudo.htmlFor = "snake-pseudo";
    pseudo.textContent = "Pseudo (facultatif)";
    const pseudoInput = document.createElement("input");
    pseudoInput.id = "snake-pseudo";
    pseudoInput.type = "text";
    pseudoInput.maxLength = 40;
    pseudoInput.autocomplete = "nickname";
    // Le pseudo du compte est pré-rempli dans le champ du comptage unique,
    // par le serveur. Le serpent le recopie : sans cette ligne, le pseudo
    // serait rempli pour un comptage unique et absent pour un serpent, donc
    // présent une fois sur deux — et le champ du serpent est vide au
    // moment où la personne ne s'y attend pas le moins, à la fin d'un
    // relevé de dix arrêts.
    if ($("pseudo").value) pseudoInput.value = $("pseudo").value;
    const comment = document.createElement("label");
    comment.htmlFor = "snake-comment";
    comment.textContent = "Commentaire, publié dans le CSV";
    const commentInput = document.createElement("input");
    commentInput.id = "snake-comment";
    commentInput.type = "text";
    commentInput.maxLength = 280;
    extra.append(resume, pseudo, pseudoInput, comment, commentInput);
    $("snake-fields").append(extra);
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
    pseudo: $("snake-pseudo").value,
    comment: $("snake-comment").value,
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
    ...materiel(),
    snapshot: state.photo
  };
}
// Composition et périmètre vont ensemble, et le serveur les refuse séparés.
// Le dire à l'écran avant l'envoi vaut mieux qu'un 422 après un comptage déjà
// fait dans le train : l'usager est encore là pour corriger.
function signalerMateriel() {
  const composition = $("composition").value;
  const perimetre = $("perimetre").value;
  const message = $("materiel-note");
  let texte = "";
  if (composition && !perimetre) {
    texte = "Dites ce que vous avez compté : une voiture ou toute la rame.";
  } else if (!composition && perimetre) {
    texte = "Il faut la composition de la rame pour savoir ce que ce chiffre compte.";
  } else if (composition === "US" && perimetre === "um") {
    texte = "Une US, c'est une seule voiture : le périmètre « toute la rame » ne s'y applique pas.";
  }
  message.textContent = texte;
  message.hidden = !texte;
  return !texte;
}
$("composition").addEventListener("change", signalerMateriel);
$("perimetre").addEventListener("change", signalerMateriel);
function materiel() {
  const composition = $("composition").value;
  const perimetre = $("perimetre").value;
  if (!signalerMateriel()) throw new Error("composition et périmètre incohérents");
  return {
    materiel: $("materiel").value.trim(),
    composition: composition,
    perimetre: perimetre
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
  // Le contrôle de cohérence a son propre message : le try/catch en dessous
  // sert à la file hors ligne, et son texte y parlait de réseau.
  if (!signalerMateriel()) {
    $("error").textContent = $("materiel-note").textContent;
    return;
  }
  let body;
  try {
    body = countPayload();
  } catch (error) {
    $("error").textContent = "Le compte n'a pas pu partir. Réessayez.";
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
      $("error").textContent = "Le compte n'a pas été gardé. Vérifiez l'effectif et la fiabilité.";
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

# Le champ pseudo du comptage unique, tel qu'il est écrit dans `PAGE`. La
# substitution se fait sur cette chaîne exacte : un `id` qui change ne peut
# pas produire une page silencieusement sans pseudo, parce que le remplacement
# ne trouve plus rien et que `page_avec_pseudo` le voit.
_CHAMP_PSEUDO = '<input id="pseudo" type="text" maxlength="40" autocomplete="nickname">'


def page_avec_pseudo(pseudo: str) -> str:
    """`PAGE` avec le pseudo de la session déjà rempli.

    Le comptage unique a un champ pseudo dans le HTML ; le serpent le crée
    en JavaScript, au moment où l'on arrive au dernier arrêt. Les deux
    reçoivent la même valeur par défaut, sinon le pseudo serait rempli pour
    un comptage unique et absent pour un serpent — donc présent une fois
    sur deux, ce qui est la pire des deux réponses. Le serpent lit le champ
    du comptage unique, qui porte donc la valeur.

    La valeur est **échappée** : elle vient de la base, donc d'une donnée,
    et `docs/regles.md` § 1 est sans exception sur ce point. Un pseudo
    contenant une double quote fermerait l'attribut et injecterait du HTML
    sur la page de comptage, qui est la page la plus visitée du site.

    Le remplacement est compté : un `PAGE` modifié sans que le champ soit
    trouvé doit lever, pas rendre une page qui a l'air pré-remplie et qui
    ne l'est pas — le défaut que ce correctif corrige, ailleurs.
    """
    from html import escape

    if not pseudo:
        return PAGE
    remplacement = (
        '<input id="pseudo" type="text" maxlength="40" autocomplete="nickname"'
        f' value="{escape(pseudo[:40], quote=True)}">'
    )
    if _CHAMP_PSEUDO not in PAGE:
        raise ValueError("le champ pseudo a changé de forme : page_avec_pseudo ne sait plus le remplir")
    return PAGE.replace(_CHAMP_PSEUDO, remplacement)


def page_comptage(pseudo: str = "") -> str:
    page = page_avec_pseudo(pseudo)
    page = page.replace("</style>", f"{NAV_STYLE}</style>", 1)
    return page.replace("<body>", f"<body>{navigation_html('/')}", 1)
