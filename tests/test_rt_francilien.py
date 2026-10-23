"""Temps réel des RER et Transiliens, joint au GTFS par numéro de train et UIC."""

import sqlite3
from datetime import UTC, datetime

from comptagefer.rt_francilien import import_transcodification, store_siri_francilien
from comptagefer.timetable import TRANSILIEN, etat_of, import_timetable, listed_trips
from tests.test_transilien import _csv

JOUR = "2026-10-23"
TRIP = f"{TRANSILIEN}rer-b"
JUMEAU = f"{TRANSILIEN}autre-jour"
MAINTENANT = datetime(2026, 10, 23, 6, 0, tzinfo=UTC)


def _horaires(tmp_path):
    (tmp_path / "trips.txt").write_text(
        _csv(["trip_id", "service_id", "route_id", "trip_short_name"],
             [TRIP, "S1", "IDFM:C01743", "GLAK75"],
             [JUMEAU, "S2", "IDFM:C01743", "GLAK75"]),
        encoding="utf-8",
    )
    (tmp_path / "stop_times.txt").write_text(
        _csv(["trip_id", "stop_id", "departure_time", "arrival_time"],
             [TRIP, "IDFM:463182", "08:00:00", "08:00:00"],
             [TRIP, "IDFM:monomodalStopPlace:45102", "08:05:00", "08:05:00"],
             [JUMEAU, "IDFM:463182", "08:00:00", "08:00:00"]),
        encoding="utf-8",
    )
    (tmp_path / "calendar_dates.txt").write_text(
        _csv(["service_id", "date", "exception_type"], ["S1", "20261023", "1"], ["S2", "20261024", "1"]),
        encoding="utf-8",
    )
    (tmp_path / "routes.txt").write_text(
        _csv(["route_id", "route_short_name", "route_type"], ["IDFM:C01743", "B", "2"]), encoding="utf-8"
    )
    timetable = tmp_path / "timetable.db"
    import_timetable(
        timetable, tmp_path / "trips.txt", tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt", tmp_path / "routes.txt",
    )
    transco = (
        "UIC8;__ZDEr_ID;__ZDLr_ID;ZDE Libelle\n"
        "87271007;463182;71410;GARE DU NORD\n"
        "87758011;999;45102;CHATELET LES HALLES\n"
    ).encode("latin-1")
    import_transcodification(timetable, transco)
    return timetable


def _siri(numero="GLAK75", jour=JOUR, annule=False):
    annulation = "<Cancellation>true</Cancellation>" if annule else ""
    return f"""<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery><EstimatedTimetableDelivery>
<EstimatedJourneyVersionFrame><EstimatedVehicleJourney>
<LineRef>B</LineRef><FramedVehicleJourneyRef><DataFrameRef>{jour}</DataFrameRef>
<DatedVehicleJourneyRef>FR:VehicleJourney::xx:LOC</DatedVehicleJourneyRef></FramedVehicleJourneyRef>
{annulation}<TrainNumbers><TrainNumberRef>{numero}</TrainNumberRef></TrainNumbers>
<EstimatedCalls>
<EstimatedCall><StopPointRef> FR:ScheduledStopPoint::87271007</StopPointRef>
<AimedDepartureTime>{jour}T08:00:00+02:00</AimedDepartureTime>
<ExpectedDepartureTime>{jour}T08:02:00+02:00</ExpectedDepartureTime></EstimatedCall>
<EstimatedCall><StopPointRef> FR:ScheduledStopPoint::87758011</StopPointRef>
<AimedArrivalTime>{jour}T08:05:00+02:00</AimedArrivalTime>
<ExpectedArrivalTime>{jour}T08:07:00+02:00</ExpectedArrivalTime></EstimatedCall>
</EstimatedCalls></EstimatedVehicleJourney></EstimatedJourneyVersionFrame>
</EstimatedTimetableDelivery></ServiceDelivery></Siri>""".encode()


def test_un_rer_en_retard_est_rattache_a_son_trip_et_a_sa_gare(tmp_path):
    timetable = _horaires(tmp_path)
    rt = tmp_path / "rt.db"
    assert store_siri_francilien(rt, timetable, _siri(), MAINTENANT) == 1
    with sqlite3.connect(rt) as connection:
        trips = connection.execute("SELECT trip_id, schedule_relationship FROM trip_update").fetchall()
        stops = dict(connection.execute("SELECT stop_id, departure_delay FROM stop_update").fetchall())
    assert trips == [(TRIP, "SCHEDULED")], "le jumeau d'un autre jour ne doit rien recevoir"
    assert stops == {"IDFM:463182": 120, "IDFM:monomodalStopPlace:45102": 120}


def test_la_liste_affiche_le_retard_du_rer(tmp_path):
    timetable = _horaires(tmp_path)
    rt = tmp_path / "rt.db"
    store_siri_francilien(rt, timetable, _siri(), MAINTENANT)
    trains = listed_trips(timetable, rt, "IDFM:463182", "IDFM:monomodalStopPlace:45102", MAINTENANT)
    train = next(item for item in trains if item["trip_id"] == TRIP)
    assert train["kind"] == "RER B"
    assert train["delay_seconds"] == 120
    assert train["etat"] == etat_of("SCHEDULED", 120) == "retard"


def test_une_suppression_francilienne_est_supprime(tmp_path):
    timetable = _horaires(tmp_path)
    rt = tmp_path / "rt.db"
    store_siri_francilien(rt, timetable, _siri(annule=True), MAINTENANT)
    with sqlite3.connect(rt) as connection:
        assert connection.execute("SELECT schedule_relationship FROM trip_update").fetchone()[0] == "CANCELED"


def test_un_numero_inconnu_ou_ambigu_nannote_rien(tmp_path):
    timetable = _horaires(tmp_path)
    with sqlite3.connect(timetable) as connection:
        connection.execute("UPDATE service_day SET day = ? WHERE service_id = 'S2'", (JOUR,))
    rt = tmp_path / "rt.db"
    assert store_siri_francilien(rt, timetable, _siri(), MAINTENANT) == 0, "deux trips le même jour"
    assert store_siri_francilien(rt, timetable, _siri(numero="999999"), MAINTENANT) == 0


def test_un_flux_inchange_ne_reecrit_pas_de_ligne(tmp_path):
    timetable = _horaires(tmp_path)
    rt = tmp_path / "rt.db"
    store_siri_francilien(rt, timetable, _siri(), MAINTENANT)
    store_siri_francilien(rt, timetable, _siri(), MAINTENANT.replace(minute=6))
    with sqlite3.connect(rt) as connection:
        assert connection.execute("SELECT COUNT(*) FROM trip_update").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM stop_update").fetchone()[0] == 2


def test_sans_table_uic_le_flux_est_ignore_sans_erreur(tmp_path):
    timetable = _horaires(tmp_path)
    with sqlite3.connect(timetable) as connection:
        connection.execute("DROP TABLE uic_arret")
    assert store_siri_francilien(tmp_path / "rt.db", timetable, _siri(), MAINTENANT) == 0
