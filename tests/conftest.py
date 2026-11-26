import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from os import path as osp
from statistics import median
from tempfile import TemporaryDirectory
from textwrap import dedent
from time import sleep, time
from typing import Dict, Iterator, List, Optional

import jwt
import pytest


from botocore.client import BaseClient
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from github import GithubIntegration
from github.Consts import MAX_JWT_EXPIRY
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance
from infrahouse_core.github import GitHubActions, KMSSigner
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

# SEC-46: the kms case imports the test App's key into a new KMS key with the commands the
# README documents. The fixture runs the shell block that follows each marker in README.md.
README_PATH = osp.join(osp.dirname(__file__), "..", "README.md")
README_IMPORT_KEY_MARKER = "<!-- kms-import-key-material -->"
README_CHECK_KEY_MARKER = "<!-- kms-check-key -->"
KMS_SIGNING_ALGORITHM = "RSASSA_PKCS1_V1_5_SHA_256"
# The minimum: a key left over from a run that couldn't clean up goes away in a week.
KMS_KEY_PENDING_WINDOW_DAYS = 7
KMS_KEY_FIXTURE_TAG = "infrahouse/terraform-aws-actions-runner/github_app_kms_key_arn"

# Role name prefixes from issue #109. A policy that selects principals by role name
# (e.g. ArnLike on aws:PrincipalArn) relies on these, so the tests check them against
# the roles AWS reports rather than against Terraform strings. Since issue #111 the
# instance role is exactly INSTANCE_ROLE_PREFIX + <pool>, with the Lambda roles' <pool>.
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

# Error-rate alarm periods (issue #91). An error-rate alarm can fire only if every period
# contains an invocation, so the period follows how often the function runs.
DEREGISTRATION_ERROR_RATE_PERIOD = 1800  # one period per 30-minute sweep
RECORD_METRIC_ERROR_RATE_PERIOD = 60  # record_metric runs every minute


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
        help=f"Name or ARN of the Secrets Manager secret with the test GitHub App's PEM key.",
    )
    parser.addoption(
        "--github-app-pem-secret-region",
        action="store",
        help=(
            "Region of the --github-app-pem-secret secret, if it is given by name. "
            "By default, the --aws-region of the test."
        ),
    )


@pytest.fixture(scope="session")
def github_token(request):
    return request.config.getoption("--github-token")


@pytest.fixture(scope="session")
def github_app_pem_secret_arn(request, boto3_session, aws_region) -> Optional[str]:
    """
    Return the ARN of the secret with the test GitHub App's PEM key, or ``None`` if it isn't given.

    ``--github-app-pem-secret`` may name the secret, so that no account ID needs to be written in
    the Makefile. The secret may be in another region than the test, given by
    ``--github-app-pem-secret-region``.
    """
    secret = request.config.getoption("--github-app-pem-secret")
    if secret is None or secret.startswith("arn:"):
        return secret
    region = request.config.getoption("--github-app-pem-secret-region") or aws_region
    return boto3_session.client("secretsmanager", region_name=region).describe_secret(
        SecretId=secret
    )["ARN"]


def readme_shell_block(marker: str) -> str:
    """
    Return the shell commands of the README's code block that follows ``marker``.

    :param marker: An HTML comment on its own line in README.md, before a fenced ``bash`` block.
    :return: The block's commands, dedented.
    :raises ValueError: If the marker isn't in the README once, or no bash block follows it.
    """
    with open(README_PATH) as fp:
        readme = fp.read()
    if readme.count(marker) != 1:
        raise ValueError(
            f"README.md must have {marker} once, found {readme.count(marker)}."
        )
    match = re.compile(
        r"\n(?P<indent>[ \t]*)```bash\n(?P<body>.*?)\n(?P=indent)```\n", re.DOTALL
    ).match(readme, readme.index(marker) + len(marker))
    if match is None:
        raise ValueError(f"README.md has no bash block right after {marker}.")
    return dedent(match["body"])


