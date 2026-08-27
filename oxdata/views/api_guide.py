"""
API Key Guide — How to get your OpenRouter API key.
"""

import streamlit as st
import os

st.title("🔑 Getting Your OpenRouter API Key")

st.markdown("""
## Step-by-Step Guide

### 1. Create an OpenRouter Account
1. Go to [openrouter.ai](https://openrouter.ai)
2. Click **Login** and sign up via Google or Email.

### 2. Generate API Key
1. Go to the **Keys** section in your dashboard.
2. Click **Create Key**.
3. Name it "LENS Intelligence".
4. Copy the key and store it safely.

### 3. Add Key to LENS
1. Open the `.env` file in the project root.
2. Update the `OPENROUTER_API_KEY` variable.

---

## ⚠️ Important Notes

- **Unified Access**: OpenRouter provides access to Llama-3, DeepSeek, and more via a single key.
- **Security**: Never share your API key publicly.

For more help, visit the [OpenRouter Documentation](https://openrouter.ai/docs).
""")

st.divider()
st.caption(f"Powered by OpenRouter {os.getenv('OPENROUTER_MODEL_A')} Engine")
