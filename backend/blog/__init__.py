# backend/blog/__init__.py

from flask import Blueprint

# Убираем url_prefix отсюда
bp = Blueprint('blog', __name__, template_folder='templates')

from . import routes