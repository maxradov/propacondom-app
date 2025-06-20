import os
from flask import Flask, request, jsonify, render_template # <-- Импортируем render_template
from flask_cors import CORS
from tasks import celery
from celery.result import AsyncResult

# Flask автоматически найдет папки 'static' и 'templates'
app = Flask(__name__)
CORS(app)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    # ... (ваш существующий код) ...
    pass

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    # ... (ваш существующий код) ...
    pass

# Этот маршрут будет обслуживать главную страницу и любые другие пути (для SPA)
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    # Используем render_template для отдачи index.html из папки 'templates'
    return render_template("index.html")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"Starting Flask app on 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)