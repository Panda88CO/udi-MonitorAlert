#!/bin/sh
echo "Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt --user
echo "Installation Finished successfully."
