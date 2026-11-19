import json
import logging
import re
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from time import sleep
from typing import Dict, Optional

import pytest


from github import GithubIntegration
from github.Consts import MAX_JWT_EXPIRY
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance
from infrahouse_core.github import GitHubActions
from infrahouse_core.logging import setup_logging
from infrahouse_core.timeout import timeout
from pytest_infrahouse.plugin import aws_region
from pytest_infrahouse.utils import wait_for_instance_refresh
from requests import get

LOG = logging.getLogger(__name__)
GITHUB_ORG_NAME = "infrahouse"
TERRAFORM_ROOT_DIR = "test_data"
GH_APP_ID = 1016363
INSPECTOR_EXCLUSION_TAG = "InspectorEc2Exclusion"

# Role name prefixes from issue #109. A policy that selects principals by role name
# (e.g. ArnLike on aws:PrincipalArn) relies on these, so the tests check them against
# the roles AWS reports rather than against Terraform strings.
INSTANCE_ROLE_PREFIX = "actions-runner-instance-"
LAMBDA_ROLE_PREFIX = "actions-runner-lambda-"
LAMBDA_ROLE_PATTERN = "actions-runner-lambda-*"
LAMBDA_ROLE_NAME_RE = re.compile(
    r"^actions-runner-lambda-(?P<pool>[^-]+)-(?P<function>[^-]+)-"
)

# Maximum LambdaInsights memory_utilization (percent) we tolerate in tests.
# The prod alarm fires at 80%; the test margin is tighter so regressions are
# caught in CI before they would page.
LAMBDA_MEMORY_UTILIZATION_MAX_PERCENT = 70


setup_logging(LOG, debug=True, debug_botocore=False)
setup_logging(logging.getLogger("pytest_infrahouse"), debug=True, debug_botocore=False)


def pytest_addoption(parser):
    parser.addoption(
        "--github-token",
        action="store",
        help=f"GitHub Token with Admin permissions.",
    )
    parser.addoption(
        "--github-app-pem-secret",
        action="store",
        help=f"Secret ARN with a GitHub App PEM key.",
    )


@pytest.fixture(scope="session")
def github_token(request):
    return request.config.getoption("--github-token")


@pytest.fixture(scope="session")
def github_app_pem_secret_arn(request):
    return request.config.getoption("--github-app-pem-secret")


def ensure_runners(
    gha: GitHubActions,
    aws_region,
    autoscaling_client,
    timeout_time=900,
    test_role_arn=None,
):
    try:
        with timeout(timeout_time):
            while True:
                try:
                    runners = list(gha.find_runners_by_label("awesome"))
                    LOG.info("Found %d runners", len(runners))
                    assert len(runners) > 0

                    LOG.info(
                        "Using runner %s (%s) to seed autoscaling instance.",
                        runners[0].name,
                        runners[0].instance_id,
                    )
                    asg_instance = ASGInstance(
                        instance_id=runners[0].instance_id,
                        role_arn=test_role_arn,
                        region=aws_region,
                    )
                    asg = ASG(
                        asg_instance.asg_name, role_arn=test_role_arn, region=aws_region
                    )
                    wait_for_instance_refresh(
                        asg_instance.asg_name,
                        autoscaling_client,
                        timeout=timeout_time,
                    )

                    for instance in asg.instances:
                        LOG.info("Checking instance %s", instance.instance_id)
                        runner = gha.find_runner_by_label(
                            f"instance_id:{instance.instance_id}"
                        )
                        assert runner.status == "online"

                    break
                except AssertionError:
                    LOG.info("No registered runners yet")
                    sleep(3)
    except TimeoutError:
        LOG.error("No registered runners after %d seconds.", timeout_time)
        assert False


