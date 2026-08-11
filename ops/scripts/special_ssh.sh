#!/usr/bin/env bash
# Shared named-operator SSH convention for canonical SPECIAL BOT/NL tooling.
# Source this file after set -u; no production action is performed here.
: "${SPECIAL_SSH_USER:=specialops}"
: "${SPECIAL_BOT_SSH_USER:=$SPECIAL_SSH_USER}"
: "${SPECIAL_NL_SSH_USER:=$SPECIAL_SSH_USER}"
: "${SPECIAL_SSH_TMP_DIR:=/tmp}"

special_ssh_target() {
  local user=$1 host=$2
  [[ $user =~ ^[a-z_][a-z0-9_-]*$ ]] && [[ $host =~ ^([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])$ ]] || {
    echo 'BLOCK: invalid SPECIAL SSH operator or host' >&2
    return 2
  }
  printf '%s@%s' "$user" "$host"
}

special_ssh_require_tmp_dir() {
  [[ $SPECIAL_SSH_TMP_DIR =~ ^/tmp(/[A-Za-z0-9._-]+)*$ ]] && [[ $SPECIAL_SSH_TMP_DIR != *'/../'* ]] && \
    [[ $SPECIAL_SSH_TMP_DIR != */.. ]] && [[ $SPECIAL_SSH_TMP_DIR != */. ]] || {
    echo 'BLOCK: SPECIAL_SSH_TMP_DIR must be a shell-safe path confined under /tmp' >&2
    return 2
  }
}

special_ssh_require_abs_path() {
  local path=$1 label=${2:-SPECIAL path}
  [[ $path =~ ^/[A-Za-z0-9._/-]+$ ]] && [[ $path != *'/../'* ]] && [[ $path != */.. ]] || {
    echo "BLOCK: invalid $label" >&2
    return 2
  }
}

special_ssh_require_relative_path() {
  local path=$1 label=${2:-SPECIAL relative path}
  [[ $path =~ ^[A-Za-z0-9._/-]+$ ]] && [[ $path != /* ]] && [[ $path != ../* ]] && \
    [[ $path != *'/../'* ]] && [[ $path != */.. ]] || {
    echo "BLOCK: invalid $label" >&2
    return 2
  }
}

special_require_commit() {
  local value=$1 label=${2:-SPECIAL commit}
  [[ $value =~ ^[0-9a-fA-F]{7,40}$ ]] || {
    echo "BLOCK: invalid $label" >&2
    return 2
  }
}

special_shell_quote() {
  printf '%q' "$1"
}
