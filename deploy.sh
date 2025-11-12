#!/bin/bash

# loveUAD Cloud Run Deployment Script

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}loveUAD Cloud Run Deployment${NC}"
echo "=================================="

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please copy .env.example to .env and configure it"
    exit 1
fi

# Load environment variables
source .env

# Check required variables
if [ -z "$GCP_PROJECT_ID" ]; then
    echo -e "${RED}Error: GCP_PROJECT_ID not set in .env${NC}"
    exit 1
fi

echo -e "${YELLOW}Project ID:${NC} $GCP_PROJECT_ID"
echo -e "${YELLOW}Region:${NC} ${GCP_LOCATION:-europe-west2}"

# Confirm deployment
read -p "Deploy to Cloud Run? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 0
fi

echo -e "${GREEN}Starting deployment...${NC}"

# Set project
gcloud config set project $GCP_PROJECT_ID

# Deploy to Cloud Run
gcloud run deploy loveuad-api \
    --source . \
    --region ${GCP_LOCATION:-europe-west2} \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars GCP_PROJECT_ID=$GCP_PROJECT_ID \
    --set-env-vars GCP_LOCATION=${GCP_LOCATION:-europe-west2} \
    --set-env-vars ENCRYPTION_KEY=$ENCRYPTION_KEY \
    --set-env-vars DB_NAME=${DB_NAME:-loveuad} \
    --set-env-vars DB_USER=${DB_USER:-postgres} \
    --add-cloudsql-instances $INSTANCE_CONNECTION_NAME \
    --set-secrets "DB_PASSWORD=db-password:latest" \
    --memory 2Gi \
    --cpu 2 \
    --timeout 300 \
    --max-instances 10

echo -e "${GREEN}Deployment complete!${NC}"

# Get service URL
SERVICE_URL=$(gcloud run services describe loveuad-api \
    --region ${GCP_LOCATION:-europe-west2} \
    --format='value(status.url)')

echo ""
echo -e "${GREEN}Service URL:${NC} $SERVICE_URL"
echo -e "${GREEN}Health Check:${NC} $SERVICE_URL/api/health"
echo ""
echo "Test the API:"
echo "curl $SERVICE_URL/api/health"
