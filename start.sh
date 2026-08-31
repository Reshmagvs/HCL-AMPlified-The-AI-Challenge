#!/usr/bin/env bash
set -e

# Railway deployment script for Lodestar backend
echo "Starting Lodestar backend deployment..."

# Install backend dependencies
echo "Installing backend dependencies..."
pip install --upgrade pip
pip install -r backend/requirements.txt

# Change to backend directory
cd backend

# Download embedding model
echo "Downloading embedding model..."
python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# Seed the database
echo "Seeding database..."
python -m scripts.seed

# Start the FastAPI server
echo "Starting FastAPI server on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
