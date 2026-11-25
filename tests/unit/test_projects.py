from app.main import ProjectIn, create_project, list_projects
import app.main as main

OWNER = "Bearer owner-token"
MEMBER = "Bearer member-token"


def test_create_project_stamps_id_name_and_owner():
    created = create_project(ProjectIn(name="Alpha"), OWNER)
    assert created == {"id": 1, "name": "Alpha", "owner_id": 1}
    assert main.PROJECTS[1] == created


def test_create_project_allocates_sequential_ids():
    # The shared fixture rewinds NEXT_PROJECT to 1 before every test, which is what makes
    # the allocated ids assertable as exact values rather than as ids relative to each other.
    first = create_project(ProjectIn(name="Alpha"), OWNER)
    second = create_project(ProjectIn(name="Beta"), MEMBER)
    assert first == {"id": 1, "name": "Alpha", "owner_id": 1}
    assert second == {"id": 2, "name": "Beta", "owner_id": 2}
    assert set(main.PROJECTS) == {1, 2}


def test_list_projects_scopes_by_role():
    # The second project belongs to the member: without a member-owned record the owner's
    # listing would look correct even if the role check in list_projects were removed.
    create_project(ProjectIn(name="Alpha"), OWNER)
    create_project(ProjectIn(name="Beta"), MEMBER)
    assert list_projects(OWNER) == [main.PROJECTS[1], main.PROJECTS[2]]
    assert list_projects(MEMBER) == [main.PROJECTS[2]]
