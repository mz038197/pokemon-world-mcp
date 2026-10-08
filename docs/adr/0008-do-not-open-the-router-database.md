# This service does not open the router database

pokemon-world-mcp checks a Classroom API Key from its signature and the Revocation List. A legacy key is sent to the router at `POST /internal/legacy-key`. Game saves and the catalog cache stay in this service's own database. This service does not open the router's database, including not for `api_keys`.

## Considered Options

- **Keep a router database connection only to verify keys**: rejected. Verification no longer reads those tables, and the game data already lives elsewhere.
