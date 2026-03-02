from google import genai
import os
import dotenv

dotenv.load_dotenv()

MODEL = "gemini-2.0-flash"

# Set up API key
client = genai.Client(api_key=os.getenv("GEMINI_KEY"))
# Initialize the model - will be used per request
# model = client.models.get(MODEL)


def prompt(prompt):
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )
    return response.text.strip()

if __name__ == "__main__":
    print(prompt("This is just a test, reply with what is on your mind right now"))