def assert_no_inspector_exclusion_tag(
    gha: GitHubActions,
    aws_region: str,
    test_role_arn: Optional[str] = None,
) -> None:
    """
    Fail if a registered runner still carries the Amazon Inspector exclusion tag.

    Instances launch tagged with ``InspectorEc2Exclusion`` so Inspector does not scan
    them, and raise findings against the AMI's package baseline, before the boot-time
    security upgrade has finished. The tag is removed from ``post_runcmd``, which the
    cloud-init bootstrap script runs before it signals the bootstrap lifecycle hook.
    A runner that is registered and online has therefore passed that point, so a
    leftover tag means the instance is serving jobs while invisible to vulnerability
    scanning.

    :param gha: GitHub Actions client used to find the registered runners.
    :param aws_region: AWS region the autoscaling group lives in.
    :param test_role_arn: Optional role ARN to assume for the AWS calls.
    :raises AssertionError: If no runners are registered, or if any instance in the
        autoscaling group still carries the exclusion tag.
    """
    runners = list(gha.find_runners_by_label("awesome"))
    assert (
        runners
    ), "No registered runners found to check for the Inspector exclusion tag."

    asg_instance = ASGInstance(
        instance_id=runners[0].instance_id, role_arn=test_role_arn, region=aws_region
    )
    asg = ASG(asg_instance.asg_name, role_arn=test_role_arn, region=aws_region)

    for instance in asg.instances:
        # Inspector matches the exclusion key case-insensitively, so a tag differing
        # only in case would still suppress scanning.
        leftover = [
            key
            for key in instance.tags
            if key.lower() == INSPECTOR_EXCLUSION_TAG.lower()
        ]
        LOG.info("Instance %s exclusion tags: %s", instance.instance_id, leftover)
        assert not leftover, (
            f"Instance {instance.instance_id} is a registered runner but still carries "
            f"{leftover}, so Amazon Inspector will not scan it."
        )


def assert_role_name_prefixes(
    boto3_session,
    aws_region: str,
    runner_role_arn: str,
    lambda_functions: Dict[str, str],
) -> Dict[str, str]:
    """
    Fail unless the instance role and the Lambda execution roles carry the issue #109 prefixes.

    The instance role must start with ``actions-runner-instance-``. Each Lambda's execution
    role, as the Lambda service reports it, must be named
    ``actions-runner-lambda-<pool>-<function>-<suffix>``: the three roles share ``<pool>`` and
    each carries its own ``<function>`` token. The pattern ``actions-runner-lambda-*`` must match
    every Lambda role and must not match the instance role, so a policy can grant the Lambdas
    something by pattern without granting it to the runners.

    :param boto3_session: Boto3 session for the test account.
    :param aws_region: AWS region the Lambda functions live in.
    :param runner_role_arn: ARN of the instance role (the module's ``runner_role_arn`` output).
    :param lambda_functions: Lambda function name by the ``<function>`` token its role must
        carry, e.g. ``{"reg": "<asg>_registration"}``.
    :return: Lambda execution role ARN by ``<function>`` token, as IAM reports it.
    :raises AssertionError: If any role name breaks the rules above.
    """
    iam_client = boto3_session.client("iam")
    lambda_client = boto3_session.client("lambda", region_name=aws_region)

    instance_role = iam_client.get_role(RoleName=runner_role_arn.split("/")[-1])["Role"]
    instance_role_name = instance_role["RoleName"]
    LOG.info("Instance role: %s", instance_role["Arn"])
    assert instance_role_name.startswith(
        INSTANCE_ROLE_PREFIX
    ), f"Instance role {instance_role_name} does not start with {INSTANCE_ROLE_PREFIX}"
    assert not fnmatchcase(instance_role_name, LAMBDA_ROLE_PATTERN), (
        f"Instance role {instance_role_name} matches {LAMBDA_ROLE_PATTERN}, "
        "so a policy meant for the Lambdas would also cover the runners."
    )

    lambda_role_arns = {}
    pools = set()
    for token, function_name in lambda_functions.items():
        role_arn = lambda_client.get_function(FunctionName=function_name)[
            "Configuration"
        ]["Role"]
        role = iam_client.get_role(RoleName=role_arn.split("/")[-1])["Role"]
        role_name = role["RoleName"]
        LOG.info("Lambda %s runs as %s", function_name, role["Arn"])

        assert role_name.startswith(LAMBDA_ROLE_PREFIX), (
            f"Lambda {function_name} runs as {role_name}, "
            f"which does not start with {LAMBDA_ROLE_PREFIX}"
        )
        assert fnmatchcase(
            role_name, LAMBDA_ROLE_PATTERN
        ), f"Lambda {function_name} role {role_name} does not match {LAMBDA_ROLE_PATTERN}"
        match = LAMBDA_ROLE_NAME_RE.match(role_name)
        assert match, (
            f"Lambda {function_name} role {role_name} is not named "
            "actions-runner-lambda-<pool>-<function>-<suffix>"
        )
        # Each token is a distinct dict key, so matching it also proves the three
        # roles differ in the <function> part.
        assert match.group("function") == token, (
            f"Lambda {function_name} role {role_name} carries function token "
            f"{match.group('function')!r}, expected {token!r}"
        )
        pools.add(match.group("pool"))
        lambda_role_arns[token] = role["Arn"]

    assert (
        len(pools) == 1
    ), f"The Lambda roles of one pool must share the <pool> part, got {sorted(pools)}"
    return lambda_role_arns


