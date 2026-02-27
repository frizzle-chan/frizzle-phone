#!/usr/bin/env bash
# Download RFC text files needed for SIP/RTP/SDP development.
# Usage: bash rfcs/download.sh
set -euo pipefail

cd "$(dirname "$0")"

rfcs=(3261 2617 4566 3264 3550 3551 3665)

for rfc in "${rfcs[@]}"; do
    file="rfc${rfc}.txt"
    if [[ -f "$file" ]]; then
        echo "skip  $file (exists)"
    else
        echo "fetch $file"
        curl -fsSL "https://www.rfc-editor.org/rfc/rfc${rfc}.txt" -o "$file"
    fi
done

echo "done"
