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
         "env": {
           "SKYSQL_API_KEY": "<your-skysql-api-key>"
         }
       }
     }
   }
   ```

> Cursor supports passing `env` variables directly in the MCP config.

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

> **Note:** Windsurf uses `serverUrl` (not `url`) and does not support the `env` parameter. Make sure `SKYSQL_API_KEY` is set in the `.env` file (step 3) or exported in your shell before starting the server.

6. (Optional) Test the server interactively with [MCP CLI](https://github.com/wong2/mcp-cli):
   ```bash
   npx @wong2/mcp-cli uv run python src/mcp-server/server.py
   ```
