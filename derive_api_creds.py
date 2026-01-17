#!/usr/bin/env python3
"""
Script to derive Polymarket API credentials from your private key.

This uses L1 authentication to derive or create API credentials.
Run this script locally to get your API key, secret, and passphrase.
"""

import os
import sys
import time
import json
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

CLOB_BASE_URL = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


def create_eip712_signature(private_key: str, timestamp: int, nonce: int = 0) -> tuple:
    """
    Create EIP-712 signature for L1 authentication.
    
    Based on Polymarket's CLOB authentication spec.
    """
    account = Account.from_key(private_key)
    address = account.address
    
    # EIP-712 domain and message structure for Polymarket CLOB
    domain = {
        "name": "ClobAuthDomain",
        "version": "1",
        "chainId": CHAIN_ID,
    }
    
    message = {
        "address": address,
        "timestamp": timestamp,
        "nonce": nonce,
    }
    
    # EIP-712 typed data
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "ClobAuth",
        "domain": domain,
        "message": {
            "address": address,
            "timestamp": str(timestamp),
            "nonce": nonce,
        },
    }
    
    # Sign the typed data
    signable_message = encode_typed_data(full_message=typed_data)
    signed = account.sign_message(signable_message)
    
    return address, signed.signature.hex()


def derive_api_credentials(private_key: str) -> dict:
    """
    Derive existing API credentials from Polymarket.
    
    If credentials don't exist, this will fail and you need to create them.
    """
    timestamp = int(time.time())
    nonce = 0
    
    address, signature = create_eip712_signature(private_key, timestamp, nonce)
    
    headers = {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_NONCE": str(nonce),
    }
    
    print(f"\n📍 Your wallet address (derived from private key): {address}")
    print(f"⏰ Timestamp: {timestamp}")
    print(f"🔑 Signature: {signature[:20]}...{signature[-20:]}")
    
    print("\n🔄 Attempting to derive existing API credentials...")
    
    response = requests.get(
        f"{CLOB_BASE_URL}/auth/derive-api-key",
        headers=headers,
    )
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"❌ Derive failed: {response.status_code} - {response.text}")
        return None


def create_api_credentials(private_key: str) -> dict:
    """
    Create new API credentials on Polymarket.
    """
    timestamp = int(time.time())
    nonce = 0
    
    address, signature = create_eip712_signature(private_key, timestamp, nonce)
    
    headers = {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_NONCE": str(nonce),
    }
    
    print("\n🔄 Attempting to create new API credentials...")
    
    response = requests.post(
        f"{CLOB_BASE_URL}/auth/api-key",
        headers=headers,
    )
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"❌ Create failed: {response.status_code} - {response.text}")
        return None


def main():
    print("=" * 60)
    print("🔐 Polymarket API Credentials Derivation Tool")
    print("=" * 60)
    
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    
    if not private_key:
        print("\n❌ ERROR: POLYMARKET_PRIVATE_KEY not found in environment!")
        print("   Make sure your .env file contains POLYMARKET_PRIVATE_KEY=0x...")
        sys.exit(1)
    
    # Ensure it has 0x prefix
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    
    # Get wallet address
    account = Account.from_key(private_key)
    print(f"\n📍 Wallet Address (from private key): {account.address}")
    
    # Check if this matches your Polymarket profile
    print("\n⚠️  IMPORTANT: Compare this address with your Polymarket profile!")
    print("   Go to: https://polymarket.com/settings")
    print("   Your profile address should match the address above.")
    print("   If they don't match, you're using the wrong private key!")
    
    input("\nPress Enter to continue...")
    
    # Try to derive existing credentials first
    creds = derive_api_credentials(private_key)
    
    if creds:
        print("\n✅ Successfully derived existing API credentials!")
    else:
        print("\n🔄 No existing credentials found. Creating new ones...")
        creds = create_api_credentials(private_key)
        
        if creds:
            print("\n✅ Successfully created new API credentials!")
        else:
            print("\n❌ Failed to create API credentials.")
            print("   This might mean:")
            print("   1. You haven't logged into Polymarket.com with this wallet")
            print("   2. The wallet doesn't have a proxy wallet deployed")
            print("   3. There's a network/API issue")
            sys.exit(1)
    
    print("\n" + "=" * 60)
    print("🎉 YOUR API CREDENTIALS (save these securely!):")
    print("=" * 60)
    print(f"\nPOLYMARKET_API_KEY={creds.get('apiKey', 'N/A')}")
    print(f"POLYMARKET_API_SECRET={creds.get('secret', 'N/A')}")
    print(f"POLYMARKET_API_PASSPHRASE={creds.get('passphrase', 'N/A')}")
    
    print("\n" + "=" * 60)
    print("📋 Add these to your Railway environment variables!")
    print("=" * 60)


if __name__ == "__main__":
    main()
