import asyncio
import hashlib
import os
import httpx
import json
import logging
import re
import sys
import signal
from typing import Optional, List, Dict, Any, Union
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers, get_http_request
from pydantic import BaseModel
from dotenv import load_dotenv
import pymysql as mysql_connector

# Configure logging with both file and console handlers
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('skysql_mcp_server.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

# Signal handler for graceful shutdown
def signal_handler(signum, frame):
    logger.info(f"Signal handler called with signal {signum} from frame {frame}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Load environment variables from .env file
load_dotenv()

# Initialize MCP server with debug info
logger.info("Initializing MCP server...")
logger.debug("Python version: %s", sys.version)
logger.debug("Working directory: %s", os.getcwd())
logger.debug("Environment variables: %s", list(os.environ.keys()))
logger.debug("stdin isatty: %s", sys.stdin.isatty())
logger.debug("stdout isatty: %s", sys.stdout.isatty())
logger.debug("stderr isatty: %s", sys.stderr.isatty())
logger.debug("sys.argv: %s", sys.argv)
logger.debug("sys.executable: %s", sys.executable)
logger.debug("sys.path: %s", sys.path)

mcp = FastMCP("SkySQL MCP Server")

# Health check endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"}, status_code=200)

# Models for request/response handling
class ServerlessDBResponse(BaseModel):
    service_id: str
    name: str
    status: str

class AgentInfo(BaseModel):
    id: str
    name: str
    description: Optional[str]
    type: str
    status: str
    datasource_id: Optional[str]

class LlamaResponse(BaseModel):
    content: Optional[str]
    sql_text: str
    error_text: str
    col_keys: List[str]

# Cache of agent metadata, partitioned per API key. One process now serves many callers,
# so a single shared dict would let one tenant's agent ids and datasource_ids be used to
# build another tenant's request.
_agent_cache: Dict[str, Dict[str, Any]] = {}


def _cache_key() -> str:
    """Opaque per-caller cache partition. Hashed so raw keys aren't held as dict keys."""
    return hashlib.sha256(_resolve_api_key().encode()).hexdigest()

def _serving_http_request() -> bool:
    """True when this call is handling an HTTP request rather than running over stdio."""
    try:
        get_http_request()
        return True
    except RuntimeError:
        return False


def _env_api_key_allowed_over_http() -> bool:
    """Whether SKYSQL_API_KEY may serve HTTP requests (single-tenant deployments only)."""
    return os.getenv("SKYSQL_SINGLE_TENANT", "").lower() in ("1", "true", "yes")


def _resolve_api_key() -> str:
    """Resolve the caller's SkySQL API key.

    Over HTTP the key comes from the caller's own X-API-Key header. SKYSQL_API_KEY is a
    fallback for stdio/local runs, and over HTTP only when SKYSQL_SINGLE_TENANT is set:
    on a shared deployment, falling back to it would hand an unauthenticated caller the
    operator's SkySQL account. Clients that cannot send headers (Windsurf, Smithery) run
    single-tenant and opt in with that flag.
    """
    if _serving_http_request():
        api_key = get_http_headers().get("x-api-key")
        if api_key:
            logger.info("Using API key from X-API-Key header")
            return api_key

        if _env_api_key_allowed_over_http():
            api_key = os.getenv("SKYSQL_API_KEY")
            if api_key:
                logger.info("Using API key from SKYSQL_API_KEY (single-tenant mode)")
                return api_key

        raise ValueError(
            "No SkySQL API key supplied. Send your key in the X-API-Key request header, "
            "or run the server with SKYSQL_SINGLE_TENANT=true and SKYSQL_API_KEY set."
        )

    api_key = os.getenv("SKYSQL_API_KEY")
    if not api_key:
        raise ValueError(
            "SKYSQL_API_KEY not provided. Set the SKYSQL_API_KEY environment variable."
        )
    logger.info("Using API key from SKYSQL_API_KEY environment variable")
    return api_key


# SkySQL API client helper
async def get_skysql_client():
    api_key = _resolve_api_key()

    return httpx.AsyncClient(
        base_url="https://api.skysql.com",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=30.0  # Increase timeout to 30 seconds
    )

# Tool for listing available DB agents
@mcp.tool(annotations={
    "title": "List SkySQL DB Agents",
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def list_agents() -> str:
    """List all available SkySQL DB agents with their capabilities"""
    async with await get_skysql_client() as client:
        try:
            response = await client.get("/copilot/v1/agent/")
            response.raise_for_status()
            agents = response.json()

            # Cache agent information under this caller's partition
            _agent_cache[_cache_key()] = {agent['id']: agent for agent in agents}

            # Format the output to clearly show agent names and datasource IDs
            formatted_agents = []
            for agent in agents:
                agent_info = f"Name: {agent['name']}\n"
                agent_info += f"ID: {agent['id']}\n"
                agent_info += f"Type: {agent['type']}\n"
                if 'datasource_id' in agent:
                    agent_info += f"Datasource ID: {agent['datasource_id']}\n"
                else:
                    agent_info += "Datasource ID: None\n"
                if 'description' in agent:
                    agent_info += f"Description: {agent['description']}\n"
                agent_info += "---"
                formatted_agents.append(agent_info)

            return "\n\n".join(formatted_agents)
        except httpx.HTTPError as e:
            return f"Failed to list agents: {str(e)}"

# Tool for launching a serverless DB
# Marked destructive so Claude always confirms before provisioning billable infrastructure.
@mcp.tool(annotations={
    "title": "Launch Serverless Database",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
})
async def launch_serverless_db(name: str, region: str = "eastus", provider: str = "azure") -> str:
    """Launch a new Serverless DB instance in SkySQL"""
    # Convert name to lowercase
    name = name.lower()

    async with await get_skysql_client() as client:
        try:
            payload = {
                "topology": "serverless-standalone",
                "provider": provider,
                "region": region,
                "name": name
            }
            logger.debug(f"Launching serverless DB with payload: {json.dumps(payload, indent=2)}")
            response = await client.post(
                "/provisioning/v1/services",
                json=payload
            )
            logger.debug(f"Launch response status: {response.status_code}")
            logger.debug(f"Launch response body: {response.text}")

            response.raise_for_status()
            data = response.json()
            return f"Successfully launched serverless DB '{name}' with ID: {data['id']}"
        except httpx.HTTPError as e:
            logger.error(f"Failed to launch DB: {str(e)}")
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Error response body: {e.response.text}")
            return f"Failed to launch DB: {str(e)}"

# Tool for deleting a DB
@mcp.tool(annotations={
    "title": "Delete Database",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def delete_db(service_id: str) -> str:
    """Delete a DB instance from SkySQL"""
    async with await get_skysql_client() as client:
        try:
            logger.debug(f"Attempting to delete DB with ID: {service_id}")
            response = await client.delete(f"/provisioning/v1/services/{service_id}")
            logger.debug(f"Delete response status: {response.status_code}")
            logger.debug(f"Delete response body: {response.text}")
            
            response.raise_for_status()
            return f"Successfully deleted DB with ID: {service_id}"
        except httpx.HTTPError as e:
            logger.error(f"Failed to delete DB: {str(e)}")
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Error response body: {e.response.text}")
            return f"Failed to delete DB: {str(e)}"

# Tool for asking questions to DB agents
@mcp.tool(annotations={
    "title": "Ask a SkySQL DB Agent",
    "readOnlyHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
})
async def ask_agent(agent_id: str, question: str) -> str:
    """Ask a question to a specific DB agent"""
    async with await get_skysql_client() as client:
        try:
            # Get agent info from this caller's cache partition
            if agent_id not in _agent_cache.get(_cache_key(), {}):
                # If agent not in cache, refresh the cache
                await list_agents()
                if agent_id not in _agent_cache.get(_cache_key(), {}):
                    return f"Agent {agent_id} not found. Please check the agent ID and try again."

            agent_info = _agent_cache[_cache_key()][agent_id]
            # Prepare request payload
            request_payload = {
                "prompt": question,
                "agent_id": agent_id,
                "config": {}
            }
            # Only add datasource_id for DBA agents, not for IMDB or other agents
            if agent_info.get('type') == 'dba' and 'datasource_id' in agent_info:
                request_payload["datasource_id"] = agent_info["datasource_id"]
            
            logger.debug(f"Sending chat request with payload: {json.dumps(request_payload, indent=2)}")

            # Send the chat request directly
            try:
                chat_response = await client.post(
                    "/copilot/v1/chat/",
                    json=request_payload
                )

                # Log response details for debugging
                logger.debug(f"Response status: {chat_response.status_code}")
                logger.debug(f"Response headers: {dict(chat_response.headers)}")
                logger.debug(f"Response body: {chat_response.text}")
                
                chat_response.raise_for_status()
                chat_data = chat_response.json()

                # Format response with both explanation and SQL
                response_parts = []
                if chat_data["response"]["content"]:
                    response_parts.append(f"Analysis: {chat_data['response']['content']}")
                if chat_data["response"]["sql_text"]:
                    response_parts.append(f"Generated SQL:\n```sql\n{chat_data['response']['sql_text']}\n```")
                if chat_data["response"]["error_text"]:
                    response_parts.append(f"Errors: {chat_data['response']['error_text']}")

                return "\n\n".join(response_parts)
            except httpx.TimeoutException as e:
                logger.error(f"Request timed out after {client.timeout} seconds")
                return f"Request timed out. The API is taking longer than expected to respond. You may want to try again or check if the API is experiencing delays."

        except httpx.HTTPError as e:
            logger.error(f"Exception details: {str(e)}")
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Error response body: {e.response.text}")
            return f"Failed to get response from agent: {str(e)}"

# Prompts for common operations
@mcp.prompt()
def launch_db_prompt() -> str:
    """Create a prompt for launching a new serverless DB"""
    return """Please help me launch a new serverless database with the following specifications:
1. Name for the database (must be lowercase)
2. Region (optional, defaults to eastus)
3. Cloud provider (optional, one of: azure, aws, gcp. Defaults to azure)
"""

@mcp.prompt()
def delete_db_prompt() -> str:
    """Create a prompt for deleting a DB"""
    return """Please help me delete a database by providing:
1. The service ID of the database to delete.
2. Always confirm the deletion with me.
"""

@mcp.prompt()
def ask_agent_prompt() -> str:
    """Create a prompt for asking questions to DB agents"""
    return """I'd like to ask a question to a DB agent. Please provide:
1. The agent ID (use list_agents to see available agents)
2. Your question about database management
"""

async def _fetch_connection_details(service_id: str) -> Dict[str, Any]:
    """Fetch host, port and credentials for a service. Raises ValueError if the service is unknown."""
    async with await get_skysql_client() as client:
        # First get the service details to get hostname and port
        logger.debug(f"Fetching service details for ID: {service_id}")
        services_response = await client.get("/provisioning/v1/services")
        services_response.raise_for_status()
        services = services_response.json()

        # Find the matching service
        service = next((s for s in services if s['id'] == service_id), None)
        if not service:
            raise ValueError(f"Service with ID {service_id} not found")

        # Extract hostname and port from service details
        endpoint = service['endpoints'][0] if service.get('endpoints') else {}
        port = endpoint.get('ports', [{}])[0].get('port') if endpoint.get('ports') else None

        # Now get the credentials
        logger.debug(f"Fetching credentials for DB with ID: {service_id}")
        creds_response = await client.get(f"/provisioning/v1/services/{service_id}/security/credentials")
        logger.debug(f"Credentials response status: {creds_response.status_code}")

        creds_response.raise_for_status()
        creds_data = creds_response.json()

        return {
            "host": service.get('fqdn'),
            "port": port,
            "username": creds_data.get('username'),
            "password": creds_data.get('password'),
        }

@mcp.tool(annotations={
    "title": "Get Database Credentials",
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def get_db_credentials(service_id: str) -> str:
    """Get the credentials for a SkySQL database instance"""
    try:
        details = await _fetch_connection_details(service_id)
    except ValueError as e:
        return str(e)
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch credentials: {str(e)}")
        if isinstance(e, httpx.HTTPStatusError):
            logger.error(f"Error response body: {e.response.text}")
        return f"Failed to fetch credentials: {str(e)}"

    return f"""Database Credentials:
Host: {details['host'] or 'N/A'}
Port: {details['port'] or 'N/A'}
Username: {details['username'] or 'N/A'}
Password: {details['password'] or 'N/A'}"""

@mcp.tool(annotations={
    "title": "Add Current IP to Allowlist",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def update_ip_allowlist(service_id: str) -> str:
    """Update the IP allowlist for a SkySQL database instance with the current IP"""
    async with await get_skysql_client() as client:
        try:
            # First get the current IP. This must use a bare client: the SkySQL client
            # attaches X-API-Key to every request it makes, including absolute URLs, which
            # would hand the caller's key to a third-party host.
            async with httpx.AsyncClient(timeout=10.0) as ip_client:
                ip_response = await ip_client.get("https://checkip.amazonaws.com")
                ip_response.raise_for_status()
                current_ip = ip_response.text.strip()

            logger.debug(f"Current IP address: {current_ip}")

            # Update the allowlist
            payload = {
                "ip_address": f"{current_ip}/32"
            }
            response = await client.post(
                f"/provisioning/v1/services/{service_id}/security/allowlist",
                json=payload
            )
            logger.debug(f"Allowlist update response status: {response.status_code}")

            response.raise_for_status()
            return f"Successfully added IP {current_ip} to the allowlist for service {service_id}"
        except httpx.HTTPError as e:
            logger.error(f"Failed to update IP allowlist: {str(e)}")
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Error response body: {e.response.text}")
            return f"Failed to update IP allowlist: {str(e)}"

@mcp.tool(annotations={
    "title": "List SkySQL Database Services",
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def list_services() -> str:
    """List all available SkySQL database services"""
    async with await get_skysql_client() as client:
        try:
            logger.debug("Fetching all database services")
            response = await client.get("/provisioning/v1/services")
            response.raise_for_status()
            services = response.json()

            if not services:
                return "No database services found"

            # Format each service's information
            formatted_services = []
            for service in services:
                # Get endpoint details
                endpoint = service['endpoints'][0] if service.get('endpoints') else {}
                port = endpoint.get('ports', [{}])[0].get('port', 'N/A') if endpoint.get('ports') else 'N/A'

                service_info = [
                    f"Service: {service['name']}",
                    f"ID: {service['id']}",
                    f"Status: {service['status']}",
                    f"Type: {service['service_type']}",
                    f"Provider: {service['provider']}",
                    f"Region: {service['region']}",
                    f"Version: {service.get('version', 'N/A')}",
                    f"FQDN: {service.get('fqdn', 'N/A')}",
                    f"Port: {port}",
                    f"Created: {service.get('created_on', 'N/A')}",
                    "---"
                ]
                formatted_services.append("\n".join(service_info))

            return "\n\n".join(formatted_services)
        except httpx.HTTPError as e:
            logger.error(f"Failed to list services: {str(e)}")
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Error response body: {e.response.text}")
            return f"Failed to list services: {str(e)}"

# Statements that only read. Anything else has to go through run_write_query.
_READ_ONLY_STATEMENTS = frozenset({"select", "show", "describe", "desc", "explain", "with"})

# Write keywords, checked only for statements that open with a CTE, since MariaDB
# allows WITH ... UPDATE/DELETE.
_WRITE_KEYWORDS = frozenset({
    "insert", "update", "delete", "replace", "merge", "create", "drop",
    "alter", "truncate", "rename", "grant", "revoke", "call", "load",
})

_INTO_FILE = re.compile(r"\binto\s+(outfile|dumpfile)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z_]+")

# Opener of a MySQL/MariaDB executable comment: `/*!` , `/*!50300` , `/*M!` , `/*M!100200`.
# The server runs what is inside, so it is code, not a comment.
_EXEC_COMMENT_MARKER = re.compile(r"M?!\d*")


def _scrub(sql_query: str) -> str:
    """Strip comments and blank out string literals, for classification only.

    A regex can't do this: `/*` inside a quoted literal is not a comment, and a quote
    inside a comment does not open a literal. Getting that wrong lets a crafted literal
    hide the rest of a statement from the checks below, so scan with explicit state.
    """
    out = []
    i, n = 0, len(sql_query)
    quote = None  # active string/identifier delimiter, if any

    while i < n:
        ch = sql_query[i]

        if quote:
            if ch == "\\" and quote != "`":
                i += 2  # backslash escape; skip the escaped character
                continue
            if ch == quote:
                # A doubled delimiter is an escaped literal quote, not a terminator.
                if i + 1 < n and sql_query[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if ch in "'\"`":
            quote = ch
            out.append(" ")  # collapse the whole literal to whitespace
            i += 1
            continue

        if sql_query.startswith("/*", i):
            marker = _EXEC_COMMENT_MARKER.match(sql_query, i + 2)
            if marker:
                # Executable comment: MariaDB runs its contents, so drop only the marker and
                # let the loop scan the body as ordinary SQL. Discarding it instead would let
                # `WITH x AS (SELECT 1)/*!DELETE FROM t*/` read as a plain CTE.
                out.append(" ")
                i = marker.end()
                continue
            end = sql_query.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue

        # `--` only opens a comment when whitespace or end-of-input follows it; `--x` is two
        # minus signs. Treating it as a comment would hide `x` from the checks below.
        if ch == "#" or (
            sql_query.startswith("--", i)
            and (i + 2 >= n or sql_query[i + 2] in " \t\r\n\f\v")
        ):
            end = sql_query.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _reject_reason(sql_query: str) -> Optional[str]:
    """Return why this query is not read-only, or None if it is safe for run_read_query."""
    scrubbed = _scrub(sql_query)
    words = _WORD.findall(scrubbed)
    if not words:
        return "Empty query."

    # The read-only connection does not set MULTI_STATEMENTS, so the server rejects a second
    # statement anyway; refusing here keeps the guarantee even if that flag ever changes, and
    # explains the problem instead of returning a syntax error.
    if len([part for part in scrubbed.split(";") if part.strip()]) > 1:
        return "Only one statement can be run at a time."

    keyword = words[0].lower()
    if keyword not in _READ_ONLY_STATEMENTS:
        return (
            f"'{words[0]}' is not a read-only statement. "
            f"Use run_write_query for statements that modify data or schema."
        )

    # Checked against the raw query as well: if the scrubber were ever fooled into
    # hiding the clause, the raw match still catches it. False positives here only
    # redirect the caller to run_write_query.
    if _INTO_FILE.search(scrubbed) or _INTO_FILE.search(sql_query):
        return "SELECT ... INTO OUTFILE/DUMPFILE writes to disk. Use run_write_query."

    # A leading CTE can still wrap a write in MariaDB. Match on word boundaries rather
    # than whitespace splitting, so `(SELECT 1)DELETE` cannot slip through.
    if keyword == "with" and any(w.lower() in _WRITE_KEYWORDS for w in words):
        return "This CTE contains a write statement. Use run_write_query."

    return None


# LOAD DATA LOCAL INFILE makes the *client* — this container — read a file and upload it
# to whichever database the caller named. Since callers bring their own SkySQL account, that
# is a file-exfiltration primitive against the server, so it stays off unless an operator
# running single-tenant explicitly turns it back on.
_ALLOW_LOCAL_INFILE = os.getenv("SKYSQL_ALLOW_LOCAL_INFILE", "").lower() in ("1", "true", "yes")

# Cap on rows returned to the caller. The container is small and shared; an uncapped
# SELECT over a large table would materialize every row plus a markdown copy.
_MAX_ROWS = int(os.getenv("SKYSQL_MAX_ROWS", "1000"))


def _run_query(details: Dict[str, Any], sql_query: str, allow_writes: bool) -> str:
    """Open a connection, run one query and format the result. Blocking — call via a thread."""
    client_flag = 0
    if allow_writes:
        client_flag |= mysql_connector.constants.CLIENT.MULTI_STATEMENTS
    if allow_writes and _ALLOW_LOCAL_INFILE:
        client_flag |= mysql_connector.constants.CLIENT.LOCAL_FILES

    conn = mysql_connector.connect(
        host=details['host'],
        port=int(details['port']),
        user=details['username'],
        password=details['password'],
        ssl_verify_cert=True,
        ssl={"verify_cert": True},
        # Both flags stay off on the read path: multi-statement would let a second statement
        # ride in behind an allowed SELECT, and LOCAL INFILE reads files off this container.
        local_infile=allow_writes and _ALLOW_LOCAL_INFILE,
        client_flag=client_flag,
        autocommit=True,
    )

    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql_query)

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(_MAX_ROWS)
                result = ["| " + " | ".join(columns) + " |"]
                result.append("| " + " | ".join(["---" for _ in columns]) + " |")
                for row in rows:
                    result.append("| " + " | ".join(_format_cell(val) for val in row) + " |")
                if cursor.fetchone() is not None:
                    result.append(
                        f"\n_Truncated at {_MAX_ROWS} rows. Narrow the query or add LIMIT/OFFSET "
                        f"to see the rest._"
                    )
                return "\n".join(result)

            # For DDL/DML queries that don't return results
            return f"Query executed successfully. Rows affected: {cursor.rowcount}"
        except mysql_connector.Error as e:
            return f"SQL Error [{e.args[0]}]: {e.args[1]}"
        finally:
            cursor.close()
    finally:
        conn.close()


def _format_cell(value: Any) -> str:
    """Render one cell so pipes in the data don't break the markdown table."""
    if value is None:
        return "NULL"
    return str(value).replace("|", "\\|")


async def _execute(service_id: str, sql_query: str, allow_writes: bool) -> str:
    try:
        details = await _fetch_connection_details(service_id)
    except ValueError as e:
        return str(e)
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch credentials: {str(e)}")
        return f"Failed to fetch credentials: {str(e)}"

    # `is None`, not truthiness: an empty password is valid on some instances.
    if any(details.get(k) is None for k in ('host', 'port', 'username', 'password')):
        return "Missing connection details"

    try:
        return await asyncio.to_thread(_run_query, details, sql_query, allow_writes)
    except mysql_connector.Error as e:
        return f"Database connection error [{e.args[0]}]: {e.args[1]}"
    except Exception as e:
        logger.error(f"Failed to execute query: {str(e)}")
        return f"Failed to execute query: {str(e)}"


@mcp.tool(annotations={
    "title": "Run Read-Only SQL Query",
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
})
async def run_read_query(service_id: str, sql_query: str) -> str:
    """Run a single read-only SQL statement against a SkySQL database and return the rows as a
    markdown table.

    Accepts one SELECT, SHOW, DESCRIBE, EXPLAIN or WITH statement in MariaDB SQL syntax
    (reference: https://mariadb.com/kb/en/sql-statements/). Statements that write data or
    schema are rejected — use run_write_query for those.
    """
    reason = _reject_reason(sql_query)
    if reason:
        return f"Rejected: {reason}"
    return await _execute(service_id, sql_query, allow_writes=False)


@mcp.tool(annotations={
    "title": "Run Writing SQL Query",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
})
async def run_write_query(service_id: str, sql_query: str) -> str:
    """Run a data- or schema-modifying SQL statement against a SkySQL database.

    Use for INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, TRUNCATE, LOAD DATA and other
    statements that change state, in MariaDB SQL syntax (reference:
    https://mariadb.com/kb/en/sql-statements/). Runs with autocommit on, so changes cannot be
    rolled back. For queries that only read, use run_read_query.

    LOAD DATA reads files on the database server; the LOCAL variant, which would read files
    from this MCP server, is disabled.
    """
    return await _execute(service_id, sql_query, allow_writes=True)

# Update the main block with enhanced error handling and Windows compatibility
if __name__ == "__main__":
    try:
        logger.info("Starting SkySQL MCP Server (stdio mode)...")
        logger.info(f"Python version: {sys.version}")

        # Ensure stdin/stdout are in binary mode for Windows compatibility
        if sys.platform == "win32":
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        # Run the server in stdio mode
        mcp.run()
    except Exception as e:
        logger.error(f"Error starting server: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Server shutting down...") 
