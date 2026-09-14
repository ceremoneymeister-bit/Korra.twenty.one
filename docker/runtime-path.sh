#!/bin/sh
# /etc/profile resets Docker's ENV PATH when the native terminal captures a
# login-shell snapshot. Restore the same runtime/shim ordering as Dockerfile;
# user shell init files can still select their own environment afterwards.
case "$PATH" in
    /opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:*) ;;
    *) export PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:$PATH" ;;
esac
