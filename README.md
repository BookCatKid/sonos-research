# Sonos local music-service explorer

This project reproduces how the installed Sonos desktop controller discovers the
music-service accounts already configured in a household without a Sonos cloud
login.

```sh
/usr/bin/python3 sonos_accounts_gui.py
```

Use **Run all** to discover players, fetch the service catalog, subscribe to the
household event, and decode its configured account instances. The GUI redacts
credentials until **Reveal credential values locally** is enabled.

The currently selected pyenv Python on this Mac lacks `_tkinter`; use the system
Python command above for the GUI. The command-line decoder works with either
interpreter.

For a non-GUI report:

```sh
python3 decode_third_party_media_servers.py
```

The decoder's output intentionally includes the local account material in this
workspace; treat its JSON output as a secrets file.

The full protocol derivation and the Home Assistant integration direction are in
[REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md).
