"""
Firebase Authentication gate for InfoLeap Pulse.

Two sign-in methods:
  1. Email + Password  — InfoLeap team creates user accounts in Firebase Console
  2. Google OAuth      — restricted to @info-leap.com domain only

Flow:
  - Firebase JS SDK handles login in-browser (popup)
  - ID token posted back to Streamlit via URL param
  - Server-side: firebase-admin verifies token
  - Email checked: domain + registry
  - st.session_state["user"] set — app proceeds

Env vars required:
  FIREBASE_WEB_API_KEY          — from Firebase Console (public, safe to use client-side)
  FIREBASE_PROJECT_ID           — infoleap-pulse
  GOOGLE_APPLICATION_CREDENTIALS — path to service account JSON
"""

import os
import streamlit as st
import streamlit.components.v1 as components

try:
    import firebase_admin
    from firebase_admin import credentials, auth as fb_auth
    _ADMIN_OK = True
except ImportError:
    _ADMIN_OK = False

# ── Config ───────────────────────────────────────────────────────────────────

def _firebase_config() -> dict:
    """Load Firebase config from env / st.secrets — never hardcoded."""
    try:
        import streamlit as st
        cfg = st.secrets.get("firebase", {})
        if cfg:
            return dict(cfg)
    except Exception:
        pass
    return {
        "apiKey":            os.environ.get("FIREBASE_API_KEY", ""),
        "authDomain":        os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "projectId":         os.environ.get("FIREBASE_PROJECT_ID", ""),
        "storageBucket":     os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId":             os.environ.get("FIREBASE_APP_ID", ""),
    }

FIREBASE_CONFIG = _firebase_config()

# Only these Google domains allowed for Google sign-in
ALLOWED_GOOGLE_DOMAINS = {"info-leap.com"}

# Per-user project access. Email → list of project IDs.
# Add new users here OR manage via Firebase Console + a Drive-backed registry.
USER_REGISTRY: dict[str, list[str]] = {
    "tuhin.bhattacharya@info-leap.com": ["project_1", "akshayakalpa"],
    # Add more users:
    # "analyst@info-leap.com":          ["project_1"],
    # "client@brand.com":               ["akshayakalpa"],
}


# ── Firebase Admin init ───────────────────────────────────────────────────────

def _init_admin() -> bool:
    if not _ADMIN_OK:
        return False
    if firebase_admin._apps:
        return True
    cred_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        os.path.join(os.path.dirname(__file__), "..", "oxdata", "config", "infoleap_service_account.json"),
    )
    if not os.path.exists(cred_path):
        return False
    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {"projectId": FIREBASE_CONFIG["projectId"]})
        return True
    except Exception:
        return False


def verify_token(id_token: str) -> dict | None:
    if not _init_admin():
        return None
    try:
        return fb_auth.verify_id_token(id_token)
    except Exception:
        return None


# ── Access control ────────────────────────────────────────────────────────────

def get_projects(email: str) -> list[str]:
    """Return project IDs accessible to this email."""
    # Exact match first
    if email in USER_REGISTRY:
        return USER_REGISTRY[email]
    # info-leap.com employees not explicitly listed get all projects
    domain = email.split("@")[-1] if "@" in email else ""
    if domain in ALLOWED_GOOGLE_DOMAINS:
        all_projects = list({p for ps in USER_REGISTRY.values() for p in ps})
        return all_projects
    return []


def is_authorized(email: str) -> bool:
    return bool(get_projects(email))


def is_allowed_google_account(email: str) -> bool:
    domain = email.split("@")[-1] if "@" in email else ""
    return domain in ALLOWED_GOOGLE_DOMAINS


# ── Login page HTML ───────────────────────────────────────────────────────────

