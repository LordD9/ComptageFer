const assert = require("node:assert/strict");
const test = require("node:test");
const { remember, drain, photo, newId } = require("../comptagefer/offline.js");

test("a failed send keeps the photo and the same token", () => {
  const payload = {
    client_id: "jeton",
    snapshot: { precedent: { trip_id: "PREV" }, courant: { trip_id: "TRIP1" }, suivant: null },
  };

  const queued = remember([], payload);
  const again = remember(queued, { ...payload, passengers: 12 });

  assert.equal(again.length, 1);
  assert.equal(again[0].client_id, "jeton");
  assert.equal(again[0].snapshot.precedent.trip_id, "PREV");
  assert.equal(again[0].passengers, 12);
});

test("retry drops only the send that succeeded", async () => {
  const queued = [
    { client_id: "ok", snapshot: { courant: { trip_id: "A" } } },
    { client_id: "ko", snapshot: { courant: { trip_id: "B" } } },
  ];

  const kept = await drain(queued, async (item) => item.client_id === "ok");

  assert.deepEqual(kept.map((item) => item.client_id), ["ko"]);
});

test("photo is the selected train and its two neighbours", () => {
  const trains = [{ trip_id: "PREV" }, { trip_id: "TRIP1" }, { trip_id: "NEXT" }];

  assert.deepEqual(photo(trains, "TRIP1"), {
    precedent: { trip_id: "PREV" },
    courant: { trip_id: "TRIP1" },
    suivant: { trip_id: "NEXT" },
  });
  assert.equal(photo(trains, "PREV").precedent, null);
});

test("newId works when the browser refuses randomUUID", () => {
  const id = newId({
    randomUUID() {
      throw new Error("insecure");
    },
    getRandomValues(bytes) {
      for (let i = 0; i < bytes.length; i += 1) bytes[i] = i + 1;
      return bytes;
    },
  });

  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
});
