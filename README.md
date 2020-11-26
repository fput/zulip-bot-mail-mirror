# Zulip mail mirror bot

A bot that queries new mails from an IMAP server and mirrors them to a Zulip server.

## Setup

Adjust the config file `bot_config.py` and insert a zulip config file `zuliprc`.

```bash
python -m venv env
source env/bin/activate
pip install -r requirements.txt
./mail-mirror.py
```

For continued usage you can use the systemd-service file in `systemd/`.

Then place the unit in e.g. `/etc/systemd/system/` and run `systemctl start zulip-bot-mail-mirror`. You can view the logs using `journalctl -f -u zulip-bot-mail-mirror`.

## Usage

Add the bot to the streams where you want to mirror uploaded files. Then run it periodically to mirror all new mails.
