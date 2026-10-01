"""Origine des écritures navigateur et confidentialité des pages de compte."""

import os
from urllib.parse import urlsplit

from starlette.requests import Request


def https_actif() -> bool:
    """Un indicateur de déploiement, pas un activateur de TLS.

    Refuser les fautes de frappe évite de déployer des cookies non sécurisés
    en croyant avoir activé HTTPS. Le défaut vide garde le test local en HTTP.
    """
    valeur = os.environ.get("COMPTAGEFER_HTTPS", "")
    if valeur not in {"", "0", "1"}:
        raise ValueError("COMPTAGEFER_HTTPS doit être vide, 0 ou 1")
    return valeur == "1"


def _origine(url: str) -> tuple[str, str, int] | None:
    try:
        morceaux = urlsplit(url)
        if morceaux.scheme not in {"http", "https"} or not morceaux.hostname:
            return None
        if morceaux.username is not None or morceaux.password is not None:
            return None
        port = morceaux.port
        if port is None:
            port = 443 if morceaux.scheme == "https" else 80
        return morceaux.scheme, morceaux.hostname, port
    except ValueError:
        return None


def origine_autorisee(request: Request) -> bool:
    """Bloquer les POST de formulaires étrangers, même depuis un sous-domaine.

    SameSite=lax ne suffit pas contre le login CSRF ni un site frère. Les
    navigateurs transmettent Origin, ou à défaut Referer / Sec-Fetch-Site.
    Les clients sans ces en-têtes (curl, file hors ligne) restent acceptés :
    ces en-têtes ne sont pas une authentification pour les clients directs.
    Le proxy doit préserver le Host public ; X-Forwarded-Host n'est pas cru.
    """
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        return False
    source = request.headers.get("origin")
    if source is None:
        source = request.headers.get("referer")
    if source is None:
        return True
    scheme = "https" if https_actif() else request.url.scheme
    attendue = _origine(scheme + "://" + request.headers.get("host", ""))
    return attendue is not None and _origine(source) == attendue
