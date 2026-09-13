"""Flask routes for MCP OAuth discovery, DCR, authorize, and token."""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request

from bigas.chat.auth import authenticate_request, is_chat_allowed, verify_dev_token, verify_firebase_token
from bigas.mcp_urls import mcp_public_base_url, mcp_resource_url
from bigas.oauth import service

logger = logging.getLogger(__name__)


def _json_for_script(value: Any) -> str:
    """Serialize JSON for embedding in HTML <script> tags (mitigate XSS)."""
    return json.dumps(value, ensure_ascii=True).replace("<", "\\u003c")


def _oauth_error(error: str, description: str = "", status: int = 400):
    payload = {"error": error}
    if description:
        payload["error_description"] = description
    return jsonify(payload), status


def _form_or_json() -> dict[str, Any]:
    if request.form:
        return {key: request.form.get(key) for key in request.form}
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _access_keys() -> set[str]:
    from flask import current_app

    return set(current_app.config.get("BIGAS_ACCESS_KEYS") or set())


def _identity_from_request(body: dict[str, Any]):
    access_key = (body.get("access_key") or "").strip()
    if access_key and access_key in _access_keys():
        return {"uid": "access-key", "email": "mcp-access-key@bigas.local"}, None

    user, err = authenticate_request()
    if user:
        return user, None

    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if token and token in _access_keys():
        return {"uid": "access-key", "email": "mcp-access-key@bigas.local"}, None
    if token:
        user = verify_firebase_token(token) or verify_dev_token(token)
        if user and is_chat_allowed(user):
            return user, None
    return None, err or (jsonify({"error": "Sign in with your Bigas account or access key."}), 401)


def _validate_authorize_request(params: dict[str, Any]):
    response_type = (params.get("response_type") or "").strip()
    client_id = (params.get("client_id") or "").strip()
    redirect_uri = (params.get("redirect_uri") or "").strip()
    code_challenge = (params.get("code_challenge") or "").strip()
    code_challenge_method = (params.get("code_challenge_method") or "S256").strip()
    state = params.get("state") or ""
    resource = (params.get("resource") or "").strip()
    scope = (params.get("scope") or "mcp").strip() or "mcp"

    if response_type != "code":
        return None, _oauth_error("unsupported_response_type", "Only response_type=code is supported.")
    client = service.get_client(client_id)
    if not client:
        return None, _oauth_error("invalid_client", "Unknown client_id. Register first.", 401)
    if not redirect_uri or not service.client_accepts_redirect(client, redirect_uri):
        return None, _oauth_error("invalid_request", "redirect_uri is not registered for this client.")
    if not code_challenge or code_challenge_method.upper() != "S256":
        return None, _oauth_error("invalid_request", "S256 PKCE is required.")
    expected_resource = mcp_resource_url()
    if resource and resource != expected_resource:
        return None, _oauth_error("invalid_target", "resource must match the MCP URL.")
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource or expected_resource,
        "scope": "mcp" if "mcp" in scope.split() else scope,
        "client_name": client.get("client_name") or "MCP client",
    }, None


