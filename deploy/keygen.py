"""Create the PULSE_SVC key pair ON the server (the private key never leaves it) and print the
ALTER USER statement that registers the public key in Snowflake."""
import pathlib
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\Mahesh\Python\v2pulse\keys")
out.mkdir(parents=True, exist_ok=True)
priv = out / "pulse_svc_rsa.p8"
if priv.exists():
    key = serialization.load_pem_private_key(priv.read_bytes(), password=None)
    print("Key already exists, re-printing its public half:", priv)
else:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    print("Created", priv)
pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
body = "".join(line for line in pub.splitlines() if "-----" not in line)
print("\nRun this in Snowflake as ACCOUNTADMIN (after Part B of deploy\\snowflake_setup.sql):\n")
print("alter user PULSE_SVC set rsa_public_key = '%s';" % body)
