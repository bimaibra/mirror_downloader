# Google Drive API Setup Guide

Follow these steps to enable Google Drive API and get your credentials.

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click on the project dropdown at the top
3. Click "New Project"
4. Enter a project name (e.g., "Mirror Download Server")
5. Click "Create"

## Step 2: Enable Google Drive API

1. In your project, go to "APIs & Services" > "Library"
2. Search for "Google Drive API"
3. Click on "Google Drive API"
4. Click "Enable"

## Step 3: Create OAuth Credentials

1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth client ID"
3. If prompted, configure the OAuth consent screen:
   - Select "External" (or "Internal" if using Google Workspace)
   - Fill in app name and user support email
   - Add developer contact email
   - Click "Save and Continue"
   - Skip scopes for now
   - Click "Save and Continue"
   - Click "Back to Dashboard"

4. Click "Create Credentials" > "OAuth client ID" again
5. Select "Desktop app" as Application type
6. Name it "Mirror Download Client"
7. Click "Create"
8. Click "Download JSON"
9. Save the file as `credentials.json` in your project directory

## Step 4: First Run Authentication

1. Copy `credentials.json` to your server/project directory
2. Run the application:
   ```bash
   python main.py
   ```
3. On first run, it will open a browser for you to authorize the app
4. Sign in with your Google account
5. Grant permission to access Google Drive
6. The token will be saved as `token.json` for future runs

## Step 5: (Optional) Service Account for Server

For a server without browser access, use a Service Account:

1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "Service account"
3. Enter a name and click "Create"
4. Grant role: "Editor" (or "Drive File" for Drive-only access)
5. Click "Continue" and "Done"
6. Click on the created service account
7. Go to "Keys" tab
8. Click "Add Key" > "Create new key" > JSON
9. Download the key file
10. Share your Google Drive folder with the service account email (found in the JSON file)

## Notes

- The token.json file contains your refresh token - keep it secure
- Desktop app credentials will require browser auth on first run
- Service accounts are better for headless/server deployments
- Store credentials.json and token.json securely - don't commit them to git
