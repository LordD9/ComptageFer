from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from comptagefer.rt import (
    alert_count,
    departure_delay,
    poll_once,
    purge_older_than,
    schedule_relationship,
    store_trip_updates,
    trip_count,
)


def _feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    entity = feed.entity.add()
    entity.id = "1"
    entity.trip_update.trip.trip_id = "TRIP1"
    entity.trip_update.trip.start_date = "20260924"
    entity.trip_update.trip.schedule_relationship = (
        gtfs_realtime_pb2.TripDescriptor.CANCELED
    )
    stop = entity.trip_update.stop_time_update.add()
    stop.stop_id = "STOP"
    stop.departure.delay = 300
    return feed.SerializeToString()


def test_stores_a_trip_update(tmp_path: Path):
    database = tmp_path / "rt.db"
    fetched_at = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)

    stored = store_trip_updates(database, _feed(), fetched_at)

    assert stored == 1
    assert trip_count(database) == 1
    assert schedule_relationship(database, "TRIP1") == "CANCELED"
    assert departure_delay(database, "TRIP1", "STOP") == 300


def test_forgets_updates_older_than_six_hours(tmp_path: Path):
    database = tmp_path / "rt.db"
    fetched_at = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    store_trip_updates(database, _feed(), fetched_at)

    purge_older_than(database, now=fetched_at + timedelta(hours=6, minutes=1))

    assert trip_count(database) == 0


def _alerts() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    entity = feed.entity.add()
    entity.id = "ALERT1"
    entity.alert.header_text.translation.add().text = "Train supprimé"
    return feed.SerializeToString()


def test_stores_a_service_alert(tmp_path: Path):
    database = tmp_path / "rt.db"
    fetched_at = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)

    from comptagefer.rt import store_service_alerts

    stored = store_service_alerts(database, _alerts(), fetched_at)

    assert stored == 1
    assert alert_count(database) == 1


def test_empty_trip_updates_fall_back_to_siri(tmp_path: Path):
    database = tmp_path / "rt.db"
    called: list[str] = []

    def fetch_trips() -> bytes:
        called.append("tu")
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.timestamp = 1
        return feed.SerializeToString()

    def fetch_siri() -> bytes:
        called.append("siri")
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri">
  <ServiceDelivery>
    <EstimatedTimetableDelivery>
      <EstimatedJourneyVersionFrame>
        <EstimatedVehicleJourney>
          <Cancellation>true</Cancellation>
          <FramedVehicleJourneyRef>
            <DatedVehicleJourneyRef>JOURNEY1</DatedVehicleJourneyRef>
          </FramedVehicleJourneyRef>
          <EstimatedCalls>
            <EstimatedCall>
              <StopPointRef>STOP</StopPointRef>
              <AimedDepartureTime>2026-09-24T12:22:00+02:00</AimedDepartureTime>
              <ExpectedDepartureTime>2026-09-24T12:27:00+02:00</ExpectedDepartureTime>
            </EstimatedCall>
          </EstimatedCalls>
        </EstimatedVehicleJourney>
      </EstimatedJourneyVersionFrame>
    </EstimatedTimetableDelivery>
  </ServiceDelivery>
</Siri>"""

    def fetch_alerts() -> bytes:
        called.append("alerts")
        return _alerts()

    poll_once(
        database,
        fetch_trips=fetch_trips,
        fetch_siri=fetch_siri,
        fetch_alerts=fetch_alerts,
        now=datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
    )

    assert called == ["tu", "siri", "alerts"]
    assert schedule_relationship(database, "JOURNEY1") == "CANCELED"
    assert alert_count(database) == 1


def test_status_reports_cached_trip_updates(tmp_path: Path):
    from fastapi.testclient import TestClient

    from comptagefer.app import create_app

    fetched_at = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    store_trip_updates(tmp_path / "rt.db", _feed(), fetched_at)
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/rt")

    assert response.status_code == 200
    body = response.json()
    assert body["trip_updates"] == 1
    assert body["last_fetch"] is not None


def test_unchanged_trip_does_not_stack_rows(tmp_path: Path):
    database = tmp_path / "rt.db"
    first = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    second = first + timedelta(minutes=2)

    store_trip_updates(database, _feed(), first)
    store_trip_updates(database, _feed(), second)

    assert trip_count(database) == 1
