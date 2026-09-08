#!/bin/bash
# TimeWeave — double-click this file to start the web interface.
cd "$(dirname "$0")"
echo "TimeWeave — starting the web interface"
echo

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

if ! $PY -c "import flask" >/dev/null 2>&1; then
  echo "Installing Flask (one time)..."
  $PY -m pip install --quiet flask || {
    echo "Could not install Flask. Try:  $PY -m pip install flask"
    read -r -p "Press return to close."; exit 1; }
fi

echo "Opening http://127.0.0.1:5000 in your browser."
( sleep 2; open http://127.0.0.1:5000 ) &
echo "Press Ctrl+C in this window to stop the server."
echo
$PY app.py
