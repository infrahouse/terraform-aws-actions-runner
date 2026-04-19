import json
import logging
from datetime import datetime, timedelta, timezone
from time import sleep

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
