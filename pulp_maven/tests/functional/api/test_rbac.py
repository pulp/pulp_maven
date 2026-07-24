import uuid

import pytest


@pytest.fixture()
def gen_users(gen_user):
    """Returns a user generator function for the tests."""

    def _gen_users(role_names=None):
        if role_names is None:
            role_names = []
        if isinstance(role_names, str):
            role_names = [role_names]
        viewer_roles = [f"maven.{role}_viewer" for role in role_names]
        creator_roles = [f"maven.{role}_creator" for role in role_names]
        alice = gen_user(model_roles=viewer_roles)
        bob = gen_user(model_roles=creator_roles)
        charlie = gen_user()
        return alice, bob, charlie

    return _gen_users


@pytest.fixture
def try_action(maven_bindings, monitor_task):
    def _try_action(user, client, action, outcome, *args, **kwargs):
        action_api = getattr(client, f"{action}_with_http_info")
        try:
            with user:
                response = action_api(*args, **kwargs)
            if isinstance(response, tuple):
                data, status_code, _ = response
            else:
                data = response.data
                status_code = response.status_code
            if isinstance(data, maven_bindings.module.AsyncOperationResponse):
                data = monitor_task(data.task)
        except maven_bindings.module.ApiException as e:
            assert e.status == outcome, f"{e}"
        else:
            assert status_code == outcome, (
                f"User performed {action} when they shouldn't have been able to"
            )
            return data

    return _try_action


@pytest.mark.parallel
def test_basic_actions(gen_users, maven_bindings, try_action, maven_repo_factory):
    """Test list, read, create, update and delete on repositories."""
    alice, bob, charlie = gen_users("mavenrepository")

    a_list = try_action(alice, maven_bindings.RepositoriesMavenApi, "list", 200)
    assert a_list.count >= 1
    b_list = try_action(bob, maven_bindings.RepositoriesMavenApi, "list", 200)
    c_list = try_action(charlie, maven_bindings.RepositoriesMavenApi, "list", 200)
    assert (b_list.count, c_list.count) == (0, 0)

    # Create testing
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "create",
        403,
        {"name": str(uuid.uuid4())},
    )
    repo = try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "create",
        201,
        {"name": str(uuid.uuid4())},
    )
    try_action(
        charlie,
        maven_bindings.RepositoriesMavenApi,
        "create",
        403,
        {"name": str(uuid.uuid4())},
    )

    # View testing
    try_action(alice, maven_bindings.RepositoriesMavenApi, "read", 200, repo.pulp_href)
    try_action(bob, maven_bindings.RepositoriesMavenApi, "read", 200, repo.pulp_href)
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 404, repo.pulp_href)

    # Update testing
    update_args = [repo.pulp_href, {"name": str(uuid.uuid4())}]
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "partial_update",
        403,
        *update_args,
    )
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "partial_update",
        202,
        *update_args,
    )
    try_action(
        charlie,
        maven_bindings.RepositoriesMavenApi,
        "partial_update",
        404,
        *update_args,
    )

    # Delete testing
    try_action(alice, maven_bindings.RepositoriesMavenApi, "delete", 403, repo.pulp_href)
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "delete", 404, repo.pulp_href)
    try_action(bob, maven_bindings.RepositoriesMavenApi, "delete", 202, repo.pulp_href)


@pytest.mark.parallel
def test_repository_actions(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    maven_remote_factory,
    try_action,
):
    """Test add_cached_content, repair_metadata, and modify actions."""
    alice, bob, charlie = gen_users(["mavenrepository", "mavenremote"])

    with bob:
        bob_remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
        repo = maven_repo_factory(remote=bob_remote.pulp_href)

    # add_cached_content tests
    body = {"remote": bob_remote.pulp_href}
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "add_cached_content",
        403,
        repo.pulp_href,
        body,
    )
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "add_cached_content",
        202,
        repo.pulp_href,
        body,
    )
    try_action(
        charlie,
        maven_bindings.RepositoriesMavenApi,
        "add_cached_content",
        404,
        repo.pulp_href,
        body,
    )

    # repair_metadata tests
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "repair_metadata",
        403,
        repo.pulp_href,
    )
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "repair_metadata",
        202,
        repo.pulp_href,
    )
    try_action(
        charlie,
        maven_bindings.RepositoriesMavenApi,
        "repair_metadata",
        404,
        repo.pulp_href,
    )

    # modify tests
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "modify",
        403,
        repo.pulp_href,
        {},
    )
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "modify",
        202,
        repo.pulp_href,
        {},
    )
    try_action(
        charlie,
        maven_bindings.RepositoriesMavenApi,
        "modify",
        404,
        repo.pulp_href,
        {},
    )


