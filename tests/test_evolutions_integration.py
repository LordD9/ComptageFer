"""Régressions trouvées pendant la revue intégrée des six évolutions."""

import csv
import io
import sqlite3
from datetime import UTC, datetime, timedelta

from tests.conftest import TER
from tests.test_historique import _application_avec_offre, _corps_historique


def test_carte_et_csv_excluent_le_signalement_y_compris_du_total(tmp_path):
    from fastapi.testclient import TestClient
    from comptagefer.app import create_app

    client = TestClient(create_app(tmp_path))
    assert client.post('/api/missing', json={
        'client_id': 'signalement-seul', 'origin_stop_id': 'A',
        'destination_stop_id': 'B', 'origin_name': 'Origine', 'destination_name': 'Destination',
    }).status_code == 200
    carte = client.get('/carte').text
    assert '0 comptage au total, 0 sur la carte' in carte
    assert 'signalement-seul' not in carte
    assert "n'a pas de coordonnées" not in carte
    assert 'Origine → Destination' in client.get('/comptages').text
    response = client.get('/api/export.csv')
    assert response.text.startswith('created_at,origin,destination,')
    assert list(csv.DictReader(io.StringIO(response.text))) == []
    assert 'Licence Ouverte 2.0' in client.get('/methode').text


def test_historique_filtre_par_date_observee_et_exporte_les_deux_dates(tmp_path):
    client, data, depart = _application_avec_offre(tmp_path)
    assert client.post('/api/sessions', json=_corps_historique(depart)).status_code == 200
    jour = depart.astimezone(UTC).date().isoformat()
    filtered = client.get('/comptages', params={'depuis': jour, 'jusqu': jour}).text
    assert 'historique-1' in filtered
    today = datetime.now(UTC).date().isoformat()
    assert 'historique-1' not in client.get('/comptages', params={'depuis': today}).text
    row = list(csv.DictReader(io.StringIO(client.get('/api/export.csv').text)))[0]
    assert row['is_historical'] == '1'
    assert datetime.fromisoformat(row['observed_at']) == depart
    assert row['created_at'] != row['observed_at']
    assert not any(row[field] for field in ('courant', 'precedent', 'suivant'))
    with sqlite3.connect(data / 'app.db') as db:
        plan = db.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM saisie WHERE '
            '(CASE WHEN is_historical = 1 THEN observed_at ELSE created_at END) >= ?', (jour,)
        ).fetchall()
    assert 'saisie_date_observee' in str(plan)


def test_train_apres_minuit_conserve_la_journee_de_service_gtfs(tmp_path):
    client, data, depart = _application_avec_offre(tmp_path)
    service_date = depart.date().isoformat()
    with sqlite3.connect(data / 'timetable.db') as db:
        db.execute('UPDATE passage SET depart_sec = depart_sec + 86400 WHERE trip_id = ?', (TER,))
    observed_at = depart + timedelta(days=1)
    response = client.post('/api/sessions', json=_corps_historique(observed_at))
    assert response.status_code == 200, response.text
    row = client.get('/api/sessions').json()[0]
    assert row['trajet']['service_date'] == service_date
    assert datetime.fromisoformat(row['observed_at']).date() != depart.date()


def test_recherche_historique_ne_propose_pas_train_hors_bornes(tmp_path, monkeypatch):
    import comptagefer.app as module

    client, _data, depart = _application_avec_offre(tmp_path)
    monkeypatch.setattr(module, '_maintenant_utc', lambda: depart.astimezone(UTC) - timedelta(minutes=1))
    response = client.get('/api/trips/historical', params={
        'from': 'StopArea:Lyon', 'to': 'StopArea:Valence',
        'at': (depart - timedelta(minutes=2)).isoformat(),
    })
    assert response.status_code == 200
    assert all(item['trip_id'] != TER for item in response.json())


def test_traiter_contact_retire_aussi_les_octets_du_fichier_sqlite(tmp_path):
    from comptagefer import retours
    from comptagefer.app import create_app

    create_app(tmp_path)
    database = tmp_path / 'app.db'
    email = 'contact-physique-unique@example.org'
    retours.enregistrer(database, 'Retour sans adresse dans le message', email=email)
    assert email.encode() in database.read_bytes()
    assert retours.traiter(database, 1)
    assert email.encode() not in database.read_bytes()
