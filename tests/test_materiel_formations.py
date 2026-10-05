"""La liste fermée des formations TER.

Ce fichier ne teste pas l'application : il teste la **table** qui donne le
nombre de voitures d'une rame. C'est elle qui décide combien de cases le schéma
de comptage dessine, donc une entrée fausse se voit sur l'écran de quelqu'un,
dans un train — pas dans une exception.

Les nombres viennent des séries telles qu'elles circulent : un AGC existe en 3
et en 4 caisses, un Régiolis en 3, 4 et 6, un TER 2N NG en 3, 4 et 5. Chacune a
son entrée, parce qu'on ne compte pas la même chose dans les deux.
"""

from comptagefer.materiel import (
    CAISSE,
    FORMATIONS,
    PAR_LABELLE,
    VOITURE,
    formation,
    libelles,
    normaliser,
)


def test_every_formation_has_a_coherent_count_and_word():
    """Une formation sans unité, ou sans mot, ne se dessine pas."""
    for entree in FORMATIONS:
        assert entree.voitures >= 1, entree.label
        assert entree.mot in {CAISSE, VOITURE}, entree.label
        assert entree.famille.strip(), entree.label
        assert entree.label.strip(), entree.famille
        assert entree.label not in ("", None)


def test_the_labels_are_unique():
    """Deux entrées de même libellé rendraient la liste ambiguë au choix."""
    labels = [entree.label for entree in FORMATIONS]
    assert len(labels) == len(set(labels))
    assert len(PAR_LABELLE) == len(FORMATIONS)


def test_the_word_follows_the_material():
    """Un AGC se compte par caisse, un Regio 2N par voiture.

    Écrire « voiture » sur un AGC n'est pas une faute d'orthographe : c'est le
    vocabulaire de celui qui compte, et c'est lui qui lit l'écran.
    """
    assert formation("AGC 3 caisses").mot == CAISSE
    assert formation("Régiolis 6 caisses").mot == CAISSE
    assert formation("Regio 2N 8 voitures").mot == VOITURE
    assert formation("TER 2N 2 voitures").mot == VOITURE
    assert formation("Regio 2N 8 voitures").pluriel == "voitures"


def test_the_counts_are_the_ones_of_the_series():
    """Quelques nombres vérifiables, pour qu'une faute de frappe ne passe pas."""
    attendus = {
        "AGC 3 caisses": 3,
        "AGC 4 caisses": 4,
        "Régiolis 3 caisses": 3,
        "Régiolis 4 caisses": 4,
        "Régiolis 6 caisses": 6,
        "Regio 2N 7 voitures": 7,
        "Regio 2N 8 voitures": 8,
        "TER 2N NG 3 caisses": 3,
        "TER 2N NG 4 voitures": 4,
        "TER 2N NG 5 voitures": 5,
        "TER 2N 2 voitures": 2,
        "Z 20500 4 voitures": 4,
        "Z 20500 5 voitures": 5,
        "Z 50000 7 voitures": 7,
        "Z 50000 8 voitures": 8,
        "X 73500 1 caisse": 1,
    }
    for label, voitures in attendus.items():
        entree = formation(label)
        assert entree is not None, label
        assert entree.voitures == voitures, label


def test_the_normaliser_forgives_case_accents_and_spaces():
    """Le libellé est choisi sur un téléphone, dans un train.

    La casse, les accents et les espaces multiples y arrivent tout seuls. Les
    refuser ferait perdre un comptage pour une raison qui n'est pas une
    information fausse.
    """
    assert normaliser("  agc  3 CAISSES ") == "AGC 3 caisses"
    assert normaliser("regiolis 4 caisses") == "Régiolis 4 caisses"
    assert normaliser("REGIO 2N 8 VOITURES") == "Regio 2N 8 voitures"
    assert normaliser("z 20500 5 voitures") == "Z 20500 5 voitures"
    assert normaliser("X 73500 1 caisse") == "X 73500 1 caisse"


def test_the_canonical_label_is_what_comes_back():
    """C'est la forme de la liste qui est stockée, jamais celle tapée."""
    assert normaliser("agc 3 caisses") == "AGC 3 caisses"
    assert formation("agc 3 caisses").label == "AGC 3 caisses"


def test_an_unknown_material_is_not_normalised():
    """Rien hors liste : `None` dit « pas dans la liste », pas « vide ».

    Le matériel est facultatif, donc `None` n'est pas une erreur en soi — c'est
    l'appelant qui décide si l'absence est légitime. Ce qui compte ici, c'est
    qu'un libellé inventé ne se rapproche pas silencieusement d'une entrée.
    """
    assert normaliser("Z 20500") is None
    assert normaliser("UM2 ramesihat") is None
    assert normaliser("AGC 5 caisses") is None
    assert normaliser("") is None
    assert normaliser(None) is None
    assert normaliser(180) is None
    assert formation("TGV Duplex") is None


def test_the_labels_are_the_order_of_the_list():
    """Le client lit `libelles()` : il ne peut pas voir autre chose que la table."""
    assert libelles() == tuple(entree.label for entree in FORMATIONS)
    assert len(libelles()) == len(FORMATIONS)