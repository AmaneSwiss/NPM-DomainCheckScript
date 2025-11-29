# Nginx Proxy Manager - Domain Check Script
This is a Python script that use a domain to update the IP address in the NPM access-list.

[scripts/npm_domain_check.py](scripts/npm_domain_check.py)
#
Follow the steps below and run the script. A new column called `domain` will be added to the database and a file called `npm_domain_check.json` will be created in the same path where the script is located.
Ensure you are created the access-list first in NPM with ip and a network `/32` that you wand change dynamically, then add a domain to an IP address in the json file:
```json
// Example
{
  "74.125.29.139": "google.com",
  "140.82.121.4": "github.com"
}
```

---
### If you want to use the script, follow these steps:

1. Use a container_name in docker-compose or run command:
```yaml
services:
  app:
    container_name: npm # For example
```

2. Copy `npm_domain_check.py` script to `/usr/local/bin/npm_domain_check.py` in your **docker-machine**

Note: not into container!
```bash
sudo curl -o "/usr/local/bin/npm_domain_check.py" "https://raw.githubusercontent.com/AmaneSwiss/NPM-DomainCheckScript/refs/heads/main/scripts/npm_domain_check.py"
sudo chmod +x /usr/local/bin/npm_domain_check.py
```

3. Change line 10 in `npm_domain_check.py` from CONTAINER_NAME to container_name from step 1

Note: use `nano` for example
```py
# Your 'Nginx Proxy Manager' container name here
CONTAINER_NAME = "npm" # <- Change here
```

4. Add a cron job to run the script continuously `sudo crontab -e` for edit the root cron table an add:
```bash
# NPM domain-check
* * * * * /usr/local/bin/npm_domain_check.py
```
This will run the script every minute. You can change `* * * * *` to `*/5 * * * *` to run it every 5 minutes for example.

[Help for cronjobs can you find here](https://crontab.guru/)

---
### To undo, follow these steps:

1. Remove the cron job with `sudo crontab -e` and delete the added lines.
```bash
# NPM domain-check # <- remove
* * * * * /usr/local/bin/npm_domain_check.py # <- remove
```

2. Remove the script and config file
```bash
rm -f /usr/local/bin/npm_domain_check*
```

3. Remove the created `domain` column using a Python script as well:

[scripts/remove_domain_column.py](scripts/remove_domain_column.py)
```bash
sudo curl -o "/tmp/remove_domain_column.py" "https://raw.githubusercontent.com/AmaneSwiss/NPM-DomainCheckScript/refs/heads/main/scripts/remove_domain_column.py"
sudo chmod +x /tmp/remove_domain_column.py
```

4. Change line 7 in `remove_domain_column.py` from CONTAINER_NAME to container_name
```py
# Your 'Nginx Proxy Manager' container name here
CONTAINER_NAME = "npm" # <- Change here
```

5. Run script
```bash
/tmp/remove_domain_column.py
```
