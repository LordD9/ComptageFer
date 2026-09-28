"""Le README ne doit pas décrire un compose différent de celui du dépôt.

La section « Le compose, ligne par ligne » recopie `compose.yaml` en entier,
avec ses commentaires. C'est le moyen le plus rapide de faire comprendre la
syntaxe à qui n'a jamais lu un compose, et le plus rapide aussi de le laisser
devenir faux : une variable renommée dans le compose reste dans le README
jusqu'à ce qu'un déployant essaie.

Ces tests lient donc la documentation au fichier réel : ils extraient le bloc
du README et le comparent au compose, sans comparer deux résumés.
"""

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).parent.parent
COMPOSE = RACINE / "compose.yaml"
README = RACINE / "README.md"
ENV_EXAMPLE = RACINE / ".env.example"


def _blocs_yaml_du_readme() -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL)


def test_le_readme_recopie_le_compose_entier():
    """Le bloc yaml du README est le compose, pas une approximation."""
    compose = COMPOSE.read_text(encoding="utf-8")

    assert compose in _blocs_yaml_du_readme(), (
        "le bloc yaml du README ne reproduit plus compose.yaml ; "
        "le recopier ou le retirer, ne pas le laisser mentir"
    )


def test_chaque_variable_du_compose_est_documentee():
    """Une variable ajoutée au compose doit apparaître dans le README.

    Sinon elle fonctionne mais personne ne la renseigne, et le déploiement
    échoue en silence sur une variable vide.
    """
    compose = COMPOSE.read_text(encoding="utf-8")
    variables = set(re.findall(r"\$\{([A-Z0-9_]+)", compose))
    assert variables, "aucune variable trouvée : la lecture du compose a échoué"

    readme = README.read_text(encoding="utf-8")

    for nom in sorted(variables):
        assert nom in readme, f"{nom} est dans compose.yaml mais pas dans le README"


def test_chaque_variable_du_compose_a_un_exemple_dans_env_example():
    """`.env.example` est le fichier que l'utilisateur copie : il porte toute
    variable que le compose sait lire, sinon le déploiement manuel demande une
    variable que rien ne documente."""
    variables = set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE.read_text(encoding="utf-8")))
    exemple = ENV_EXAMPLE.read_text(encoding="utf-8")

    manquantes = [nom for nom in sorted(variables) if nom not in exemple]

    assert not manquantes, f"absentes de .env.example : {', '.join(manquantes)}"


def test_le_compose_ne_porte_aucune_valeur_secrete():
    """Un compose versionné ne contient que des noms. La clé et le jeton
    vivent dans le `.env` local, jamais dans git."""
    compose = COMPOSE.read_text(encoding="utf-8")
    variables = set(re.findall(r"\$\{([A-Z0-9_]+)", compose))

    for ligne in compose.splitlines():
        if ":" not in ligne or ligne.strip().startswith("#"):
            continue
        nom, _, valeur = ligne.partition(":")
        nom, valeur = nom.strip(), valeur.strip()
        if not nom.replace("_", "").isalnum() or not nom:
            continue
        if nom in variables:
            continue
        # Une variable en dur reste acceptable si sa valeur n'est pas un secret.
        assert not re.fullmatch(r"[A-Za-z0-9_\-]{24,}", valeur), (
            f"{nom} porte une valeur longue en dur dans compose.yaml"
        )


@pytest.mark.parametrize("mot", ["ADMIN_TOKEN", "DATAGOUV_API_KEY", "DATAGOUV_DATASET_ID"])
def test_le_readme_nomme_les_variables_sensibles(mot: str):
    """Le README doit nommer la variable qu'il demande de remplir."""
    assert mot in README.read_text(encoding="utf-8")