@pytest.mark.parallel
def test_repository_version_actions(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test list, retrieve, destroy, and repair on repository versions."""
    alice, bob, charlie = gen_users("mavenrepository")
    with bob:
        repo = maven_repo_factory()

    # Trigger a modify to create version 1 (version 0 is the initial empty version)
    with bob:
        try_action(bob, maven_bindings.RepositoriesMavenApi, "modify", 202, repo.pulp_href, {})
        # Re-read the repo to get the updated latest_version_href
        repo = maven_bindings.RepositoriesMavenApi.read(repo.pulp_href)

    ver_href = repo.latest_version_href

    # List versions
    a_vers = try_action(
        alice, maven_bindings.RepositoriesMavenVersionsApi, "list", 200, repo.pulp_href
    )
    assert a_vers.count >= 1
    b_vers = try_action(
        bob, maven_bindings.RepositoriesMavenVersionsApi, "list", 200, repo.pulp_href
    )
    assert b_vers.count >= 1
    try_action(
        charlie,
        maven_bindings.RepositoriesMavenVersionsApi,
        "list",
        403,
        repo.pulp_href,
    )

    # Retrieve specific version
    try_action(alice, maven_bindings.RepositoriesMavenVersionsApi, "read", 200, ver_href)
    try_action(bob, maven_bindings.RepositoriesMavenVersionsApi, "read", 200, ver_href)
    try_action(charlie, maven_bindings.RepositoriesMavenVersionsApi, "read", 403, ver_href)

    # Destroy version — permission checks only (don't execute bob's delete
    # as deleting the only version fails with PLP0011)
    try_action(alice, maven_bindings.RepositoriesMavenVersionsApi, "delete", 403, ver_href)
    try_action(charlie, maven_bindings.RepositoriesMavenVersionsApi, "delete", 403, ver_href)


@pytest.mark.parallel
def test_content_viewset_permissions(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    random_artifact_factory,
    monitor_task,
    try_action,
):
    """Test that content listing is scoped by repository view permissions."""
    alice, bob, charlie = gen_users("mavenrepository")

    # Admin creates repo and uploads content
    repo = maven_repo_factory()
    artifact = random_artifact_factory(size=64)
    content = maven_bindings.ContentArtifactApi.upload(
        artifact=artifact.pulp_href,
        relative_path="com/example/test-rbac/1.0.0/test-rbac-1.0.0.jar",
    )
    monitor_task(
        maven_bindings.RepositoriesMavenApi.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    # Alice (viewer) can see content in repo she has view perms on
    a_list = try_action(alice, maven_bindings.ContentArtifactApi, "list", 200)
    assert a_list.count >= 1

    # Bob (creator, no view on admin repo) sees no content
    b_list = try_action(bob, maven_bindings.ContentArtifactApi, "list", 200)
    assert b_list.count == 0

    # Charlie (no roles) sees no content
    c_list = try_action(charlie, maven_bindings.ContentArtifactApi, "list", 200)
    assert c_list.count == 0

    # Grant charlie object-level viewer role on the repo
    maven_bindings.RepositoriesMavenApi.add_role(
        repo.pulp_href,
        {"users": [charlie.username], "role": "maven.mavenrepository_viewer"},
    )

    # Now charlie can see the content
    c_list2 = try_action(charlie, maven_bindings.ContentArtifactApi, "list", 200)
    assert c_list2.count >= 1


@pytest.mark.parallel
def test_cross_object_permissions(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    maven_remote_factory,
    try_action,
):
    """Test that cross-object references enforce permissions on referenced objects."""
    _alice, bob, _charlie = gen_users(["mavenrepository", "mavenremote", "mavendistribution"])

    # Admin creates resources
    admin_repo = maven_repo_factory()
    admin_remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")

    # Bob creates his own resources
    with bob:
        bob_remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
        bob_repo = maven_repo_factory()

    # Distribution creation: bob cannot reference admin repo (lacks view perm)
    try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "create",
        403,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": admin_repo.pulp_href,
        },
    )

    # Distribution creation: bob can reference own repo
    distro = try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "create",
        202,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": bob_repo.pulp_href,
        },
    )
    distro_href = distro.created_resources[0]

    # Distribution update: bob cannot switch to admin repo
    try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "partial_update",
        403,
        distro_href,
        {"repository": admin_repo.pulp_href},
    )

    # Distribution update: bob can switch to own repo (same repo, tests the perm check)
    try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "partial_update",
        202,
        distro_href,
        {"repository": bob_repo.pulp_href},
    )

    # Repository creation: bob cannot reference admin remote
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "create",
        403,
        {"name": str(uuid.uuid4()), "remote": admin_remote.pulp_href},
    )

    # Repository creation: bob can reference own remote
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "create",
        201,
        {"name": str(uuid.uuid4()), "remote": bob_remote.pulp_href},
    )


@pytest.mark.parallel
def test_remote_actions(gen_users, maven_bindings, try_action):
    """Test CRUD on remotes with role-based access."""
    alice, bob, charlie = gen_users("mavenremote")

    a_list = try_action(alice, maven_bindings.RemotesMavenApi, "list", 200)
    assert a_list.count >= 0
    b_list = try_action(bob, maven_bindings.RemotesMavenApi, "list", 200)
    c_list = try_action(charlie, maven_bindings.RemotesMavenApi, "list", 200)
    assert (b_list.count, c_list.count) == (0, 0)

    # Create
    remote_body = {
        "name": str(uuid.uuid4()),
        "url": "https://repo1.maven.org/maven2/",
    }
    try_action(alice, maven_bindings.RemotesMavenApi, "create", 403, remote_body)
    remote = try_action(bob, maven_bindings.RemotesMavenApi, "create", 201, remote_body)
    try_action(charlie, maven_bindings.RemotesMavenApi, "create", 403, remote_body)

    # Read
    try_action(alice, maven_bindings.RemotesMavenApi, "read", 200, remote.pulp_href)
    try_action(bob, maven_bindings.RemotesMavenApi, "read", 200, remote.pulp_href)
    try_action(charlie, maven_bindings.RemotesMavenApi, "read", 404, remote.pulp_href)

    # Update
    update_args = [remote.pulp_href, {"name": str(uuid.uuid4())}]
    try_action(alice, maven_bindings.RemotesMavenApi, "partial_update", 403, *update_args)
    try_action(bob, maven_bindings.RemotesMavenApi, "partial_update", 202, *update_args)
    try_action(charlie, maven_bindings.RemotesMavenApi, "partial_update", 404, *update_args)

    # Delete
    try_action(alice, maven_bindings.RemotesMavenApi, "delete", 403, remote.pulp_href)
    try_action(charlie, maven_bindings.RemotesMavenApi, "delete", 404, remote.pulp_href)
    try_action(bob, maven_bindings.RemotesMavenApi, "delete", 202, remote.pulp_href)


@pytest.mark.parallel
def test_remote_role_management(
    gen_users,
    maven_bindings,
    maven_remote_factory,
    try_action,
):
    """Test role management APIs on remotes."""
    alice, bob, charlie = gen_users("mavenremote")
    with bob:
        remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")

    # list_roles
    try_action(alice, maven_bindings.RemotesMavenApi, "list_roles", 403, remote.pulp_href)
    try_action(bob, maven_bindings.RemotesMavenApi, "list_roles", 200, remote.pulp_href)

    # add_role — give charlie viewer
    nested_role = {
        "users": [charlie.username],
        "role": "maven.mavenremote_viewer",
    }
    try_action(
        alice, maven_bindings.RemotesMavenApi, "add_role", 403, remote.pulp_href, nested_role
    )
    try_action(bob, maven_bindings.RemotesMavenApi, "add_role", 201, remote.pulp_href, nested_role)

    # charlie can now see the remote
    try_action(charlie, maven_bindings.RemotesMavenApi, "read", 200, remote.pulp_href)

    # remove_role — revoke charlie's viewer
    try_action(
        alice, maven_bindings.RemotesMavenApi, "remove_role", 403, remote.pulp_href, nested_role
    )
    try_action(
        bob, maven_bindings.RemotesMavenApi, "remove_role", 201, remote.pulp_href, nested_role
    )

    # charlie can no longer see the remote
    try_action(charlie, maven_bindings.RemotesMavenApi, "read", 404, remote.pulp_href)


@pytest.mark.parallel
def test_distribution_actions(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test CRUD on distributions with role-based access."""
    alice, bob, charlie = gen_users(["mavendistribution", "mavenrepository"])

    with bob:
        repo = maven_repo_factory()

    # Create
    try_action(
        alice,
        maven_bindings.DistributionsMavenApi,
        "create",
        403,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": repo.pulp_href,
        },
    )
    distro = try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "create",
        202,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": repo.pulp_href,
        },
    )
    distro_href = distro.created_resources[0]
    try_action(
        charlie,
        maven_bindings.DistributionsMavenApi,
        "create",
        403,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": repo.pulp_href,
        },
    )

    # Read
    try_action(alice, maven_bindings.DistributionsMavenApi, "read", 200, distro_href)
    try_action(bob, maven_bindings.DistributionsMavenApi, "read", 200, distro_href)
    try_action(charlie, maven_bindings.DistributionsMavenApi, "read", 404, distro_href)

    # Update
    update_args = [distro_href, {"name": str(uuid.uuid4())}]
    try_action(
        alice,
        maven_bindings.DistributionsMavenApi,
        "partial_update",
        403,
        *update_args,
    )
    try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "partial_update",
        202,
        *update_args,
    )
    try_action(
        charlie,
        maven_bindings.DistributionsMavenApi,
        "partial_update",
        404,
        *update_args,
    )

    # Delete
    try_action(alice, maven_bindings.DistributionsMavenApi, "delete", 403, distro_href)
    try_action(charlie, maven_bindings.DistributionsMavenApi, "delete", 404, distro_href)
    try_action(bob, maven_bindings.DistributionsMavenApi, "delete", 202, distro_href)


