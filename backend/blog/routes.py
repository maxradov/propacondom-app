# backend/blog/routes.py

from flask import render_template, abort
from google.cloud import firestore
from . import bp
from tasks import get_db_client

# БЫЛО:
# @bp.route('/')
# def blog_index():

# СТАЛО:
@bp.route('/')
def blog_index(lang): # <-- Добавьте 'lang' в качестве аргумента
    """
    Этот код выполняется, когда пользователь заходит на /<lang>/blog/
    """
    # 'lang' здесь не используется, так как язык обрабатывается
    # глобально через Babel, но функция ОБЯЗАНА его принять.
    db = get_db_client()
    query = db.collection('blog_articles').order_by(
        'published_at', direction=firestore.Query.DESCENDING).limit(20)
    
    articles = [{"id": doc.id, **doc.to_dict()} for doc in query.stream()]
    
    return render_template('blog_index.html', articles=articles)


# БЫЛО:
# @bp.route('/<string:slug>')
# def article_detail(slug):

# СТАЛО:
@bp.route('/<string:slug>')
def article_detail(lang, slug): # <-- Добавьте 'lang' и здесь
    """
    Этот код выполняется для адресов вида /<lang>/blog/my-first-article
    """
    db = get_db_client()
    doc_ref = db.collection('blog_articles').document(slug)
    article_doc = doc_ref.get()
    
    if not article_doc.exists:
        abort(404)
        
    return render_template('blog_article.html', article=article_doc.to_dict())