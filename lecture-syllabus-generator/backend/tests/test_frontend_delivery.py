import pytest


@pytest.mark.parametrize('route', ['/', '/progress.html', '/js/api.js', '/js/upload.js', '/css/home.css'])
def test_frontend_resources_are_revalidated_after_restart(client, route):
    response = client.get(route)
    assert response.status_code == 200
    assert response.headers.get('cache-control') == 'no-cache'


def test_service_metadata_cannot_be_reused_from_cache(client):
    response = client.get('/api/meta')
    assert response.status_code == 200
    assert response.headers.get('cache-control') == 'no-store'
