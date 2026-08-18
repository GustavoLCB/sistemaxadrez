"""
Cria (ou reseta a senha d)o usuário administrador. Rode uma vez, direto no console
Bash do PythonAnywhere (ou localmente), dentro da pasta do projeto:

    python3 create_admin.py

"""
import getpass
import sys
from db import init_db, write_transaction
from logic import new_id
from werkzeug.security import generate_password_hash

def main():
    init_db()
    username = input("Usuário do administrador (ex: gustavo): ").strip()
    if not username:
        print("Usuário não pode ser vazio.")
        sys.exit(1)
    full_name = input("Nome completo: ").strip()
    password = getpass.getpass("Senha (não aparece na tela, mín. 4 caracteres): ")
    if len(password) < 4:
        print("Senha muito curta.")
        sys.exit(1)
    password2 = getpass.getpass("Confirme a senha: ")
    if password != password2:
        print("As senhas não coincidem.")
        sys.exit(1)

    with write_transaction() as conn:
        existing = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            conn.execute("UPDATE users SET password_hash=?, full_name=?, role='admin' WHERE username=?",
                         (generate_password_hash(password), full_name, username))
            print(f'Usuário "{username}" já existia — senha atualizada e papel confirmado como admin.')
        else:
            uid = new_id()
            conn.execute("INSERT INTO users (id, username, password_hash, full_name, role) VALUES (?,?,?,?,'admin')",
                         (uid, username, generate_password_hash(password), full_name))
            print(f'Administrador "{username}" criado com sucesso.')

if __name__ == "__main__":
    main()
