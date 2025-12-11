"""
Test script for WhatsApp API connection.
Sends a test message to verify API credentials and connectivity.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from whatsapp_client import WhatsAppClient, WhatsAppConfig, MediaCache


def load_config() -> WhatsAppConfig:
    """Load WhatsApp API configuration from environment variables."""
    load_dotenv(override=False)
    
    token = os.getenv("WHATSAPP_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    api_version = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    
    if not token or not phone_id:
        print("❌ Error: Missing required environment variables")
        print("   Please set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in your .env file")
        sys.exit(1)
    
    return WhatsAppConfig(
        token=token,
        phone_number_id=phone_id,
        api_version=api_version
    )


def test_api_connection():
    """Test WhatsApp API connection by sending a test message."""
    
    # Test account number
    TEST_NUMBER = "201113025205"
    
    print("🔧 Testing WhatsApp API Connection...")
    print(f"📱 Test recipient: +{TEST_NUMBER}")
    print("-" * 50)
    
    # Load configuration
    try:
        config = load_config()
        print("✅ Configuration loaded successfully")
        print(f"   API Version: {config.api_version}")
        print(f"   Phone Number ID: {config.phone_number_id}")
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return False
    
    # Initialize client
    try:
        media_cache = MediaCache(Path("media_cache.json"))
        client = WhatsAppClient(config=config, media_cache=media_cache, log_requests=True)
        print("✅ WhatsApp client initialized")
    except Exception as e:
        print(f"❌ Client initialization error: {e}")
        return False
    
    # Prepare test payload (simple text template)
    # Note: You may need to adjust this based on your approved templates
    payload = {
        "messaging_product": "whatsapp",
        "to": TEST_NUMBER,
        "type": "template",
        "template": {
            "name": "hello_world",  # Default template - change if you have different templates
            "language": {
                "code": "en_US"
            }
        }
    }
    
    # Send test message
    print("\n📤 Sending test message...")
    try:
        response = client.send_message(payload)
        print("✅ Message sent successfully!")
        print(f"   Response: {response}")
        return True
    except Exception as e:
        print(f"❌ Failed to send message: {e}")
        print("\nℹ️  Troubleshooting tips:")
        print("   1. Verify your WHATSAPP_TOKEN is valid and not expired")
        print("   2. Check that WHATSAPP_PHONE_NUMBER_ID is correct")
        print("   3. Ensure the template name 'hello_world' exists in your account")
        print("   4. Verify the test number is in correct format (without + sign)")
        print("   5. Check that your WhatsApp Business account has necessary permissions")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("WhatsApp API Connection Test")
    print("=" * 50)
    
    success = test_api_connection()
    
    print("\n" + "=" * 50)
    if success:
        print("✅ Test completed successfully!")
    else:
        print("❌ Test failed - please check the errors above")
    print("=" * 50)
    
    sys.exit(0 if success else 1)