@pytest.mark.parallel
def test_distribution_role_management(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test role management APIs on distributions."""
    alice, bob, charlie = gen_users(["mavendistribution", "mavenrepository"])
    with bob:
        repo = maven_repo_factory()

    distro = try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "create",
        202,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": repo.pulp_href,
        },
    )
    distro_href = distro.created_resources[0]

    # list_roles
    try_action(alice, maven_bindings.DistributionsMavenApi, "list_roles", 403, distro_href)
    try_action(bob, maven_bindings.DistributionsMavenApi, "list_roles", 200, distro_href)

    # add_role — give charlie viewer
    nested_role = {
        "users": [charlie.username],
        "role": "maven.mavendistribution_viewer",
    }
    try_action(
        alice,
        maven_bindings.DistributionsMavenApi,
        "add_role",
        403,
        distro_href,
        nested_role,
    )
    try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "add_role",
        201,
        distro_href,
        nested_role,
    )

    # charlie can now see the distribution
    try_action(charlie, maven_bindings.DistributionsMavenApi, "read", 200, distro_href)

    # remove_role — revoke charlie's viewer
    try_action(
        alice,
        maven_bindings.DistributionsMavenApi,
        "remove_role",
        403,
        distro_href,
        nested_role,
    )
    try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "remove_role",
        201,
        distro_href,
        nested_role,
    )

    # charlie can no longer see the distribution
    try_action(charlie, maven_bindings.DistributionsMavenApi, "read", 404, distro_href)


@pytest.mark.parallel
def test_label_operations(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test set_label and unset_label on repositories."""
    alice, bob, charlie = gen_users("mavenrepository")
    with bob:
        repo = maven_repo_factory()

    label_args = [repo.pulp_href, {"key": "test", "value": "val"}]
    try_action(alice, maven_bindings.RepositoriesMavenApi, "set_label", 403, *label_args)
    try_action(bob, maven_bindings.RepositoriesMavenApi, "set_label", 201, *label_args)
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "set_label", 404, *label_args)

    unlabel_args = [repo.pulp_href, {"key": "test"}]
    try_action(alice, maven_bindings.RepositoriesMavenApi, "unset_label", 403, *unlabel_args)
    try_action(bob, maven_bindings.RepositoriesMavenApi, "unset_label", 201, *unlabel_args)
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "unset_label", 404, *unlabel_args)


