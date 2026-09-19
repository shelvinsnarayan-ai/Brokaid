"""Session middleware. Attaches request.state.session and provides
`session_conn` dependency that binds `app.tenant_id` GUC per request.
"""
