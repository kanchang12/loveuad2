#!/usr/bin/env python3
"""
Generate encryption key for loveUAD
"""

from cryptography.fernet import Fernet

def main():
    """Generate and print encryption key"""
    key = Fernet.generate_key()
    print("Generated Encryption Key:")
    print(key.decode())
    print("\nAdd this to your .env file as:")
    print(f"ENCRYPTION_KEY={key.decode()}")

if __name__ == '__main__':
    main()
