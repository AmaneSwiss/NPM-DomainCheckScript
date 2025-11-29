#!/usr/bin/env python3

import subprocess
import sys
import socket
import json
import os
from datetime import datetime

# Your 'Nginx Proxy Manager' container name here
CONTAINER_NAME = "npm"

# Try to import MySQL driver
try:
    import mysql.connector
except ImportError:
    print("The package 'python3-mysql.connector' is required but not installed.")
    print("Please install it using your package manager, e.g., 'apt install python3-mysql.connector'")
    sys.exit(1)

# Config file path setup (Same directory and name as script, with .json extension)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = os.path.splitext(os.path.basename(__file__))[0]
CONFIG_PATH = os.path.join(SCRIPT_DIR, SCRIPT_NAME + ".json")

# Command execution helper
def run_command(cmd):
    # Runs a shell command and returns stdout, stderr, returncode.
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode

# Checks if Docker container exists
def container_exists(name):
    stdout, stderr, rc = run_command(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if rc != 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error in querying Docker containers: {stderr}")
        sys.exit(1)
    return name in stdout.splitlines()

# Reads environment variables from inside the container.
def get_env_from_container(name):
    stdout, stderr, rc = run_command(["docker", "exec", name, "env"])
    if rc != 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error reading environment variables from container '{name}': {stderr}")
        sys.exit(1)

    env = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env

# Resolves a domain to IPv4 address. Returns None on failure.
def resolve_ip(domain):
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return None

# Updates IP on right place of data folder inside container.
def update_ip_in_container(container, old_ip, new_ip):
    if not old_ip or not new_ip:
        return

    cmd = [
        "docker", "exec", container,
        "sh", "-c",
        f"sed -i 's/{old_ip}/{new_ip}/g' /data/nginx/proxy_host/*"
    ]
    _, stderr, rc = run_command(cmd)

    if rc != 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error running sed in container: {stderr}")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] sed executed in container: {old_ip} → {new_ip}")

# Reloads nginx inside container.
def reload_nginx(container):
    _, stderr, rc = run_command(["docker", "exec", container, "nginx", "-s", "reload"])
    if rc == 0:
        print("Nginx successfully reloaded.")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error reloading Nginx: {stderr}")


# Loads JSON config from file.
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except:
        return {}

# Saves JSON config to file.
def save_config(data):
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)

# Main function
def main():
    # STEP 1: check container existence
    if not container_exists(CONTAINER_NAME):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Container '{CONTAINER_NAME}' does not exist.")
        sys.exit(0)

    # STEP 2: load JSON configuration
    domain_map = load_config()

    # STEP 3: get env vars from container
    env = get_env_from_container(CONTAINER_NAME)

    # STEP 3.1: verify required env vars
    required_vars = [
        "DB_MYSQL_HOST",
        "DB_MYSQL_PORT",
        "DB_MYSQL_NAME",
        "DB_MYSQL_USER",
        "DB_MYSQL_PASSWORD",
    ]

    missing = [v for v in required_vars if v not in env]

    if missing:
        print("Missing environment variables in container:")
        print("\n".join(missing))
        sys.exit(1)

    # STEP 3.2: extract env vars
    db_host = env["DB_MYSQL_HOST"]
    db_port = int(env["DB_MYSQL_PORT"])
    db_name = env["DB_MYSQL_NAME"]
    db_user = env["DB_MYSQL_USER"]
    db_password = env["DB_MYSQL_PASSWORD"]

    # STEP 4: Try to connect to the database
    try:
        conn = mysql.connector.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            ssl_disabled=True,
        )
    except mysql.connector.Error as err:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] DB connection error: {err}")
        sys.exit(1)

    cursor = conn.cursor(dictionary=True)

    # STEP 5: Process the access_list_client table
    try:
        # STEP 5.1: check if column "domain" exists, create if missing
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'access_list_client'
            AND COLUMN_NAME = 'domain'
            """, (db_name,)
        )

        result = cursor.fetchone()
        column_exists = list(result.values())[0] == 1

        if not column_exists:
            print("Column 'domain' is missing, creating it...")
            try:
                cursor.execute("ALTER TABLE access_list_client ADD COLUMN domain VARCHAR(255)")
                conn.commit()
                print("Column 'domain' successfully created.")
            except mysql.connector.Error as err:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error creating column 'domain': {err}")
                sys.exit(1)

        # STEP 5.2: fetch all rows from access_list_client
        cursor.execute(
            """
            SELECT id, domain, address
            FROM access_list_client
            """
        )
        rows = cursor.fetchall()

        # STEP 5.3: process each row
        table_by_ip = {}
        for row in rows:
            ip = None
            if row["address"]:
                ip = row["address"].split("/")[0]
            table_by_ip[ip] = row

        # initialize flags and counters
        nginx_reload_needed = False
        updated_count = 0

        # STEP 5.4: restore missing domains from config
        for ip, domain in list(domain_map.items()):
            if ip not in table_by_ip:
                del domain_map[ip]
                continue

            db_row = table_by_ip[ip]
            if db_row["domain"] != domain:
                cursor.execute(
                    "UPDATE access_list_client SET domain=%s WHERE id=%s",
                    (domain, db_row["id"])
                )
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Domain for IP {ip} restored: {domain}")
                updated_count += 1

        # STEP 5.5: resolve domains and update IPs
        for row in rows:
            row_id = row["id"]
            domain = row["domain"]
            address = row["address"]

            ip_old = None
            if address:
                ip_old = address.split("/")[0]

            if domain:
                ip_new = resolve_ip(domain)
                # Skip if resolution failed
                if not ip_new:
                    continue

                # Prepare new address with /32 suffix
                new_address = f"{ip_new}/32"

                # Update IP if not same
                if ip_new != ip_old:
                    cursor.execute(
                        "UPDATE access_list_client SET address=%s WHERE id=%s",
                        (new_address, row_id)
                    )
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] IP updated for {domain}: {ip_old} → {ip_new}")

                    update_ip_in_container(CONTAINER_NAME, ip_old, ip_new)
                    nginx_reload_needed = True
                    updated_count += 1

                # Update domain_map for config saving
                domain_map[ip_new] = domain
                if ip_old in domain_map and ip_old != ip_new:
                    del domain_map[ip_old]

        save_config(domain_map)

        conn.commit()

        # STEP 6: Reload nginx if needed
        if nginx_reload_needed:
            reload_nginx(CONTAINER_NAME)

        if updated_count > 0:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Total changes: {updated_count}")

    # STEP 7: Cleanup
    finally:
        cursor.close()
        conn.close()

# Run main function
if __name__ == "__main__":
    main()
