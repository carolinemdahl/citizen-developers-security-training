"""
Leser e-post fra delte Outlook-innbokser via Microsoft Graph API.

Bruker app-only autentisering (client credentials) som tillater
tilgang til delte postbokser uten brukerinnlogging.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import msal
import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.environ["AZURE_TENANT_ID"]
CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPE = ["https://graph.microsoft.com/.default"]

SHARED_MAILBOXES = [
    "SIOS@storebrand.no",
    "ik@storebrand.no",
    "clientdeliveries@contact.storebrand.no",
]


@dataclass
class Email:
    id: str
    subject: str
    sender: str
    received: datetime
    body_preview: str
    body_html: str
    is_read: bool
    mailbox: str


def get_access_token() -> str:
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Kunne ikke hente token: {result.get('error_description')}")
    return result["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_emails(
    mailbox: str,
    top: int = 20,
    only_unread: bool = True,
    token: Optional[str] = None,
) -> list[Email]:
    """Henter e-poster fra en delt innboks."""
    token = token or get_access_token()
    filter_query = "isRead eq false" if only_unread else ""
    params = {
        "$top": top,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,bodyPreview,body,isRead",
    }
    if filter_query:
        params["$filter"] = filter_query

    url = f"{GRAPH_BASE}/users/{mailbox}/mailFolders/inbox/messages"
    response = requests.get(url, headers=_headers(token), params=params, timeout=30)
    response.raise_for_status()

    emails = []
    for msg in response.json().get("value", []):
        emails.append(
            Email(
                id=msg["id"],
                subject=msg.get("subject", "(ingen emne)"),
                sender=msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                received=datetime.fromisoformat(
                    msg["receivedDateTime"].replace("Z", "+00:00")
                ),
                body_preview=msg.get("bodyPreview", ""),
                body_html=msg.get("body", {}).get("content", ""),
                is_read=msg.get("isRead", False),
                mailbox=mailbox,
            )
        )
    return emails


def get_email(mailbox: str, message_id: str, token: Optional[str] = None) -> Email:
    """Henter en enkelt e-post med full innhold."""
    token = token or get_access_token()
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    params = {"$select": "id,subject,from,receivedDateTime,bodyPreview,body,isRead"}
    response = requests.get(url, headers=_headers(token), params=params, timeout=30)
    response.raise_for_status()
    msg = response.json()
    return Email(
        id=msg["id"],
        subject=msg.get("subject", "(ingen emne)"),
        sender=msg.get("from", {}).get("emailAddress", {}).get("address", ""),
        received=datetime.fromisoformat(
            msg["receivedDateTime"].replace("Z", "+00:00")
        ),
        body_preview=msg.get("bodyPreview", ""),
        body_html=msg.get("body", {}).get("content", ""),
        is_read=msg.get("isRead", False),
        mailbox=mailbox,
    )


def mark_as_read(mailbox: str, message_id: str, token: Optional[str] = None) -> None:
    """Markerer en e-post som lest."""
    token = token or get_access_token()
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    requests.patch(
        url, headers=_headers(token), json={"isRead": True}, timeout=30
    ).raise_for_status()


def list_all_inboxes(top: int = 10) -> dict[str, list[Email]]:
    """Henter uleste e-poster fra alle konfigurerte innbokser."""
    token = get_access_token()
    return {
        mailbox: list_emails(mailbox, top=top, only_unread=True, token=token)
        for mailbox in SHARED_MAILBOXES
    }


if __name__ == "__main__":
    print("Henter uleste e-poster...\n")
    all_emails = list_all_inboxes(top=5)
    for mailbox, emails in all_emails.items():
        print(f"📬 {mailbox}: {len(emails)} uleste")
        for email in emails:
            print(f"  [{email.received:%Y-%m-%d %H:%M}] {email.sender}: {email.subject}")
            print(f"    {email.body_preview[:80]}...")
        print()
