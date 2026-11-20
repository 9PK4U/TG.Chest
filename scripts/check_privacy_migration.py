"""Exercise fresh/legacy PostgreSQL migrations and tests, without published ports."""
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, **kwargs)


def main():
    name = "tg-chest-migration-check-" + secrets.token_hex(4)
    password = secrets.token_hex(16)
    created = False
    def sql(query, database="migration_check"):
        return docker("exec", name, "psql", "-U", "chest", "-d", database, "-v", "ON_ERROR_STOP=1", "-At", "-c", query, capture_output=True).stdout.decode().strip()
    def migrate():
        docker("run", "--rm", "--network", "container:" + name, "-e",
               f"DATABASE_URL=postgresql+psycopg://chest:{password}@127.0.0.1:5432/migration_check",
               "tg-chest:local", "alembic", "upgrade", "head")
    try:
        docker("run", "-d", "--name", name, "--network", "none", "-e", "POSTGRES_USER=chest", "-e",
               "POSTGRES_PASSWORD=" + password, "-e", "POSTGRES_DB=migration_check", "pgvector/pgvector:pg17", stdout=subprocess.DEVNULL)
        created = True
        for _ in range(60):
            result = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "chest"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError("Isolated PostgreSQL did not start")
        migrate()
        assert sql("SELECT version_num FROM alembic_version") == "0003"
        # Recreate the actual legacy column shape, then verify fail-closed upgrade.
        sql("""DROP TABLE captures;
            DROP TABLE ai_configurations;
            ALTER TABLE entries DROP COLUMN ai_policy;
            ALTER TABLE entries DROP COLUMN capture_id;
            ALTER TABLE users DROP COLUMN bot_ai_policy;
            UPDATE alembic_version SET version_num='0001';
            INSERT INTO users(id,name) VALUES(1,'Legacy');
            INSERT INTO entries(id,user_id,kind,title,body,tags,favorite,created_at,updated_at,revision,index_status,source)
                VALUES('legacy',1,'note','Legacy','Private old text','[]',false,1,1,1,'ready','telegram');
            INSERT INTO chunks(id,entry_id,user_id,text,model) VALUES('chunk','legacy',1,'Private old text','old-external');""")
        migrate()
        assert sql("SELECT ai_policy FROM entries WHERE id='legacy'") == "trusted"
        assert sql("SELECT model FROM chunks WHERE id='chunk'") == ""
        assert sql("SELECT bot_ai_policy FROM users WHERE id=1") == "external"
        print("Fresh schema + legacy privacy migration: OK", flush=True)
        sql("CREATE DATABASE unit_check")
        docker("run", "--rm", "--network", "container:" + name, "-e",
               f"TEST_DATABASE_URL=postgresql+psycopg://chest:{password}@127.0.0.1:5432/unit_check",
               "-v", str(ROOT / "scripts") + ":/app/scripts:ro", "tg-chest:local",
               "python", "-m", "pytest", "-q", "tests")
    finally:
        if created:
            docker("rm", "-f", "-v", name, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