def _run_readme_block(marker: str, environment: Dict[str, str]) -> str:
    """
    Run the README's shell block after ``marker`` with bash, as an operator would.

    :param marker: The README marker of the block.
    :param environment: The whole environment of the commands.
    :return: The commands' standard output.
    :raises AssertionError: If the commands fail.
    """
    LOG.info("Running the README's block after %s", marker)
    # --norc and no stdin: bash reads ~/.bashrc when it thinks a remote shell daemon started it,
    # and the commands must not depend on the runner's shell setup.
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", readme_shell_block(marker)],
        stdin=subprocess.DEVNULL,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    # The commands print no key material: the key stays in files, and the JWT goes to curl in one.
    LOG.info("stdout: %s\nstderr: %s", result.stdout, result.stderr)
    assert (
        result.returncode == 0
    ), f"The README's block after {marker} failed with exit code {result.returncode}: {result.stderr}"
    return result.stdout


def _readme_environment(
    boto3_session, work_dir: str, variables: Dict[str, str]
) -> Dict[str, str]:
    """
    Build the environment for the README's commands: the test role's credentials and nothing else from AWS.

    :param boto3_session: Session with the test role's credentials.
    :param work_dir: Private directory for the commands' temporary files.
    :param variables: The variables the README tells the operator to set.
    :return: The environment.
    :raises AssertionError: If ``openssl`` in ``PATH`` isn't OpenSSL 3 or later.
    """
    credentials = boto3_session.get_credentials().get_frozen_credentials()
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", work_dir),
        "LANG": "C",
        # mktemp creates the commands' own directory in here, so it is deleted even if they fail.
        "TMPDIR": work_dir,
        "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_PAGER": "",
        "AWS_ACCESS_KEY_ID": credentials.access_key,
        "AWS_SECRET_ACCESS_KEY": credentials.secret_key,
        **variables,
    }
    if credentials.token:
        environment["AWS_SESSION_TOKEN"] = credentials.token

    openssl_version = subprocess.run(
        ["openssl", "version"],
        stdin=subprocess.DEVNULL,
        env=environment,
        capture_output=True,
        text=True,
    ).stdout
    assert re.match(r"OpenSSL ([3-9]|[1-9][0-9])\.", openssl_version), (
        f"The README's key import needs OpenSSL 3 or later for AES key wrap, but openssl is "
        f"{openssl_version!r}. Put OpenSSL 3 first in PATH, e.g. $(brew --prefix openssl@3)/bin."
    )
    return environment


def _assert_kms_signs_as_pem(boto3_session, key_arn: str, pem: str) -> None:
    """
    Fail unless a JWT signed through KMS verifies against the PEM key's public key.

    :param boto3_session: Session with the test role's credentials, in the key's region.
    :param key_arn: The KMS key the PEM was imported into.
    :param pem: The App's private key.
    :raises AssertionError: If the KMS key isn't the PEM key.
    """
    now = int(time())
    payload = {"iat": now - 60, "exp": now + 540, "iss": str(GH_APP_ID)}
    signed = KMSSigner(key_arn, session=boto3_session).sign(payload)
    public_key = load_pem_private_key(pem.encode(), password=None).public_key()
    try:
        claims = jwt.decode(
            signed, public_key, algorithms=["RS256"], issuer=str(GH_APP_ID)
        )
    except jwt.InvalidTokenError as err:
        raise AssertionError(
            f"A JWT that {key_arn} signed doesn't verify with the PEM key's public key: {err}"
        ) from err
    assert claims == payload


