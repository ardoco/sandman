#!/bin/bash
set -euo pipefail

function build() {
  echo "building"
  make all
}

check_it() {
  echo "checking"
  ./run-checks.sh
}

build
check_it
