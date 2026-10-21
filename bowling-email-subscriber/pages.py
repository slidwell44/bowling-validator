"""Public HTML pages for OAuth consent screen verification.

Google's OAuth app verification requires a home page that links to a
privacy policy, and a privacy policy served as responsive HTML with
sufficient detail about data collection and usage. These routes serve
both pages directly from the Cloud Run service.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

_PAGE_STYLE = """
    body { font-family: system-ui, sans-serif; margin: 0; color: #1a1a1a;
           background: #fafafa; line-height: 1.6; }
    main { max-width: 720px; margin: 0 auto; padding: 3rem 1.25rem; }
    h1 { font-size: 1.9rem; margin-bottom: 0.25rem; }
    h2 { font-size: 1.2rem; margin-top: 2rem; }
    a { color: #0b57d0; }
    nav { margin-top: 2rem; padding-top: 1.25rem;
          border-top: 1px solid #ddd; font-size: 1.05rem; }
    nav a { margin-right: 1.25rem; font-weight: 600; }
    footer { max-width: 720px; margin: 0 auto; padding: 0 1.25rem 2rem;
             color: #666; font-size: 0.85rem; }
    code { background: #eee; padding: 0.1rem 0.3rem; border-radius: 4px; }
"""

HOME_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bowling Subscriber</title>
<meta name="description" content="Bowling Subscriber monitors a Gmail inbox
for bowling league invitation emails and accepts them automatically.">
<style>{_PAGE_STYLE}</style>
</head>
<body>
<main>
<h1>Bowling Subscriber</h1>
<p>
Bowling Subscriber is a personal automation service that watches a single
Gmail inbox for bowling league invitation emails and, when one arrives,
accepts the invitation on the account owner's behalf by submitting the
league's sign-up form.
</p>
<p>
The service uses the Gmail API with read-only access
(<code>gmail.readonly</code>) and runs on Google Cloud Run. It does not
send, modify, or delete email.
</p>
<nav>
<a href="/privacy">Privacy Policy</a>
<a href="/docs">API documentation</a>
</nav>
</main>
<footer>
<p>Bowling Subscriber &mdash; automated bowling league invite responder.</p>
</footer>
</body>
</html>
"""

PRIVACY_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Policy — Bowling Subscriber</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
<main>
<h1>Privacy Policy</h1>
<p><em>Effective date: October 21, 2026</em></p>

<p>
This privacy policy describes how Bowling Subscriber ("the service")
accesses, uses, stores, and protects information. The service is a
personal automation tool operated by the owner of the Gmail account it
monitors, hosted on Google Cloud Run.
</p>

<h2>Information We Access</h2>
<p>
The service connects to the Gmail API using OAuth 2.0 with a single
read-only scope, <code>https://www.googleapis.com/auth/gmail.readonly</code>.
This grants the service permission to read email messages and metadata
(such as sender, subject, labels, and history identifiers) in the one
Gmail account that completed the OAuth authorization flow. The service
cannot send, modify, or delete email, and it does not request access to
any other Google service.
</p>

<h2>How We Use Information</h2>
<p>
The service receives Gmail push notifications via Google Cloud Pub/Sub
when the monitored inbox changes. In response, it reads new messages to
identify bowling league invitation emails. When an invitation is found,
the service parses the message content (such as the proposed date) and
submits the league's sign-up form to accept the invitation on the account
owner's behalf. Message content is processed transiently in memory for
this purpose only.
</p>
<p>
Google user data is used solely to provide this invitation-acceptance
functionality. It is not used for advertising, profiling, market research,
or any other purpose, and it is never used to train machine learning
models.
</p>

<h2>Information We Store</h2>
<p>
To avoid processing the same message twice and to resume from the correct
point in the mailbox history, the service stores the following in a
secured database:</p>
<ul>
<li>Gmail message identifiers of messages already processed</li>
<li>Mailbox history cursors (history IDs) and timestamps</li>
<li>Gmail watch subscription expiration timestamps</li>
</ul>
<p>
Email subject lines and message bodies are not permanently stored. The
OAuth refresh token used to access the Gmail API is stored as an
encrypted Google Cloud secret and is never written to logs.
</p>

<h2>Information Sharing</h2>
<p>
The service does not sell, rent, trade, or otherwise share Google user
data with any third party. Data leaves the service only in two ways:
(a) API calls to Google (Gmail API, Google Cloud Pub/Sub) to perform the
functions described above, and (b) submission of the bowling league's
own sign-up form when accepting an invitation, which transmits only the
information that form requests.
</p>

<h2>Data Retention and Deletion</h2>
<p>
Processing metadata (message IDs, history cursors, timestamps) is
retained only as long as the service operates. You may revoke the
service's access to your Google account at any time at
<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>;
revoking access stops all further data access immediately. To request
deletion of stored processing metadata, contact the operator at the
address below and it will be deleted promptly.
</p>

<h2>Security</h2>
<p>
The service runs on Google Cloud Run and accepts only authenticated
requests from Google-operated service accounts. Credentials are managed
through Google Cloud Secret Manager, and all traffic is encrypted in
transit with TLS.
</p>

<h2>Limited Use Disclosure</h2>
<p>
The service's use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google
API Services User Data Policy</a>, including the Limited Use requirements.
</p>

<h2>Changes to This Policy</h2>
<p>
If this policy changes, the updated version will be published at this URL
with a revised effective date.
</p>

<h2>Contact</h2>
<p>
Questions about this policy or requests to delete stored data may be sent
to the operator of this service at the Gmail account this service
monitors.
</p>

<nav>
<a href="/">Home</a>
</nav>
</main>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def home_page() -> HTMLResponse:
    return HTMLResponse(HOME_HTML)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy() -> HTMLResponse:
    return HTMLResponse(PRIVACY_HTML)
