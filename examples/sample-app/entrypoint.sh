#!/bin/sh
set -eu

child_pid=""

on_term() {
  if [ -n "$child_pid" ]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}

trap on_term INT TERM

if [ "$#" -eq 0 ]; then
  while :; do
    sleep 3600 &
    child_pid="$!"
    wait "$child_pid"
    child_pid=""
  done
else
  "$@" &
  child_pid="$!"
  wait "$child_pid"
fi