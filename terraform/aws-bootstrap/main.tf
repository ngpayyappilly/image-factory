data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  ssm_arn    = "arn:${local.partition}:ssm:*:${local.account_id}:parameter${var.ssm_prefix}"

  github_enabled      = var.github_repository != ""
  github_oidc_arn     = local.github_enabled ? (var.github_oidc_provider_arn != "" ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn) : ""
  github_oidc_host    = "token.actions.githubusercontent.com"
  github_environments = ["imagefactory-canary", "imagefactory-stable", "imagefactory-maintenance"]
}

# --- GitHub Actions OIDC: short-lived credentials, nothing stored in GitHub secrets ---------
resource "aws_iam_openid_connect_provider" "github" {
  count          = local.github_enabled && var.github_oidc_provider_arn == "" ? 1 : 0
  url            = "https://${local.github_oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

# --- artifact bucket: manifests (permanent) and build evidence (expires) --------------------
resource "aws_kms_key" "store" {
  description             = "${var.name_prefix} artifact bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "store" {
  name          = "alias/${var.name_prefix}-store"
  target_key_id = aws_kms_key.store.key_id
}

resource "aws_s3_bucket" "store" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "store" {
  bucket = aws_s3_bucket.store.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "store" {
  bucket = aws_s3_bucket.store.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.store.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "store" {
  bucket                  = aws_s3_bucket.store.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "store" {
  bucket = aws_s3_bucket.store.id
  rule {
    id     = "expire-raw-build-evidence"
    status = "Enabled"
    filter {
      prefix = "evidence/"
    }
    expiration {
      days = var.evidence_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# --- manifest signing key (cosign: awskms:///alias/<name>-signing) --------------------------
resource "aws_kms_key" "signing" {
  description              = "${var.name_prefix} manifest signing"
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = 30
}

resource "aws_kms_alias" "signing" {
  name          = "alias/${var.name_prefix}-signing"
  target_key_id = aws_kms_key.signing.key_id
}

# --- EBS key for AMIs. A customer-managed key is required to share encrypted AMIs ----------
data "aws_iam_policy_document" "ebs_key" {
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  dynamic "statement" {
    for_each = length(var.share_with_account_ids) > 0 ? [1] : []
    content {
      sid       = "ConsumerAccountsMayLaunch"
      actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:CreateGrant"]
      resources = ["*"]
      principals {
        type        = "AWS"
        identifiers = [for a in var.share_with_account_ids : "arn:${local.partition}:iam::${a}:root"]
      }
    }
  }
}

resource "aws_kms_key" "ebs" {
  description             = "${var.name_prefix} AMI snapshots"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.ebs_key.json
}

resource "aws_kms_alias" "ebs" {
  name          = "alias/${var.name_prefix}-ebs"
  target_key_id = aws_kms_key.ebs.key_id
}

# --- builder role: runs Packer, writes manifests, signs, can only publish to the dev channel -
data "aws_iam_policy_document" "assume_builder" {
  dynamic "statement" {
    for_each = length(var.builder_trusted_principal_arns) > 0 ? [1] : []
    content {
      actions = ["sts:AssumeRole"]
      principals {
        type        = "AWS"
        identifiers = var.builder_trusted_principal_arns
      }
    }
  }

  # Workflow runs on the protected branch only: a pull request or feature branch cannot build or sign.
  dynamic "statement" {
    for_each = local.github_enabled ? [1] : []
    content {
      actions = ["sts:AssumeRoleWithWebIdentity"]
      principals {
        type        = "Federated"
        identifiers = [local.github_oidc_arn]
      }
      condition {
        test     = "StringEquals"
        variable = "${local.github_oidc_host}:aud"
        values   = ["sts.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "${local.github_oidc_host}:sub"
        values   = ["repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"]
      }
    }
  }
}

data "aws_iam_policy_document" "builder" {
  # Packer's amazon-ebs permission set. Most EC2 actions cannot be resource-scoped at create time.
  statement {
    sid = "PackerBuild"
    actions = [
      "ec2:AttachVolume", "ec2:AuthorizeSecurityGroupIngress", "ec2:CopyImage", "ec2:CreateImage",
      "ec2:CreateKeyPair", "ec2:CreateSecurityGroup", "ec2:CreateSnapshot", "ec2:CreateTags",
      "ec2:CreateVolume", "ec2:DeleteKeyPair", "ec2:DeleteSecurityGroup", "ec2:DeleteSnapshot",
      "ec2:DeleteVolume", "ec2:DeregisterImage", "ec2:DescribeImageAttribute", "ec2:DescribeImages",
      "ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "ec2:DescribeRegions",
      "ec2:DescribeSecurityGroups", "ec2:DescribeSnapshots", "ec2:DescribeSubnets", "ec2:DescribeTags",
      "ec2:DescribeVolumes", "ec2:DescribeVpcs", "ec2:DetachVolume", "ec2:GetPasswordData",
      "ec2:ModifyImageAttribute", "ec2:ModifyInstanceAttribute", "ec2:ModifySnapshotAttribute",
      "ec2:RegisterImage", "ec2:RunInstances",
    ]
    resources = ["*"]
  }

  # Only ever stop/terminate instances this pipeline started.
  statement {
    sid       = "OnlyOwnBuildInstances"
    actions   = ["ec2:StopInstances", "ec2:TerminateInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/imagefactory:managed"
      values   = ["true"]
    }
  }

  statement {
    sid       = "EbsKeyForEncryptedAmis"
    actions   = ["kms:CreateGrant", "kms:DescribeKey", "kms:Decrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*"]
    resources = [aws_kms_key.ebs.arn]
  }

  statement {
    sid       = "ManifestsAndEvidence"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.store.arn, "${aws_s3_bucket.store.arn}/*"]
  }
  statement {
    sid       = "StoreKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.store.arn]
  }

  statement {
    sid       = "SignManifests"
    actions   = ["kms:Sign", "kms:Verify", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = [aws_kms_key.signing.arn]
  }

  # Boot-and-join smoke test (imagefactory release): reads SSM registration, hands the test instance its profile.
  statement {
    sid       = "SmokeTestJoinCheck"
    actions   = ["ssm:DescribeInstanceInformation"]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = length(var.smoke_instance_role_arns) > 0 ? [1] : []
    content {
      sid       = "SmokeTestInstanceProfile"
      actions   = ["iam:PassRole"]
      resources = var.smoke_instance_role_arns
      condition {
        test     = "StringEquals"
        variable = "iam:PassedToService"
        values   = ["ec2.amazonaws.com"]
      }
    }
  }

  # The builder can publish to dev only. canary and stable need the promoter role (approval-gated).
  statement {
    sid       = "DevChannelOnly"
    actions   = ["ssm:PutParameter", "ssm:AddTagsToResource"]
    resources = ["${local.ssm_arn}/*/dev/*"]
  }
}

resource "aws_iam_role" "builder" {
  name               = "${var.name_prefix}-builder"
  assume_role_policy = data.aws_iam_policy_document.assume_builder.json
  # Packer builds (Windows especially) can outlast the 1h default; OIDC sessions cannot be refreshed.
  max_session_duration = 14400

  lifecycle {
    precondition {
      condition     = length(var.builder_trusted_principal_arns) > 0 || var.github_repository != ""
      error_message = "Set builder_trusted_principal_arns and/or github_repository, otherwise nothing can assume this role."
    }
  }
}

resource "aws_iam_role_policy" "builder" {
  role   = aws_iam_role.builder.id
  name   = "builder"
  policy = data.aws_iam_policy_document.builder.json
}

# --- promoter role: repoints channels (promote, rollback) and runs retention -----------------
data "aws_iam_policy_document" "assume_promoter" {
  dynamic "statement" {
    for_each = length(var.promoter_trusted_principal_arns) > 0 ? [1] : []
    content {
      actions = ["sts:AssumeRole"]
      principals {
        type        = "AWS"
        identifiers = var.promoter_trusted_principal_arns
      }
    }
  }

  # Only jobs running inside a GitHub *environment* can promote. Required reviewers on the canary and
  # stable environments are therefore enforced by AWS: no approval, no token.
  dynamic "statement" {
    for_each = local.github_enabled ? [1] : []
    content {
      actions = ["sts:AssumeRoleWithWebIdentity"]
      principals {
        type        = "Federated"
        identifiers = [local.github_oidc_arn]
      }
      condition {
        test     = "StringEquals"
        variable = "${local.github_oidc_host}:aud"
        values   = ["sts.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "${local.github_oidc_host}:sub"
        values   = [for e in local.github_environments : "repo:${var.github_repository}:environment:${e}"]
      }
    }
  }
}

data "aws_iam_policy_document" "promoter" {
  statement {
    sid       = "ManifestsAndChannelState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.store.arn, "${aws_s3_bucket.store.arn}/*"]
  }
  statement {
    sid       = "StoreKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.store.arn]
  }
  statement {
    sid       = "VerifySignatures"
    actions   = ["kms:Verify", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = [aws_kms_key.signing.arn]
  }
  statement {
    sid       = "ChannelPointers"
    actions   = ["ssm:PutParameter", "ssm:GetParameter", "ssm:AddTagsToResource"]
    resources = ["${local.ssm_arn}/*"]
  }
  statement {
    sid       = "CheckReplication"
    actions   = ["ec2:DescribeImages"]
    resources = ["*"]
  }
  statement {
    sid       = "Retention"
    actions   = ["ec2:DeregisterImage", "ec2:DeleteSnapshot", "ec2:DescribeSnapshots"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/imagefactory:managed"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role" "promoter" {
  name               = "${var.name_prefix}-promoter"
  assume_role_policy = data.aws_iam_policy_document.assume_promoter.json

  lifecycle {
    precondition {
      condition     = length(var.promoter_trusted_principal_arns) > 0 || var.github_repository != ""
      error_message = "Set promoter_trusted_principal_arns and/or github_repository, otherwise nothing can assume this role."
    }
  }
}

resource "aws_iam_role_policy" "promoter" {
  role   = aws_iam_role.promoter.id
  name   = "promoter"
  policy = data.aws_iam_policy_document.promoter.json
}

# --- consumers: attach to any role that deploys VMs from golden images (stable only) --------
data "aws_iam_policy_document" "consumer" {
  statement {
    sid       = "ResolveStable"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["${local.ssm_arn}/*/stable/*"]
  }
  statement {
    sid       = "DescribeImages"
    actions   = ["ec2:DescribeImages"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "consumer" {
  name        = "${var.name_prefix}-consumer"
  description = "Resolve the stable golden image"
  policy      = data.aws_iam_policy_document.consumer.json
}
