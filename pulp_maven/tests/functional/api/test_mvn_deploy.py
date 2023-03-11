import os
import subprocess
from urllib.parse import urljoin
from pulp_maven.tests.functional.utils import download_file


def test_mvn_deploy_workflow(
    maven_repo_api_client, maven_repo_factory, maven_distribution_factory, tmp_path
):
    # Create a repository and distribution pointing to that repository.
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/0/")

    # Deploy the simple project into the snapshot repository
    current_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        # Copy the simple-project to a temporary directory to ensure proper permissions
        subprocess.check_output(
            ["cp", "-r", f"{current_dir}/../../assets/simple-project", f"{tmp_path}/simple-project"]
        )
        # Update pom.xml to point the Snapshots repository to the test repository
        subprocess.check_output(
            ["sed", "-i", f"s/maven-snapshots/{repo.name}/g", f"{tmp_path}/simple-project/pom.xml"]
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

    # Assert that you can get the metadata for the simple-project
    pulp_unit_url = urljoin(
        "http://localhost:24817",
        f"/pulp/maven/{repo.name}/org/sonatype/nexus/examples/simple-project/maven-metadata.xml",
    )
    downloaded_file = download_file(pulp_unit_url)
    assert downloaded_file.response_obj.status == 200

    # Assert that a GET to the Maven API redirects to the content app
    assert not downloaded_file.response_obj.real_url.path.startswith("/pulp/maven")
