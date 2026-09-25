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

if (typeof module !== "undefined") {
  module.exports = { remember, photo, drain };
}