@pytest.mark.parallel
def test_remote_label_operations(
    gen_users,
    maven_bindings,
    maven_remote_factory,
    try_action,
):
    """Test set_label and unset_label on remotes."""
    alice, bob, charlie = gen_users("mavenremote")
    with bob:
        remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")

    label_args = [remote.pulp_href, {"key": "test", "value": "val"}]
    try_action(alice, maven_bindings.RemotesMavenApi, "set_label", 403, *label_args)
    try_action(bob, maven_bindings.RemotesMavenApi, "set_label", 201, *label_args)
    try_action(charlie, maven_bindings.RemotesMavenApi, "set_label", 404, *label_args)

    unlabel_args = [remote.pulp_href, {"key": "test"}]
    try_action(alice, maven_bindings.RemotesMavenApi, "unset_label", 403, *unlabel_args)
    try_action(bob, maven_bindings.RemotesMavenApi, "unset_label", 201, *unlabel_args)
    try_action(charlie, maven_bindings.RemotesMavenApi, "unset_label", 404, *unlabel_args)


@pytest.mark.parallel
def test_distribution_label_operations(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test set_label and unset_label on distributions."""
    alice, bob, charlie = gen_users(["mavendistribution", "mavenrepository"])
    with bob:
        repo = maven_repo_factory()

    distro = try_action(
        bob,
        maven_bindings.DistributionsMavenApi,
        "create",
        202,
        {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": repo.pulp_href,
        },
    )
    distro_href = distro.created_resources[0]

    label_args = [distro_href, {"key": "test", "value": "val"}]
    try_action(alice, maven_bindings.DistributionsMavenApi, "set_label", 403, *label_args)
    try_action(bob, maven_bindings.DistributionsMavenApi, "set_label", 201, *label_args)
    try_action(charlie, maven_bindings.DistributionsMavenApi, "set_label", 404, *label_args)

    unlabel_args = [distro_href, {"key": "test"}]
    try_action(alice, maven_bindings.DistributionsMavenApi, "unset_label", 403, *unlabel_args)
    try_action(bob, maven_bindings.DistributionsMavenApi, "unset_label", 201, *unlabel_args)
    try_action(
        charlie,
        maven_bindings.DistributionsMavenApi,
        "unset_label",
        404,
        *unlabel_args,
    )


@pytest.mark.parallel
def test_content_label_operations(
    gen_user,
    maven_bindings,
    maven_repo_factory,
    random_artifact_factory,
    monitor_task,
    try_action,
):
    """Test set_label and unset_label on content (MavenArtifact)."""
    label_user = gen_user(model_roles=["core.content_labeler", "maven.mavenrepository_viewer"])
    no_perm_user = gen_user()

    repo = maven_repo_factory()
    artifact = random_artifact_factory(size=64)
    content = maven_bindings.ContentArtifactApi.upload(
        artifact=artifact.pulp_href,
        relative_path="com/example/test-label/1.0.0/test-label-1.0.0.jar",
    )
    monitor_task(
        maven_bindings.RepositoriesMavenApi.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    label_args = [content.pulp_href, {"key": "env", "value": "test"}]
    try_action(label_user, maven_bindings.ContentArtifactApi, "set_label", 201, *label_args)
    try_action(no_perm_user, maven_bindings.ContentArtifactApi, "set_label", 403, *label_args)

    unlabel_args = [content.pulp_href, {"key": "env"}]
    try_action(
        label_user,
        maven_bindings.ContentArtifactApi,
        "unset_label",
        201,
        *unlabel_args,
    )
    try_action(
        no_perm_user,
        maven_bindings.ContentArtifactApi,
        "unset_label",
        403,
        *unlabel_args,
    )


@pytest.mark.parallel
def test_role_management(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test role management APIs on repositories."""
    alice, bob, charlie = gen_users("mavenrepository")
    with bob:
        href = maven_repo_factory().pulp_href

    # my_permissions
    aperm = try_action(alice, maven_bindings.RepositoriesMavenApi, "my_permissions", 200, href)
    assert aperm.permissions == []
    bperm = try_action(bob, maven_bindings.RepositoriesMavenApi, "my_permissions", 200, href)
    assert len(bperm.permissions) > 0
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "my_permissions", 404, href)

    # list_roles / add_role / remove_role
    try_action(alice, maven_bindings.RepositoriesMavenApi, "list_roles", 403, href)
    try_action(bob, maven_bindings.RepositoriesMavenApi, "list_roles", 200, href)

    nested_role = {
        "users": [charlie.username],
        "role": "maven.mavenrepository_viewer",
    }
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "add_role",
        403,
        href,
        nested_role,
    )
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "add_role",
        201,
        href,
        nested_role,
    )

    # charlie can now see the repo
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 200, href)

    # remove_role
    try_action(
        alice,
        maven_bindings.RepositoriesMavenApi,
        "remove_role",
        403,
        href,
        nested_role,
    )
    try_action(
        bob,
        maven_bindings.RepositoriesMavenApi,
        "remove_role",
        201,
        href,
        nested_role,
    )

    # charlie can no longer see the repo
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 404, href)


