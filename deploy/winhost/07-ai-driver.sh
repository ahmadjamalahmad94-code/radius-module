#!/bin/bash
# NVIDIA driver inside the GPU guest. The headless/server flavour: this
# machine never draws a pixel, it only computes.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -q
sudo apt-get install -y -q ubuntu-drivers-common build-essential python3-venv \
  python3-pip git curl jq >/dev/null
sudo ubuntu-drivers install --gpgpu
echo "driver installed - reboot required"
