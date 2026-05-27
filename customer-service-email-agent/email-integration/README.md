# E-post integrasjon – Microsoft Graph API

Denne integrasjonen gir e-postagenten tilgang til de delte innboksene:
- `SIOS@storebrand.no`
- `ik@storebrand.no`
- `clientdeliveries@contact.storebrand.no`

## Forutsetninger

### 1. Registrer en Azure AD-applikasjon

1. Gå til [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**
2. Gi appen et navn, f.eks. `Caroline-EmailAgent`
3. Velg **Accounts in this organizational directory only**
4. Klikk **Register**

### 2. Sett opp API-tillatelser

Under **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**:

| Tillatelse        | Beskrivelse                                     |
|-------------------|-------------------------------------------------|
| `Mail.Read`       | Lese e-post fra delte innbokser                 |
| `Mail.ReadWrite`  | Markere e-post som lest (valgfritt)             |

> ⚠️ **Application permissions** (ikke Delegated) krever adminsamtykke. Be en Azure AD-admin om å klikke **Grant admin consent**.

### 3. Opprett en klienthemmelighet

**Certificates & secrets** → **New client secret** → noter ned verdien.

### 4. Konfigurer miljøvariabler

```bash
AZURE_TENANT_ID=<din-tenant-id>
AZURE_CLIENT_ID=<din-app-client-id>
AZURE_CLIENT_SECRET=<din-klienthemmelighet>
```

Lagre disse i en `.env`-fil (aldri commit til git).

---

## Filer

| Fil                  | Beskrivelse                                         |
|----------------------|-----------------------------------------------------|
| `email_reader.py`    | Leser e-post fra delte innbokser via Graph API      |
| `mcp_email_tool.py`  | MCP-verktøy som e-postagenten kaller direkte        |
| `requirements.txt`   | Python-avhengigheter                                |

---

## Kjør lokalt

```bash
pip install -r requirements.txt
python email_reader.py
```

## MCP-integrasjon

Legg til `mcp_email_tool.py` som en MCP-server i agentoppsettet ditt for at
e-postagenten skal kunne lese innboks direkte fra samtalen.
