"""Isolated manual draft-creation check. Never dispatches to any recipient.

The owner-only delivery check happens in Buttondown's draft-preview UI after this
succeeds. No recipient address, subscriber change, publication queue, or Git tag
is involved here. Do not replace this with /publish.
"""
import hashlib
import html
import os
import sys

from send_notice import Buttondown, GitHub, Stop, article_url, email_url_kind, git_file, verify_publication

SMOKE_SLUG = "prueba-tecnica-jurilupaia-v1"
# Public idempotency request ID, NOT a credential. Preserve it across reruns.
CREATE_KEY = "c2d26825-021c-4b19-8e51-9758e5b81b97"
PUBLIC_ARTICLE = {
    "slug": "citas-inexistentes-general-roca",
    "title": "¿Quién responde cuando una cita judicial no existe?",
    "summary": "El caso de General Roca permite practicar una habilidad central: comprobar cada precedente antes de convertir una respuesta de IA en un argumento profesional.",
}


def smoke_payload():
    return {
        "subject": "Prueba técnica · JuriLupa IA",
        "slug": "nota-" + SMOKE_SLUG,
        "canonical_url": article_url(PUBLIC_ARTICLE),
        "body": '<!-- buttondown-editor-mode: fancy -->'
                '<p>Prueba técnica autorizada para comprobar el formato y el enlace. '
                'No es un aviso de una nueva nota y no modifica ninguna suscripción.</p>'
                f'<p>{html.escape(PUBLIC_ARTICLE["title"])}</p>'
                f'<p>{html.escape(PUBLIC_ARTICLE["summary"])}</p>'
                f'<p><a href="{article_url(PUBLIC_ARTICLE)}">Leer la nota pública en JuriLupa IA</a></p>',
        "status": "draft",
        "archival_mode": "disabled",
        "commenting_mode": "disabled",
        "template": "classic",
        "should_trigger_pay_per_email_billing": False,
        "metadata": {"jurilupaia_smoke_test": "v1"},
    }


def verify_smoke(email):
    expected = smoke_payload()
    for field in expected:
        if email.get(field) != expected[field]:
            # Never log complete provider responses or unreviewed draft content.
            raise Stop("Technical draft differs in field " + field + "; inspect normalization in Buttondown before enabling live mode.")
    if email_url_kind(email) == "unexpected":
        raise Stop("Technical draft belongs to an unexpected newsletter.")
    filters = email.get("filters")
    if filters not in (None, {}, {"filters": [], "groups": [], "predicate": "and"}):
        raise Stop("Unexpected technical draft audience filters.")


def prepare(sha, bd, verify=verify_publication, read_file=git_file):
    public = dict(PUBLIC_ARTICLE)
    raw = read_file(sha, "articulos/" + public["slug"] + ".html")
    public["article_sha256"] = hashlib.sha256(raw).hexdigest()
    verify(public, sha)
    # existing() also verifies account identity, with read-only history first.
    draft = bd.existing({"slug": SMOKE_SLUG})
    bd.free_cap()  # Zero active recipients is valid for an owner-only draft test.
    if draft is None:
        draft = bd.call("POST", "emails", body=smoke_payload(), key=CREATE_KEY)
    verify_smoke(draft)
    print("Technical draft ready for owner-only preview in Buttondown:", draft["id"])
    print("Provider draft URL form:", email_url_kind(draft))
    print("No recipient contacted, no subscription changed, no queue or Git tag changed.")
    return draft["id"]


def main():
    if os.environ.get("NEWSLETTER_FREE_ACCOUNT") != "free-no-payment-method":
        raise Stop("Verify Free/no payment method before creating a technical draft.")
    gh = GitHub(os.environ.get("GITHUB_TOKEN"))
    sha = gh.verify_run(os.environ.get("PAGES_RUN_ID", ""))
    bd = Buttondown(os.environ.get("BUTTONDOWN_API_KEY"))
    prepare(sha, bd)


if __name__ == "__main__":
    try:
        main()
    except Stop as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(1)
