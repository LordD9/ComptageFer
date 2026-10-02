"""Sections ferroviaires : l'appartenance ne dépend ni du sens ni des extrémités."""

from comptagefer.carte import _sections


def test_section_commune_aux_traces_inversees_et_recouvrement_partiel():
    """Deux traces à sens opposés partagent seulement le tronçon intermédiaire."""
    features = [
        {"trace": [[0, 0], [0, 1], [0, 2], [0, 3]]},
        {"trace": [[0, 3], [0, 2], [0, 1]]},
    ]

    sections = _sections(features)

    assert sections == [
        {"trace": [[0, 0], [0, 1]], "features": [0]},
        {"trace": [[0, 1], [0, 2], [0, 3]], "features": [0, 1]},
    ]


def test_section_detecte_un_recouvrement_de_serpent_et_ne_joint_pas_les_branches():
    """Un serpent partage une seule arête avec un autre trajet, sans inclure ses branches."""
    features = [
        {"trace": [[1, 0], [1, 1], [1, 2], [2, 2]]},
        {"trace": [[1, 1], [1, 2], [1, 3]]},
    ]

    sections = _sections(features)

    assert {tuple(section["features"]) for section in sections} == {(0,), (0, 1), (1,)}
    commune = next(section for section in sections if section["features"] == [0, 1])
    assert commune["trace"] == [[1, 1], [1, 2]]


def test_section_conserve_les_comptages_au_dela_de_trois():
    features = [{"trace": [[0, 0], [0, 1]]} for _ in range(4)]

    sections = _sections(features)

    assert sections == [{"trace": [[0, 0], [0, 1]], "features": [0, 1, 2, 3]}]
