import google.generativeai as genai
import os
import dotenv

dotenv.load_dotenv()

MODEL = "gemini-2.0-flash"

# Set up API key
genai.configure(api_key=os.getenv("GEMINI_KEY"), transport="rest")
# Initialize the model
model = genai.GenerativeModel(MODEL)


def prompt(prompt):
    response = model.generate_content(prompt)
    return response.text.strip()

if __name__ == "__main__":
    print(prompt("This is just a test, reply with what is on your mind right now"))
