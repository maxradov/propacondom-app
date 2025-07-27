from flask import render_template, abort
from google.cloud import firestore
from . import bp
from tasks import get_db_client

@bp.route('/')
def blog_index():
    db = get_db_client()
    query = db.collection('blog_articles').order_by('published_at', direction=firestore.Query.DESCENDING).limit(20)
    articles = [{"id": doc.id, **doc.to_dict()} for doc in query.stream()]
    return render_template('blog_index.html', articles=articles)

@bp.route('/<string:slug>')
def article_detail(slug):
    db = get_db_client()
    doc_ref = db.collection('blog_articles').document(slug)
    article_doc = doc_ref.get()
    if not article_doc.exists:
        abort(404)
    return render_template('blog_article.html', article=article_doc.to_dict())