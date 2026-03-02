from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

# Directory to store received webhooks
WEBHOOK_DATA_DIR = "webhook_data"
os.makedirs(WEBHOOK_DATA_DIR, exist_ok=True)

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint to receive JSON webhook data.
    """
    try:
        # Get JSON data from request
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data received"}), 400
        
        # Log the received data
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"webhook_{timestamp}.json"
        filepath = os.path.join(WEBHOOK_DATA_DIR, filename)
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"Received webhook data and saved to {filepath}")
        print(f"Data: {json.dumps(data, indent=2)}")
        
        # Return success response
        return jsonify({"status": "success", "message": "Webhook received"}), 200
        
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint.
    """
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    # Run the Flask server
    # Default: http://localhost:5000/webhook
    port = int(os.getenv('WEBHOOK_PORT', 5000))
    print(f"Starting webhook server on port {port}")
    print(f"Webhook URL: http://localhost:{port}/webhook")
    app.run(host='0.0.0.0', port=port, debug=True)
