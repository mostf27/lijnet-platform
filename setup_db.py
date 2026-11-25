import getpass
import pathlib
import re
import urllib.parse
import psycopg

pw = getpass.getpass("Enter your postgres password: ")

with psycopg.connect(
    host="localhost",
    port=5432,
    dbname="postgres",
    user="postgres",
    password=pw,
    autocommit=True,
) as conn:
    exists = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = 'lijnet_db'"
    ).fetchone()
    if not exists:
        conn.execute("CREATE DATABASE lijnet_db")
        print("Created database: lijnet_db")
    else:
        print("Database lijnet_db already exists.")

encoded_pw = urllib.parse.quote_plus(pw)
env_path = pathlib.Path(".env")
new_line = f'DATABASE_URL="postgresql+psycopg://postgres:{encoded_pw}@localhost:5432/lijnet_db"'
content = env_path.read_text(encoding="utf-8")

if re.search(r"^DATABASE_URL=.*$", content, flags=re.M):
    content = re.sub(r"^DATABASE_URL=.*$", new_line, content, flags=re.M)
else:
    content = content.rstrip() + "\n" + new_line + "\n"

env_path.write_text(content, encoding="utf-8")
print(".env updated successfully!")