@pytest.mark.parallel
def test_object_level_roles(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    try_action,
):
    """Test that object-level roles grant access to specific objects only."""
    _alice, bob, charlie = gen_users("mavenrepository")

    with bob:
        repo_a = maven_repo_factory()
        repo_b = maven_repo_factory()

    # charlie cannot see either repo
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 404, repo_a.pulp_href)
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 404, repo_b.pulp_href)

    # Grant charlie object-level viewer on repo_a only
    with bob:
        maven_bindings.RepositoriesMavenApi.add_role(
            repo_a.pulp_href,
            {"users": [charlie.username], "role": "maven.mavenrepository_viewer"},
        )

    # charlie can see repo_a but not repo_b
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 200, repo_a.pulp_href)
    try_action(charlie, maven_bindings.RepositoriesMavenApi, "read", 404, repo_b.pulp_href)

    # charlie's list returns only repo_a
    c_list = try_action(charlie, maven_bindings.RepositoriesMavenApi, "list", 200)
    assert c_list.count == 1


@pytest.mark.parallel
def test_package_viewset_permissions(
    gen_users,
    maven_bindings,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
    try_action,
):
    """Test that package listing is scoped by repository view permissions."""
    alice, bob, charlie = gen_users("mavenrepository")

    repo = maven_repo_factory()
    pom_path = pom_file_factory(
        group_id="com.example.rbac",
        artifact_id="pkg-perm-test",
        version="1.0.0",
    )
    content = maven_bindings.ContentArtifactApi.upload(
        file=str(pom_path),
        relative_path="com/example/rbac/pkg-perm-test/1.0.0/pkg-perm-test-1.0.0.pom",
    )
    monitor_task(
        maven_bindings.RepositoriesMavenApi.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    # Alice (viewer) can see packages
    a_list = try_action(alice, maven_bindings.ContentPackageApi, "list", 200)
    assert a_list.count >= 1

    # Bob (creator, no view on admin repo) sees no packages
    b_list = try_action(bob, maven_bindings.ContentPackageApi, "list", 200)
    assert b_list.count == 0

    # Charlie (no roles) sees no packages
    c_list = try_action(charlie, maven_bindings.ContentPackageApi, "list", 200)
    assert c_list.count == 0

    # Grant charlie object-level viewer role on the repo
    maven_bindings.RepositoriesMavenApi.add_role(
        repo.pulp_href,
        {"users": [charlie.username], "role": "maven.mavenrepository_viewer"},
    )

    # Now charlie can see the packages
    c_list2 = try_action(charlie, maven_bindings.ContentPackageApi, "list", 200)
    assert c_list2.count >= 1
