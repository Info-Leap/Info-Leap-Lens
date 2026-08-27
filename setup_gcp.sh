#!/usr/bin/env bash
# Run once after: gcloud auth login
# Sets up GCP project, APIs, and service account for InfoLeap Pulse.

set -e

PROJECT_ID="infoleap-pulse"
PROJECT_NAME="InfoLeap Pulse"
SA_NAME="infoleap-app"
SA_DISPLAY="InfoLeap Pulse App"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
CREDS_PATH="oxdata/config/infoleap_service_account.json"

echo "=== 1. Create GCP project ==="
gcloud projects create "$PROJECT_ID" --name="$PROJECT_NAME" || echo "(already exists)"

echo "=== 2. Set active project ==="
gcloud config set project "$PROJECT_ID"

echo "=== 3. Link billing ==="
# List billing accounts — pick the one tied to info-leap.com
gcloud billing accounts list
echo ""
echo ">>> Paste your billing account ID (e.g. XXXXXX-XXXXXX-XXXXXX):"
read BILLING_ACCOUNT
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"

echo "=== 4. Enable APIs ==="
gcloud services enable \
  drive.googleapis.com \
  sheets.googleapis.com \
  firebase.googleapis.com \
  identitytoolkit.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com

echo "=== 5. Create service account ==="
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="$SA_DISPLAY" || echo "(already exists)"

echo "=== 6. Grant roles ==="
# Drive + Sheets access goes through OAuth scope on the service account,
# not IAM roles — handled when sharing the Shared Drive with this SA email.
# Firebase Admin access:
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/firebase.sdkAdminServiceAgent"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountTokenCreator"

echo "=== 7. Download service account key ==="
gcloud iam service-accounts keys create "$CREDS_PATH" \
  --iam-account="$SA_EMAIL"

echo ""
echo "=== DONE ==="
echo "Service account key saved to: $CREDS_PATH"
echo "Service account email: $SA_EMAIL"
echo ""
echo "NEXT STEPS (manual — browser required):"
echo "  1. Go to https://console.firebase.google.com"
echo "     → Add project → select '$PROJECT_ID' (existing GCP project)"
echo "     → Enable Google Analytics: optional"
echo "  2. Firebase Console → Authentication → Sign-in methods"
echo "     → Enable: Google provider"
echo "     → Add authorised domain: your Streamlit URL"
echo "  3. Firebase Console → Project Settings → General"
echo "     → Copy 'Web API Key' → add to .env as FIREBASE_WEB_API_KEY"
echo "  4. Google Drive: create Shared Drive 'InfoLeap Pulse Projects'"
echo "     → Share it with: $SA_EMAIL (Editor role)"
echo "     → Copy Drive root folder ID → add to .env as GDRIVE_ROOT_FOLDER_ID"
echo "  5. Add to .env:"
echo "     FIREBASE_PROJECT_ID=$PROJECT_ID"
echo "     FIREBASE_WEB_API_KEY=<from step 3>"
echo "     GOOGLE_APPLICATION_CREDENTIALS=$CREDS_PATH"
echo "     GDRIVE_ROOT_FOLDER_ID=<from step 4>"
echo "     INFOLEAP_AUTH_ENABLED=1"
