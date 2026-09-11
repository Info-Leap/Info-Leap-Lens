import os
import requests
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("OPENROUTER_API_KEY")
print(f"Key starts with: {key[:10]}...")

# Check models
response = requests.get(
    url="https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {key}"}
)

if response.status_code == 200:
    models = response.json().get("data", [])
    print(f"Found {len(models)} models.")
    
    # Check if our target models are in the list
    target_a = os.getenv("OPENROUTER_MODEL_A")
    target_b = os.getenv("OPENROUTER_MODEL_B")
    
    found_a = any(m["id"] == target_a for m in models)
    found_b = any(m["id"] == target_b for m in models)
    
    print(f"Model A ({target_a}) found: {found_a}")
    print(f"Model B ({target_b}) found: {found_b}")
    
    if not found_a:
        # Print some free models
        free_models = [m["id"] for m in models if ":free" in m["id"]]
        print(f"Sample free models: {free_models[:10]}")
else:
    print(f"Error fetching models: {response.status_code}")
    print(response.text)
