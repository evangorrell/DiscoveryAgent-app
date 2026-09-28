import pytest

from da_selection_ui.app import app


@pytest.mark.parametrize("host", ["localhost", "localhost:54321", "127.0.0.1:8000", "[::1]:49152"])
def test_loopback_hosts_and_dynamic_ports(host):
    assert app.test_client().get('/api/health', base_url='http://' + host).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "127.0.0.1.evil.example"])
def test_foreign_hosts_are_rejected(host):
    assert app.test_client().get('/api/health', base_url='http://' + host).status_code == 403


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://localhost:9999"}, {"Sec-Fetch-Site": "cross-site"},
    {"Referer": "https://evil.example/form"},
])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "GET"])
def test_cross_origin_requests_fail_before_routes(method, headers):
    response = app.test_client().open('/api/projects', method=method, headers=headers)
    assert response.status_code == 403


def test_cross_site_form_cannot_create_project():
    client = app.test_client()
    fields = dict(project_name='Boundary fixture', task_description='Test only', dataset_context='Test context')
    before = client.get('/api/projects').get_json()['projects']
    rejected = client.post('/api/projects', data=fields, headers={'Origin': 'https://evil.example'})
    assert rejected.status_code == 403
    assert client.get('/api/projects').get_json()['projects'] == before
    created = client.post('/api/projects', data=fields, headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'})
    assert created.status_code == 200
    assert created.get_json()['project_id']


def test_foreign_origin_cannot_spoof_forwarded_host():
    assert app.test_client().get('/api/projects', headers={
        'Host': 'evil.example', 'X-Forwarded-Host': 'localhost',
    }).status_code == 403
