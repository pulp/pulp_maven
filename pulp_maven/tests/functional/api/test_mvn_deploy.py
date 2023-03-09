import os
import subprocess


def test_mvn_deploy_workflow(
    maven_repo_api_client, maven_repo_factory, maven_distribution_factory, tmp_path
):
    # Create a repository and distribution pointing to that repository.
    repo = maven_repo_factory(name="maven-snapshots")
    maven_distribution_factory(
        name="maven-releases-distribution", base_path=repo.name, repository=repo.pulp_href
    )
    assert repo.latest_version_href.endswith("/versions/0/")

    # Deploy the simple project into the snapshot repository
    current_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        # Copy the simple-project to a temporary directory to ensure proper permissions
        subprocess.check_output(
            ["cp", "-r", f"{current_dir}/../../assets/simple-project", f"{tmp_path}/simple-project"]
        )
        # Run mvn deploy
        result = subprocess.run(
            ["mvn", "deploy"],
            cwd=f"{tmp_path}/simple-project",
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(result.stdout.decode())
        print(result.stderr.decode())
    except subprocess.CalledProcessError as e:
        # The command had a non-zero exit code
        print(e.stderr.decode())

    # Assert that the latest version is 12
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/12/")
