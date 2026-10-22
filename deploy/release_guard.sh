#!/usr/bin/env bash
# Sourced by deploy.yml after checkout. All state lives outside the repository.
# Database changes are additive; recovery deliberately retains database/outbox.

release_guard_prepare() {
  RECOVERY_DIR="$(mktemp -d "${RELEASE_RECOVERY_ROOT:-/var/lib/boya-agent}/release-recovery.XXXXXX")"
  chmod 700 "$RECOVERY_DIR"
  VENV_SNAPSHOT_READY=0
  DEPENDENCIES_TOUCHED=0
  RELEASE_MUTATED=0
  if [ "$DEPS_CHANGED" = "true" ] && [ -d "$APP_DIR/venv" ]; then
    cp -a "$APP_DIR/venv" "$RECOVERY_DIR/venv"
    VENV_SNAPSHOT_READY=1
  fi
}

release_guard_recover() {
  local recovery_failed=0
  echo "Release failed; restoring commit $PREVIOUS_SHA (database and uploads retained)."
  if [ "${RELEASE_MUTATED:-0}" = "1" ]; then
    sudo systemctl stop "$SERVICE_NAME" || recovery_failed=1
  fi
  git -C "$APP_DIR" switch --detach "$PREVIOUS_SHA" || recovery_failed=1
  if [ "${DEPENDENCIES_TOUCHED:-0}" = "1" ]; then
    if [ "${VENV_SNAPSHOT_READY:-0}" = "1" ]; then
      if [ -d "$APP_DIR/venv" ]; then
        mv "$APP_DIR/venv" "$RECOVERY_DIR/failed-venv" || recovery_failed=1
      fi
      mv "$RECOVERY_DIR/venv" "$APP_DIR/venv" || recovery_failed=1
    else
      echo "No previous venv snapshot; manual environment recovery required."
      recovery_failed=1
    fi
  fi
  if [ "$recovery_failed" = "0" ] && [ "${SERVICE_WAS_ACTIVE:-0}" = "1" ]; then
    sudo systemctl start "$SERVICE_NAME" || recovery_failed=1
    sudo systemctl is-active --quiet "$SERVICE_NAME" || recovery_failed=1
    local healthy=0
    for attempt in $(seq 1 12); do
      if curl --fail --silent --show-error --max-time 5 "$LOCAL_HEALTHCHECK_URL" >/dev/null; then
        healthy=1
        break
      fi
      sleep 2
    done
    if [ "$healthy" != "1" ]; then recovery_failed=1; fi
  fi
  echo "Recovery artifacts retained at: ${RECOVERY_DIR:-not-created}"
  return "$recovery_failed"
}

release_guard_exit() {
  local status=$?
  trap - EXIT
  set +e
  local mail_status=0
  if declare -F restore_email_state >/dev/null; then
    restore_email_state || mail_status=$?
  fi
  if [ "$status" != "0" ]; then
    release_guard_recover || echo "RECOVERY FAILED: inspect service, environment and previous commit before retrying."
  fi
  if declare -F cleanup_wheelhouse >/dev/null; then cleanup_wheelhouse; fi
  if [ "$mail_status" != "0" ]; then
    echo "Email state restore failed; manual configuration verification required."
    status=1
  fi
  exit "$status"
}
