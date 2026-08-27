[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/skysqlinc-skysql-mcp-badge.png)](https://mseep.ai/app/skysqlinc-skysql-mcp)

# SkySQL MCP Server

[![Trust Score](https://archestra.ai/mcp-catalog/api/badge/quality/skysqlinc/skysql-mcp)](https://archestra.ai/mcp-catalog/skysqlinc__skysql-mcp)

This package contains everything needed to set up the SkySQL/MariaDB Cloud MCP (Model Context Protocol) server, which provides a powerful interface for managing SkySQL MariaDB database instances and interacting with AI Agents.

## Features

- Launch and manage serverless MariaDB database instances
- Interact with AI-powered database agents
- Execute SQL queries directly on SkySQL (MySQL/MariaDB) instances
- Manage database credentials and IP allowlists
- List and monitor database services

## Installation

#### Prerequisites
- Python 3.10 or higher
- A SkySQL/MariaDB Cloud API key

### Option 1: Run locally

#### Installation steps

1. Clone the repository:
   ```bash
   git clone git@github.com:skysqlinc/skysql-mcp.git
   cd skysql-mcp
   ```

2. Run the installation script:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

3. Create a `.env` file in the root directory of the cloned git repository with your SkySQL API key. Obtain API key by signing up for free on [SkySQL](https://app.skysql.com).

   ```
   SKYSQL_API_KEY=<your_skysql_api_key_here>
   ```

4. Start the MCP server (HTTP mode):
   ```bash
   chmod +x launch.sh
   ./launch.sh
   ```
   The server will start on `http://localhost:8000/mcp` by default.

5. Configure your IDE:

#### Cursor

Add the following to your Cursor MCP config (`~/.cursor/mcp.json` or `.cursor/mcp.json` in your project):

   ```json
   {
     "mcpServers": {
       "skysql-mcp-server": {
         "url": "http://localhost:8000/mcp",
         "headers": {
           "X-API-Key": "<your-skysql-api-key>"
         }
       }
     }
   }
   ```

> The `X-API-Key` header is sent with each request, allowing per-user API keys when the server is hosted remotely.

#### Windsurf

Add the following to your Windsurf MCP config (`~/.codeium/windsurf/mcp_config.json`):

   ```json
   {
     "mcpServers": {
       "skysql-mcp-server": {
         "serverUrl": "http://localhost:8000/mcp"
       }
     }
   }
   ```

> **Note:** Windsurf uses `serverUrl` (not `url`) and cannot send request headers. Start the
> server in single-tenant mode so it falls back to the API key in your environment:
>
> ```bash
> SKYSQL_SINGLE_TENANT=true ./launch.sh
> ```
>
> With `SKYSQL_API_KEY` set in `.env` (step 3). Without `SKYSQL_SINGLE_TENANT`, the server
> requires every HTTP caller to supply its own `X-API-Key` header and will reject requests that
> don't — deliberately, so a shared deployment can never serve requests using the operator's key.

#### Claude

Add the server as a custom connector (**Settings > Connectors > Add custom connector**), then supply
your API key under **Request headers**:

| Field | Value |
| --- | --- |
| URL | your deployed server URL, ending in `/mcp` |
| Header name | `x-api-key` |
| Header value | your SkySQL API key, with no prefix |
| Required | yes |

Generate the key at [app.skysql.com/user-profile/api-keys](https://app.skysql.com/user-profile/api-keys).
Enter it exactly as-is — Claude sends the value verbatim and does not add a scheme or prefix.

> Claude requires a publicly reachable **HTTPS** URL, so `http://localhost:8000/mcp` will not work
> here; deploy the server first. Request header authentication is currently in beta and may need to
> be enabled for your organization.

6. (Optional) Test the server interactively with [MCP CLI](https://github.com/wong2/mcp-cli):
   ```bash
   npx @wong2/mcp-cli uv run python src/mcp-server/server.py
   ```
