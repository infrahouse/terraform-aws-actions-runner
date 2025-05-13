import logging
from collections import Counter
from os import environ

from infrahouse_core.timeout import timeout

from infrahouse_core.aws.asg import ASG

from infrahouse_core.github import get_tmp_token, GitHubAuth, GitHubActions

from infrahouse_core.aws import get_secret

import boto3

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)


def lambda_handler(event, context):
    """
    AWS Lambda function to record metrics for GitHub runners in an Auto Scaling Group (ASG).

    This function retrieves the status of GitHub runners associated with the instances in the ASG,
    counts the number of idle and busy runners, and sends these metrics to AWS CloudWatch.

    :param event: The event data passed to the Lambda function.
    :type event: dict
    :param context: The context object providing runtime information about the Lambda function.
    :type context: LambdaContext
    :return: None
    """
    LOG.info(f"{event = }")
    asg_name = environ["ASG_NAME"]
    asg = ASG(asg_name=asg_name)
    github = GitHubAuth(
        _get_github_token(environ["GITHUB_ORG_NAME"]), environ["GITHUB_ORG_NAME"]
    )
    gha = GitHubActions(github)

    status_counts = Counter()
    for instance in asg.instances:
        runner = gha.find_runner_by_label(f"instance_id:{instance.instance_id}")
        if runner and runner.status == "online":
            status_counts["busy" if runner.busy else "idle"] += 1

    LOG.info(f"{status_counts['idle'] = }, {status_counts['busy'] = }")

    # Send the custom metric
    cloudwatch = boto3.client("cloudwatch")
    cloudwatch.put_metric_data(
        Namespace="GitHubRunners",
        MetricData=[
            {
                "MetricName": "BusyRunners",
                "Dimensions": [
                    {"Name": "asg_name", "Value": asg_name},
                ],
                "Value": status_counts["busy"],
                "Unit": "Count",
            },
            {
                "MetricName": "IdleRunners",
                "Dimensions": [
                    {"Name": "asg_name", "Value": asg_name},
                ],
                "Value": status_counts["idle"],
                "Unit": "Count",
            },
        ],
    )


def _get_github_token(org):
    with timeout(5):
        return (
            get_secret(boto3.client("secretsmanager"), environ["GITHUB_SECRET"])
            if environ["GITHUB_SECRET_TYPE"] == "token"
            else get_tmp_token(int(environ["GH_APP_ID"]), environ["GITHUB_SECRET"], org)
        )
