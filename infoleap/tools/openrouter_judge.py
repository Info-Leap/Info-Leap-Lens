import os
import time
import asyncio
from openai import OpenAI
from dotenv import load_dotenv
import pandas as pd
from groq import Groq

load_dotenv()

# Load from ENV
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_A = os.getenv("OPENROUTER_MODEL_A")
OPENROUTER_MODEL_B = os.getenv("OPENROUTER_MODEL_B")
GROQ_KEY = os.getenv("GROQ_API_KEY")

or_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY)
groq_client = Groq(api_key=GROQ_KEY)

async def run_call(client, model: str, provider: str, prompt: str):
    start = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        return {
            "model": model,
            "provider": provider,
            "latency": f"{time.time()-start:.2f}s",
            "status": "Success",
            "content": resp.choices[0].message.content[:100].replace("\n", " ") + "..."
        }
    except Exception as e:
        return {"model": model, "provider": provider, "latency": "ERR", "status": "Failed", "content": str(e)[:100]}

async def main():
    prompt = "You are a Strategy Critic. Evaluate this: 'We should focus on North zone because NPS is 5 points lower than South'."
    print(f"🚀 Benchmarking LENS Strategy Critics...")
    print(f"Model A: {OPENROUTER_MODEL_A}")
    print(f"Model B: {OPENROUTER_MODEL_B}")
    
    tasks = [
        run_call(or_client, OPENROUTER_MODEL_A, "OR", prompt),
        run_call(or_client, OPENROUTER_MODEL_B, "OR", prompt),
        run_call(groq_client, "llama-3.3-70b-versatile", "GROQ", prompt)
    ]
    
    results = await asyncio.gather(*tasks)
    df = pd.DataFrame(results)
    print("\n" + df.to_markdown(index=False))

if __name__ == "__main__":
    asyncio.run(main())
