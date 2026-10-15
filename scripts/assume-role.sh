#!/usr/bin/env bash
# Prints shell exports for short-lived credentials of the given role. Use as:
#   eval "$(scripts/assume-role.sh "$ROLE_ARN" session-name)"
# Credentials go straight into the shell environment, never into pipeline logs or variables.
set -euo pipefail
role=${1:?role arn}; session=${2:-imagefactory}
aws sts assume-role --role-arn "$role" --role-session-name "$session" --duration-seconds 3600 \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text |
  awk '{printf "export AWS_ACCESS_KEY_ID=%s AWS_SECRET_ACCESS_KEY=%s AWS_SESSION_TOKEN=%s\n", $1, $2, $3}'
