"""
MCP-verktøy som lar e-postagenten lese og håndtere e-poster
fra de delte Storebrand-innboksene direkte fra samtalen.

Start serveren med:
    python mcp_email_tool.py
"""

import json
from email_reader import (
    Email,
    SHARED_MAILBOXES,
    get_access_token,
    get_email,
    list_emails,
    mark_as_read,
)

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

server = Server("storebrand-email")


def _email_to_text(email: Email) -> str:
    return (
        f"Fra: {email.sender}\n"
        f"Til: {email.mailbox}\n"
        f"Mottatt: {email.received:%Y-%m-%d %H:%M}\n"
        f"Emne: {email.subject}\n"
        f"Lest: {'Ja' if email.is_read else 'Nei'}\n"
        f"ID: {email.id}\n"
        f"---\n"
        f"{email.body_preview}"
    )


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_inbox_emails",
            description=(
                "Henter e-poster fra en delt Storebrand-innboks. "
                "Innbokser: SIOS@storebrand.no, ik@storebrand.no, "
                "clientdeliveries@contact.storebrand.no"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mailbox": {
                        "type": "string",
                        "description": "E-postadressen til innboksen",
                        "enum": SHARED_MAILBOXES,
                    },
                    "top": {
                        "type": "integer",
                        "description": "Antall e-poster å hente (standard: 10)",
                        "default": 10,
                    },
                    "only_unread": {
                        "type": "boolean",
                        "description": "Bare uleste e-poster (standard: true)",
                        "default": True,
                    },
                },
                "required": ["mailbox"],
            },
        ),
        types.Tool(
            name="get_email_details",
            description="Henter full innhold for en enkelt e-post.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mailbox": {
                        "type": "string",
                        "description": "E-postadressen til innboksen",
                        "enum": SHARED_MAILBOXES,
                    },
                    "message_id": {
                        "type": "string",
                        "description": "ID-en til e-posten (fra list_inbox_emails)",
                    },
                },
                "required": ["mailbox", "message_id"],
            },
        ),
        types.Tool(
            name="mark_email_as_read",
            description="Markerer en e-post som lest i innboksen.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mailbox": {
                        "type": "string",
                        "enum": SHARED_MAILBOXES,
                    },
                    "message_id": {
                        "type": "string",
                        "description": "ID-en til e-posten som skal markeres som lest",
                    },
                },
                "required": ["mailbox", "message_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    token = get_access_token()

    if name == "list_inbox_emails":
        emails = list_emails(
            mailbox=arguments["mailbox"],
            top=arguments.get("top", 10),
            only_unread=arguments.get("only_unread", True),
            token=token,
        )
        if not emails:
            text = f"Ingen {'uleste ' if arguments.get('only_unread', True) else ''}e-poster i {arguments['mailbox']}."
        else:
            text = f"{len(emails)} e-poster i {arguments['mailbox']}:\n\n"
            text += "\n\n".join(_email_to_text(e) for e in emails)

    elif name == "get_email_details":
        email = get_email(
            mailbox=arguments["mailbox"],
            message_id=arguments["message_id"],
            token=token,
        )
        text = _email_to_text(email)

    elif name == "mark_email_as_read":
        mark_as_read(
            mailbox=arguments["mailbox"],
            message_id=arguments["message_id"],
            token=token,
        )
        text = f"E-post {arguments['message_id']} er markert som lest."

    else:
        text = f"Ukjent verktøy: {name}"

    return [types.TextContent(type="text", text=text)]


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.server.stdio.run(server))
