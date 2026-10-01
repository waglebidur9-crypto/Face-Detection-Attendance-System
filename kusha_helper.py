import os
import requests
from dotenv import load_dotenv

load_dotenv()

def send_kusha_sms(phone_number, message):
    """
    Sends an automated SMS via Kusha SMS v4 API.
    Returns a tuple: (success: bool, response_data_or_error)
    """
    api_url = "https://kushasms.com/sms/v4/send-user"
    
    # Uses the environment variable, falling back to your active token if needed
    api_token = os.getenv("KUSHA_API_TOKEN", "IU8C97kDdoL0uzduN35bWgMwdzJy5H7AFSupHe21QGw")

    headers = {
        "auth-token": api_token,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "to": [str(phone_number)],
        "text": [message]
    }

    try:
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=15
        )

        response_data = response.json()

        if response_data.get("errors"):
            return False, response_data

        responses = response_data.get("responses", [])

        if responses and not responses[0].get("error"):
            return True, response_data

        return False, response_data

    except requests.exceptions.RequestException as e:
        return False, str(e)

    except ValueError:
        return False, "Invalid JSON response from Kusha SMS"