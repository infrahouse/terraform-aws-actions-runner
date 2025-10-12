terraform {
  required_version = "~> 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.31, < 7.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
