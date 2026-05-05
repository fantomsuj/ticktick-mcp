#!/usr/bin/env python3
"""
Command-line interface for TickTick Companion.
"""

import sys
import os
import argparse
import logging
from dotenv import load_dotenv


def check_auth_setup() -> bool:
    """Check if authentication is set up properly."""
    load_dotenv()
    return os.getenv("TICKTICK_ACCESS_TOKEN") is not None

def main():
    """Entry point for the CLI."""
    parser = argparse.ArgumentParser(description="TickTick Companion")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # 'run' command for running the server
    run_parser = subparsers.add_parser("run", help="Run the TickTick MCP server")
    run_parser.add_argument(
        "--debug", 
        action="store_true", 
        help="Enable debug logging"
    )
    run_parser.add_argument(
        "--transport", 
        default="stdio", 
        choices=["stdio"], 
        help="Transport type (currently only stdio is supported)"
    )
    
    # 'auth' command for authentication
    auth_parser = subparsers.add_parser("auth", help="Authenticate with TickTick")

    # 'dashboard' command for the local triage UI
    dash_parser = subparsers.add_parser("dashboard", help="Run the local triage dashboard")
    dash_parser.add_argument("--mock", action="store_true",
                             help="Run with seeded fake data (no credentials needed)")
    dash_parser.add_argument("--host", default="127.0.0.1")
    dash_parser.add_argument("--port", type=int, default=8765)
    dash_parser.add_argument("--no-browser", action="store_true")
    dash_parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()
    
    # If no command specified, default to 'run'
    if not args.command:
        args.command = "run"
    
    # The dashboard subcommand handles its own auth gate (and supports --mock).
    if args.command == "dashboard":
        from .dashboard.app import main as dashboard_main
        dash_argv = []
        if args.mock: dash_argv.append("--mock")
        dash_argv += ["--host", args.host, "--port", str(args.port)]
        if args.no_browser: dash_argv.append("--no-browser")
        if args.debug: dash_argv.append("--debug")
        sys.exit(dashboard_main(dash_argv))

    # For the run command, check if auth is set up
    if args.command == "run" and not check_auth_setup():
        print("""
╔════════════════════════════════════════════════╗
║        TickTick Companion Authentication       ║
╚════════════════════════════════════════════════╝

Authentication setup required!
You need to set up TickTick authentication before running the server.

Would you like to set up authentication now? (y/n): """, end="")
        choice = input().lower().strip()
        if choice == 'y':
            from .api.oauth import main as auth_main

            # Run the auth flow
            auth_result = auth_main()
            if auth_result != 0:
                # Auth failed, exit
                sys.exit(auth_result)
        else:
            print("""
Authentication is required to use TickTick Companion.
Run 'ticktick-companion auth' to set up authentication later.
            """)
            sys.exit(1)
    
    # Run the appropriate command
    if args.command == "auth":
        from .api.oauth import main as auth_main

        # Run authentication flow
        sys.exit(auth_main())
    elif args.command == "run":
        from .mcp.server import main as server_main

        # Configure logging based on debug flag
        log_level = logging.DEBUG if args.debug else logging.INFO
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        
        # Start the server
        try:
            server_main()
        except KeyboardInterrupt:
            print("Server stopped by user", file=sys.stderr)
            sys.exit(0)
        except Exception as e:
            print(f"Error starting server: {e}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
