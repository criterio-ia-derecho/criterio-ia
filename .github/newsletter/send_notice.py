"""Explicit post-publication notices. Defaults to preview; standard library only.

Durable Git tags reserve creation and dispatch before each external mutation.
An uncertain attempt stops for review rather than risking a duplicate message.
No subscriber addresses, provider response bodies, or credentials are logged.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "jurilupa-ia/jurilupa-ia"
REPO_ID = 1414741918
SITE = "https://jurilupa-ia.github.io/jurilupa-ia/"
API = "https://api.buttondown.com/v1/"
QUEUE_PATH = ".github/newsletter/queue.json"
PAGES_WORKFLOW_ID = 381572227
BACKLOG = frozenset({
    "citas-inexistentes-general-roca",
    "chatgpt-razonamiento-judicial-t323",
    "supervision-profesional-ayinde-al-haroun",
})
ACCEPTED = {"about_to_send", "in_flight", "throttled", "resending", "sent"}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_BYTES = 2_000_000


class Stop(RuntimeError):
    """Safe, public-log-friendly error, containing no response body or token."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method, url, *, token=None, body=None, key=None, github=False,
            missing_ok=False):
    # Authentication is never forwarded through a redirect or to another host.
    headers = {"User-Agent": "JuriLupaIA-article-notices/1", "Accept": "application/json"}
    if token:
        expected = "api.github.com" if github else "api.buttondown.com"
        if urlparse(url).scheme != "https" or urlparse(url).netloc != expected:
            raise Stop("Unexpected credential destination; stopped.")
        headers["Authorization"] = ("Bearer " if github else "Token ") + token
    if github:
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    if key:
        headers["X-Idempotency-Key"] = key
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with build_opener(NoRedirect()).open(Request(url, data=data, headers=headers,
                                                   method=method), timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise Stop("Response too large; stopped without logging it.")
        return json.loads(raw)
    except HTTPError as exc:
        if exc.code == 404 and missing_ok and method == "GET":
            return None
        # No retries for writes, including 429 and 5xx. A timeout may mean success.
        raise Stop(f"{method} request failed (HTTP {exc.code}); inspect provider/marker before retry.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise Stop(f"{method} response unavailable or invalid; outcome may be uncertain. No automatic retry.") from None


def git_file(sha, path):
    if not SHA.fullmatch(sha):
        raise Stop("Invalid deployed commit.")
    try:
        return subprocess.check_output(["git", "show", f"{sha}:{path}"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        raise Stop("Required public file is absent from deployed commit.") from None


def validate_queue(value):
    if not isinstance(value, dict) or set(value) != {"version", "entries"} or value["version"] != 1:
        raise Stop("Invalid queue schema.")
    entries = value["entries"]
    if not isinstance(entries, list) or len(entries) > 1000:
        raise Stop("Invalid queue entries.")
    seen, keys = set(), set()
    required = {"slug", "title", "summary", "published_date", "article_sha256", "create_key", "send_key"}
    for e in entries:
        if not isinstance(e, dict) or set(e) != required:
            raise Stop("Invalid notice fields.")
        if not isinstance(e["slug"], str) or not SLUG.fullmatch(e["slug"]) or len(e["slug"]) > 150:
            raise Stop("Invalid article slug.")
        if e["slug"] in BACKLOG or e["slug"] in seen:
            raise Stop("Existing backlog or duplicate article cannot be queued.")
        seen.add(e["slug"])
        for field, limit in (("title", 160), ("summary", 600)):
            text = e[field]
            if not isinstance(text, str) or not text.strip() or len(text) > limit or any(ord(c) < 32 for c in text):
                raise Stop("Notice text must be short, single-line public text.")
            if any(x in text for x in ("{{", "{%", "{#")):
                raise Stop("Provider template syntax is forbidden in article text.")
        try:
            published = date.fromisoformat(e["published_date"])
        except (ValueError, TypeError):
            raise Stop("Invalid explicit publication date.") from None
        if published > date.today():
            raise Stop("A future article cannot be announced.")
        if not isinstance(e["article_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", e["article_sha256"]):
            raise Stop("Invalid publication content hash.")
        for field in ("create_key", "send_key"):
            try:
                parsed = uuid.UUID(e[field])
            except (ValueError, TypeError, AttributeError):
                raise Stop("Invalid stable idempotency UUID.") from None
            if parsed.version != 4 or str(parsed) != e[field] or e[field] in keys:
                raise Stop("Idempotency UUIDs must be unique persisted UUID4 values.")
            keys.add(e[field])
    return entries


def article_url(entry):
    return SITE + "articulos/" + entry["slug"] + ".html"


def payload(entry):
    url = article_url(entry)
    return {
        "subject": "Nueva nota · " + entry["title"],
        "slug": "nota-" + entry["slug"],
        "canonical_url": url,
        "body": '<!-- buttondown-editor-mode: fancy -->'
                f'<p>{html.escape(entry["summary"])}</p>'
                f'<p><a href="{html.escape(url, quote=True)}">Leer la nota en JuriLupa IA</a></p>'
                '<p>Recibís este aviso porque confirmaste tu suscripción a las nuevas notas de JuriLupa IA.</p>',
        "status": "draft",
        "archival_mode": "disabled",
        "commenting_mode": "disabled",
        "template": "classic",  # Keep Buttondown's standard unsubscribe/footer handling.
        "should_trigger_pay_per_email_billing": False,
        "metadata": {"jurilupaia_article": entry["slug"], "jurilupaia_notice": "v1"},
    }


def verify_publication(entry, sha, read_file=git_file, fetch=None):
    expected = read_file(sha, "articulos/" + entry["slug"] + ".html")
    if hashlib.sha256(expected).hexdigest() != entry["article_sha256"]:
        raise Stop("Queued article differs from deployed bytes; review it before sending.")
    if fetch is None:
        try:
            req = Request(article_url(entry), headers={"User-Agent": "JuriLupaIA-publication-check/1", "Cache-Control": "no-cache"})
            with build_opener(NoRedirect()).open(req, timeout=30) as r:
                if r.status != 200 or r.geturl() != article_url(entry):
                    raise Stop("Article is not public at its exact expected URL.")
                actual = r.read(MAX_BYTES + 1)
        except (URLError, TimeoutError, OSError):
            raise Stop("Published article cannot be verified; no email sent.") from None
    else:
        actual = fetch(article_url(entry))
    if actual != expected:
        raise Stop("Live article differs from successful deployment; no email sent.")
    doc = html.unescape(expected.decode("utf-8"))
    if entry["title"] not in doc or entry["summary"] not in doc:
        raise Stop("Notice title/summary must already appear in the public article.")


class GitHub:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, **kw):
        return request(method, "https://api.github.com/repos/" + REPO + "/" + path,
                       token=self.token, github=True, **kw)

    def verify_run(self, run_id):
        if not re.fullmatch(r"[0-9]+", str(run_id)):
            raise Stop("A verified Pages run ID is required.")
        run = self.call("GET", "actions/runs/" + str(run_id))
        if (run.get("status") != "completed" or run.get("conclusion") != "success" or
            run.get("head_branch") != "main" or run.get("workflow_id") != PAGES_WORKFLOW_ID or
            run.get("name") != "pages build and deployment" or
            run.get("path") != "dynamic/pages/pages-build-deployment" or
            run.get("event") != "dynamic" or run.get("head_repository", {}).get("id") != REPO_ID or
            run.get("repository", {}).get("id") != REPO_ID or
            run.get("repository", {}).get("private") is not False):
            raise Stop("Only this public repository's successful main-branch Pages deployment is eligible.")
        sha = run.get("head_sha", "")
        if not SHA.fullmatch(sha):
            raise Stop("Missing deployed commit.")
        if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise Stop("Deployed commit is not in the trusted checkout's history.")
        return sha

    def exists(self, stage, slug):
        return self.call("GET", "git/ref/tags/newsletter-" + stage + "/" + slug,
                         missing_ok=True) is not None

    def reserve(self, stage, slug, sha):
        # Atomic create. Existing reference or unknown result stops this attempt.
        self.call("POST", "git/refs", body={"ref": "refs/tags/newsletter-" + stage + "/" + slug, "sha": sha})


class Buttondown:
    def __init__(self, token):
        if not token:
            raise Stop("Buttondown secret is absent; no provider call made.")
        self.token = token

    def call(self, method, path, **kw):
        return request(method, API + path, token=self.token, **kw)

    def existing(self, entry):
        matches = []
        total = None
        seen = 0
        account_seen = False
        # Only parse responses in memory. Never print or upload draft contents.
        for page in range(1, 101):
            result = self.call("GET", f"emails?page={page}")
            count, rows = result.get("count"), result.get("results")
            if type(count) is not int or count < 0 or not isinstance(rows, list):
                raise Stop("Cannot establish a complete email history.")
            if total is None:
                total = count
            elif total != count:
                raise Stop("Email history changed while reading; retry read later.")
            for email in rows:
                archive = email.get("absolute_url", "")
                if archive:
                    if not archive.startswith("https://buttondown.com/jurilupaia/archive/"):
                        raise Stop("Credential email history belongs to an unexpected newsletter; no write.")
                    account_seen = True
                if (email.get("metadata", {}).get("jurilupaia_article") == entry["slug"] or
                    email.get("canonical_url") == article_url(entry) or
                    email.get("slug") == "nota-" + entry["slug"]):
                    matches.append(email)
            seen += len(rows)
            if seen == count:
                if not account_seen:
                    raise Stop("Cannot verify jurilupaia from existing email history; first create a harmless draft in its authenticated UI.")
                if len(matches) > 1:
                    raise Stop("Multiple matching emails exist; manual review required.")
                return matches[0] if matches else None
            if not rows or seen > count:
                raise Stop("Incomplete email history; no create or send.")
        raise Stop("Email history is too large to verify safely.")

    def free_cap(self):
        # Count every stored subscriber conservatively (even inactive records).
        # The API includes a page of records; discard it immediately without logging.
        count = self.call("GET", "subscribers").get("count")
        active = self.call("GET", "subscribers?type=regular").get("count")
        if type(count) is not int or type(active) is not int or min(count, active) < 0 or active > count:
            raise Stop("Subscriber count is unavailable; no send.")
        if count > 100:
            raise Stop("Free safety cap reached (>100 subscriber records); no upgrade or charge authorized.")
        return active

    def create(self, entry):
        return self.call("POST", "emails", body=payload(entry), key=entry["create_key"])

    def retrieve(self, email_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", email_id):
            raise Stop("Invalid provider email identifier.")
        return self.call("GET", "emails/" + email_id)

    def publish(self, entry, email_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", email_id):
            raise Stop("Invalid provider email identifier.")
        # /publish applies these fields before dispatch: pin the actual content
        # and audience even if a concurrent draft edit races the last GET.
        reviewed = payload(entry)
        reviewed.pop("status")
        reviewed["filters"] = {"filters": [], "groups": [], "predicate": "and"}
        return self.call("POST", f"emails/{email_id}/publish", body=reviewed, key=entry["send_key"])


def validate_draft(entry, email):
    wanted = payload(entry)
    for field in ("subject", "slug", "canonical_url", "body", "archival_mode", "template", "metadata"):
        if email.get(field) != wanted[field]:
            raise Stop("Provider draft differs from reviewed notice; do not overwrite or send.")
    if email.get("status") != "draft" or email.get("should_trigger_pay_per_email_billing") is not False:
        raise Stop("Unexpected provider draft/billing status.")
    archive = email.get("absolute_url", "")
    if not archive.startswith("https://buttondown.com/jurilupaia/archive/"):
        raise Stop("Cannot verify draft belongs to Buttondown account jurilupaia.")
    filters = email.get("filters")
    if filters not in (None, {}, {"filters": [], "groups": [], "predicate": "and"}):
        raise Stop("Unexpected audience filters; manual review required.")


def process(entry, sha, mode, gh, bd, verify=verify_publication):
    slug = entry["slug"]
    if mode == "preview":
        verify(entry, sha)
        print("Preview only:", slug)
        return "preview"
    if mode not in ("draft", "live"):
        raise Stop("Unknown mode.")
    if gh.exists("skip-empty", slug):
        print("No recipients at publication; permanently skipped:", slug)
        return "skipped"
    email = bd.existing(entry)
    if gh.exists("send", slug):
        if email and email.get("status") in ACCEPTED:
            print("Already submitted; no repeat:", slug, email["status"])
            return email["status"]
        raise Stop("Dispatch reservation exists with an uncertain/failed outcome; inspect Buttondown. Never remove it blindly.")
    if email and email.get("status") != "draft":
        raise Stop("Matching email is not a draft; no resend allowed.")
    verify(entry, sha)
    active = bd.free_cap()
    if active == 0 and mode == "live":
        gh.reserve("skip-empty", slug, sha)
        print("No confirmed active recipients; permanently skipped:", slug)
        return "skipped"
    if email is None:
        if gh.exists("create", slug):
            raise Stop("Creation reservation exists but draft is absent; outcome uncertain, review manually.")
        gh.reserve("create", slug, sha)
        email = bd.create(entry)
    elif not gh.exists("create", slug):
        raise Stop("Unowned existing draft; no automatic takeover.")
    validate_draft(entry, email)
    if mode == "draft":
        print("Draft ready for review; no subscribers contacted:", slug)
        return "draft"
    # Recheck immediately before the one permitted dispatch attempt.
    if bd.free_cap() == 0:
        raise Stop("No confirmed active recipients at dispatch; paused.")
    validate_draft(entry, bd.retrieve(email["id"]))
    gh.reserve("send", slug, sha)
    result = bd.publish(entry, email["id"])
    status = result.get("status")
    if status not in ACCEPTED:
        raise Stop("Send outcome is uncertain/failed; reservation retained. Inspect Buttondown without resending.")
    print("Provider submission status:", slug, status)
    return status


def current_entries(deployed, current):
    """A later queue removal/correction cancels work from an older deployment."""
    by_slug = {e["slug"]: e for e in current}
    return [e for e in deployed if by_slug.get(e["slug"]) == e]


def main():
    mode = os.environ.get("NEWSLETTER_MODE", "preview") or "preview"
    if mode not in ("preview", "draft", "live"):
        raise Stop("Mode must be preview, draft, or live.")
    if mode != "preview" and os.environ.get("NEWSLETTER_ENABLED") != "true":
        raise Stop("Provider writes are disabled; preview mode only.")
    if mode != "preview" and os.environ.get("NEWSLETTER_FREE_ACCOUNT") != "free-no-payment-method":
        raise Stop("Free account and no payment method must be verified before authenticated operation.")
    if mode == "live" and os.environ.get("NEWSLETTER_LIVE_APPROVED") != "true":
        raise Stop("Live automation is not enabled after reviewed testing.")
    gh = GitHub(os.environ.get("GITHUB_TOKEN"))
    sha = gh.verify_run(os.environ.get("PAGES_RUN_ID", ""))
    try:
        deployed = validate_queue(json.loads(git_file(sha, QUEUE_PATH)))
        current = validate_queue(json.loads(Path(QUEUE_PATH).read_text()))
        entries = current_entries(deployed, current)
    except (ValueError, UnicodeError, OSError):
        raise Stop("Invalid publication queue.") from None
    if not entries:
        print("No explicitly queued new articles; nothing to send.")
        return
    bd = None if mode == "preview" else Buttondown(os.environ.get("BUTTONDOWN_API_KEY"))
    failures = 0
    for entry in entries:
        try:
            process(entry, sha, mode, gh, bd)
        except Stop as exc:
            # A blocked earlier article must not suppress an independent new one.
            print("STOP:", entry["slug"], str(exc), file=sys.stderr)
            failures += 1
    if failures:
        raise Stop(f"{failures} notice(s) need review; their reservations were retained.")


if __name__ == "__main__":
    try:
        main()
    except Stop as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(1)
