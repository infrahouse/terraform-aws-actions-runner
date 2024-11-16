import json
from os import path as osp
from textwrap import dedent

import pytest
from infrahouse_toolkit.terraform import terraform_apply

from tests.conftest import (
    LOG,
    TRACE_TERRAFORM,
    TEST_ZONE,
    REGION,
    TERRAFORM_ROOT_DIR,
    GITHUB_ORG_NAME,
    ensure_runners,
    get_tmp_token,
    GH_APP_ID,
)


@pytest.mark.parametrize("secret_type", ["token", "pem"])
def test_module(
    service_network,
    secretsmanager_client,
    test_role_arn,
    keep_after,
    github_token,
    github_app_pem_secret_arn,
    secret_type,
):
    subnet_public_ids = service_network["subnet_public_ids"]["value"]
    subnet_private_ids = service_network["subnet_private_ids"]["value"]

    if secret_type == "token":
        assert github_token, "Set GitHub token value with --github-token CLI argument."
    elif secret_type == "pem":
        assert (
            github_app_pem_secret_arn
        ), "Set a secret ARN containing GitHub APP PEM key token value with --github-app-pem-secret CLI argument."

    terraform_module_dir = osp.join(TERRAFORM_ROOT_DIR, "actions-runner")
    with open(osp.join(terraform_module_dir, "terraform.tfvars"), "w") as fp:
        fp.write(
            dedent(
                f"""
                    region          = "{REGION}"
                    role_arn        = "{test_role_arn}"
                    test_zone       = "{TEST_ZONE}"
                    github_org_name = "{GITHUB_ORG_NAME}"

                    subnet_public_ids  = {json.dumps(subnet_public_ids)}
                    subnet_private_ids = {json.dumps(subnet_private_ids)}
                    """
            )
        )
        fp.write(
            f'github_token = "{github_token}"'
            if secret_type == "token"
            else dedent(
                f"""
                github_app_pem_secret_arn = "{github_app_pem_secret_arn}"
                github_app_id = {GH_APP_ID}
                """
            )
        )

    with terraform_apply(
        terraform_module_dir,
        destroy_after=not keep_after,
        json_output=True,
        enable_trace=TRACE_TERRAFORM,
    ) as tf_output:
        LOG.info("%s", json.dumps(tf_output, indent=4))
        ensure_runners(
            github_token
            if secret_type == "token"
            else get_tmp_token(secretsmanager_client, github_app_pem_secret_arn)
        )
