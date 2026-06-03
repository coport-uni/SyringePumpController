kill $(cat /tmp/sy01b-server.pid)
.venv/bin/sy01b-server --config server/pump.toml

# http://localhost:17050/docs