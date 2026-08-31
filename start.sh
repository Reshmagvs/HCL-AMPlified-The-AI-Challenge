#!/usr/bin/env bash
set -e

# Railway deployment script for Lodestar backend
echo "Starting Lodestar backend deployment..."

# Create virtual environment if it doesn't exist
if [ ! -d "backend/.venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv backend/.venv
fi

# Activate virtual environment
source backend/.venv/bin/activate

# Install backend dependencies
echo "Installing backend dependencies..."
pip install --upgrade pip
pip install -r backend/requirements.txt

# Change to backend directory
cd backend

# Seed the database
echo "Seeding database..."
python -m scripts.seed

# Start the FastAPI server
echo "Starting FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
