from flask import Flask

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.config.from_object("app.config")

from app import views  # noqa: E402,F401