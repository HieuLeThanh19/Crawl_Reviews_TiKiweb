
from fastapi.testclient import TestClient
from api.app import app
 
client = TestClient(app)
 
def test_search_products():
    response = client.get('/api/tiki/search?q=test&limit=5')
    assert response.status_code == 200
    assert isinstance(response.json(), list)
 
def test_crawl_request_validation():
    # max_pages > 100 → phải trả 422 Unprocessable Entity
    response = client.post('/api/tiki/crawl',
        json={'product_id': '123', 'max_pages': 999})
    assert response.status_code == 422
 
def test_export_csv():
    response = client.get('/api/products/197214029/export')
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert 'text/csv' in response.headers['content-type']