@pytest.fixture()
def github_app_kms_key_arn(
    boto3_session, aws_region, keep_after, github_app_pem_secret_arn
) -> Iterator[str]:
    """
    Import the test GitHub App's key into a new KMS key, and return the key's ARN.

    SEC-46 decided on a fresh key per run. The key is created as the README's step 2 creates it,
    with tags to find a leftover, and the PEM is imported with the README's own commands (steps 3
    and 4). The fixture then checks that a JWT signed through KMS verifies against the PEM's
    public key. At teardown the key is scheduled for deletion, unless ``--keep-after``.

    The PEM, and every file the import makes, stays in memory or in a private temporary directory
    that is deleted, also when the import fails. Nothing is logged from them.
    """
    assert github_app_pem_secret_arn, (
        "The kms case needs the test App's PEM key: pass the secret with --github-app-pem-secret "
        "(and --github-app-pem-secret-region if it is in another region)."
    )
    pem_region = github_app_pem_secret_arn.split(":")[3]
    pem = boto3_session.client(
        "secretsmanager", region_name=pem_region
    ).get_secret_value(SecretId=github_app_pem_secret_arn)["SecretString"]

    kms_client = boto3_session.client("kms", region_name=aws_region)
    key_arn = kms_client.create_key(
        Description=f"Test key of GitHub App {GH_APP_ID}, for terraform-aws-actions-runner tests",
        KeyUsage="SIGN_VERIFY",
        KeySpec="RSA_2048",
        Origin="EXTERNAL",
        Tags=[
            {
                "TagKey": "created_by",
                "TagValue": "infrahouse/terraform-aws-actions-runner",
            },
            {"TagKey": "created_by_fixture", "TagValue": KMS_KEY_FIXTURE_TAG},
        ],
    )["KeyMetadata"]["Arn"]
    LOG.info("Created KMS key %s for the test GitHub App's key", key_arn)
    try:
        with TemporaryDirectory(prefix="github-app-key-") as work_dir:
            pem_file = osp.join(work_dir, "github-app.pem")
            with os.fdopen(
                os.open(pem_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as fp:
                fp.write(pem)
            environment = _readme_environment(
                boto3_session,
                work_dir,
                {
                    "KEY_ARN": key_arn,
                    "PEM_FILE": pem_file,
                    "GITHUB_APP_ID": str(GH_APP_ID),
                },
            )
            _run_readme_block(README_IMPORT_KEY_MARKER, environment)
            slug = _run_readme_block(README_CHECK_KEY_MARKER, environment)
            LOG.info("GitHub accepts a JWT %s signed: %s", key_arn, slug.strip())

        _assert_kms_signs_as_pem(boto3_session, key_arn, pem)
        del pem
        yield key_arn
    finally:
        if keep_after:
            LOG.info("--keep-after: KMS key %s is kept.", key_arn)
        else:
            kms_client.schedule_key_deletion(
                KeyId=key_arn, PendingWindowInDays=KMS_KEY_PENDING_WINDOW_DAYS
            )
            LOG.info(
                "Scheduled KMS key %s for deletion in %d days.",
                key_arn,
                KMS_KEY_PENDING_WINDOW_DAYS,
            )


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


def asg_pool(asg_name: str) -> str:
    """
    Return the ``<pool>`` part the instance and Lambda role names of an ASG must carry.

    It is the random suffix after the ASG name's last ``-``. Unlike the rest of the ASG
    name, which comes from the launch template's timestamped name, it stays the same for
    the life of the pool.

    :param asg_name: Name of the pool's autoscaling group.
    :return: The ``<pool>`` part.
    """
    return asg_name.rsplit("-", 1)[-1]


def assert_role_name_prefixes(
    boto3_session,
    aws_region: str,
    runner_role_arn: str,
    asg_name: str,
    lambda_functions: Dict[str, str],
) -> Dict[str, str]:
    """
    Fail unless the instance role and the Lambda execution roles carry the pool's names.

    ``<pool>`` is the random suffix of the ASG name (see :func:`asg_pool`). The instance role
    must be named exactly ``actions-runner-instance-<pool>`` (issue #111). Each Lambda's
    execution role, as the Lambda service reports it, must be named
    ``actions-runner-lambda-<pool>-<function>-<suffix>`` (issue #109), where ``<function>`` is
    the role's own token, so all four roles share ``<pool>``. The pattern
    ``actions-runner-lambda-*`` must match every Lambda role and must not match the instance
    role, so a policy can grant the Lambdas something by pattern without granting it to the
    runners.

    :param boto3_session: Boto3 session for the test account.
    :param aws_region: AWS region the Lambda functions live in.
    :param runner_role_arn: ARN of the instance role (the module's ``runner_role_arn`` output).
    :param asg_name: Name of the pool's autoscaling group. After an upgrade, pass the name
        from before it, so the check also proves the pool kept its ``<pool>``.
    :param lambda_functions: Lambda function name by the ``<function>`` token its role must
        carry, e.g. ``{"reg": "<asg>_registration"}``.
    :return: Lambda execution role ARN by ``<function>`` token, as IAM reports it.
    :raises AssertionError: If any role name breaks the rules above.
    """
    iam_client = boto3_session.client("iam")
    lambda_client = boto3_session.client("lambda", region_name=aws_region)

    pool = asg_pool(asg_name)

    instance_role = iam_client.get_role(RoleName=runner_role_arn.split("/")[-1])["Role"]
    instance_role_name = instance_role["RoleName"]
    LOG.info("Instance role: %s", instance_role["Arn"])
    assert instance_role_name.startswith(
        INSTANCE_ROLE_PREFIX
    ), f"Instance role {instance_role_name} does not start with {INSTANCE_ROLE_PREFIX}"
    # The Lambda roles below must carry the same pool, so this also proves the
    # instance role and the Lambda roles share <pool>.
    expected_instance_role_name = f"{INSTANCE_ROLE_PREFIX}{pool}"
    assert instance_role_name == expected_instance_role_name, (
        f"Instance role is {instance_role_name}, expected {expected_instance_role_name}, "
        f"named from pool {pool!r}, the random suffix of ASG {asg_name}"
    )
    assert not fnmatchcase(instance_role_name, LAMBDA_ROLE_PATTERN), (
        f"Instance role {instance_role_name} matches {LAMBDA_ROLE_PATTERN}, "
        "so a policy meant for the Lambdas would also cover the runners."
    )

    lambda_role_arns = {}
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
        # Every role carrying the ASG's suffix also proves the three share <pool>.
        assert match.group("pool") == pool, (
            f"Lambda {function_name} role {role_name} carries pool {match.group('pool')!r}, "
            f"expected {pool!r}, the random suffix of ASG {asg_name}"
        )
        lambda_role_arns[token] = role["Arn"]

    return lambda_role_arns


def assert_role_outputs(
    boto3_session,
    tf_output: dict,
    asg_name: str,
    lambda_role_arns: Dict[str, str],
) -> None:
    """
    Fail unless the module's role outputs describe the roles AWS reports.

    ``runner_role_name`` must name the role behind ``runner_role_arn``.
    ``lambda_role_name_prefix`` must be ``actions-runner-lambda-<pool>-`` for this pool's
    ``<pool>`` (see :func:`asg_pool`), and ``lambda_role_arn_pattern`` must end with
    ``:role/<that prefix>*``, so that neither covers another pool. Every Lambda execution
    role must start with the prefix and match the pattern (IAM wildcard semantics), and the
    instance role must match neither, so a caller can write a policy for the Lambdas from
    the outputs alone.

    :param boto3_session: Boto3 session for the test account.
    :param tf_output: ``terraform output -json`` of the test root module.
    :param asg_name: Name of the pool's autoscaling group.
    :param lambda_role_arns: Lambda execution role ARNs, as returned by
        :func:`assert_role_name_prefixes`.
    :raises AssertionError: If an output doesn't match the roles.
    """
    iam_client = boto3_session.client("iam")
    runner_role_arn = tf_output["runner_role_arn"]["value"]
    runner_role = iam_client.get_role(RoleName=tf_output["runner_role_name"]["value"])[
        "Role"
    ]
    assert runner_role["Arn"] == runner_role_arn, (
        f"runner_role_name names {runner_role['Arn']}, "
        f"but runner_role_arn is {runner_role_arn}"
    )

    prefix = tf_output["lambda_role_name_prefix"]["value"]
    pattern = tf_output["lambda_role_arn_pattern"]["value"]
    LOG.info(
        "lambda_role_name_prefix: %s, lambda_role_arn_pattern: %s", prefix, pattern
    )
    expected_prefix = f"{LAMBDA_ROLE_PREFIX}{asg_pool(asg_name)}-"
    assert prefix == expected_prefix, (
        f"lambda_role_name_prefix is {prefix}, expected {expected_prefix} "
        f"for ASG {asg_name}"
    )
    assert pattern.endswith(
        f":role/{expected_prefix}*"
    ), f"lambda_role_arn_pattern {pattern} does not end with :role/{expected_prefix}*"
    for role_arn in lambda_role_arns.values():
        assert role_arn.split("/")[-1].startswith(
            prefix
        ), f"Lambda role {role_arn} does not start with lambda_role_name_prefix {prefix}"
        assert fnmatchcase(
            role_arn, pattern
        ), f"Lambda role {role_arn} does not match lambda_role_arn_pattern {pattern}"

    assert not runner_role["RoleName"].startswith(
        prefix
    ), f"Instance role {runner_role_arn} starts with lambda_role_name_prefix {prefix}"
    assert not fnmatchcase(
        runner_role_arn, pattern
    ), f"Instance role {runner_role_arn} matches lambda_role_arn_pattern {pattern}"


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


def _describe_metric_alarm(
    cloudwatch_client: BaseClient, alarm_name: str
) -> Optional[Dict]:
    """
    Return the metric alarm with exactly this name.

    :param cloudwatch_client: Boto3 CloudWatch client.
    :param alarm_name: Full alarm name.
    :return: The alarm as describe_alarms reports it, or None if there is no such alarm.
    """
    alarms = cloudwatch_client.describe_alarms(
        AlarmNames=[alarm_name], AlarmTypes=["MetricAlarm"]
    )["MetricAlarms"]
    return alarms[0] if alarms else None


def assert_error_rate_alarm(
    cloudwatch_client: BaseClient, function_name: str, period: int
) -> None:
    """
    Fail unless a Lambda's error-rate alarm evaluates 2 of 2 periods of ``period`` seconds.

    The alarm is ``<function>-errors-threshold`` from terraform-aws-lambda-monitored. It divides
    the Errors metric by the Invocations metric, so both metric queries must use the period.

    :param cloudwatch_client: Boto3 CloudWatch client.
    :param function_name: Lambda function name.
    :param period: Expected period of both metric queries, in seconds.
    :raises AssertionError: If the alarm is missing or has another period, number of
        evaluation periods or datapoints to alarm.
    """
    alarm_name = f"{function_name}-errors-threshold"
    alarm = _describe_metric_alarm(cloudwatch_client, alarm_name)
    assert alarm is not None, f"Alarm {alarm_name} doesn't exist."
    metric_periods = {
        query["Id"]: query["MetricStat"]["Period"]
        for query in alarm["Metrics"]
        if "MetricStat" in query
    }
    LOG.info(
        "%s: metric periods %s, %s of %s datapoints to alarm",
        alarm_name,
        metric_periods,
        alarm["DatapointsToAlarm"],
        alarm["EvaluationPeriods"],
    )
    assert metric_periods == {"errors": period, "invocations": period}, (
        f"{alarm_name}: expected period {period} on both metric queries, "
        f"got {metric_periods}"
    )
    assert (
        alarm["EvaluationPeriods"] == 2
    ), f"{alarm_name}: expected 2 evaluation periods, got {alarm['EvaluationPeriods']}"
    assert (
        alarm["DatapointsToAlarm"] == 2
    ), f"{alarm_name}: expected 2 datapoints to alarm, got {alarm['DatapointsToAlarm']}"


def assert_immediate_error_alarm(
    cloudwatch_client: BaseClient, function_name: str
) -> None:
    """
    Fail unless a Lambda alarms on any error, with no error-rate alarm.

    terraform-aws-lambda-monitored names the alarm ``<function>-errors-immediate`` for
    ``alert_strategy = "immediate"`` and ``<function>-errors-threshold`` for "threshold".

    :param cloudwatch_client: Boto3 CloudWatch client.
    :param function_name: Lambda function name.
    :raises AssertionError: If the immediate alarm is missing or the threshold alarm exists.
    """
    immediate_alarm_name = f"{function_name}-errors-immediate"
    threshold_alarm_name = f"{function_name}-errors-threshold"
    assert (
        _describe_metric_alarm(cloudwatch_client, immediate_alarm_name) is not None
    ), f"Alarm {immediate_alarm_name} doesn't exist."
    assert (
        _describe_metric_alarm(cloudwatch_client, threshold_alarm_name) is None
    ), f"Alarm {threshold_alarm_name} still exists."


def assert_lambdas_use_kms_key(
    lambda_client: BaseClient, function_names: List[str], key_arn: str
) -> None:
    """
    Fail unless each Lambda is configured to sign with the KMS key and is given no secret ARN.

    :param lambda_client: Boto3 Lambda client.
    :param function_names: The pool's Lambda function names.
    :param key_arn: The KMS key with the GitHub App's key.
    :raises AssertionError: If a Lambda's environment names another credential type, another key,
        or any Secrets Manager ARN.
    """
    for function_name in function_names:
        variables = lambda_client.get_function_configuration(
            FunctionName=function_name
        )["Environment"]["Variables"]
        LOG.info(
            "%s: GITHUB_SECRET_TYPE=%s, GITHUB_APP_KMS_KEY_ARN=%s",
            function_name,
            variables.get("GITHUB_SECRET_TYPE"),
            variables.get("GITHUB_APP_KMS_KEY_ARN"),
        )
        assert variables.get("GITHUB_SECRET_TYPE") == "kms", (function_name, variables)
        assert variables.get("GITHUB_APP_KMS_KEY_ARN") == key_arn, (
            function_name,
            variables,
        )
        assert "GITHUB_SECRET" not in variables, (function_name, variables)
        secret_arns = [v for v in variables.values() if ":secretsmanager:" in v]
        assert (
            not secret_arns
        ), f"{function_name} is given secret ARNs {secret_arns} in kms mode."


def _simulate(
    iam_client: BaseClient,
    role_arn: str,
    action: str,
    resource_arn: str,
    signing_algorithm: Optional[str] = None,
) -> str:
    """
    Return IAM's decision for one action of a role, by its identity policies.

    :param iam_client: Boto3 IAM client.
    :param role_arn: The role whose policies to evaluate.
    :param action: The action, e.g. ``kms:Sign``.
    :param resource_arn: The resource of the action.
    :param signing_algorithm: The ``kms:SigningAlgorithm`` of the request, if any.
    :return: ``allowed``, ``implicitDeny`` or ``explicitDeny``.
    """
    context = (
        [
            {
                "ContextKeyName": "kms:SigningAlgorithm",
                "ContextKeyValues": [signing_algorithm],
                "ContextKeyType": "string",
            }
        ]
        if signing_algorithm
        else []
    )
    return iam_client.simulate_principal_policy(
        PolicySourceArn=role_arn,
        ActionNames=[action],
        ResourceArns=[resource_arn],
        ContextEntries=context,
    )["EvaluationResults"][0]["EvalDecision"]


def assert_lambda_roles_sign_only(
    boto3_session,
    lambda_role_arns: Dict[str, str],
    key_arn: str,
    pem_secret_arn: str,
) -> None:
    """
    Fail unless each Lambda role may sign with the key, with RS256's algorithm only, and can't read the PEM.

    ``iam:SimulatePrincipalPolicy`` evaluates the roles' identity policies, the ones the module
    writes. The KMS key's own policy is the default one, which delegates to IAM.

    :param boto3_session: Boto3 session for the test account.
    :param lambda_role_arns: Lambda execution role ARN by function token.
    :param key_arn: The KMS key with the GitHub App's key.
    :param pem_secret_arn: The secret with the App's PEM key, which a kms pool must not read.
    :raises AssertionError: If a role may do less or more than that.
    """
    iam_client = boto3_session.client("iam")
    # Another key in the same account and region: the grant must be on exactly the pool's key.
    other_key_arn = re.sub(
        r"key/.*$", "key/00000000-0000-0000-0000-000000000000", key_arn
    )
    expected = [
        ("kms:Sign", key_arn, KMS_SIGNING_ALGORITHM, "allowed"),
        ("kms:Sign", key_arn, "RSASSA_PSS_SHA_256", "implicitDeny"),
        ("kms:Sign", key_arn, None, "implicitDeny"),
        ("kms:Sign", other_key_arn, KMS_SIGNING_ALGORITHM, "implicitDeny"),
        ("secretsmanager:GetSecretValue", pem_secret_arn, None, "implicitDeny"),
    ]
    for token, role_arn in lambda_role_arns.items():
        for action, resource_arn, algorithm, decision in expected:
            actual = _simulate(iam_client, role_arn, action, resource_arn, algorithm)
            LOG.info(
                "%s: %s on %s with %s: %s",
                role_arn,
                action,
                resource_arn,
                algorithm,
                actual,
            )
            assert actual == decision, (
                f"{token} role {role_arn}: {action} on {resource_arn} with signing algorithm "
                f"{algorithm} is {actual}, expected {decision}."
            )


def assert_record_metric_publishes(
    cloudwatch_client: BaseClient,
    asg_name: str,
    function_name: str,
    minutes: int = 10,
) -> None:
    """
    Fail unless record_metric published both runner metrics and ran without errors lately.

    The window is the last few minutes, long after the apply: a Lambda can fail in its first
    minute, before IAM has propagated its new role's policies.

    :param cloudwatch_client: Boto3 CloudWatch client.
    :param asg_name: The pool's ASG, the dimension of the runner metrics.
    :param function_name: The record_metric Lambda.
    :param minutes: How many minutes back to look.
    :raises AssertionError: If a metric has no datapoint, the Lambda didn't run, or it failed.
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=minutes)

    def _sum(namespace: str, metric_name: str, dimensions: List[Dict]) -> List[float]:
        return [
            datapoint["Sum"]
            for datapoint in cloudwatch_client.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=60,
                Statistics=["Sum"],
            )["Datapoints"]
        ]

    for metric_name in ("BusyRunners", "IdleRunners"):
        datapoints = _sum(
            "GitHubRunners", metric_name, [{"Name": "asg_name", "Value": asg_name}]
        )
        LOG.info(
            "%s: %d datapoints in %d minutes", metric_name, len(datapoints), minutes
        )
        assert (
            datapoints
        ), f"record_metric published no {metric_name} for {asg_name} in {minutes} minutes."

    function = [{"Name": "FunctionName", "Value": function_name}]
    invocations = sum(_sum("AWS/Lambda", "Invocations", function))
    errors = sum(_sum("AWS/Lambda", "Errors", function))
    LOG.info(
        "%s: %d invocations, %d errors in %d minutes",
        function_name,
        invocations,
        errors,
        minutes,
    )
    assert invocations > 0, f"{function_name} didn't run in {minutes} minutes."
    assert errors == 0, f"{function_name} failed {errors} times in {minutes} minutes."


def log_record_metric_token_times(
    boto3_session, aws_region: str, function_name: str, minutes: int = 30
) -> List[float]:
    """
    Log how long record_metric took to get its GitHub token, and how long it ran.

    record_metric logs ``Got a <type> GitHub token in <seconds> s.`` from inside its five-second
    ``timeout()``. The numbers go into the SEC-46 pull request.

    :param boto3_session: Boto3 session for the test account.
    :param aws_region: The Lambda's region.
    :param function_name: The record_metric Lambda.
    :param minutes: How many minutes back to look.
    :return: The token times, in seconds.
    """
    start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    logs_client = boto3_session.client("logs", region_name=aws_region)
    times = []
    for page in logs_client.get_paginator("filter_log_events").paginate(
        logGroupName=f"/aws/lambda/{function_name}",
        startTime=int(start_time.timestamp() * 1000),
        filterPattern='"GitHub token in"',
    ):
        for event in page["events"]:
            match = re.search(r"GitHub token in ([0-9.]+) s", event["message"])
            if match:
                times.append(float(match.group(1)))
    if times:
        LOG.info(
            "%s got a GitHub token %d times in %d minutes: min %.3f s, median %.3f s, max %.3f s",
            function_name,
            len(times),
            minutes,
            min(times),
            median(times),
            max(times),
        )

    cloudwatch_client = boto3_session.client("cloudwatch", region_name=aws_region)
    durations = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Duration",
        Dimensions=[{"Name": "FunctionName", "Value": function_name}],
        StartTime=start_time,
        EndTime=datetime.now(timezone.utc),
        Period=minutes * 60,
        Statistics=["Average", "Maximum"],
        Unit="Milliseconds",
    )["Datapoints"]
    for datapoint in durations:
        LOG.info(
            "%s duration in %d minutes: average %.0f ms, maximum %.0f ms",
            function_name,
            minutes,
            datapoint["Average"],
            datapoint["Maximum"],
        )
    return times


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