def assert_lambda_memory_within_limit(
    cloudwatch_client,
    function_name: str,
    max_percent: float = LAMBDA_MEMORY_UTILIZATION_MAX_PERCENT,
    wait_timeout: int = 900,
) -> None:
    """
    Fail if a Lambda's observed memory utilization exceeds max_percent.

    Polls the LambdaInsights memory_utilization metric for the given function
    (same approach as terraform-aws-lambda-monitored's test). Lambda Insights
    can lag up to ~15 minutes before a datapoint becomes queryable, so the poll
    is wrapped in a wait_timeout loop.

    :param cloudwatch_client: Boto3 CloudWatch client.
    :param function_name: Lambda function name to inspect.
    :param max_percent: Threshold in percent. The test fails if
        max observed utilization exceeds this value.
    :param wait_timeout: Seconds to wait for Lambda Insights to publish at
        least one datapoint.
    :raises AssertionError: When the observed utilization exceeds max_percent
        or no datapoints arrive within wait_timeout.
    """
    utilization_pct = None
    with timeout(wait_timeout):
        while utilization_pct is None:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=15)
            util_stats = cloudwatch_client.get_metric_statistics(
                Namespace="LambdaInsights",
                MetricName="memory_utilization",
                Dimensions=[{"Name": "function_name", "Value": function_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=60,
                Statistics=["Maximum"],
            )
            util_points = util_stats.get("Datapoints", [])
            if util_points:
                utilization_pct = max(dp["Maximum"] for dp in util_points)
                break
            LOG.info(
                "Waiting for LambdaInsights memory_utilization datapoints for %s...",
                function_name,
            )
            sleep(30)

    LOG.info(
        "Lambda %s max memory utilization: %.2f%% (limit %s%%)",
        function_name,
        utilization_pct,
        max_percent,
    )
    assert utilization_pct < max_percent, (
        f"Lambda {function_name} used {utilization_pct:.2f}% of its allocated "
        f"memory, which exceeds the test limit of {max_percent}%. "
        f"Either reduce memory pressure in the handler or raise memory_size."
    )


def invoke_deregistration_sweep(
    lambda_client, function_name: str, invocations: int = 3
) -> None:
    """
    Invoke the runner_deregistration lambda in sweep mode.

    The deregistration lambda runs on a 30-minute EventBridge schedule to clean
    up stale runners (the path that OOM'd originally). A typical test run is
    shorter than that schedule, so the test must invoke the lambda explicitly
    to exercise the _clean_runners branch. The branch is selected by sending
    an event whose `detail` has no `LifecycleHookName` key.

    :param lambda_client: Boto3 Lambda client.
    :param function_name: Name of the runner_deregistration lambda.
    :param invocations: How many times to invoke the lambda.
    :raises AssertionError: If any invocation returns a non-200 status or a
        FunctionError.
    """
    sweep_event = {
        "source": "aws.events",
        "detail-type": "Scheduled Event",
        "detail": {},
    }
    for i in range(invocations):
        LOG.info("Invoking %s in sweep mode (%d/%d)", function_name, i + 1, invocations)
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(sweep_event),
        )
        assert response["StatusCode"] == 200, (
            f"Lambda {function_name} sweep invocation returned status "
            f"{response['StatusCode']}"
        )
        assert "FunctionError" not in response, (
            f"Lambda {function_name} sweep invocation returned FunctionError: "
            f"{response.get('FunctionError')}"
        )


def get_secret(secretsmanager_client, secret_name):
    """
    Retrieve a value of a secret by its name.
    """
    return secretsmanager_client.get_secret_value(
        SecretId=secret_name,
    )["SecretString"]


def get_tmp_token(secretsmanager_client, pem_key_secret: str) -> str:
    """
    Generate a temporary GitHub token from GitHUb App PEM key

    :param secretsmanager_client: Boto3 client
    :param pem_key_secret: Secret ARN with the PEM key.
    :type pem_key_secret: str
    :return: GitHub token
    :rtype: str
    """
    github_client = GithubIntegration(
        GH_APP_ID,
        get_secret(secretsmanager_client, pem_key_secret),
        jwt_expiry=MAX_JWT_EXPIRY,
    )
    for installation in github_client.get_installations():
        if installation.target_type == "Organization":
            if GITHUB_ORG_NAME == _get_org_name(github_client, installation.id):
                return github_client.get_access_token(
                    installation_id=installation.id
                ).token

    raise RuntimeError(
        f"Could not find installation of {GH_APP_ID} in organization {GITHUB_ORG_NAME}"
    )


def _get_org_name(github_client, installation_id):
    url = f"https://api.github.com/app/installations/{installation_id}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_client.create_jwt()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = get(url, headers=headers, timeout=600)
    return response.json()["account"]["login"]
