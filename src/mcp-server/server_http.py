"""
HTTP transport entry point for SkySQL MCP Server.
This file is used for HTTP-based deployments (e.g., Smithery.ai).
For local stdio usage, use server.py instead.
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the mcp instance from server.py
from server import mcp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('skysql_mcp_server.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

if __name__ == "__main__":
    try:
        import uvicorn
        
        logger.info("Starting SkySQL MCP Server (HTTP mode)...")
        logger.info(f"Python version: {sys.version}")
        
        host = os.getenv("MCP_HOST", "0.0.0.0")
        # Smithery sets PORT environment variable, fallback to MCP_PORT for local testing
        port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8000")))
        logger.info(f"Starting HTTP server on {host}:{port}")
        
        # FastMCP exposes the HTTP app via .http_app() method
        try:
            app = mcp.run(transport="http", host=host, port=port)
        except AttributeError:
            logger.error("FastMCP http_app method not found. FastMCP may not support HTTP transport in this version.")
            logger.error("Available attributes: " + ", ".join([attr for attr in dir(mcp) if not attr.startswith('_')]))
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error calling http_app(): {str(e)}")
            sys.exit(1)
        
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError as e:
        logger.error(f"Missing required dependency: {str(e)}")
        logger.error("Please install uvicorn: pip install uvicorn>=0.27.0")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error starting server: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Server shutting down...")

