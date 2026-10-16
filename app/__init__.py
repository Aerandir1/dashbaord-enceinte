import os

from flask import Flask

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.config.from_object("app.config")


@app.url_defaults
def _static_version(endpoint, values):
    """Ajoute ?v=<date de modification> aux fichiers statiques.

    Après un `git pull`, l'URL du CSS/JS change : ni le navigateur ni le
    service worker ne peuvent resservir l'ancienne version avec la nouvelle page.
    """
    if endpoint != "static" or "filename" not in values:
        return
    try:
        values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
    except OSError:
        pass


from app import views  # noqa: E402,F401
