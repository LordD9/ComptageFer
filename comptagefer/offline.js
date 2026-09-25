function remember(queue, payload) {
  return queue.filter((item) => item.client_id !== payload.client_id).concat([payload]);
}

function photo(trains, tripId) {
  const index = trains.findIndex((train) => train.trip_id === tripId);
  if (index < 0) {
    return { precedent: null, courant: null, suivant: null };
  }
  return {
    precedent: index > 0 ? trains[index - 1] : null,
    courant: trains[index],
    suivant: index + 1 < trains.length ? trains[index + 1] : null,
  };
}

async function drain(queue, send) {
  const kept = [];
  for (const item of queue) {
    try {
      if (!(await send(item))) kept.push(item);
    } catch (_error) {
      kept.push(item);
    }
  }
  return kept;
}

function newId(source) {
  const cryptoSource = source || (typeof crypto !== "undefined" ? crypto : null);
  try {
    if (cryptoSource && cryptoSource.randomUUID) return cryptoSource.randomUUID();
  } catch (_error) {
    // Sur http://10.x, le navigateur refuse randomUUID. On continue sans.
  }
  const bytes = new Uint8Array(16);
  cryptoSource.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

if (typeof module !== "undefined") {
  module.exports = { remember, photo, drain, newId };
}
