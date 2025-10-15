# list_models.py
import google.generativeai as genai
import os, sys

# Configure API key the same way you do in app.py
genai.configure(api_key="AIzaSyDwDDUhsXwlzd0IP7ZawZSYxLceyvBNb34")  # replace with your key or export env var

def main():
    print("Fetching available models...")

    # Try the high-level helper if available
    try:
        models = genai.list_models()  # high-level wrapper if present
        print("Models returned by genai.list_models():")
        for m in models:
            # many model objects have a 'name' attribute / str() representation
            try:
                print(" -", getattr(m, "name", str(m)))
            except Exception:
                print(" -", m)
        return
    except Exception as e:
        # If genai.list_models() isn't available, try lower-level call via the underlying client
        print("genai.list_models() not available or failed:", repr(e))

    # Fallback: attempt to call underlying client if present
    try:
        client = genai._client  # private field in some versions
        resp = client.list_models()
        print("Models returned by client.list_models():")
        for m in resp.models:
            print(" -", m.name)
        return
    except Exception as e:
        print("Fallback client.list_models() also failed:", repr(e))

    print("\nIf both methods failed, please open Google AI Studio (https://aistudio.google.com) -> Models to view available models,")
    print("or upgrade the google-generativeai package: pip install -U google-generativeai")
    sys.exit(1)

if __name__ == "__main__":
    main()