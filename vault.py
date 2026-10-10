"""Encrypted credential storage. The encryption key never goes in the data directory."""
import json, os, uuid
from pathlib import Path

class Vault:
    def __init__(self, directory):
        self.directory=Path(directory); self.path=self.directory/'credentials.enc'
    def cipher(self):
        from cryptography.fernet import Fernet
        key=os.environ.get('GOVERNTRADE_MASTER_KEY')
        if not key:
            import keyring
            # Refuse plaintext/file backends. macOS Keychain is the local default.
            if not keyring.get_keyring().__class__.__module__.startswith('keyring.backends.macOS'):
                raise ValueError('Set GOVERNTRADE_MASTER_KEY or use macOS Keychain to enable encrypted credentials.')
            service='GovernTrade:'+str(self.directory.resolve())
            key=keyring.get_password(service,'master')
            if not key:
                key=Fernet.generate_key().decode();keyring.set_password(service,'master',key)
        return Fernet(key.encode())
    def read(self):
        return json.loads(self.cipher().decrypt(self.path.read_bytes())) if self.path.exists() else {}
    def write(self, values):
        self.directory.mkdir(exist_ok=True);temporary=self.path.with_suffix('.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'wb') as f:f.write(self.cipher().encrypt(json.dumps(values).encode()))
        temporary.replace(self.path);os.chmod(self.path,0o600)
    def put(self, credentials, reference=None):
        values=self.read();reference=reference or str(uuid.uuid4());values[reference]=credentials;self.write(values);return reference
    def get(self, reference):return self.read()[reference]
    def delete(self, reference):
        values=self.read();values.pop(reference,None);self.write(values)

def masks(credentials):return {k:'••••'+str(v).strip()[-4:] for k,v in credentials.items()}
