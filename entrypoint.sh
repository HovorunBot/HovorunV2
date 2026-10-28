#!/bin/sh
set -e

# Schema upgrades run in the dedicated migration container before application startup.
exec "$@"