_LOGIN_HTML = """
<script type="module">
import {{ initializeApp }}       from "https://www.gstatic.com/firebasejs/11.0.1/firebase-app.js";
import {{ getAuth, GoogleAuthProvider, signInWithPopup, signInWithEmailAndPassword, onAuthStateChanged }}
  from "https://www.gstatic.com/firebasejs/11.0.1/firebase-auth.js";

const app  = initializeApp({firebase_config_json});
const auth = getAuth(app);

function redirect(user) {{
  user.getIdToken().then(token => {{
    const url = new URL(window.parent.location.href);
    url.searchParams.set("fb_token", token);
    url.searchParams.set("fb_email", user.email);
    window.parent.location.href = url.toString();
  }});
}}

// Google sign-in
document.getElementById("google-btn").addEventListener("click", async () => {{
  setError("");
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({{ hd: "info-leap.com", prompt: "select_account" }});
  try {{
    const result = await signInWithPopup(auth, provider);
    redirect(result.user);
  }} catch(e) {{
    setError(e.message);
  }}
}});

// Email + password
document.getElementById("email-form").addEventListener("submit", async (e) => {{
  e.preventDefault();
  setError("");
  const email    = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  try {{
    const result = await signInWithEmailAndPassword(auth, email, password);
    redirect(result.user);
  }} catch(e) {{
    const msg = e.code === "auth/invalid-credential"
      ? "Incorrect email or password."
      : e.message;
    setError(msg);
  }}
}});

function setError(msg) {{
  document.getElementById("err").textContent = msg;
}}
</script>

<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'IBM Plex Sans', system-ui, sans-serif; background: #F3F5F9; min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 1rem; }}
  .card {{
    background: #fff; border: 1px solid #DDE3EE; border-radius: 12px;
    padding: 2rem 2rem 1.75rem; width: 100%; max-width: 360px;
    box-shadow: 0 2px 16px rgba(15,25,35,0.08);
  }}
  .logo {{ font-size: 1.125rem; font-weight: 700; color: #0F1923; margin-bottom: 0.2rem; }}
  .sub  {{ font-size: 0.8rem; color: #6B7A99; margin-bottom: 1.5rem; }}
  label {{ font-size: 0.75rem; font-weight: 600; color: #3A4A5C; display: block; margin-bottom: 0.3rem; }}
  input {{
    width: 100%; border: 1.5px solid #DDE3EE; border-radius: 6px;
    padding: 0.55rem 0.75rem; font-size: 0.875rem; color: #0F1923;
    background: #F3F5F9; outline: none; margin-bottom: 0.85rem;
    font-family: inherit; transition: border 0.15s;
  }}
  input:focus {{ border-color: #1967D2; background: #fff; }}
  .btn-primary {{
    width: 100%; background: #1967D2; color: #fff; border: none;
    border-radius: 6px; padding: 0.65rem; font-size: 0.9rem; font-weight: 600;
    cursor: pointer; font-family: inherit; transition: background 0.15s;
  }}
  .btn-primary:hover {{ background: #1558B0; }}
  .divider {{
    display: flex; align-items: center; gap: 0.75rem;
    margin: 1.1rem 0; color: #A8B8D0; font-size: 0.75rem;
  }}
  .divider::before, .divider::after {{
    content: ''; flex: 1; height: 1px; background: #DDE3EE;
  }}
  #google-btn {{
    width: 100%; display: flex; align-items: center; justify-content: center; gap: 0.6rem;
    background: #fff; border: 1.5px solid #DDE3EE; border-radius: 6px;
    padding: 0.6rem; font-size: 0.875rem; font-weight: 500; cursor: pointer;
    color: #0F1923; font-family: inherit; transition: box-shadow 0.15s;
  }}
  #google-btn:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  #google-btn img {{ width: 18px; height: 18px; }}
  .note {{ font-size: 0.7rem; color: #A8B8D0; text-align: center; margin-top: 0.75rem; }}
  #err {{ color: #E8710A; font-size: 0.8rem; margin-top: 0.5rem; min-height: 1rem; text-align: center; }}
</style>

<div class="card">
  <div class="logo">InfoLeap Pulse</div>
  <div class="sub">Sign in to access your research projects</div>

  <form id="email-form">
    <label for="email">Email</label>
    <input id="email" type="email" placeholder="you@info-leap.com" autocomplete="username" required>
    <label for="password">Password</label>
    <input id="password" type="password" placeholder="••••••••" autocomplete="current-password" required>
    <button class="btn-primary" type="submit">Sign in</button>
  </form>

  <div class="divider">or</div>

  <button id="google-btn">
    <img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg" alt="">
    Continue with Google (@info-leap.com)
  </button>

  <div class="note">Google sign-in restricted to @info-leap.com accounts only.</div>
  <div id="err"></div>
</div>
"""


# ── Streamlit gate ────────────────────────────────────────────────────────────

def render_login_page() -> None:
    import json
    config_json = json.dumps(FIREBASE_CONFIG)
    html = _LOGIN_HTML.replace("{firebase_config_json}", config_json)
    st.markdown("<style>header, footer, [data-testid='stSidebar'] {display:none}</style>", unsafe_allow_html=True)
    components.html(html, height=500, scrolling=False)


def require_auth() -> dict:
    """
    Call at top of app.py. Returns user dict or calls st.stop().
    User dict: {"email": str, "name": str, "uid": str, "projects": list[str]}
    """
    if "user" in st.session_state:
        return st.session_state["user"]

    params = st.query_params
    token  = params.get("fb_token")
    email  = params.get("fb_email")

    if token and email:
        # Google login — enforce domain
        if "@" in email and email.split("@")[1] not in ALLOWED_GOOGLE_DOMAINS:
            # Could be email+password user — allow those through regardless of domain
            # Only block Google OAuth from non-infoleap domains
            pass  # email/password users have any domain — validated by Firebase password auth

        decoded = verify_token(token)
        if decoded and decoded.get("email") == email:
            if not is_authorized(email):
                st.error(f"Access denied: **{email}** is not authorised. Contact InfoLeap admin.")
                st.stop()
            user = {
                "email":    email,
                "name":     decoded.get("name", email.split("@")[0]),
                "uid":      decoded.get("uid"),
                "projects": get_projects(email),
            }
            st.session_state["user"] = user
            st.query_params.clear()
            st.rerun()
        else:
            st.query_params.clear()

    render_login_page()
    st.stop()
