"""Exercise deployment command selection without contacting Docker or AWS."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize("skip_build", ["false", "true"])
def test_deploy_builds_for_eks_architecture_and_supports_rollback(tmp_path, skip_build):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.jsonl"
    stub = bin_dir / "stub"
    stub.write_text("#!/usr/bin/env python3\n" + textwrap.dedent("""\
            import json, os, sys
            from pathlib import Path

            name = Path(sys.argv[0]).name
            args = sys.argv[1:]
            with open(os.environ['DEPLOY_CALL_LOG'], 'a') as log:
                log.write(json.dumps([name, *args]) + '\\n')
            if name == 'terraform':
                outputs = {
                    'aws_region': 'us-east-1',
                    'cluster_name': 'test-cluster',
                    'application_secret_arn': 'test-secret',
                    'kafka_bootstrap_brokers_sasl_scram': 'broker.test:9096',
                    'ecr_repository_urls': json.dumps({
                        'backend': 'registry.test/backend',
                        'dashboard': 'registry.test/dashboard',
                    }),
                }
                print(outputs[args[-1]])
            elif name == 'aws' and args[:2] == ['secretsmanager', 'get-secret-value']:
                print('{}')
            elif name == 'aws' and args[:2] == ['ecr', 'get-login-password']:
                print('test-password')
            elif name == 'jq':
                data = json.load(sys.stdin)
                if args[0] == '-r':
                    print(data[args[1][1:]])
                else:
                    print(json.dumps({'kind': 'Secret', 'data': {}}))
            elif name == 'docker' and args[0] == 'login':
                sys.stdin.read()
            elif name == 'kubectl' and args[-2:] == ['-f', '-']:
                sys.stdin.read()
            """))
    stub.chmod(0o755)
    (bin_dir / "python3").symlink_to(sys.executable)
    for command in ("aws", "docker", "git", "jq", "kubectl", "kustomize", "terraform"):
        (bin_dir / command).symlink_to(stub)
    subprocess.run(
        ["bash", "scripts/deploy-aws.sh"],
        cwd=Path(__file__).resolve().parents[2],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "IMAGE_TAG": "test-revision",
            "SKIP_BUILD": skip_build,
            "DOCKER_DEFAULT_PLATFORM": "linux/arm64",
            "DEPLOY_CALL_LOG": str(calls),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        capture_output=True,
    )
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    builds = [command for command in commands if command[:2] == ["docker", "build"]]
    if skip_build == "true":
        assert not builds
        assert not any(command[0] == "docker" for command in commands)
    else:
        assert len(builds) == 2
        for build in builds:
            assert build[build.index("--platform") + 1] == "linux/amd64"
        assert len([command for command in commands if command[:2] == ["docker", "push"]]) == 2
    assert any("job/database-migrations" in command and "wait" in command for command in commands)
