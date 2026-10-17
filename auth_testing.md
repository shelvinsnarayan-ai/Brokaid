# Auth testing (Emergent Google Auth + brokAId real sign-in)
See /app/memory/test_credentials.md. Google sign-in cannot be driven end-to-end by automation (real Google account needed);
test the backend exchange by mocking app.kernel.auth_flows.google_profile, and test that the 'Continue with Google' button
redirects to https://auth.emergentagent.com/?redirect=<origin>/login. After Google, TOTP is still required.
