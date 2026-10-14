"""One authorized owner-only Morgan preview; no publication or subscriber writes.

A durable Git reservation is taken before ANY provider mutation. Once reserved,
all future invocations stop, including after errors/timeouts. Never remove it to
retry without inspecting the provider and obtaining a fresh authorization.
"""
import hashlib
import os
import re
import sys
from pathlib import Path

from send_notice import Buttondown, GitHub, Stop, article_url, email_url_kind, verify_publication

OWNER_SHA256 = "31e8c0bd49795def01cbeb12ccd629beaa2f06c79f3046e69c5332953b7c9e47"
PUBLISHED_SHA = "b200fc673bdb4728af5d83aee7350cc3c2e0d6e3"
TEST_SLUG = "owner-preview-morgan-20261014-v1"
RESERVATION_STAGE = "owner-test-attempt"
CREATE_KEY = "5d14f635-e554-44ed-992c-488ae9986020"
SEND_KEY = "d425bd10-96bc-4ad7-ba05-211c58a97129"
COMMIT_MESSAGE = "Send one authorized owner-only Morgan newsletter preview"
ARTICLE = {
    "slug": "ia-documentos-confidenciales-morgan-v2x",
    "title": "¿Qué podemos cargar en una IA cuando preparamos un juicio?",
    "summary": "Morgan v. V2X permite estudiar cómo aprovechar la asistencia de una IA sin confundir la protección de la estrategia con el permiso para compartir documentos reservados.",
    "article_sha256": "fef4c2fdc8c8198c082adbe74871324f14d7fe666e696d09b18ea685faa64eac",
}


def draft_payload():
    # All content is already public. No user data appears in the draft body.
    return {
        "subject": "Prueba · " + ARTICLE["title"],
        "slug": "nota-" + TEST_SLUG,
        "canonical_url": article_url(ARTICLE),
        "body": '<!-- buttondown-editor-mode: fancy -->'
                '<p>Prueba de JuriLupa IA para revisar el formato y el enlace. '
                'No es un envío a la lista de suscriptores.</p>'
                '<p>' + ARTICLE["title"] + '</p>'
                '<p>' + ARTICLE["summary"] + '</p>'
                '<p><a href="' + article_url(ARTICLE) + '">Leer la nota en JuriLupa IA</a></p>',
        "status": "draft",
        "archival_mode": "disabled",
        "commenting_mode": "disabled",
        "template": "classic",
        "should_trigger_pay_per_email_billing": False,
        "metadata": {"jurilupaia_owner_preview": TEST_SLUG},
    }


def verify_draft(draft):
    for field, value in draft_payload().items():
        if draft.get(field) != value:
            raise Stop("Owner preview differs in field " + field + "; stopped.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(draft.get("id", ""))):
        raise Stop("Invalid preview ID.")
    if email_url_kind(draft) == "unexpected":
        raise Stop("Unexpected preview newsletter URL.")
    if draft.get("filters") not in (None, {}, {"filters": [], "groups": [], "predicate": "and"}):
        raise Stop("Unexpected preview filters.")


def verified_owner(bd):
    # Resolve privately from the already-connected provider, never public source.
    account = bd.call("GET", "accounts/me")
    address = account.get("email_address")
    if (not isinstance(address, str) or
        hashlib.sha256(address.encode("utf-8")).hexdigest() != OWNER_SHA256):
        raise Stop("Connected account owner differs from the one approved recipient.")
    return address


def run(gh, bd, verify=verify_publication):
    if gh.exists(RESERVATION_STAGE, TEST_SLUG):
        raise Stop("One-time owner preview already reserved; no retry. Inspect delivery before any new action.")
    verify(ARTICLE, PUBLISHED_SHA)
    # Establish credential newsletter identity via read-only history. Never reuse
    # or overwrite a preexisting draft (even one apparently created by this test).
    if bd.existing({"slug": TEST_SLUG}) is not None:
        raise Stop("An owner preview already exists; no automatic reuse or resend.")
    owner = verified_owner(bd)
    bd.free_cap()
    # Atomic durable lock comes before draft creation and survives every failure.
    gh.reserve(RESERVATION_STAGE, TEST_SLUG, PUBLISHED_SHA)
    draft = bd.call("POST", "emails", body=draft_payload(), key=CREATE_KEY)
    verify_draft(draft)
    verify_draft(bd.retrieve(draft["id"]))
    # Fingerprint-pinned recipient, empty subscribers, and ONLY /send-draft.
    # Never /publish, never subscriber creation, never subscription changes.
    result = bd.call("POST", "emails/" + draft["id"] + "/send-draft",
                     body={"recipients": [owner], "subscribers": []}, key=SEND_KEY)
    if result != {}:
        raise Stop("Preview response unexpected; inspect inbox/provider. Do not retry.")
    print("Buttondown accepted the single owner-only Morgan preview; inbox receipt is not yet verified.")


def main():
    if (os.environ.get("GITHUB_EVENT_NAME") != "push" or
        os.environ.get("GITHUB_REF") != "refs/heads/main" or
        os.environ.get("GITHUB_REPOSITORY") != "jurilupa-ia/jurilupa-ia" or
        os.environ.get("GITHUB_RUN_ATTEMPT") != "1" or
        os.environ.get("OWNER_TEST_COMMIT_MESSAGE") != COMMIT_MESSAGE):
        raise Stop("Only the explicitly authorized first push attempt is eligible.")
    if os.environ.get("NEWSLETTER_FREE_ACCOUNT") != "free-no-payment-method":
        raise Stop("Verified free/no-payment account is required.")
    # This isolated test never consumes or modifies the public publication queue.
    import json
    if json.loads(Path(".github/newsletter/queue.json").read_text()) != {"version": 1, "entries": []}:
        raise Stop("Public queue is not empty; stopped for independent review.")
    run(GitHub(os.environ.get("GITHUB_TOKEN")), Buttondown(os.environ.get("BUTTONDOWN_API_KEY")))


if __name__ == "__main__":
    try:
        main()
    except Stop as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(1)
