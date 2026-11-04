from app.core import SessionLocal
from app.models import Job, Link
from app.worker import process_job


def test_login_and_csrf(client):
    assert client.get('/api/categories').status_code == 401
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-strong'}).status_code == 200
    assert client.post('/api/categories', json={'name': 'Nicho teste'}).status_code == 403


def test_category_import_validation_dashboard_export(authed):
    client, headers = authed
    cat = client.post('/api/categories', json={'name': 'Tecnologia', 'keywords': ['arduino', 'esp32'],
               'platforms': ['telegram', 'facebook', 'whatsapp'], 'color': '#22c55e'}, headers=headers)
    assert cat.status_code == 201, cat.text
    catid = cat.json()['id']
    imp = client.post('/api/links/import', json={'urls': ['https://www.facebook.com/groups/12345678',
        'https://chat.whatsapp.com/AbCdEfGhIjKlMnOpQr', 'https://t.me/exemploteste1234',
        'https://www.facebook.com/groups/12345678', 'http://localhost/admin'], 'category_id': catid}, headers=headers)
    assert imp.status_code == 200, imp.text
    assert imp.json()['imported'] == 3
    assert len(imp.json()['duplicates']) == 1
    assert len(imp.json()['invalid']) == 1
    # External Telegram request mocked; no live scraping in unit tests.
    from unittest.mock import patch
    with patch('app.worker.preview_link', side_effect=lambda p,u: {'status': 'preview' if p == 'telegram' else 'pending', 'name': 'Canal teste'}):
        process_job(imp.json()['job_id'])
    job = client.get('/api/jobs/' + str(imp.json()['job_id'])).json()
    assert job['status'] == 'done' and job['processed'] == 3
    data = client.get('/api/links').json()
    assert data['total'] == 3
    item = next(x for x in data['items'] if x['platform'] == 'facebook')
    update = client.patch(f"/api/links/{item['id']}", json={'favorite': True, 'permission': 'allowed'}, headers=headers)
    assert update.status_code == 200
    assert client.post('/api/posts', json={'link_id': item['id'], 'leads': 2, 'sales': 1}, headers=headers).status_code == 201
    stats = client.get('/api/dashboard').json()
    assert stats['links'] == 3 and stats['sales'] == 1 and stats['favorites'] == 1
    csv = client.get('/api/export.csv')
    assert csv.status_code == 200 and 'facebook' in csv.text
    assert client.get('/api/links?favorite=true').json()['total'] == 1


def test_caption_ai_and_search_job(authed):
    client, headers = authed
    cat = client.post('/api/categories', json={'name': 'Ofertas', 'keywords': ['promoções']}, headers=headers).json()
    caption = client.post('/api/captions', json={'category_id': cat['id'], 'name': 'Oferta', 'text': 'Olá, confira o link!', 'offer_url': 'https://example.com'}, headers=headers)
    assert caption.status_code == 201
    from unittest.mock import patch
    with patch('app.main.generate_captions', return_value=['Primeira legenda', 'Segunda legenda']):
        res = client.post('/api/captions/generate', json={'product': 'Curso de programação', 'audience': 'iniciantes', 'variants': 2}, headers=headers)
    assert res.status_code == 200 and len(res.json()['texts']) == 2
    job = client.post(f"/api/jobs/search/{cat['id']}", headers=headers).json()
    with patch('app.worker.brave_search', return_value=[{'url': 'https://www.facebook.com/groups/compraevenda2026', 'title': 'Compra e venda', 'description': 'Público'}]):
        with patch('app.worker.preview_link', return_value={'status':'pending'}):
            process_job(job['job_id'])
    result = client.get('/api/jobs/' + str(job['job_id'])).json()
    assert result['status'] == 'done' and result['added'] == 1
    assert any(x['platform'] == 'facebook' for x in client.get('/api/links').json()['items'])
