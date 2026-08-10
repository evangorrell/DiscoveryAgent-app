import json
from pathlib import Path

from da_selection_ui import app as app_module
from da_selection_ui.project_storage import get_data_root


def test_macos_default_data_root_is_application_support():
    home = Path('/Users/scientist')
    assert get_data_root(platform='darwin', environ={}, home=home) == (
        home / 'Library' / 'Application Support' / 'DiscoveryAgent'
    )


def test_creation_endpoint_creates_managed_macos_parent_directories(monkeypatch, tmp_path):
    home = tmp_path / 'Users' / 'scientist'
    data_root = home / 'Library' / 'Application Support' / 'DiscoveryAgent'
    projects_root = data_root / 'projects'
    legacy_root = tmp_path / 'source-checkout' / 'local_disk'

    monkeypatch.setattr(app_module, 'get_data_root', lambda: data_root)
    monkeypatch.setattr(app_module, 'get_projects_root', lambda: projects_root)
    monkeypatch.setattr(app_module, 'get_project_root', lambda project_id: projects_root / project_id)
    monkeypatch.setattr(app_module, '_get_disk_path', lambda: legacy_root)

    assert not data_root.exists()
    client = app_module.app.test_client()
    response = client.post('/api/projects', data={
        'project_name': 'RGC / α-analysis? <V1>',
        'task_description': 'Test managed project creation.',
        'dataset_context': 'Disposable regression-test context.',
    })

    assert response.status_code == 200
    project_id = response.get_json()['project_id']
    assert project_id.startswith('prj_')
    project_root = projects_root / project_id
    assert project_root.is_dir()
    assert projects_root.is_dir()
    assert not legacy_root.exists()

    metadata = json.loads(
        (project_root / '.discoveryagent' / 'project.json').read_text(encoding='utf-8')
    )
    assert metadata == {
        'project_id': project_id,
        'display_name': 'RGC / α-analysis? <V1>',
    }
    registry = json.loads((data_root / 'v2_projects.json').read_text(encoding='utf-8'))
    record = registry['projects'][0]
    assert record['id'] == project_id
    assert record['project_root'] == '.'
    assert record['code_dir'] == 'code'
    assert str(tmp_path / 'source-checkout') not in json.dumps(record)


def test_project_endpoint_uses_autouse_temporary_storage(tmp_path):
    """The suite-wide guard must contain endpoint writes without local patches."""
    client = app_module.app.test_client()
    response = client.post('/api/projects', data={
        'project_name': 'Isolation sentinel',
        'task_description': 'Prove test storage isolation.',
        'dataset_context': 'Temporary test data only.',
    })

    assert response.status_code == 200
    project_id = response.get_json()['project_id']
    assert (tmp_path / project_id / '.discoveryagent' / 'project.json').is_file()
    assert (tmp_path / 'v2_projects.json').is_file()
