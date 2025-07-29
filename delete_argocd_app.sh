#!/bin/bash

set -euo pipefail

ARGOCD_SERVER="$1"
APP_NAME="$2"

if [[ -z "$ARGOCD_SERVER" || -z "$APP_NAME" ]]; then
  echo "[ERROR] Missing argument(s)"
  exit 1
fi

#Logging into ArgoCD using SSO - Browser Based, no credentials needed
echo "Logging into ArgoCD..."
argocd login "$ARGOCD_SERVER" \
  --sso \
  --grpc-web \
  --insecure || {
    echo "[ERROR] Failed to log in to ArgoCD"
    exit 1
}

echo "Checking if ArgoCD app '$APP_NAME' exists..."
if ! argocd app get "$APP_NAME" --grpc-web &>/dev/null; then
  echo "App '$APP_NAME' does not exist or already deleted. Skipping deletion."
  exit 0
fi

# Adding delay before deletion, to make sure finalizer is removed
echo "Waiting 5 seconds before deleting the app..."
sleep 5

echo "Deleting ArgoCD app: $APP_NAME"
if argocd app delete "$APP_NAME" --grpc-web --yes; then
  echo "[OK] App '$APP_NAME' deleted successfully"
else
  echo "[ERROR] Failed to delete app '$APP_NAME'"
  exit 1
fi


# Attempting to re-delete the Argo App, as it's sometimes left in a missing state
echo "Waiting 5 seconds before attempting to delete the app again..."
sleep 5

echo "Checking again if ArgoCD app '$APP_NAME' exists..."
if ! argocd app get "$APP_NAME" --grpc-web &>/dev/null; then
  echo "App '$APP_NAME' does not exist. It's already deleted on first trial."
  exit 0
fi

echo "Deleting ArgoCD app: $APP_NAME"
if argocd app delete "$APP_NAME" --grpc-web --yes; then
  echo "[OK] App '$APP_NAME' deleted successfully"
else
  echo "[ERROR] Failed to delete app '$APP_NAME'"
  exit 1
fi