#!/usr/bin/env python3
"""
Generate architecture diagram for terraform-aws-actions-runner module.

This diagram is generated from analysis of the actual Terraform code.

Requirements:
    pip install diagrams

Usage:
    python architecture.py
    
Output:
    architecture.png (in current directory)
"""
from textwrap import dedent

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EC2, AutoScaling, Lambda
from diagrams.aws.management import Cloudwatch, SystemsManager
from diagrams.aws.security import SecretsManager, IAM
from diagrams.aws.network import VPC
from diagrams.onprem.vcs import Github

fontsize = "16"

# Match MkDocs Material theme fonts (Roboto)
# Increase sizes for better readability
graph_attr = {
    "splines": "spline",
    "nodesep": "1.5",
    "ranksep": "1.5",
    "fontsize": fontsize,
    "fontname": "Roboto",
    "dpi": "200",           # Higher resolution
}

node_attr = {
    "fontname": "Roboto",
    "fontsize": fontsize,
}

edge_attr = {
    "fontname": "Roboto",
    "fontsize": fontsize,
}

with Diagram(
    "GitHub Actions Runner - AWS Architecture",
    filename="architecture",
    show=False,
    direction="TB",
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
    outformat="png",
):
    # External - GitHub
    github = Github(
        "\nGitHub\nOrganization"
    )

    with Cluster("AWS Account"):
        
        # Secrets and credentials
        with Cluster("Secrets Manager"):
            secrets = SecretsManager("\nGitHub Token\nor App PEM")
            reg_token = SecretsManager("\nRegistration\nTokens")
        
        with Cluster("VPC"):
            # sg = VPC("Security Group\n(SSH, ICMP, Egress)")
            
            with Cluster("Auto Scaling Group"):
                # Launch Template
                # lt = EC2("Launch Template\n(cloud-init, Puppet)")
                
                # Active runners
                with Cluster("Active Instances"):
                    runners = [
                        EC2("\nRunner"),
                        EC2("\nRunner"),
                        EC2("\nRunner"),
                    ]
                
                # Warm Pool
                with Cluster("Warm Pool (Hibernated)"):
                    warm = [
                        EC2("\nStandby"),
                        EC2("\nStandby"),
                    ]
            
                # ASG configuration
                asg_hooks = AutoScaling(
                    dedent(
                        """
                        ASG
                        Lifecycle Hooks
                        """
                    )
                )
        
        # Lambda functions
        with Cluster("Lambda Functions"):
            lambda_reg = Lambda("\nRegistration")
            lambda_dereg = Lambda("\nDeregistration")
            lambda_metric = Lambda("\nRecord Metric")
        
        # CloudWatch
        with Cluster("CloudWatch"):
            metric = Cloudwatch(
                dedent(
                    """
                    IdleRunners
                    Metric
                    """
                )
            )
            with Cluster("IdleRunners Alarms"):
                alarm_low = Cloudwatch(
                    dedent(
                        """
                        TooLow
                        """
                    )
                )
                alarm_high = Cloudwatch(
                    dedent(
                        """
                        TooHigh
                        """
                    )
                )

        # Scaling policies
        with Cluster("Autoscaling Policies"):
            scale_out = AutoScaling(
                "\nScale Out"
            )
            scale_in = AutoScaling(
                "\nScale In"
            )
        
        # SSM
        ssm = SystemsManager("\nSSM\nsystemctl start")
        
        # IAM
        # iam = IAM("Instance Profile\n+ Lambda Roles")

    # ============ CONNECTIONS ============
    
    # GitHub <-> Runners (bidirectional job flow)
    # github >> Edge(label="Jobs") >> runners[0]
    # runners[0] >> Edge(label="Results", style="dashed") >> github
    
    # Lifecycle hooks trigger lambdas
    asg_hooks >> Edge(color="green") >> lambda_reg
    asg_hooks >> Edge(color="red") >> lambda_dereg
    
    # Registration lambda flow
    secrets >> Edge(label="Get token") >> lambda_reg
    lambda_reg >> Edge(label="Store token") >> reg_token
    lambda_reg >> Edge(label="Configure runner") >> ssm
    
    # Runners get registration token
    reg_token >> Edge(style="dashed") >> runners[1]
    
    # Deregistration lambda
    secrets >> Edge(label="Get credentials") >> lambda_dereg
    lambda_dereg >> Edge(label="Deregister", style="dashed") >> github
    
    # Metric recording flow
    lambda_metric << Edge(label="Query runners") << github
    lambda_metric >> Edge(label="PutMetricData") >> metric
    
    # CloudWatch alarms and scaling
    metric >> alarm_low
    metric >> alarm_high
    alarm_low >> Edge(label="+N instances", color="green") >> scale_out
    alarm_high >> Edge(label="-N instances", color="red") >> scale_in

    scale_out >> runners[0]
    scale_in >> runners[2]
    
    # Warm pool to active
    warm[0] >> Edge(label="Wake in\nseconds", style="dotted", color="orange") >> runners[0]

    runners[1] >> Edge(label="Register", style="dashed") >> github

    # Security group applies to runners and lambdas
    # sg >> Edge(style="dotted") >> runners[0]
    # sg >> Edge(style="dotted") >> lambda_reg
