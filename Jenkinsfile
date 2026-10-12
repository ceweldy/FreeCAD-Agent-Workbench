// Runs a script inside a throwaway container. Source (including .git) is
// streamed in with tar, so no host bind mounts are needed.
def inDocker(String image, String script) {
    writeFile file: '.ci-step.sh', text: "set -eux\n" + script + "\n"
    sh """
        set -eu
        tar -cf - . | docker run --rm -i --memory 6g --cpus 4 \\
          -e CI=true -e HOME=/tmp/home ${image} \\
          sh -c 'mkdir -p /src /tmp/home && cd /src && tar -xf - && git config --global --add safe.directory /src 2>/dev/null || true; sh .ci-step.sh'
    """
}

def ciSteps = '''python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m ruff check .
python -m pytest
python scripts/agent-workbench-doctor --json --skip-codex'''

pipeline {
    agent { label 'linux-docker' }
    options { timeout(time: 20, unit: 'MINUTES'); timestamps(); disableConcurrentBuilds(); buildDiscarder(logRotator(numToKeepStr: '20')) }
    stages {
        stage('Test') {
            parallel {
                stage('Python 3.11') { steps { script { inDocker('python:3.11', ciSteps) } } }
                stage('Python 3.12') { steps { script { inDocker('python:3.12', ciSteps) } } }
                stage('Python 3.13') { steps { script { inDocker('python:3.13', ciSteps) } } }
            }
        }
    }
    post { always { deleteDir() } }
}
