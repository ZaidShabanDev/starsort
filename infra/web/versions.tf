terraform {
  required_version = ">= 1.11" # S3 native state locking (use_lockfile)

  # Bucket comes from `terraform init -backend-config="bucket=<tfstate_bucket output of infra/>"`, so the account ID stays out of the repo.
  backend "s3" {
    key          = "web/terraform.tfstate"
    region       = "us-west-2"
    use_lockfile = true
    encrypt      = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "starsort"
      Component = "web"
      ManagedBy = "terraform"
    }
  }
}