def _authorize_page(params: dict[str, Any]) -> str:
    redirect_host = urlparse(params["redirect_uri"]).hostname or params["redirect_uri"]
    loopback = service.is_loopback_redirect(params["redirect_uri"])
    auth_payload = _json_for_script(
        {
            "client_id": params["client_id"],
            "redirect_uri": params["redirect_uri"],
            "code_challenge": params["code_challenge"],
            "code_challenge_method": params["code_challenge_method"],
            "state": params["state"],
            "resource": params["resource"],
            "scope": params["scope"],
            "response_type": "code",
        }
    )
    client_name_json = _json_for_script(params["client_name"])
    warning = (
        "<p class=\"warn\">This client will receive the code on your local machine. Confirm the app is Claude or Claude Code.</p>"
        if loopback
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connect Claude — Bigas</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: #f8fafc; color: #0f172a; }}
    .card {{ width: min(28rem, calc(100vw - 2rem)); background: #fff; border: 1px solid #e2e8f0;
      border-radius: 1rem; padding: 1.75rem; box-shadow: 0 10px 24px -4px rgba(15,23,42,.08); }}
    img {{ height: 4rem; width: 4rem; border-radius: .75rem; display: block; margin: 0 auto 1rem; }}
    h1 {{ margin: 0 0 .35rem; font-size: 1.35rem; text-align: center; }}
    p {{ color: #64748b; font-size: .9rem; line-height: 1.45; }}
    .client {{ font-weight: 600; color: #0f172a; }}
    .warn {{ color: #b45309; background: #fffbeb; border-radius: .5rem; padding: .6rem .75rem; }}
    label {{ display: block; font-size: .8rem; font-weight: 600; margin: .75rem 0 .3rem; }}
    input {{ width: 100%; box-sizing: border-box; border: 1px solid #cbd5e1; border-radius: .6rem;
      padding: .65rem .75rem; font: inherit; }}
    button {{ width: 100%; margin-top: .9rem; border: 0; border-radius: .6rem; padding: .75rem 1rem;
      font: inherit; font-weight: 650; cursor: pointer; background: #73cdfb; color: #0f172a; }}
    button.secondary {{ background: #0f172a; color: #f8fafc; }}
    .err {{ color: #b91c1c; min-height: 1.2rem; font-size: .85rem; }}
    details {{ margin-top: 1rem; color: #64748b; font-size: .85rem; }}
  </style>
</head>
<body>
  <div class="card">
    <img src="/bigas-logo.png" alt="Bigas">
    <h1>Connect to Bigas</h1>
    <p><span class="client" id="client-name"></span> wants to use your board and MCP tools.
      After you approve, we send you back to <strong>{redirect_host}</strong>.</p>
    {warning}
    <p class="err" id="error"></p>
    <div id="signed-in" hidden>
      <button class="secondary" id="continue-btn" type="button">Continue</button>
    </div>
    <form id="login-form">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="username">
      <label for="password">Password or access key</label>
      <input id="password" name="password" type="password" autocomplete="current-password">
      <button type="submit">Sign in and connect</button>
      <button class="secondary" id="google-btn" type="button" hidden>Continue with Google</button>
    </form>
    <details>
      <summary>Use a Bigas access key</summary>
      <label for="access-key">Access key</label>
      <input id="access-key" type="password" autocomplete="off">
      <button id="key-btn" type="button">Connect with access key</button>
    </details>
  </div>
  <script>
    const AUTH = {auth_payload};
    document.getElementById("client-name").textContent = {client_name_json};
    const errorEl = document.getElementById("error");
    const signedIn = document.getElementById("signed-in");
    const loginForm = document.getElementById("login-form");
    const googleBtn = document.getElementById("google-btn");
    let authConfigPromise = fetch("/api/auth/config").then((r) => r.json());
    let firebaseReadyPromise = null;

    function token() {{ return localStorage.getItem("bigas_chat_token") || ""; }}
    function showError(msg) {{ errorEl.textContent = msg || "Could not connect."; }}

    function ensureFirebaseReady(cfg) {{
      if ((cfg.auth_mode || "dev") !== "firebase" || !cfg.firebase || !cfg.firebase.apiKey) {{
        return Promise.resolve(false);
      }}
      if (window.firebase && firebase.auth) {{
        return Promise.resolve(true);
      }}
      if (!firebaseReadyPromise) {{
        firebaseReadyPromise = new Promise((resolve, reject) => {{
          const s1 = document.createElement("script");
          s1.src = "https://www.gstatic.com/firebasejs/10.13.2/firebase-app-compat.js";
          const s2 = document.createElement("script");
          s2.src = "https://www.gstatic.com/firebasejs/10.13.2/firebase-auth-compat.js";
          s1.onload = () => document.body.appendChild(s2);
          s2.onload = () => resolve(true);
          s1.onerror = () => reject(new Error("Could not load Firebase SDK."));
          s2.onerror = () => reject(new Error("Could not load Firebase Auth SDK."));
          document.body.appendChild(s1);
        }});
      }}
      return firebaseReadyPromise;
    }}

    async function complete(extra = {{}}, bearer = "") {{
      const headers = {{ "Content-Type": "application/json" }};
      if (bearer) headers.Authorization = "Bearer " + bearer;
      const res = await fetch("/oauth/authorize/complete", {{
        method: "POST",
        headers,
        body: JSON.stringify(Object.assign({{}}, AUTH, extra)),
      }});
      const data = await res.json().catch(() => ({{}}));
      if (!res.ok || !data.redirect_to) {{
        throw new Error(data.error_description || data.error || "Authorization failed");
      }}
      window.location.assign(data.redirect_to);
    }}

    if (token()) {{
      signedIn.hidden = false;
      loginForm.hidden = true;
    }}
    document.getElementById("continue-btn").onclick = () => complete({{}}, token()).catch((err) => showError(err.message));
    document.getElementById("key-btn").onclick = () => {{
      const access_key = document.getElementById("access-key").value.trim();
      complete({{ access_key }}).catch((err) => showError(err.message));
    }};
    loginForm.onsubmit = async (event) => {{
      event.preventDefault();
      const email = document.getElementById("email").value.trim();
      const password = document.getElementById("password").value;
      try {{
        const cfg = await authConfigPromise;
        if ((cfg.auth_mode || "dev") === "dev") {{
          localStorage.setItem("bigas_chat_token", password || "bigas-dev-token");
          await complete({{}}, password || "bigas-dev-token");
          return;
        }}
        if (cfg.firebase && cfg.firebase.apiKey) {{
          await ensureFirebaseReady(cfg);
          if (!window.firebase) {{
            throw new Error("Sign-in is still loading. Please try again.");
          }}
          if (!firebase.apps.length) firebase.initializeApp(cfg.firebase);
          const cred = await firebase.auth().signInWithEmailAndPassword(email, password);
          const idToken = await cred.user.getIdToken();
          localStorage.setItem("bigas_chat_token", idToken);
          await complete({{}}, idToken);
          return;
        }}
        await complete({{ access_key: password }});
      }} catch (err) {{
        showError(err.message);
      }}
    }};
    authConfigPromise.then((cfg) => {{
      if (cfg.auth_mode === "firebase" && cfg.firebase && cfg.firebase.apiKey) {{
        ensureFirebaseReady(cfg).then(() => {{
          googleBtn.hidden = false;
          googleBtn.onclick = async () => {{
            try {{
              const latest = await authConfigPromise;
              await ensureFirebaseReady(latest);
              if (!firebase.apps.length) firebase.initializeApp(latest.firebase);
              const cred = await firebase.auth().signInWithPopup(new firebase.auth.GoogleAuthProvider());
              const idToken = await cred.user.getIdToken();
              localStorage.setItem("bigas_chat_token", idToken);
              await complete({{}}, idToken);
            }} catch (err) {{
              showError(err.message);
            }}
          }};
        }}).catch((err) => showError(err.message));
      }}
    }}).catch(() => {{}});
  </script>
</body>
</html>
"""


def register_mcp_oauth_routes(app: Flask) -> None:
    @app.route("/.well-known/oauth-protected-resource", methods=["GET"])
    @app.route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
    def oauth_protected_resource():
        return jsonify(service.protected_resource_metadata())

    @app.route("/.well-known/oauth-authorization-server", methods=["GET"])
    @app.route("/.well-known/openid-configuration", methods=["GET"])
    def oauth_authorization_server():
        return jsonify(service.authorization_server_metadata())

    @app.route("/oauth/register", methods=["POST"])
    def oauth_register():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _oauth_error("invalid_client_metadata", "Expected a JSON object.")
        try:
            record = service.register_client(body)
        except ValueError as exc:
            return _oauth_error(str(exc) or "invalid_client_metadata", "Could not register the OAuth client.")
        return jsonify(record), 201

    @app.route("/oauth/authorize", methods=["GET"])
    def oauth_authorize():
        params, err = _validate_authorize_request(request.args)
        if err:
            return err
        return Response(_authorize_page(params), mimetype="text/html; charset=utf-8")

    @app.route("/oauth/authorize/complete", methods=["POST"])
    def oauth_authorize_complete():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return _oauth_error("invalid_request", "Expected a JSON object.")
        params, err = _validate_authorize_request(body)
        if err:
            return err
        user, auth_err = _identity_from_request(body)
        if auth_err:
            return auth_err
        code = service.issue_authorization_code(
            client_id=params["client_id"],
            redirect_uri=params["redirect_uri"],
            code_challenge=params["code_challenge"],
            code_challenge_method=params["code_challenge_method"],
            email=str(user.get("email") or ""),
            uid=str(user.get("uid") or ""),
            resource=params["resource"],
            scope=params["scope"],
        )
        redirect_to = service.build_redirect(
            params["redirect_uri"],
            {
                "code": code,
                "iss": mcp_public_base_url(),
                **({"state": params["state"]} if params["state"] != "" else {}),
            },
        )
        return jsonify({"redirect_to": redirect_to})

    @app.route("/oauth/token", methods=["POST"])
    def oauth_token():
        data = _form_or_json()
        grant_type = (data.get("grant_type") or "").strip()
        client_id = (data.get("client_id") or "").strip()
        if not service.get_client(client_id):
            return _oauth_error("invalid_client", "Unknown client_id.", 401)
        try:
            if grant_type == "authorization_code":
                tokens = service.exchange_authorization_code(
                    code=(data.get("code") or "").strip(),
                    client_id=client_id,
                    redirect_uri=(data.get("redirect_uri") or "").strip(),
                    code_verifier=(data.get("code_verifier") or "").strip(),
                    resource=(data.get("resource") or "").strip(),
                )
            elif grant_type == "refresh_token":
                tokens = service.exchange_refresh_token(
                    refresh_token=(data.get("refresh_token") or "").strip(),
                    client_id=client_id,
                )
            else:
                return _oauth_error("unsupported_grant_type", "Use authorization_code or refresh_token.")
        except ValueError as exc:
            return _oauth_error(str(exc) or "invalid_grant", "Token request was rejected.")
        response = jsonify(tokens)
        response.headers["Cache-Control"] = "no-store"
        return response
