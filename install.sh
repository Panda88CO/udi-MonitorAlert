#!/bin/sh
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt --user
echo "Installation Finished successfully."
